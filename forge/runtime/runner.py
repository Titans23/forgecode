'''The single model/tool turn loop, independent of product event consumers.'''

from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import asdict, replace
from time import monotonic
from typing import TYPE_CHECKING

from forge.context.working import WorkingState
from forge.hooks import HookEvent
from forge.runtime.model_budget import BudgetedModelClient
from forge.runtime.completion import TaskPolicy
from forge.runtime.model_client import ModelCallError, ModelOutputTruncatedError, ModelProtocolError
from forge.runtime.state import (
    ModelCallCompleted, ModelCallFailed, ModelCallStarted,
    ModelTextDelta, ModelToolCallCompleted, ModelUsageUpdate, ToolCall, ModelProviderState, ModelResponseCompleted,
    ModelToolCallStarted, ModelToolCallArgumentsDelta,
    ToolExecutionCompleted, ToolExecutionStarted, TurnCompleted, TurnResult,
    VerificationCompleted, WorkspaceChanged,
)
from forge.runtime.turn_state import BudgetExhausted, TurnState, add_usage, parent_budget
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
            parent=parent_budget.get(),
        )
        if self.state.parent is not None:
            self.state.request_event_sink = self.state.parent.request_event_sink
        self.calls: list[ToolCall] = []
        self.messages: list[dict] = []
        self.checkpoint_id: str | None = None
        self.read_only = False
        self.owns_task = False
        self.terminal: tuple[str, str, str, tuple[str, ...]] | None = None
        self.finished = False
        self.output_continuations = 0
        self.continued_text = ''
        self.reactive_compaction_used = False
        self.batch_active = False
        self.pending_feedback: list[str] = []
        self.mutation_attempted = False
        self.mutation_failed = False
        self.consecutive_tool_protocol_errors = 0
        self.declared_status: str | None = None
        self.delivery_repair_reasons: set[tuple[str, ...]] = set()

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
        from forge.observability.events import current
        recorder=current()
        if self.journal is not None or c.event_recorder is not None or recorder:
            self.state.request_event_sink = c.record_model_request
        if recorder:
            recorder.reserve_budget(self.state)
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
        task_id = c.task_manager.active.id if c.task_manager.active else ''
        # A new turn is a new observation epoch. Old run IDs remain addressable
        # for checker inheritance, but numerical revision=0 never refreshes them.
        c.verification_history[:] = [replace(item, freshness='unknown')
                                     for item in c.verification_history]
        if recorder:
            for item in c.verification_history:
                recorder.invalidate_verification(item)
        self.state.evidence.verification.extend(
            item for item in c.verification_history if item.task_id == task_id)
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
        task = c.task_manager.active
        if definitions is not None and (task is None or not task.acceptance_criteria):
            definitions = [item for item in definitions if item['name'] != 'task_revise_requirement']
        if self.read_only and c.registry is not None and definitions is not None:
            definitions = [
                item for item in definitions
                if c.registry.effect(item['name']) == 'read_only'
                and item['name'] not in {'task_plan', 'task_update', 'task_revise_requirement', 'finish_task'}
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
            self._evidence_context(),
            'Task plans and scope hints organize work; they do not grant permissions. '
            'Tool batches execute sequentially and stop after a failure. Failed checks '
            'remain diagnostic evidence. Explain what verification actually covers and '
            'what remains untested; a zero exit code alone does not prove the user goal. '
            'Either a final answer or finish_task submits the same completion request. '
            'Submission ends the turn; caller acceptance is reported separately. '
            'When review_delivery is available, use it before submission to inspect gaps '
            'while there is budget to repair. Choose independent checks: unseen algorithm inputs, '
            'exact requested source components, observed sample values, and delivery in a clean '
            'directory without reference programs. Do not weaken expectations to match output. '
            'Use partial for incomplete delivery, failed for an unsuccessful attempt, '
            'blocked only for a specific external dependency.',
            ('[Acceptance reconciliation]\nThe acceptance criteria above are source-anchored model interpretations, not independent proof or permissions. '
            'For each requirement, bind verify.output_checks.requirement_id and expected_source to an actual '
            'falsifiable measurement. Report missing or uncertain checks with finish_task status=failed; '
            'replanning does not remove requirements.'
            if c.task_manager.active is not None and c.task_manager.active.acceptance_criteria else ''),
            '[Remaining turn budget]\n'
            + f'Model requests used: {self.state.model_calls}/{self.state.max_model_calls}; '
            + f'tool requests used: {self.state.tool_requests}/{self.state.max_tool_calls}; '
            + (f'time remaining: {max(0, int(self.state.max_seconds - (monotonic() - self.state.started_at)))} seconds. '
               if self.state.max_seconds is not None else 'No turn time limit. ')
            + 'Choose command timeouts within this remaining budget. Reserve time to validate '
              'the actual deliverable. Repeated dependency downloads/builds consume this same budget.',
        ) if part)

    def _evidence_context(self) -> str:
        '''Deterministic projection survives model summaries and context trimming.'''
        import json
        latest = {}
        for item in self.state.evidence.verification:
            latest[item.check_id or (item.command, item.cwd, item.check_signature)] = item
        if not latest:
            return ''
        rows = []
        for item in list(latest.values())[-8:]:
            current = (item.freshness == 'current' and item.workspace_revision == self.state.workspace_revision
                       and item.environment_epoch == self.state.environment_epoch)
            rows.append({'check_id': item.check_id, 'run_id': item.verification_id,
                         'command': item.command[:160], 'observed_success': item.success,
                         'freshness': 'current' if current else 'unknown',
                         'requirements': item.requirement_ids})
        return ('[Executed check state]\nHistorical success is not current proof. '
                'Use verify.check_id to rerun a definition; task_get contains full history.\n'
                + json.dumps(rows, ensure_ascii=False))

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
        from forge.observability.events import current, active, bind
        from contextlib import nullcontext
        recorder=current()
        scope=active.get().scope.child() if recorder else None
        if recorder:
            recorder.compaction_started(scope,self.messages,'forced' if force else 'automatic')
        try:
            with bind(recorder,scope,branch=active.get().branch,role=active.get().role) if recorder else nullcontext():
                report = await c.context.compact_history(
                    self.messages, BudgetedModelClient(c.client, self.state, 'compaction'),
                    force=force, task_goal=self.state.goal, **kwargs,
                )
        except BaseException as error:
            if recorder:
                recorder.compaction_finished(scope,self.messages,None,error)
            raise
        if recorder:
            recorder.compaction_finished(scope,self.messages,report)
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
            partial_tools = False
            provider_state = None
            provider_completed = False
            try:
                client = BudgetedModelClient(c.client, self.state)
                async with aclosing(client.stream(c.context.prepare(self.messages), tools=tools, system=system)) as stream:
                    async for event in stream:
                        if isinstance(event, ModelProviderState):
                            provider_state = {'provider': event.provider, 'data': event.data}
                            continue
                        if isinstance(event, ModelResponseCompleted):
                            provider_completed = True
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
                        elif isinstance(event, (ModelToolCallStarted, ModelToolCallArgumentsDelta)):
                            partial_tools = True
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
                # Transport/empty output correction is safe only before any
                # semantic output, including an incomplete tool block.
                safe_to_correct = not parts and not calls and not partial_tools
                if isinstance(error, ModelProtocolError) and safe_to_correct and self.state.response_errors < min(2, c.max_protocol_recoveries):
                    self.state.response_errors += 1
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
                        f'Recovery attempt {self.state.response_errors} of {limit}.'
                    )
                    continue
                if isinstance(error, ModelCallError) and error.reason in {'context_length_exceeded', 'context_overflow'} and safe_to_correct and not self.reactive_compaction_used:
                    self.reactive_compaction_used = True
                    await self._compact(system, tools, force=True)
                    continue
                yield await self._finish('failed', getattr(error, 'reason', 'model_protocol_error'), str(error), (str(error),))
                return
            yield ModelCallCompleted(iteration)
            text = ''.join(parts)
            if usage is None and not provider_completed:
                raise ModelResponseError('Model response did not contain token usage.')
            if not text.strip() and not calls:
                if self.state.response_errors < min(2, c.max_protocol_recoveries):
                    self.state.response_errors += 1
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
            message = build_assistant_message(text, calls)
            if provider_state is not None:
                message['provider_state'] = provider_state
            self._append_assistant(message)
            if calls and c.registry is not None:
                async for event in self._batch(calls):
                    yield event
                if self.terminal is not None:
                    yield await self._finish(*self.terminal)
                    return
                continue
            reasons = await self._completion_reasons()
            if reasons:
                self.declared_status = 'completed'
                if self._offer_delivery_repair(reasons):
                    continue
                yield await self._finish('partial', 'acceptance_unmet', self.continued_text + text, reasons)
                return
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
        if not self.state.can_request_tool_batch(0):
            batch_reason = 'tool_budget_exhausted'
            self.terminal = ('failed', batch_reason, batch_reason, (batch_reason,))
        mixed_finish = len(calls) > 1 and any(call.name == 'finish_task' for call in calls)
        # 完成声明必须单独处理，防止同批后续工具改动产物后仍沿用旧验收结论。
        if mixed_finish:
            batch_reason = 'finish_must_be_alone'
        interrupted = False
        self.batch_active = True
        try:
            for call in ordered:
                if not self.state.can_request_tool_batch(0):
                    batch_reason = 'tool_budget_exhausted'
                    self.terminal = ('failed', batch_reason, batch_reason, (batch_reason,))
                if batch_reason:
                    outcome = executor.record_result(call, ToolResult.fail(
                        batch_reason, 'This tool did not execute. Replan using the preceding results.',
                    ), status='cancelled')
                else:
                    if self.state.budget_reason(include_model=False):
                        raise BudgetExhausted(self.state.budget_reason(include_model=False))
                    yield ToolExecutionStarted(call)
                    if call.name == 'verify' and (call.arguments.get('inherit_checks_from') or call.arguments.get('check_id')):
                        # 先展开已存检查，再交给统一执行器校验和授权；不能用引用绕过权限边界。
                        from forge.runtime.check_contracts import inherit_check_arguments, resolve_stored_check
                        try:
                            arguments = resolve_stored_check(call.arguments, self.state.evidence.verification)
                            call = replace(call, arguments=inherit_check_arguments(arguments, self.state.evidence.verification)
                                           if arguments.get('inherit_checks_from') else arguments)
                        except ValueError as error:
                            outcome = executor.record_result(call, ToolResult.fail('check_contract_conflict', str(error)), status='rejected')
                            results.append((call, outcome.result))
                            self.state.record_execution(outcome.record)
                            yield ToolExecutionCompleted(call, outcome.result)
                            batch_reason = 'not_executed_after_failure'
                            continue
                    budget_token = parent_budget.set(self.state)
                    try:
                        outcome = await executor.execute(
                            call, checkpoint_id=self.checkpoint_id,
                            scope_checker=self._scope_error,
                            result_transformer=(self._finish_declaration if call.name == 'finish_task'
                                                else self._review_delivery if call.name == 'review_delivery' else None),
                        )
                    finally:
                        parent_budget.reset(budget_token)
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
                    task = c.task_manager.active
                    evidence = replace(evidence, task_id=task.id if task else '',
                                       turn_id=self.journal.turn_id if self.journal else '')
                    c.verification_history.append(evidence)
                    if self.journal is not None:
                        self.journal.append('verification_recorded', {'evidence': asdict(evidence)})
                    from forge.observability.events import current
                    recorder=current()
                    if recorder:
                        await recorder.verification(evidence,call.id)
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
                if (outcome.record.status == 'cancelled' and outcome.result.error
                        and outcome.result.error.code == 'execution_cancelled'):
                    raise asyncio.CancelledError
                if outcome.record.status == 'indeterminate':
                    interrupted = True
                    batch_reason = 'not_executed_after_interruption'
                elif not outcome.result.success:
                    if batch_reason is None:
                        batch_reason = 'not_executed_after_failure'
                    self._diagnose(effective, outcome.result)
                    if (outcome.record.status == 'executed' and outcome.result.error
                            and outcome.result.error.code not in {'invalid_arguments', 'unknown_tool', 'unsupported_shell_syntax'}):
                        self.consecutive_tool_protocol_errors = 0
                elif outcome.record.status == 'executed':
                    self.consecutive_tool_protocol_errors = 0
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
        task = c.task_manager.active
        retired = {item['id'] for item in task.acceptance_history} if task is not None else set()
        evidence = tuple(item for item in evidence if not item.requirement_ids
                         or not set(item.requirement_ids).issubset(retired))
        decision = await c.completion_gate.evaluate(
            self.tracker, evidence[-1] if evidence else None,
            verification_history=evidence, mutation_attempted=False,
            acceptance_criteria=(c.task_manager.active.acceptance_criteria if c.task_manager.active is not None else ()),
        )
        return decision.reasons

    async def _review_delivery(self, result: ToolResult) -> ToolResult:
        if not result.success or not result.metadata.get('review_delivery'):
            return result
        from forge.runtime.delivery import completion_report
        from forge.runtime.acceptance import requirement_observation
        import json
        reasons = await self._completion_reasons()
        report = completion_report(
            status='completed', evidence=tuple(self.state.evidence.verification),
            workspace_revision=self.tracker.revision if self.tracker else 0,
            environment_epoch=getattr(self.tracker, 'environment_epoch', 0),
            reasons=reasons, has_contract=self.conversation.completion_gate is not None,
            usage_complete=self.state.unknown_usage_requests == 0,
        )
        content = report.summary()
        if reasons:
            content += '\nCurrent gaps:\n' + '\n'.join('- ' + reason[:700] for reason in reasons[:12])
        task = self.conversation.task_manager.active
        observations = [requirement_observation(criterion, self.state.evidence.verification,
            self.tracker.revision if self.tracker else 0, getattr(self.tracker, 'environment_epoch', 0))
            for criterion in (task.acceptance_criteria if task else ())]
        # Bound diagnostic context; IDs allow retrieving full stored checks with task_get.
        selected, remaining = [], 24000
        for item in sorted(observations, key=lambda item: item['state'] == 'recorded_assertion_passed'):
            size = len(json.dumps(item, ensure_ascii=False))
            if size <= remaining and len(selected) < 12:
                selected.append(item)
                remaining -= size
        omitted = len(observations) - len(selected)
        if selected:
            content += '\nSee metadata.requirement_observations for linked checks, failure diagnostics and next actions.'
        if omitted:
            content += f'\n{omitted} requirement details omitted for context budget; task_get retains the full ledger.'
        content += '\nThis review did not run tests or establish independent correctness. Repair concrete gaps, or submit partial with limitations.'
        report_payload = asdict(report)
        report_payload['unmet_requirements'] = tuple(reason[:700] for reason in reasons[:12])
        for key in ('passed_checks', 'failed_checks', 'historical_checks'):
            report_payload[key] = report_payload[key][:20]
        return ToolResult.ok(report.summary(), content=content, metadata={
            'review_delivery': True, 'completion_report': report_payload,
            'requirement_observations': selected, 'observations_omitted': omitted,
            'detail_limits': 'Up to 12 requirements, 3 runs and 8 assertions per run; strings are excerpts. Use task_get for complete stored definitions.',
            'gap_count': len(reasons),
        })

    async def _finish_declaration(self, result: ToolResult) -> ToolResult:
        if not result.success or not result.metadata.get('finish_task'):
            return result
        metadata = result.metadata
        status = metadata['status']
        self.declared_status = status
        observations: list[str] = []
        task_kind = metadata.get('task_kind')
        if (
            status == 'completed'
            and task_kind == 'inspection'
            and not self.conversation.working_state.evidence_paths
        ):
            reasons = ('An inspection completion requires repository evidence from a read or search tool.',)
            observations.extend(reasons)
        if status == 'completed':
            task = self.conversation.task_manager.active
            if metadata.get('acceptance_criteria') and task is not None and not task.acceptance_criteria:
                try:
                    task = self.conversation.task_manager.register_acceptance(metadata['acceptance_criteria'])
                    metadata = {**metadata, 'acceptance_criteria': task.acceptance_criteria}
                except ValueError as error:
                    observations.append(str(error))
            # Completion is read-only once a contract exists. Explicit planning
            # may add clauses, but repeated finish declarations cannot do so.
            metadata = {**metadata, 'acceptance_criteria': task.acceptance_criteria if task is not None else ()}
            reasons = await self._completion_reasons()
            observations.extend(reasons)
        elif status == 'blocked' and not self.conversation.working_state.has_external_blocker:
            reasons = ('blocked requires observed external evidence such as permission, credentials, network, or an unavailable dependency. If the attempted task is unsuccessful, use finish_task status=failed; it does not require successful verification.',)
            observations.extend(reasons)
        if observations and status == 'completed' and self._offer_delivery_repair(observations):
            return ToolResult.fail('delivery_repair_required',
                                   'Completion checks failed. Repair the observed gaps within the remaining budget, or submit an honest partial/failed result.',
                                   metadata={'acceptance_reasons': tuple(observations)})
        if observations:
            status = 'partial'
        metadata = {**metadata, 'agent_assessment': self.declared_status,
                    'status': status, 'acceptance_reasons': tuple(observations)}
        self.terminal = (
            status, 'acceptance_unmet' if observations else status, str(metadata['summary']),
            tuple(dict.fromkeys((*observations, *metadata.get('blocked_reasons', ())))),
        )
        return replace(result, metadata=metadata)

    def _offer_delivery_repair(self, reasons) -> bool:
        gate = self.conversation.completion_gate
        limit = gate.policy.effective_delivery_repairs if gate else 0
        signature = tuple(sorted(set(reasons)))
        if (len(self.delivery_repair_reasons) >= limit
                or signature in self.delivery_repair_reasons
                or not self.state.can_request_model()):
            return False
        self.delivery_repair_reasons.add(signature)
        from forge.observability.events import current
        recorder=current()
        if recorder:
            attributes={'reason':'\n'.join(signature)[:1024],'workspace_revision':self.state.workspace_revision,
                'evidence_revision':self.state.evidence.verification[-1].workspace_revision if self.state.evidence.verification else self.state.workspace_revision,
                'repairs_remaining':max(0,limit-len(self.delivery_repair_reasons))}
            recorder.emit('completion.rejected',attributes)
            recorder.emit('completion.repair_started',attributes)
        self._feedback('Completion was not accepted. Fix the implementation or supply missing evidence; '
                       'do not weaken acceptance checks. The original budget still applies. '
                       'If the gaps cannot be repaired, finish with partial or failed.\n'
                       + '\n'.join(reason[:1000] for reason in signature[:8]))
        return True

    def _protocol_feedback(self, reason: str) -> None:
        self.state.tool_protocol_errors += 1
        self.consecutive_tool_protocol_errors += 1
        recovery_limit = self.conversation.max_tool_protocol_recoveries
        if (self.consecutive_tool_protocol_errors >= recovery_limit
                or self.state.tool_protocol_errors > max(6, recovery_limit * 3)):
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
        from dataclasses import fields
        from forge.runtime.delivery import completion_report
        c = self.conversation
        self.state.stop_reason = reason
        if self.owns_task:
            if status == 'completed':
                c.task_manager.complete()
            elif status == 'partial':
                c.task_manager.partial(reasons)
            elif status == 'blocked':
                c.task_manager.block(reasons)
            else:
                c.task_manager.fail(reasons)
        self._commit_messages()
        evidence = tuple(self.state.evidence.verification)
        policy = c.completion_gate.policy if c.completion_gate is not None else None
        has_contract = policy is not None and any(
            bool(getattr(policy, field.name)) for field in fields(policy)
            if field.name not in {'forbidden_paths', 'max_delivery_repairs'})
        has_contract = has_contract or bool(policy and policy.forbidden_paths != TaskPolicy().forbidden_paths)
        report = completion_report(status=status, evidence=evidence,
            workspace_revision=self.state.workspace_revision,
            environment_epoch=self.state.environment_epoch, reasons=reasons,
            has_contract=has_contract, agent_assessment=self.declared_status,
            usage_complete=self.state.unknown_usage_requests == 0)
        from forge.observability.events import current
        recorder=current()
        if recorder:
            recorder.finish_budget(self.state,reason)
            recorder.evidence_state(self.state.workspace_revision,self.state.environment_epoch)
            recorder.completion(accepted=status=='completed',reason=reason,revision=self.state.workspace_revision,
                evidence_revision=evidence[-1].workspace_revision if evidence else self.state.workspace_revision,
                repairs_remaining=max(0,(policy.effective_delivery_repairs if policy else 0)-len(self.delivery_repair_reasons)),report=asdict(report))
        event = TurnCompleted(TurnResult(
            text=text, status=status, stop_reason=reason,
            usage=self.state.usage, last_request_usage=self.state.last_request_usage,
            model_calls=self.state.model_calls, tool_calls=tuple(self.calls),
            changed_paths=self.tracker.changed_paths if self.tracker is not None else (),
            verification=evidence[-1] if evidence else None, verification_history=evidence,
            completion_reasons=reasons, statistics=self.state.statistics(),
            completion_report=report,
        ))
        # Persist the fully populated result before a CLI/channel sees it.
        c.record_session_event(event)
        self.finished = True
        return event
