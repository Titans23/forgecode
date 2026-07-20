'''Command verification that produces completion evidence.'''

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from forge.runtime.workspace import WorkspaceTracker
from forge.tools.base import (
    Tool,
    ToolExecutionError,
    ToolInput,
    ToolResult,
    display_path,
    resolve_repository_path,
)
from forge.tools.shell import (
    process_metadata,
    render_process_output,
    run_process,
)


class VerifyInput(ToolInput):
    command: str = Field(min_length=1)
    cwd: str = '.'
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)


class VerifyTool(Tool[VerifyInput]):
    name = 'verify'
    description = (
        'Run a test, build, lint, or type-check command as formal completion '
        'evidence after workspace changes. Choose the most relevant project '
        'command; use git diff --check only when no more specific validation '
        'exists. A successful result applies only to the exact current '
        'workspace revision, so verify again after later edits.'
    )
    input_model = VerifyInput
    effect = 'process'

    def __init__(self, root: Path, tracker: WorkspaceTracker) -> None:
        super().__init__(root)
        self.tracker = tracker

    async def execute(self, arguments: VerifyInput) -> ToolResult:
        cwd = resolve_repository_path(self.root, arguments.cwd)
        if not cwd.is_dir():
            raise ToolExecutionError(
                'not_a_directory',
                f'Verification cwd is not a directory: {arguments.cwd}',
            )
        revision = self.tracker.revision
        result = await run_process(
            arguments.command,
            cwd=cwd,
            timeout_seconds=arguments.timeout_seconds,
            shell=True,
        )
        metadata = {
            **process_metadata(result),
            'command': arguments.command,
            'cwd': display_path(self.root, cwd),
            'workspace_revision': revision,
            'verification': True,
        }
        content = render_process_output(result)
        if result.timed_out:
            return ToolResult.fail(
                'verification_timeout',
                f'Verification timed out after '
                f'{arguments.timeout_seconds:g}s.',
                content=content,
                metadata=metadata,
            )
        if result.exit_code != 0:
            return ToolResult.fail(
                'verification_failed',
                f'Verification exited with code {result.exit_code}.',
                content=content,
                metadata=metadata,
            )
        return ToolResult.ok(
            f'Verification passed in {result.duration_seconds:.3f}s.',
            content=content,
            metadata=metadata,
        )
