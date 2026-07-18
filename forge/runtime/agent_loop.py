'''Multi-step model and tool execution for the M1 Agent Loop.'''

from collections.abc import AsyncIterator
from functools import cache
import json
from pathlib import Path
from typing import Any

from forge.context.compactor import CompactionConfig
from forge.context.manager import (
    CompactionReport,
    ContextManager,
    ContextStats,
)
from forge.runtime.model_client import (
    AnthropicModelClient,
    ModelCallError,
    ModelClient,
    ModelProtocolError,
)
from forge.runtime.completion import CompletionGate, TaskPolicy
from forge.runtime.state import (
    CompletionBlocked,
    ConversationEvent,
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    ModelTextDelta,
    ModelToolCallCompleted,
    ModelUsageUpdate,
    TokenUsage,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
    TurnResult,
    ToolCall,
    VerificationCompleted,
    VerificationEvidence,
    WorkspaceChanged,
)
from forge.runtime.workspace import WorkspaceTracker
from forge.tools.base import ToolRegistry, ToolResult


class ModelResponseError(RuntimeError):
    '''Raised when a model response cannot continue the Agent Loop.'''


class AgentLoopLimitError(RuntimeError):
    '''Raised when one user turn exceeds its model-call safety limit.'''


@cache
def load_system_prompt() -> str:
    '''Load the packaged ForgeCode identity and behavior prompt.'''
    prompt_path = Path(__file__).resolve().parents[1] / 'prompts' / 'system.md'
    prompt = prompt_path.read_text(encoding='utf-8').strip()
    if not prompt:
        raise RuntimeError('ForgeCode system prompt is empty.')
    return prompt


