'''Controlled access to large tool-result artifacts retained by ContextManager.'''

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from forge.tools.base import Tool, ToolExecutionError, ToolInput, ToolResult

if TYPE_CHECKING:
    from forge.context.manager import ContextManager


class ReadContextArtifactInput(ToolInput):
    artifact_id: str = Field(
        min_length=64, max_length=68,
        description='The sha256 artifact ID shown in a truncated tool result.',
    )
    max_characters: int = Field(default=20_000, ge=1, le=100_000)
    offset: int = Field(default=0, ge=0, description='Zero-based character offset for paging through the artifact.')


class ReadContextArtifactTool(Tool[ReadContextArtifactInput]):
    name = 'read_context_artifact'
    description = (
        'Read a page of a large tool result by its sha256 artifact ID. Increase '
        'offset to recover middle diagnostics or later pages. This cannot access '
        'arbitrary .forge files or workspace paths.'
    )
    input_model = ReadContextArtifactInput

    def __init__(self, root: Path, manager: ContextManager) -> None:
        super().__init__(root)
        self.manager = manager

    async def execute(self, arguments: ReadContextArtifactInput) -> ToolResult:
        try:
            content = self.manager.read_artifact(
                arguments.artifact_id, max_characters=arguments.max_characters,
                offset=arguments.offset,
            )
        except (ValueError, FileNotFoundError) as error:
            raise ToolExecutionError('invalid_context_artifact', str(error)) from error
        return ToolResult.ok(
            'Read the bounded context artifact.', content=content,
            metadata={
                'artifact_id': arguments.artifact_id,
                'offset': arguments.offset,
                'characters_returned': len(content),
                'next_offset': arguments.offset + len(content),
            },
        )
