'''Read-only model tools for progressive skill loading.'''

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import Field

from forge.skills.manager import SkillError, SkillManager
from forge.tools.base import Tool, ToolInput, ToolResult


class LoadSkillInput(ToolInput):
    name: str = Field(
        min_length=1,
        max_length=64,
        pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$',
    )


class LoadSkillTool(Tool[LoadSkillInput]):
    name = 'load_skill'
    description = (
        'Load the full SKILL.md instructions for one skill from the metadata '
        'listed in the system context. Call this only when that skill clearly '
        'matches the current request and was not already explicitly activated '
        'with $skill-name. The result also lists bundled resources that can be '
        'read separately with read_skill_resource.'
    )
    input_model = LoadSkillInput

    def __init__(self, root: Path, manager: SkillManager) -> None:
        super().__init__(root)
        self.manager = manager

    async def execute(self, arguments: LoadSkillInput) -> ToolResult:
        try:
            record = await asyncio.to_thread(
                self.manager.require,
                arguments.name,
            )
        except SkillError as error:
            return ToolResult.fail(
                error.code,
                str(error),
                details=error.details,
            )
        return ToolResult.ok(
            f'Loaded skill {record.name} from {record.source}.',
            content=record.render(),
            metadata={
                'skill': record.name,
                'source': record.source,
                'resources': [item.path for item in record.resources],
            },
        )


class ReadSkillResourceInput(ToolInput):
    skill: str = Field(
        min_length=1,
        max_length=64,
        pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$',
    )
    path: str = Field(min_length=1, max_length=500)


class ReadSkillResourceTool(Tool[ReadSkillResourceInput]):
    name = 'read_skill_resource'
    description = (
        'Read one UTF-8 bundled resource named by a previously loaded skill. '
        'The path must be copied from that skill resource list and start with '
        'scripts/, references/, or assets/. Do not guess paths or recursively '
        'scan skill directories.'
    )
    input_model = ReadSkillResourceInput

    def __init__(self, root: Path, manager: SkillManager) -> None:
        super().__init__(root)
        self.manager = manager

    async def execute(self, arguments: ReadSkillResourceInput) -> ToolResult:
        try:
            content, resource = await asyncio.to_thread(
                self.manager.read_resource,
                arguments.skill,
                arguments.path,
            )
        except SkillError as error:
            return ToolResult.fail(
                error.code,
                str(error),
                details=error.details,
            )
        return ToolResult.ok(
            f'Read skill resource {arguments.skill}/{resource.path}.',
            content=content,
            metadata={
                'skill': arguments.skill,
                'path': resource.path,
                'characters': len(content),
            },
        )
