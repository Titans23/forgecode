'''Queries and cancellation address only jobs owned by this live session.'''
from pydantic import Field

from forge.permissions.policy import PermissionRequest
from forge.tools.base import Tool, ToolInput


class JobInput(ToolInput):
    job_id: str = Field(min_length=1)


class JobOutputInput(JobInput):
    after: int = Field(default=0, ge=0, description='Use next_cursor from the previous result for incremental output.')
    wait_seconds: float = Field(default=0, ge=0, le=60, allow_inf_nan=False,
        description='Wait for output or completion, up to 60 seconds. Does not stop the command.')


class JobOutputTool(Tool[JobOutputInput]):
    name = 'job_output'
    description = 'Read incremental output and status of a background command in this session. Use after=next_cursor to avoid repeats.'
    input_model = JobOutputInput
    concurrency_safe = True

    def __init__(self, root, jobs):
        super().__init__(root)
        self.jobs = jobs

    def permission_request(self, arguments):
        return PermissionRequest(self.name, 'repository.read', 'low', reason='Read an owned command job')

    async def execute(self, arguments):
        return await self.jobs.output(arguments.job_id, after=arguments.after, wait_seconds=arguments.wait_seconds)


class JobKillTool(Tool[JobInput]):
    name = 'job_kill'
    description = 'Cancel a background command owned by this session and wait for its process cleanup.'
    input_model = JobInput

    def __init__(self, root, jobs):
        super().__init__(root)
        self.jobs = jobs

    def permission_request(self, arguments):
        return PermissionRequest(self.name, 'process.cancel', 'low', reason='Cancel an owned command job')

    async def execute(self, arguments):
        return await self.jobs.cancel(arguments.job_id)
