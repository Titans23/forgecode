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
from forge.tools.finish import FinishTaskTool, ReviewDeliveryTool
from forge.tools.git import GitDiffTool, GitLogTool, GitStatusTool
from forge.tools.patch import ApplyPatchTool
from forge.tools.search import FindFilesTool, GrepTool
from forge.tools.shell import RunCommandTool
from forge.tools.verify import VerifyTool
from forge.runtime.workspace import WorkspaceTracker
from forge.runtime.profile import ExecutionProfile
from forge.skills import LoadSkillTool, ReadSkillResourceTool, SkillManager


def create_default_registry(
    root: Path,
    *,
    execution_profile: ExecutionProfile | None = None,
    allow_container_writes: bool = False,
    model_client_factory=None,
    tool_backend=None,
    event_recorder=None,
) -> ToolRegistry:
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
            RunCommandTool(
                root,
                execution_profile=execution_profile,
                allow_container_writes=allow_container_writes,
            ),
            VerifyTool(root, tracker),
            GitStatusTool(root),
            GitDiffTool(root),
            ExploreRepositoryTool(root, client_factory=model_client_factory,
                                  tool_backend=tool_backend, event_recorder=event_recorder),
            FinishTaskTool(root),
            ReviewDeliveryTool(root),
        ],
        workspace_tracker=tracker,
        # Chunked whole-file writes remain an internal compatibility primitive.
        # apply_patch is the single model-visible large-edit path; no recovery
        # phase temporarily changes the schema set.
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
