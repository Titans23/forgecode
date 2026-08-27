'''Built-in ForgeCode tools.'''

from pathlib import Path

from forge.tools.base import ToolRegistry
from forge.tools.filesystem import (
    CreateDirectoryTool,
    ListDirectoryTool,
    ReadFileTool,
    RemoveDirectoryTool,
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
from forge.skills import LoadSkillTool, ReadSkillResourceTool, SkillManager


def create_default_registry(root: Path) -> ToolRegistry:
    '''Create built-in tools sharing one task-local workspace tracker.'''
    # Delayed import prevents runtime.state -> forge.tools package cycles.
    from forge.subagents.explore import ExploreRepositoryTool

    tracker = WorkspaceTracker(root)
    skill_manager = SkillManager(root)
    return ToolRegistry(
        [
            ListDirectoryTool(root),
            FindFilesTool(root),
            ReadFileTool(root),
            GrepTool(root),
            LoadSkillTool(root, skill_manager),
            ReadSkillResourceTool(root, skill_manager),
            CreateDirectoryTool(root),
            RemoveDirectoryTool(root),
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
        hidden_tools={'write_file_chunk'},
    )


__all__ = [
    'ApplyPatchTool',
    'CreateDirectoryTool',
    'FindFilesTool',
    'FinishTaskTool',
    'GitDiffTool',
    'GitLogTool',
    'GitStatusTool',
    'GrepTool',
    'ListDirectoryTool',
    'LoadSkillTool',
    'ReadFileTool',
    'ReadSkillResourceTool',
    'RemoveDirectoryTool',
    'ReplaceTextTool',
    'RunCommandTool',
    'VerifyTool',
    'WriteFileChunkTool',
    'WriteFileTool',
    'ToolRegistry',
    'create_default_registry',
]