class Conversation:
    '''Keep model-visible message history for an interactive session.'''

    def __init__(
        self,
        client: ModelClient | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        registry: ToolRegistry | None = None,
        max_iterations: int = 100,
        task_policy: TaskPolicy | None = None,
        max_completion_blocks: int = 3,
        context_config: CompactionConfig | None = None,
        context_root: Path | None = None,
        max_protocol_recoveries: int = 2,
    ) -> None:
        if tools is not None and registry is not None:
            raise ValueError('Pass tools or registry, not both.')
        if max_iterations < 1:
            raise ValueError('max_iterations must be positive')
        if max_completion_blocks < 1:
            raise ValueError('max_completion_blocks must be positive')
        if max_protocol_recoveries < 0:
            raise ValueError('max_protocol_recoveries must not be negative')
        self.client = (
            client if client is not None else AnthropicModelClient.from_config()
        )
        self.system_prompt = (
            system_prompt
            if system_prompt is not None
            else load_system_prompt()
        )
        self.messages: list[dict[str, Any]] = []
        self.registry = registry
        self.tools = registry.definitions if registry is not None else tools
        self.max_iterations = max_iterations
        tracker = (
            getattr(registry, 'workspace_tracker', None)
            if registry is not None
            else None
        )
        self.workspace_tracker: WorkspaceTracker | None = tracker
        resolved_context_root = (
            context_root
            if context_root is not None
            else tracker.root
            if tracker is not None
            else Path.cwd()
        )
        self.context = ContextManager(
            self.messages,
            resolved_context_root,
            context_config,
        )
        self.completion_gate = (
            CompletionGate(tracker.root, task_policy)
            if tracker is not None
            else None
        )
        self.max_completion_blocks = max_completion_blocks
        self.max_protocol_recoveries = max_protocol_recoveries
        self._last_repository_context = self.context.repository.system_suffix('')

    @property
    def context_stats(self) -> ContextStats:
        '''Return current committed conversation context statistics.'''
        return self.context.stats_for_request(
            system_prompt=self.system_prompt,
            repository_context=self._last_repository_context,
            tools=self.tools,
            context_window_tokens=getattr(
                self.client,
                'context_window',
                None,
            ),
            reserved_output_tokens=getattr(self.client, 'max_tokens', 0),
        )

    async def stream(self, prompt: str) -> AsyncIterator[ConversationEvent]:
        '''Run model-tool cycles until the model returns a final text answer.'''
        if not prompt.strip():
            raise ValueError('prompt must not be empty')

        user_message = {'role': 'user', 'content': prompt}
        request_messages = [*self.messages, user_message]
        completed_usage = TokenUsage(input_tokens=0, output_tokens=0)
        all_tool_calls: list[ToolCall] = []
        latest_verification: VerificationEvidence | None = None
        mutation_attempted = False
        completion_blocks = 0
        if self.workspace_tracker is not None:
            await self.workspace_tracker.begin_turn()

        self._last_repository_context = (
            self.context.repository.system_suffix(prompt)
        )
        request_system_prompt = self.system_prompt
        if self._last_repository_context:
            request_system_prompt += '\n\n' + self._last_repository_context
        await self.context.compact_history(
            request_messages,
            self.client,
            system_prompt=self.system_prompt,
            repository_context=self._last_repository_context,
            tools=self.tools,
            context_window_tokens=getattr(
                self.client,
                'context_window',
                None,
            ),
            reserved_output_tokens=getattr(
                self.client,
                'max_tokens',
                0,
            ),
        )
        reactive_compaction_attempted = False
        protocol_recoveries = 0

        for iteration in range(1, self.max_iterations + 1):
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            request_usage: TokenUsage | None = None

            yield ModelCallStarted(iteration=iteration)
            try:
                async for event in self.client.stream(
                    messages=self.context.prepare(request_messages),
                    tools=self.tools,
                    system=request_system_prompt,
                ):
                    if isinstance(event, ModelTextDelta):
                        text_parts.append(event.text)
                        yield event
                    elif isinstance(event, ModelToolCallCompleted):
                        tool_calls.append(event.tool_call)
                        yield event
                    elif isinstance(event, ModelUsageUpdate):
                        request_usage = event.usage
                        yield ModelUsageUpdate(
                            usage=add_token_usage(
                                completed_usage,
                                request_usage,
                            )
                        )
                    else:
                        yield event
            except Exception as error:
                if (
                    isinstance(error, ModelCallError)
                    and error.reason == 'context_overflow'
                    and not reactive_compaction_attempted
                ):
                    reactive_compaction_attempted = True
                    report = await self.context.compact_history(
                        request_messages,
                        self.client,
                        force=True,
                    )
                    if report is not None and report.success:
                        continue
                if (
                    isinstance(error, ModelProtocolError)
                    and protocol_recoveries < self.max_protocol_recoveries
                ):
                    protocol_recoveries += 1
                    if request_usage is not None:
                        completed_usage = add_token_usage(
                            completed_usage,
                            request_usage,
                        )
                    yield ModelCallFailed(
                        iteration=iteration,
                        reason=error.reason,
                        retryable=True,
                    )
                    request_messages.extend(
                        build_protocol_recovery_feedback(
                            error,
                            attempt=protocol_recoveries,
                            maximum=self.max_protocol_recoveries,
                            available_tools=(
                                self.registry.names
                                if self.registry is not None
                                else ()
                            ),
                        )
                    )
                    continue
                yield ModelCallFailed(
                    iteration=iteration,
                    reason=(
                        error.reason
                        if isinstance(
                            error,
                            (ModelCallError, ModelProtocolError),
                        )
                        else type(error).__name__
                    ),
                    retryable=(
                        error.retryable
                        if isinstance(error, ModelCallError)
                        else False
                    ),
                )
                raise
            yield ModelCallCompleted(iteration=iteration)

            text = ''.join(text_parts).strip()
            if not text and not tool_calls:
                raise ModelResponseError(
                    'Model response did not contain any text or tool calls.'
                )
            if request_usage is None:
                raise ModelResponseError(
                    'Model response did not contain token usage.'
                )

            completed_usage = add_token_usage(
                completed_usage,
                request_usage,
            )
            tool_calls.sort(key=lambda call: call.index)
            request_messages.append(
                build_assistant_message(text, tool_calls)
            )

            if not tool_calls:
                if (
                    self.workspace_tracker is not None
                    and self.completion_gate is not None
                ):
                    change = await self.workspace_tracker.refresh()
                    if change is not None:
                        yield WorkspaceChanged(
                            revision=change.revision,
                            paths=change.paths,
                        )
                    decision = await self.completion_gate.evaluate(
                        self.workspace_tracker,
                        latest_verification,
                        mutation_attempted=mutation_attempted,
                    )
                    if not decision.allowed:
                        completion_blocks += 1
                        yield CompletionBlocked(
                            attempt=completion_blocks,
                            reasons=decision.reasons,
                        )
                        if completion_blocks < self.max_completion_blocks:
                            request_messages.append(
                                build_completion_feedback(decision.reasons)
                            )
                            continue
                        self.messages[:] = request_messages
                        self.context.capture_explicit_memory(prompt)
                        yield TurnCompleted(
                            result=TurnResult(
                                text=text,
                                usage=completed_usage,
                                tool_calls=tuple(all_tool_calls),
                                status='blocked',
                                changed_paths=(
                                    self.workspace_tracker.changed_paths
                                ),
                                verification=latest_verification,
                                completion_reasons=decision.reasons,
                            )
                        )
                        return
                self.messages[:] = request_messages
                self.context.capture_explicit_memory(prompt)
                yield TurnCompleted(
                    result=TurnResult(
                        text=text,
                        usage=completed_usage,
                        tool_calls=tuple(all_tool_calls),
                        changed_paths=(
                            self.workspace_tracker.changed_paths
                            if self.workspace_tracker is not None
                            else ()
                        ),
                        verification=latest_verification,
                    )
                )
                return

            if self.registry is None:
                raise ModelResponseError(
                    'Model requested tools, but no ToolRegistry is configured.'
                )

            all_tool_calls.extend(tool_calls)
            tool_results: list[tuple[ToolCall, ToolResult]] = []
            for tool_call in tool_calls:
                tool_effect = self.registry.effect(tool_call.name)
                if tool_effect == 'workspace_write':
                    mutation_attempted = True
                yield ToolExecutionStarted(tool_call=tool_call)
                result = await self.registry.execute(
                    tool_call.name,
                    tool_call.arguments,
                )
                tool_results.append((tool_call, result))
                yield ToolExecutionCompleted(
                    tool_call=tool_call,
                    result=result,
                )
                if self.workspace_tracker is not None:
                    change = await self.workspace_tracker.refresh()
                    if change is not None:
                        if tool_effect == 'process':
                            mutation_attempted = True
                        yield WorkspaceChanged(
                            revision=change.revision,
                            paths=change.paths,
                        )
                if tool_call.name == 'verify':
                    latest_verification = verification_from_result(result)
                    if latest_verification is not None:
                        yield VerificationCompleted(
                            evidence=latest_verification
                        )
            request_messages.append(build_tool_result_message(tool_results))

        raise AgentLoopLimitError(
            f'Agent Loop exceeded {self.max_iterations} model calls.'
        )

    async def compact(self) -> CompactionReport:
        '''Manually summarize committed history for the /compact command.'''
        if not self.messages:
            return CompactionReport(
                success=True,
                automatic=False,
                before_characters=0,
                after_characters=0,
                transcript_path=None,
                reason='conversation history is empty',
            )
        report = await self.context.compact_history(
            self.messages,
            self.client,
            force=True,
        )
        if report is None:
            raise AssertionError('Forced compaction did not return a report.')
        return report

    def remember(self, name: str, content: str) -> str:
        record = self.context.remember(name, content)
        return f'Remembered {record.name} in {record.path.as_posix()}'

    def memory_list(self) -> str:
        records = self.context.repository.memory.list()
        if not records:
            return 'No repository memories.'
        return '\n'.join(
            f'- {record.name} [{record.memory_type}]: {record.description}'
            for record in records
        )

    def memory_show(self, name: str) -> str:
        record = self.context.repository.memory.get(name)
        if record is None:
            return f'Memory not found: {name}'
        return (
            f'{record.name} [{record.memory_type}]\n'
            f'{record.description}\n\n{record.content}'
        )

    def memory_forget(self, name: str) -> str:
        removed = self.context.repository.memory.forget(name)
        return f'Forgot {name}.' if removed else f'Memory not found: {name}'

    def memory_rebuild(self) -> str:
        path = self.context.repository.memory.rebuild_index()
        return f'Rebuilt memory index: {path.as_posix()}'

    def memory_consolidate(self) -> str:
        removed = self.context.repository.memory.consolidate()
        return f'Consolidated memory; removed {removed} duplicate(s).'


