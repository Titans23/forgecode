'''The single model/tool turn loop, independent of product event consumers.'''

from __future__ import annotations

import asyncio
from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING

from forge.context.working import WorkingState
from forge.hooks import HookEvent
from forge.runtime.model_budget import BudgetedModelClient
from forge.runtime.model_client import ModelCallError, ModelOutputTruncatedError, ModelProtocolError
from forge.runtime.state import (
    CompletionBlocked, ModelCallCompleted, ModelCallFailed, ModelCallStarted,
    ModelTextDelta, ModelToolCallCompleted, ModelUsageUpdate, ToolCall,
    ToolExecutionCompleted, ToolExecutionStarted, TurnCompleted, TurnResult,
    VerificationCompleted, WorkspaceChanged,
)
from forge.runtime.turn_state import BudgetExhausted, TurnState, add_usage
from forge.tools.base import ToolResult

if TYPE_CHECKING:
    from forge.runtime.agent_loop import Conversation


class TurnRunner:
    '''Orchestrate requests, sequential batches, evidence and one terminal path.

    No recovery phase owns a tool list or grants permission. No progress counter
    enlarges the budget or terminates a valid diagnostic strategy early.
    '''

    def __init__(self, conversation: Conversation) -> None:
        self.conversation = conversation
        self.state = TurnState(
            max_model_calls=conversation.max_iterations,
            max_tool_calls=conversation.max_tool_calls,
            max_input_tokens=conversation.max_turn_input_tokens,
            max_seconds=conversation.max_turn_seconds,
        )
        self.calls: list[ToolCall] = []
        self.messages: list[dict] = []
        self.checkpoint_id: str | None = None
        self.read_only = False
        self.owns_task = False
        self.terminal: tuple[str, str, str, tuple[str, ...]] | None = None
        self.finished = False
        self.completion_attempts = 0
        self.output_continuations = 0
        self.continued_text = ''
        self.reactive_compaction_used = False
        self.batch_active = False
        self.pending_feedback: list[str] = []
        self.pending_completion_reasons: tuple[str, ...] = ()
        self.mutation_attempted = False
        self.mutation_failed = False

    @property
    def tracker(self):
        return self.conversation.workspace_tracker

    @property
    def journal(self):
        return self.conversation.session_journal

    async def run(self, prompt: str):
        if not prompt.strip():
            raise ValueError('prompt must not be empty')
        c = self.conversation
        c.turn_state = self.state
        c._kernel_owns_events = True
        self.state.goal = prompt
        self.messages = [*c.messages]
        try:
            async with asyncio.timeout(self.state.max_seconds):
                await self._prepare_turn(prompt)
                async for event in self._loop():
                    yield event
        except BudgetExhausted as error:
            yield await self._finish('failed', error.reason, error.reason, (error.reason,))
        except TimeoutError:
            yield await self._finish(
                'failed', 'time_budget_exhausted',
                'The turn time budget was exhausted; existing work was preserved.',
                ('time_budget_exhausted',),
            )
        except (asyncio.CancelledError, GeneratorExit):
            if self.journal is not None and not self.finished:
                self._commit_messages()
                self.journal.record_turn_cancelled(
                    'Turn interrupted. Indeterminate operations require inspection, not replay.',
                )
            raise
        except Exception as error:
            self._commit_messages()
            c.record_session_error(error)
            raise
        finally:
            c._kernel_owns_events = False

    async def _prepare_turn(self, prompt: str) -> None:
        c = self.conversation
        if c.tool_executor is not None:
            c.tool_executor.session_journal = self.journal
            c.tool_executor.checkpoint_store = c.checkpoint_store
        c.permission_manager.bind_session(self.journal)
        await c.session_start(source='stream')
        if self.journal is not None:
            self.journal.record_turn_started(prompt, c.task_manager.active)
        c._persisted_event_keys.clear()
        decision = None
        router = c.intent_router
        if c.task_relation is not None:
            from forge.runtime.router import TurnDecision
            continuing = c.task_relation == 'active' and c.task_manager.active is not None
            decision = TurnDecision(
                intent='continue_task' if continuing else 'new_task',
                task_relation='active' if continuing else 'new',
                requires_workspace_change=False, confidence=1,
                reason='Explicit task contract supplied by the caller.',
            )
        elif router is not None:
            original_client = getattr(router, 'client', None)
            if original_client is not None:
                router.client = BudgetedModelClient(original_client, self.state, 'routing')
            else:
                self._check_budget()
                self.state.record_model_request(stage='routing')
            try:
                routed = await router.route(prompt, c.task_manager.active, self.messages)
            finally:
                if original_client is not None:
                    router.client = original_client
            if original_client is None:
                self.state.record_usage(routed.usage)
            decision = routed.decision
            if self.journal is not None:
                self.journal.append('turn_routed', {
                    'decision': decision.model_dump(),
                    'raw_response': routed.raw_response[:4000],
                    'degraded_reason': routed.degraded_reason,
                })
        previous = c.task_manager.active
        if decision is not None:
            self.read_only = decision.intent in {'read_only', 'conversation', 'task_query', 'ambiguous'}
            if not self.read_only:
                self.owns_task = True
                if decision.task_relation == 'active' and previous is not None:
                    c.task_manager.continue_active(prompt, requires_change=previous.requires_change)
                    self.state.goal = previous.goal
                else:
                    c.task_manager.start(prompt, requires_change=False)
        if c.mcp_manager is not None:
            await c.mcp_manager.ensure_connected()
        if c.checkpoint_store is not None:
            self.checkpoint_id = c.checkpoint_store.begin()
            if self.journal is not None:
                self.journal.record_checkpoint_created(self.checkpoint_id, c.messages, c.task_manager.active)
        user_message = {'role': 'user', 'content': prompt}
        self.messages.append(user_message)
        if self.journal is not None:
            self.journal.record_user_message(user_message, c.task_manager.active)
        if self.tracker is not None:
            await self.tracker.begin_turn()
            if previous is not None and decision is not None and decision.intent == 'continue_task':
                self.tracker.carry_existing_changes(previous.workspace_paths)
        c.working_state = WorkingState()
        c._last_repository_context = c._repository_context(prompt)
        c._last_task_context = c.task_manager.system_suffix()
        self._commit_messages()

    def _check_budget(self) -> None:
        reason = self.state.budget_reason()
        if reason:
            raise BudgetExhausted(reason)

    def _tools(self):
        c = self.conversation
        definitions = c._permission_filtered_tools(c._tool_definitions())
        if self.read_only and c.registry is not None and definitions is not None:
            definitions = [
                item for item in definitions
                if c.registry.effect(item['name']) == 'read_only'
                and item['name'] not in {'task_plan', 'task_update', 'finish_task'}
            ]
        return definitions

    def _system(self) -> str:
        c = self.conversation
        available = ', '.join(item['name'] for item in (self._tools() or ())) or 'none'
        return '\n\n'.join(part for part in (
            c._system_prompt_with_task(), c._last_repository_context,
            c._permission_system_context(),
            '[Current turn contract]\n' + self.state.goal,
            '[Runtime request tools]\nOnly the following tools are included in this model request: ' + available,
            'Task plans and scope hints organize work; they do not grant permissions. '
            'Tool batches execute sequentially and stop after a failure. Failed checks '
            'remain diagnostic evidence. Explain what verification actually covers and '
            'what remains untested; a zero exit code alone does not prove the user goal. '
            'Either a final answer or finish_task submits the same completion request. '
            'Use failed for an unsuccessful attempt, blocked only for a specific external dependency.',
            '[Remaining turn budget]\n'
            + f'Model requests used: {self.state.model_calls}/{self.state.max_model_calls}; '
            + f'tool requests used: {self.state.tool_requests}/{self.state.max_tool_calls}; '
            + (f'time remaining: {max(0, int(self.state.max_seconds - (monotonic() - self.state.started_at)))} seconds. '
               if self.state.max_seconds is not None else 'No turn time limit. ')
            + 'Choose command timeouts within this remaining budget. Reserve time to validate '
              'the actual deliverable. Repeated dependency downloads/builds consume this same budget.',
        ) if part)

    async def _compact(self, system: str, tools, *, force: bool = False) -> None:
        c = self.conversation
        kwargs = dict(
            system_prompt=system, tools=tools,
            context_window_tokens=getattr(c.client, 'context_window', None),
            reserved_output_tokens=getattr(c.client, 'max_tokens', 0),
        )
        if not force and not c.context.compaction_required(self.messages, **kwargs):
            return
        self._check_budget()
        outcome = await c._emit_hook(HookEvent(
            name='BeforeCompact', session_id=c._session_id(),
            payload={'automatic': not force, 'message_count': len(self.messages)},
        ))
        c._queue_hook_context(outcome)
        if not outcome.allowed:
            return
        report = await c.context.compact_history(
            self.messages, BudgetedModelClient(c.client, self.state, 'compaction'),
            force=force, task_goal=self.state.goal, **kwargs,
        )
        if report is not None and report.success and self.journal is not None:
            self.journal.record_context_compacted(self.messages)
        self._commit_messages()

    async def _loop(self):
        from forge.runtime.agent_loop import ModelResponseError, build_assistant_message
        c = self.conversation
        while True:
            self._check_budget()
            tools = self._tools()
            system = self._system()
            await self._compact(system, tools)
            self._check_budget()
            hook = await c._emit_hook(HookEvent(
                name='BeforeModelCall', session_id=c._session_id(),
                payload={'iteration': self.state.model_calls + 1, 'message_count': len(self.messages)},
            ))
            if not hook.allowed:
                yield await self._finish('failed', 'model_hook_denied', hook.reason, (hook.reason,))
                return
            contexts = [*c._pending_hook_context, *hook.additional_context]
            c._pending_hook_context.clear()
            if contexts:
                system += '\n\n<forge_hook_context>\n' + '\n\n'.join(contexts) + '\n</forge_hook_context>'
            iteration = self.state.model_calls + 1
            yield ModelCallStarted(iteration)
            parts: list[str] = []
            calls: list[ToolCall] = []
            usage = None
            try:
                client = BudgetedModelClient(c.client, self.state)
                async for event in client.stream(c.context.prepare(self.messages), tools=tools, system=system):
                    if isinstance(event, ModelTextDelta):
                        parts.append(event.text)
                    elif isinstance(event, ModelToolCallCompleted):
                        calls.append(event.tool_call)
                        self.calls.append(event.tool_call)
                        self.state.record_tool_request()
                    elif isinstance(event, ModelUsageUpdate):
                        usage = event.request_usage or event.usage
                        yield ModelUsageUpdate(add_usage(self.state.usage, usage), usage, self.state.model_calls)
                        continue
                    yield event
            except (ModelProtocolError, ModelCallError) as error:
                yield ModelCallFailed(iteration, str(error), isinstance(error, ModelProtocolError))
                partial = ''.join(parts)
                if calls:
                    self._append_assistant(build_assistant_message(partial, calls))
                    self._record_unexecuted(calls, 'incomplete_model_response')
                elif partial.strip():
                    self._append_assistant(build_assistant_message(partial, []))
                if isinstance(error, ModelOutputTruncatedError) and partial.strip() and not calls and not error.tool_names:
                    if self.output_continuations < c.max_output_continuations:
                        self.output_continuations += 1
                        self.continued_text += partial
                        self._feedback(
                            'The output reached the max_tokens limit. The text already '
                            'generated has been preserved; continue from the last text '
                            'without repeating earlier content. Continuation attempt '
                            f'{self.output_continuations} of {c.max_output_continuations}.'
                        )
                        continue
                    yield await self._finish(
                        'failed', 'output_continuation_exhausted',
                        self.continued_text + partial,
                        ('Model output remained truncated after the configured continuations.',),
                    )
                    return
                if isinstance(error, ModelProtocolError) and self.state.protocol_errors < min(2, c.max_protocol_recoveries):
                    self.state.protocol_errors += 1
                    limit = min(2, c.max_protocol_recoveries)
                    available = ', '.join(item['name'] for item in (tools or ())) or 'none'
                    if isinstance(error, ModelOutputTruncatedError):
                        named = ', '.join(error.tool_names) or 'the tool call'
                        detail = (
                            'The output reached the max_tokens limit while generating '
                            f'{named}. Split the operation into smaller tool calls.'
                        )
                    else:
                        detail = f'Model protocol error: {error}.'
                    self._feedback(
                        f'{detail} No tool was executed from this response. '
                        f'Available tools: {available}. Correct the response format. '
                        f'Recovery attempt {self.state.protocol_errors} of {limit}.'
                    )
                    continue
                if isinstance(error, ModelCallError) and error.reason == 'context_length_exceeded' and not self.reactive_compaction_used:
                    self.reactive_compaction_used = True
                    await self._compact(system, tools, force=True)
                    continue
                yield await self._finish('failed', getattr(error, 'reason', 'model_protocol_error'), str(error), (str(error),))
                return
            yield ModelCallCompleted(iteration)
            text = ''.join(parts)
            if usage is None:
                raise ModelResponseError('Model response did not contain token usage.')
            if not text.strip() and not calls:
                if self.state.protocol_errors < min(2, c.max_protocol_recoveries):
                    self.state.protocol_errors += 1
                    self._feedback(
                        'The model response was empty. Return a final answer or '
                        'a valid tool call; no operation was executed.',
                    )
                    continue
                yield await self._finish(
                    'failed', 'empty_model_response',
                    'The model repeatedly returned an empty response.',
                    ('empty_model_response',),
                )
                return
            self._append_assistant(build_assistant_message(text, calls))
            if calls and c.registry is not None:
                async for event in self._batch(calls):
                    yield event
                if self.pending_completion_reasons:
                    self.completion_attempts += 1
                    yield CompletionBlocked(
                        self.completion_attempts, self.pending_completion_reasons,
                    )
                    self.pending_completion_reasons = ()
                if self.terminal is not None:
                    yield await self._finish(*self.terminal)
                    return
                continue
            reasons = await self._completion_reasons()
            if reasons:
                self.completion_attempts += 1
                yield CompletionBlocked(self.completion_attempts, reasons)
                self._feedback('Completion contract is not satisfied:\n' + '\n'.join(reasons))
                continue
            yield await self._finish('completed', 'completed', self.continued_text + text, ())
            return

    async def _batch(self, calls: list[ToolCall]):
        from forge.runtime.agent_loop import verification_from_result
        c = self.conversation
        executor = c.tool_executor
        assert executor is not None
        results: list[tuple[ToolCall, ToolResult]] = []
        ordered = sorted(calls, key=lambda call: call.index)
        batch_reason = None
        if self.state.max_tool_calls is not None and self.state.tool_requests > self.state.max_tool_calls:
            batch_reason = 'tool_budget_exhausted'
            self.terminal = ('failed', batch_reason, batch_reason, (batch_reason,))
        mixed_finish = len(calls) > 1 and any(call.name == 'finish_task' for call in calls)
        if mixed_finish:
            batch_reason = 'finish_must_be_alone'
        interrupted = False
        self.batch_active = True
        try:
            for call in ordered:
                if batch_reason:
                    outcome = executor.record_result(call, ToolResult.fail(
                        batch_reason, 'This tool did not execute. Replan using the preceding results.',
                    ), status='cancelled')
                else:
                    if self.state.budget_reason(include_model=False):
                        raise BudgetExhausted(self.state.budget_reason(include_model=False))
                    yield ToolExecutionStarted(call)
                    outcome = await executor.execute(
                        call, checkpoint_id=self.checkpoint_id,
                        scope_checker=self._scope_error,
                        result_transformer=(self._finish_declaration if call.name == 'finish_task' else None),
                    )
                effective = ToolCall(call.index, call.id, call.name, outcome.arguments)
                results.append((effective, outcome.result))
                self.state.record_execution(outcome.record)
                self.state.add_time('tools', outcome.record.duration_seconds)
                self.state.observe_workspace(outcome.record.workspace_revision, outcome.record.environment_epoch)
                if outcome.workspace_change is not None:
                    change = outcome.workspace_change
                    c.task_manager.observe_mutation_paths(change.paths)
                    c.working_state.advance_revision(change.revision, change.paths)
                    yield WorkspaceChanged(change.revision, change.paths)
                if c.registry.effect(call.name) == 'workspace_write':
                    self.mutation_attempted = True
                    self.mutation_failed = self.mutation_failed or not outcome.result.success
                    if c.task_manager.active is None:
                        c.task_manager.start(self.state.goal, requires_change=True)
                        self.owns_task = True
                if c.registry.effect(call.name) != 'read_only':
                    c.working_state.invalidate_process_caches()
                evidence = verification_from_result(outcome.result)
                if evidence is not None and outcome.record.status == 'executed':
                    self.state.evidence.add(evidence)
                    yield VerificationCompleted(evidence)
                    hook = await c._emit_hook(HookEvent(
                        name='AfterVerification', session_id=c._session_id(),
                        tool_name=effective.name, tool_call_id=effective.id,
                        arguments=effective.arguments,
                        payload={
                            'command': evidence.command, 'cwd': evidence.cwd,
                            'exit_code': evidence.exit_code,
                            'timed_out': evidence.timed_out,
                            'workspace_revision': evidence.workspace_revision,
                            'environment_epoch': evidence.environment_epoch,
                            'success': evidence.success,
                        },
                    ))
                    c._queue_hook_context(hook)
                # WorkingState is evidence for context only, never a preflight gate.
                c.working_state.observe(effective, outcome.result, outcome.record.workspace_revision, call.id)
                yield ToolExecutionCompleted(effective, outcome.result)
                if outcome.record.status == 'indeterminate':
                    interrupted = True
                    batch_reason = 'not_executed_after_interruption'
                elif not outcome.result.success:
                    if batch_reason is None:
                        batch_reason = 'not_executed_after_failure'
                    self._diagnose(effective, outcome.result)
            if mixed_finish:
                self._protocol_feedback('finish_task must be submitted alone.')
        finally:
            # Closing/cancelling an event stream must not leave a replayable tail.
            done = {call.id for call, _ in results}
            for call in ordered:
                if call.id not in done:
                    outcome = executor.record_result(call, ToolResult.fail(
                        'not_executed_after_interruption', 'The tool did not execute before the turn stopped.',
                    ), status='cancelled')
                    self.state.record_execution(outcome.record)
                    results.append((call, outcome.result))
            self._append_results(results)
            self.batch_active = False
            for feedback in self.pending_feedback:
                self._feedback(feedback)
            self.pending_feedback.clear()
        if interrupted:
            raise asyncio.CancelledError

    def _scope_error(self, call: ToolCall) -> ToolResult | None:
        from forge.runtime.agent_loop import mutation_target_paths
        c = self.conversation
        if self.read_only and c.registry.effect(call.name) != 'read_only':
            return ToolResult.fail('read_only_intent', 'The current user turn only authorizes inspection.')
        if c.registry.effect(call.name) != 'workspace_write':
            return None
        paths = mutation_target_paths(call, maximum=None)
        if c.task_manager.outside_scope(paths):
            return ToolResult.fail('outside_task_scope', 'The operation targets paths outside the explicit user scope.')
        if c.completion_gate is not None:
            reasons = c.completion_gate._path_violations(paths)
            if reasons:
                return ToolResult.fail('outside_task_scope', '\n'.join(reasons))
        return None

    async def _completion_reasons(self) -> tuple[str, ...]:
        c = self.conversation
        if self.tracker is None or c.completion_gate is None or c.permission_manager.mode == 'plan':
            if self.mutation_attempted and self.mutation_failed:
                return ('A workspace mutation failed and no final workspace evidence proves the requested change.',)
            return ()
        started = monotonic()
        await self.tracker.refresh()
        self.state.add_time('workspace', monotonic() - started)
        self.state.observe_workspace(
            self.tracker.revision, getattr(self.tracker, 'environment_epoch', 0),
        )
        evidence = tuple(self.state.evidence.verification)
        decision = await c.completion_gate.evaluate(
            self.tracker, evidence[-1] if evidence else None,
            verification_history=evidence, mutation_attempted=False,
        )
        return decision.reasons

    async def _finish_declaration(self, result: ToolResult) -> ToolResult:
        if not result.success or not result.metadata.get('finish_task'):
            return result
        metadata = result.metadata
        status = metadata['status']
        task_kind = metadata.get('task_kind')
        if (
            status == 'completed'
            and task_kind == 'inspection'
            and not self.conversation.working_state.evidence_paths
        ):
            reasons = ('An inspection completion requires repository evidence from a read or search tool.',)
            self.pending_completion_reasons = reasons
            self._feedback(reasons[0])
            return ToolResult.fail('completion_rejected', reasons[0], metadata=metadata)
        if status == 'completed':
            reasons = await self._completion_reasons()
            if reasons:
                self.pending_completion_reasons = reasons
                self._feedback('Completion contract is not satisfied:\n' + '\n'.join(reasons))
                return ToolResult.fail('completion_rejected', '\n'.join(reasons), metadata=metadata)
        elif status == 'blocked' and not self.conversation.working_state.has_external_blocker:
            reasons = ('blocked requires observed external evidence such as permission, credentials, network, or an unavailable dependency.',)
            self.pending_completion_reasons = reasons
            self._feedback(reasons[0])
            return ToolResult.fail('completion_rejected', reasons[0], metadata=metadata)
        self.terminal = (
            status, status, str(metadata['summary']), tuple(metadata.get('blocked_reasons', ())),
        )
        return result

    def _protocol_feedback(self, reason: str) -> None:
        self.state.protocol_errors += 1
        if self.state.protocol_errors > min(2, self.conversation.max_tool_protocol_recoveries):
            self.terminal = ('failed', 'tool_protocol_exhausted', reason, (reason,))
        else:
            self._feedback(reason + ' Correct the tool parameters before retrying.')

    def _diagnose(self, call: ToolCall, result: ToolResult) -> None:
        code = result.error.code if result.error else 'failure'
        if code in {'invalid_arguments', 'unknown_tool', 'unsupported_shell_syntax'}:
            self._protocol_feedback(result.summary)
            return
        key = f'{call.name}:{code}'
        count = self.state.failure_counts.get(key, 0) + 1
        self.state.failure_counts[key] = count
        if count == 3:
            self._feedback(
                f'Three {key} failures have occurred. Review the diagnostics, '
                'state the cause and what evidence distinguishes your next approach. '
                'Diagnostic tools and original commands remain available. Do not '
                'weaken a check simply to obtain a zero exit code.',
            )

    def _feedback(self, text: str) -> None:
        if self.batch_active:
            self.pending_feedback.append(text)
            return
        self.state.feedback_count += 1
        self.messages.append({'role': 'user', 'content': '[Runtime feedback]\n' + text})
        self._commit_messages()

    def _append_assistant(self, message: dict) -> None:
        self.messages.append(message)
        if self.journal is not None:
            self.journal.record_assistant_message(message)
        self._commit_messages()

    def _append_results(self, results) -> None:
        from forge.runtime.agent_loop import build_tool_result_message
        message = build_tool_result_message(results)
        self.conversation.context.persist_tool_result_message(message)
        self.messages.append(message)
        if self.journal is not None:
            self.journal.record_tool_result_message(message, self.conversation.task_manager.active)
        self._commit_messages()

    def _record_unexecuted(self, calls, reason: str) -> None:
        executor = self.conversation.tool_executor
        results = []
        for call in calls:
            result = ToolResult.fail(reason, 'No tool executed from the incomplete model response.')
            if executor is not None:
                outcome = executor.record_result(call, result, status='cancelled')
                self.state.record_execution(outcome.record)
                result = outcome.result
            results.append((call, result))
        self._append_results(results)

    def _commit_messages(self) -> None:
        self.conversation.messages[:] = self.messages

    async def _finish(self, status: str, reason: str, text: str, reasons: tuple[str, ...]):
        c = self.conversation
        self.state.stop_reason = reason
        if self.owns_task:
            if status == 'completed':
                c.task_manager.complete()
            elif status == 'blocked':
                c.task_manager.block(reasons)
            else:
                c.task_manager.fail(reasons)
        self._commit_messages()
        evidence = tuple(self.state.evidence.verification)
        event = TurnCompleted(TurnResult(
            text=text, status=status, stop_reason=reason,
            usage=self.state.usage, last_request_usage=self.state.last_request_usage,
            model_calls=self.state.model_calls, tool_calls=tuple(self.calls),
            changed_paths=self.tracker.changed_paths if self.tracker is not None else (),
            verification=evidence[-1] if evidence else None, verification_history=evidence,
            completion_reasons=reasons, statistics=self.state.statistics(),
        ))
        # Persist the fully populated result before a CLI/channel sees it.
        c.record_session_event(event)
        self.finished = True
        return event
