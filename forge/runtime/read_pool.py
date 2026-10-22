"""Bounded independent reads. Mutations, extensions and native streams stay ordered."""
import asyncio


def independent_reads(conversation, calls, start, limit):
    from forge.tools.filesystem import ReadFileTool, ListDirectoryTool
    from forge.tools.search import FindFilesTool, GrepTool
    executor = conversation.tool_executor
    hooks = conversation.hook_manager
    from forge.hooks.manager import HookManager
    if limit < 2 or len(calls) - start < 2:
        return []
    if hooks is not None and (type(hooks) is not HookManager or any(hooks.settings.hooks.values())):
        return []
    if executor.backend is not None and not getattr(executor.backend, 'supports_parallel_reads', False):
        return []
    result, targets = [], set()
    for call in calls[start:start + limit]:
        implementation = conversation.registry.implementation(call.name)
        if type(implementation) not in (ReadFileTool, ListDirectoryTool, FindFilesTool, GrepTool):
            break
        key = (call.name, str(call.arguments.get('path', '.')))
        if key in targets:
            break
        targets.add(key)
        result.append(call)
    return result if len(result) > 1 else []


class ReadPool:
    def __init__(self):
        self.tasks = {}

    def start(self, call, executor, scope_checker):
        self.tasks[call.id] = asyncio.create_task(executor.execute(call, scope_checker=scope_checker))

    def contains(self, call):
        return call.id in self.tasks

    async def result(self, call):
        outcome = await self.tasks[call.id]
        del self.tasks[call.id]
        return outcome

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        identities = list(self.tasks)
        outcomes = await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        return dict(zip(identities, outcomes))