def build_assistant_message(
    text: str,
    tool_calls: list[ToolCall],
) -> dict[str, Any]:
    '''Build model-visible assistant history from a completed response.'''
    if not tool_calls:
        return {'role': 'assistant', 'content': text}

    content: list[dict[str, Any]] = []
    if text:
        content.append({'type': 'text', 'text': text})
    content.extend(
        {
            'type': 'tool_use',
            'id': call.id,
            'name': call.name,
            'input': call.arguments,
        }
        for call in sorted(tool_calls, key=lambda call: call.index)
    )
    return {'role': 'assistant', 'content': content}


def build_tool_result_message(
    tool_results: list[tuple[ToolCall, ToolResult]],
) -> dict[str, Any]:
    '''Build one user message containing ordered Anthropic tool results.'''
    content: list[dict[str, Any]] = []
    for tool_call, result in tool_results:
        content.append(
            {
                'type': 'tool_result',
                'tool_use_id': tool_call.id,
                'content': serialize_tool_result(result),
                'is_error': not result.success,
            }
        )
    return {'role': 'user', 'content': content}


def serialize_tool_result(result: ToolResult) -> str:
    '''Serialize the stable ToolResult contract for model consumption.'''
    error = None
    if result.error is not None:
        error = {
            'code': result.error.code,
            'message': result.error.message,
            'details': result.error.details,
        }
    return json.dumps(
        {
            'success': result.success,
            'summary': result.summary,
            'content': result.content,
            'error': error,
            'metadata': result.metadata,
        },
        ensure_ascii=False,
        default=str,
    )


