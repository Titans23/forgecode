'''Read-only repository exploration with an isolated model context.'''

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from forge.runtime.state import TurnCompleted
from forge.tools.base import Tool, ToolInput, ToolRegistry, ToolResult
from forge.tools.filesystem import ListDirectoryTool, ReadFileTool
from forge.tools.git import GitLogTool
from forge.tools.search import FindFilesTool, GrepTool

if TYPE_CHECKING:
    from forge.runtime.model_client import ModelClient


MAX_REPORT_CHARACTERS = 20_000
EvidenceText = Annotated[str, Field(min_length=1, max_length=1_000)]


@dataclass(frozen=True, slots=True)
class ExploreAgentConfig:
    '''Hard bounds owned by the subagent rather than the calling model.'''

    max_iterations: int = 8
    max_input_tokens: int = 120_000

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError('max_iterations must be positive')
        if self.max_input_tokens < 1:
            raise ValueError('max_input_tokens must be positive')


class RelevantFile(BaseModel):
    model_config = ConfigDict(extra='forbid')

    path: str = Field(min_length=1, max_length=500)
    relevance: str = Field(min_length=1, max_length=1_000)


class RootCauseHypothesis(BaseModel):
    model_config = ConfigDict(extra='forbid')

    hypothesis: str = Field(min_length=1, max_length=1_000)
    evidence: list[EvidenceText] = Field(default_factory=list, max_length=10)
    confidence: Literal['high', 'medium', 'low']


class SuggestedEditPoint(BaseModel):
    model_config = ConfigDict(extra='forbid')

    path: str = Field(min_length=1, max_length=500)
    location: str = Field(min_length=1, max_length=500)
    suggestion: str = Field(min_length=1, max_length=1_000)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    current_excerpt: str | None = Field(default=None, max_length=2_500)


class ExploreReport(BaseModel):
    '''The only subagent content admitted into the parent conversation.'''

    model_config = ConfigDict(extra='forbid')

    summary: str = Field(min_length=1, max_length=2_000)
    relevant_files: list[RelevantFile] = Field(
        default_factory=list,
        max_length=30,
    )
    call_paths: list[EvidenceText] = Field(default_factory=list, max_length=20)
    root_cause_hypotheses: list[RootCauseHypothesis] = Field(
        default_factory=list,
        max_length=20,
    )
    suggested_edit_points: list[SuggestedEditPoint] = Field(
        default_factory=list,
        max_length=30,
    )
    unresolved_questions: list[EvidenceText] = Field(
        default_factory=list,
        max_length=20,
    )


class ExploreRepositoryInput(ToolInput):
    question: str = Field(min_length=1, max_length=4_000)
    focus_paths: list[Annotated[str, Field(min_length=1, max_length=500)]] = (
        Field(default_factory=list, max_length=20)
    )


def create_explore_registry(root: Path) -> ToolRegistry:
    '''Build the fixed read-only capability boundary for Explore Agent.'''
    return ToolRegistry(
        [
            ListDirectoryTool(root),
            FindFilesTool(root),
            GrepTool(root),
            ReadFileTool(root),
            GitLogTool(root),
        ]
    )


def load_explore_prompt() -> str:
    prompt_path = (
        Path(__file__).resolve().parents[1] / 'prompts' / 'explore.md'
    )
    prompt = prompt_path.read_text(encoding='utf-8').strip()
    if not prompt:
        raise RuntimeError('Explore Agent system prompt is empty.')
    return prompt


