'''Bounded tool-body overlap with ordered admission and result settlement.'''
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from forge.runtime.executor import DispatchedExecution, ExecutionOutcome, PreparedExecution
from forge.runtime.state import ToolExecutionQueued, ToolExecutionReady, ToolExecutionStarted
from forge.tools.base import ToolResult


def stops_batch(result: ToolResult) -> bool:
    return bool(result.error and result.error.code in {
        'permission_denied', 'outside_task_scope', 'read_only_intent', 'hook_denied',
        'POLICY_DENIED', 'INDETERMINATE', 'execution_cancelled',
    }) or result.metadata.get('execution_status') == 'indeterminate'


@dataclass
class Slot:
    prepared: PreparedExecution | None = None
    task: asyncio.Task | None = None
    dispatched: DispatchedExecution | ExecutionOutcome | None = None


async def schedule_calls(calls, executor, prepare, *, limit, completed):
    '''Only this task prepares/finalizes; child tasks own tool bodies and cleanup.

    ``completed`` also receives drained outcomes when the consumer closes, so a
    started operation is never replaced with a fabricated unexecuted result.
    '''
    slots: dict[int, Slot] = {}
    next_start = next_commit = 0
    stopped = False
    held = None

    def safe(call):
        return (limit > 1 and not call.arguments.get('sandbox_permissions')
                and executor.registry.is_concurrency_safe(call.name, call.arguments))

    def running():
        return [slot.task for slot in slots.values() if slot.task is not None and slot.dispatched is None]

    async def settle(index, slot):
        outcome = await executor.finish(slot.dispatched)
        completed[calls[index].id] = outcome
        return outcome

    try:
        for call in calls:
            yield ToolExecutionQueued(call)
        while next_commit < len(calls):
            # Reclassify after each ordered commit, including hook/registry changes.
            while not stopped and next_start < len(calls) and len(running()) < limit:
                call = calls[next_start]
                if slots and not safe(call):
                    break
                prepared = held if held is not None else await prepare(call)
                held = None
                if isinstance(prepared, ExecutionOutcome):
                    slots[next_start] = Slot(dispatched=prepared)
                    next_start += 1
                    stopped = stops_batch(prepared.result)
                    break
                if slots and not safe(prepared.call):
                    held = prepared
                    break
                task = asyncio.create_task(executor.dispatch(prepared))
                slots[next_start] = Slot(prepared=prepared, task=task)
                next_start += 1
                yield ToolExecutionStarted(prepared.call)
                if not safe(prepared.call):
                    break

            if stopped:
                for task in running():
                    task.cancel()
            tasks = running()
            if tasks:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for index, slot in slots.items():
                    if slot.task in done and slot.dispatched is None:
                        slot.dispatched = (slot.prepared.context.run(slot.prepared.abort)
                                           if slot.task.cancelled() else slot.task.result())
                        stopped = stopped or stops_batch(slot.dispatched.result)
                        yield ToolExecutionReady(slot.prepared.call)
            while next_commit in slots and slots[next_commit].dispatched is not None:
                slot = slots[next_commit]
                outcome = await settle(next_commit, slot)
                slots.pop(next_commit)
                stopped = stopped or stops_batch(outcome.result)
                yield calls[next_commit], outcome
                next_commit += 1
            if stopped and not slots:
                break
    finally:
        tasks = running()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for index, slot in sorted(slots.items()):
            if slot.dispatched is None and slot.task is not None:
                if slot.task.cancelled():
                    slot.dispatched = slot.prepared.context.run(slot.prepared.abort)
                else:
                    error = slot.task.exception()
                    if error is not None:
                        # An internal failure after dispatch cannot establish absence of effects.
                        slot.dispatched = executor.record_result(calls[index], ToolResult.fail(
                            'INDETERMINATE', f'Tool dispatch failed: {type(error).__name__}'), status='indeterminate')
                    else:
                        slot.dispatched = slot.task.result()
            if slot.dispatched is not None:
                await settle(index, slot)
        if held is not None:
            completed[held.call.id] = executor.skip(held, 'not_executed_after_interruption')
        for call in calls:
            if call.id not in completed:
                completed[call.id] = executor.record_result(call, ToolResult.fail(
                    'not_executed_after_interruption' if not stopped else 'not_executed_after_failure',
                    'This tool did not execute. Replan using the preceding results.'), status='cancelled')