def verification_from_result(
    result: ToolResult,
) -> VerificationEvidence | None:
    '''Build stable evidence from one verify ToolResult metadata payload.'''
    metadata = result.metadata
    if metadata.get('verification') is not True:
        return None
    try:
        return VerificationEvidence(
            command=str(metadata['command']),
            cwd=str(metadata['cwd']),
            exit_code=int(metadata['exit_code']),
            duration_seconds=float(metadata['duration_seconds']),
            timed_out=bool(metadata['timed_out']),
            workspace_revision=int(metadata['workspace_revision']),
        )
    except (KeyError, TypeError, ValueError):
        return None


def build_completion_feedback(reasons: tuple[str, ...]) -> dict[str, Any]:
    '''Tell the model exactly why its final answer was not accepted.'''
    details = '\n'.join(f'- {reason}' for reason in reasons)
    return {
        'role': 'user',
        'content': (
            'ForgeCode completion check rejected the previous final answer.\n'
            f'{details}\n'
            'Continue using the available tools, then provide a new final '
            'answer after every condition is satisfied.'
        ),
    }


def build_protocol_recovery_feedback(
    error: ModelProtocolError,
    *,
    attempt: int,
    maximum: int,
    available_tools: tuple[str, ...],
) -> list[dict[str, Any]]:
    '''Represent a rejected response and request one smaller valid retry.'''
    tool = f' for tool {error.tool_name!r}' if error.tool_name else ''
    if error.reason == 'output_truncated':
        problem = 'The previous response reached the max_tokens limit.'
    elif error.reason == 'unavailable_tool':
        problem = f'The previous response requested unavailable tool{tool}.'
    else:
        problem = f'The previous tool call{tool} had invalid arguments.'
    available = (
        ', '.join(available_tools) if available_tools else 'none'
    )
    retry_limit = 4_000 if attempt == 1 else 2_000
    retry_strategy = (
        'Modify only one function or one file section.'
        if attempt == 1
        else (
            'Create only a minimal skeleton. Keep HTML, CSS, and JavaScript '
            'in separate tool calls.'
        )
    )
    return [
        {
            'role': 'assistant',
            'content': '[ForgeCode rejected an invalid model response.]',
        },
        {
            'role': 'user',
            'content': (
                f'{problem}\nError: {error}\n'
                'No tool was executed and no file was changed by that '
                f'response. Available tools: {available}. For a small complete '
                f'file, use write_file with at most {retry_limit} characters. '
                'For a '
                'focused exact change, use replace_text. For structured edits, '
                f'use apply_patch with at most {retry_limit} characters. '
                f'{retry_strategy} Split large '
                'HTML, CSS, or JavaScript across multiple calls and do not '
                'repeat the same invalid arguments.\n'
                f'Recovery attempt {attempt} of {maximum}.'
            ),
        },
    ]


def add_token_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    '''Add exact usage from separate model requests in one user turn.'''
    return TokenUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cache_creation_input_tokens=(
            left.cache_creation_input_tokens
            + right.cache_creation_input_tokens
        ),
        cache_read_input_tokens=(
            left.cache_read_input_tokens + right.cache_read_input_tokens
        ),
    )
