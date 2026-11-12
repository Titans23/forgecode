'''Built-in ForgeCode tools.'''

from __future__ import annotations
from pathlib import Path
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.runtime.profile import ExecutionProfile

from forge.tools.base import ToolRegistry
_EXPORT_MODULES = {
    **dict.fromkeys(('CreateDirectoryTool', 'ListDirectoryTool', 'ReadFileTool', 'RemoveDirectoryTool',
                    'ReplaceTextTool', 'WriteFileChunkTool', 'WriteFileTool'), 'forge.tools.filesystem'),
    **dict.fromkeys(('FinishTaskTool', 'ReviewDeliveryTool'), 'forge.tools.finish'),
    **dict.fromkeys(('GitDiffTool', 'GitLogTool', 'GitStatusTool'), 'forge.tools.git'),
    **dict.fromkeys(('FindFilesTool', 'GrepTool'), 'forge.tools.search'),
    **dict.fromkeys(('LoadSkillTool', 'ReadSkillResourceTool', 'SkillManager'), 'forge.skills'),
    'ApplyPatchTool': 'forge.tools.patch', 'RunCommandTool': 'forge.tools.shell',
    'VerifyTool': 'forge.tools.verify', 'WorkspaceTracker': 'forge.runtime.workspace',
    'ExecutionProfile': 'forge.runtime.profile',
}


def __getattr__(name):
    if name not in _EXPORT_MODULES:
        raise AttributeError(name)
    value = getattr(import_module(_EXPORT_MODULES[name]), name)
    globals()[name] = value
    return value


def create_default_registry(
    root: Path,
    *,
    execution_profile: ExecutionProfile | None = None,
    allow_container_writes: bool = False,
    model_client_factory=None,
    tool_backend=None,
    event_recorder=None,
    selected_tools: tuple[str, ...] | None = None,
    skills_enabled: bool = True,
) -> ToolRegistry:
    '''Create built-in tools sharing one task-local workspace tracker.'''
    # Delayed import prevents runtime.state -> forge.tools package cycles.
    from forge.subagents.explore import ExploreRepositoryTool
    from forge.tools.filesystem import (CreateDirectoryTool, ListDirectoryTool, ReadFileTool,
        RemoveDirectoryTool, ReplaceTextTool, WriteFileChunkTool, WriteFileTool)
    from forge.tools.finish import FinishTaskTool, ReviewDeliveryTool
    from forge.tools.git import GitDiffTool, GitStatusTool
    from forge.tools.patch import ApplyPatchTool
    from forge.tools.search import FindFilesTool, GrepTool
    from forge.tools.shell import RunCommandTool
    from forge.tools.verify import VerifyTool
    from forge.tools.image import ViewImageTool
    from forge.runtime.workspace import WorkspaceTracker

    tracker = WorkspaceTracker(root, observer=getattr(tool_backend, 'observer', None))
    skill_tools = ()
    if skills_enabled and (selected_tools is None or {'load_skill', 'read_skill_resource'} & set(selected_tools)):
        from forge.skills import LoadSkillTool, ReadSkillResourceTool, SkillManager
        skill_manager = SkillManager(root)
        skill_tools = ((LoadSkillTool, lambda: LoadSkillTool(root, skill_manager)),
                       (ReadSkillResourceTool, lambda: ReadSkillResourceTool(root, skill_manager)))
    registry = ToolRegistry(
        [create() for kind, create in (
            (ListDirectoryTool, lambda: ListDirectoryTool(root)),
            (FindFilesTool, lambda: FindFilesTool(root)),
            (ReadFileTool, lambda: ReadFileTool(root)),
            (ViewImageTool, lambda: ViewImageTool(root)),
            (GrepTool, lambda: GrepTool(root)),
            *skill_tools,
            (CreateDirectoryTool, lambda: CreateDirectoryTool(root)),
            (RemoveDirectoryTool, lambda: RemoveDirectoryTool(root)),
            (WriteFileTool, lambda: WriteFileTool(root)),
            (WriteFileChunkTool, lambda: WriteFileChunkTool(root)),
            (ReplaceTextTool, lambda: ReplaceTextTool(root)),
            (ApplyPatchTool, lambda: ApplyPatchTool(root)),
            (RunCommandTool, lambda: RunCommandTool(
                root,
                execution_profile=execution_profile,
                allow_container_writes=allow_container_writes,
            )),
            (VerifyTool, lambda: VerifyTool(root, tracker)),
            (GitStatusTool, lambda: GitStatusTool(root)),
            (GitDiffTool, lambda: GitDiffTool(root)),
            (ExploreRepositoryTool, lambda: ExploreRepositoryTool(root, client_factory=model_client_factory,
                                  tool_backend=tool_backend, event_recorder=event_recorder)),
            (FinishTaskTool, lambda: FinishTaskTool(root)),
            (ReviewDeliveryTool, lambda: ReviewDeliveryTool(root))
        ) if selected_tools is None or kind.name in selected_tools],
        workspace_tracker=tracker,
        # Chunked writes remain an internal compatibility primitive. Normal
        # model calls can use write_file or apply_patch for large text edits.
        hidden_tools={'write_file_chunk'},
    )
    if 'run_command' in registry.names and getattr(getattr(tool_backend, 'observer', None), 'policy', {}).get('execution_mode') is not None:
        registry.implementation('run_command').description = RunCommandTool.description
    return registry


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
