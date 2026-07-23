'''Built-in ForgeCode tools.'''

from pathlib import Path

from forge.tools.base import ToolRegistry
from forge.tools.filesystem import (
    ListDirectoryTool,
    ReadFileTool,
    ReplaceTextTool,
    WriteFileChunkTool,
    WriteFileTool,
)
from forge.tools.finish import FinishTaskTool
from forge.tools.git import GitDiffTool, GitLogTool, GitStatusTool
from forge.tools.patch import ApplyPatchTool
from forge.tools.search import FindFilesTool, GrepTool
from forge.tools.shell import RunCommandTool
from forge.tools.verify import VerifyTool
from forge.runtime.workspace import WorkspaceTracker


def create_default_registry(root: Path) -> ToolRegistry:
    '''Create built-in tools sharing one task-local workspace tracker.'''
    # Delayed import prevents runtime.state -> forge.tools package cycles.
    from forge.subagents.explore import ExploreRepositoryTool

    tracker = WorkspaceTracker(root)
    return ToolRegistry(
        [
            ListDirectoryTool(root),
            FindFilesTool(root),
            ReadFileTool(root),
            GrepTool(root),
            WriteFileTool(root),
            WriteFileChunkTool(root),
            ReplaceTextTool(root),
            ApplyPatchTool(root),
            RunCommandTool(root),
            VerifyTool(root, tracker),
            GitStatusTool(root),
            GitDiffTool(root),
            ExploreRepositoryTool(root),
            FinishTaskTool(root),
        ],
        workspace_tracker=tracker,
    )


__all__ = [
    'ApplyPatchTool',
    'FindFilesTool',
    'FinishTaskTool',
    'GitDiffTool',
    'GitLogTool',
    'GitStatusTool',
    'GrepTool',
    'ListDirectoryTool',
    'ReadFileTool',
    'ReplaceTextTool',
    'RunCommandTool',
    'VerifyTool',
    'WriteFileChunkTool',
    'WriteFileTool',
    'ToolRegistry',
    'create_default_registry',
]