def _extract_report(text: str) -> ExploreReport:
    stripped = text.strip()
    fenced = re.fullmatch(
        r'```(?:json)?\s*(.*?)\s*```',
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    candidate = fenced.group(1) if fenced else stripped
    payload = json.loads(candidate)
    if isinstance(payload, dict):
        edit_points = payload.get('suggested_edit_points')
        if isinstance(edit_points, list):
            for edit_point in edit_points:
                if not isinstance(edit_point, dict):
                    continue
                excerpt = edit_point.get('current_excerpt')
                if isinstance(excerpt, str) and len(excerpt) > 2_500:
                    edit_point['current_excerpt'] = excerpt[:2_500]
    return ExploreReport.model_validate(payload)


class ExploreRepositoryTool(Tool[ExploreRepositoryInput]):
    name = 'explore_repository'
    description = (
        'Delegate a broad, read-only repository investigation to an isolated '
        'Explore Agent. Use it for cross-file discovery, call-path tracing, '
        'or evidence-based root-cause analysis. The subagent cannot edit files '
        'or run arbitrary commands; this tool returns only a compact structured '
        'report, keeping raw exploration out of the main context.'
    )
    input_model = ExploreRepositoryInput

    def __init__(
        self,
        root: Path,
        *,
        config: ExploreAgentConfig | None = None,
        client_factory: Callable[[], ModelClient] | None = None,
    ) -> None:
        super().__init__(root)
        self.config = config or ExploreAgentConfig()
        self._client_factory = client_factory

    @property
    def provenance(self) -> dict[str, Any]:
        return {'source': 'subagent', 'name': 'explore'}

    def _create_client(self) -> ModelClient:
        if self._client_factory is not None:
            return self._client_factory()
        from forge.runtime.model_client import AnthropicModelClient

        return AnthropicModelClient.from_config()

    async def execute(
        self,
        arguments: ExploreRepositoryInput,
    ) -> ToolResult:
        # Local import avoids coupling module initialization to the main loop.
        from forge.runtime.agent_loop import Conversation

        focus = (
            '\nFocus paths supplied by the parent:\n'
            + '\n'.join(f'- {path}' for path in arguments.focus_paths)
            if arguments.focus_paths
            else ''
        )
        prompt = (
            'Investigate this repository question:\n'
            f'{arguments.question}{focus}'
        )
        conversation = Conversation(
            client=self._create_client(),
            system_prompt=load_explore_prompt(),
            registry=create_explore_registry(self.root),
            max_iterations=self.config.max_iterations,
            max_turn_input_tokens=self.config.max_input_tokens,
            stagnation_warning=4,
            stagnation_limit=6,
            context_root=self.root,
            include_task_tools=False,
        )
        completed = None
        async for event in conversation.stream(prompt):
            if isinstance(event, TurnCompleted):
                completed = event.result
        if completed is None:
            return ToolResult.fail(
                'explore_incomplete',
                'Explore Agent ended without a final report.',
                metadata=self._metadata(),
            )
        usage = completed.usage
        metadata = self._metadata(
            model_calls=completed.model_calls,
            input_tokens=usage.total_input_tokens,
            output_tokens=usage.output_tokens,
            status=completed.status,
        )
        if completed.status != 'completed':
            return ToolResult.fail(
                'explore_stopped',
                'Explore Agent stopped before producing a valid report.',
                content=completed.text[:2_000],
                details={'status': completed.status},
                metadata=metadata,
            )
        try:
            report = _extract_report(completed.text)
        except (json.JSONDecodeError, ValidationError, TypeError) as error:
            return ToolResult.fail(
                'explore_invalid_report',
                'Explore Agent returned an invalid structured report.',
                content=completed.text[:2_000],
                details={'validation_error': str(error)},
                metadata=metadata,
            )
        content = report.model_dump_json(indent=2)
        if len(content) > MAX_REPORT_CHARACTERS:
            return ToolResult.fail(
                'explore_report_too_large',
                'Explore Agent report exceeded the parent-context limit.',
                details={
                    'characters': len(content),
                    'maximum_characters': MAX_REPORT_CHARACTERS,
                },
                metadata=metadata,
            )
        return ToolResult.ok(
            'Explore Agent completed a read-only repository investigation.',
            content=content,
            metadata={
                **metadata,
                'report_characters': len(content),
                'relevant_file_count': len(report.relevant_files),
            },
        )

    def _metadata(
        self,
        *,
        model_calls: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        return {
            'subagent': 'explore',
            'isolated_context': True,
            'read_only': True,
            'max_iterations': self.config.max_iterations,
            'max_input_tokens': self.config.max_input_tokens,
            'model_calls': model_calls,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'status': status,
        }