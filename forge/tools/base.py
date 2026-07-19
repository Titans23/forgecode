'''Shared tool contracts, validation, registry, and repository boundaries.'''

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError


class ToolInput(BaseModel):
    '''Strict base model for model-provided tool arguments.'''

    model_config = ConfigDict(extra='forbid')


@dataclass(frozen=True, slots=True)
class ToolError:
    '''Structured failure returned without crashing the Agent Loop.'''

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResult:
    '''Provider-independent result of one tool execution.'''

    success: bool
    summary: str
    content: str = ''
    error: ToolError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.success and self.error is not None:
            raise ValueError('A successful tool result cannot contain an error.')
        if not self.success and self.error is None:
            raise ValueError('A failed tool result must contain an error.')

    @classmethod
    def ok(
        cls,
        summary: str,
        *,
        content: str = '',
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(
            success=True,
            summary=summary,
            content=content,
            metadata=metadata or {},
        )

    @classmethod
    def fail(
        cls,
        code: str,
        message: str,
        *,
        summary: str | None = None,
        content: str = '',
        details: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(
            success=False,
            summary=summary or message,
            content=content,
            error=ToolError(
                code=code,
                message=message,
                details=details or {},
            ),
            metadata=metadata or {},
        )


class ToolExecutionError(RuntimeError):
    '''Expected operational failure raised inside a concrete tool.'''

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


InputT = TypeVar('InputT', bound=ToolInput)


class Tool(ABC, Generic[InputT]):
    '''Validate model input and convert all failures to ToolResult.'''

    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[ToolInput]]
    effect: ClassVar[ToolEffect] = 'read_only'

    def __init__(self, root: Path) -> None:
        resolved_root = root.resolve()
        if not resolved_root.is_dir():
            raise ValueError(f'Tool root is not a directory: {root}')
        self.root = resolved_root

    @property
    def definition(self) -> dict[str, Any]:
        '''Return a plain tool schema understood by the model adapter.'''
        return {
            'name': self.name,
            'description': self.description,
            'input_schema': self.input_model.model_json_schema(),
        }

    async def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        '''Validate arguments and execute without leaking exceptions.'''
        try:
            validated = self.input_model.model_validate(dict(arguments))
        except ValidationError as error:
            schema = self.input_model.model_json_schema()
            properties = schema.get('properties', {})
            allowed = sorted(properties)
            required = sorted(schema.get('required', []))
            unknown = sorted(set(arguments) - set(properties))
            separator = ', '
            empty = 'none'
            return ToolResult.fail(
                'invalid_arguments',
                (
                    f'Invalid arguments for tool {self.name}. '
                    f'Allowed arguments: {separator.join(allowed) or empty}. '
                    f'Required arguments: '
                    f'{separator.join(required) or empty}.'
                ),
                details={
                    'allowed_arguments': allowed,
                    'required_arguments': required,
                    'unknown_arguments': unknown,
                    'validation_errors': error.errors(
                        include_url=False,
                        include_input=False,
                    )
                },
            )

        try:
            return await self.execute(validated)
        except ToolExecutionError as error:
            return ToolResult.fail(
                error.code,
                str(error),
                details=error.details,
            )
        except Exception as error:
            return ToolResult.fail(
                'tool_execution_failed',
                f'{self.name} failed: {error}',
                details={'exception_type': type(error).__name__},
            )

    @abstractmethod
    async def execute(self, arguments: InputT) -> ToolResult:
        '''Execute already validated arguments.'''


class ToolRegistry:
    '''Resolve tool names and expose schemas in deterministic order.'''

    def __init__(
        self,
        tools: Iterable[Tool[Any]] = (),
        *,
        workspace_tracker: Any | None = None,
    ) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        self.workspace_tracker = workspace_tracker
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool[Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f'Duplicate tool name: {tool.name}')
        self._tools[tool.name] = tool

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [tool.definition for tool in self._tools.values()]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def effect(self, name: str) -> ToolEffect | None:
        tool = self._tools.get(name)
        return None if tool is None else tool.effect

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.fail(
                'unknown_tool',
                f'Unknown tool: {name}',
                details={'available_tools': list(self._tools)},
            )
        return await tool.run(arguments)


ToolEffect = Literal['read_only', 'workspace_write', 'process']


def resolve_repository_path(
    root: Path,
    raw_path: str,
    *,
    must_exist: bool = True,
) -> Path:
    '''Resolve one relative path while preventing repository escape.'''
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ToolExecutionError(
            'path_outside_repository',
            f'Absolute paths are not allowed: {raw_path}',
        )

    resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ToolExecutionError(
            'path_outside_repository',
            f'Path is outside the repository: {raw_path}',
        ) from error
    if must_exist and not resolved.exists():
        raise ToolExecutionError(
            'path_not_found',
            f'Path does not exist: {raw_path}',
        )
    return resolved


def display_path(root: Path, path: Path) -> str:
    '''Return a stable POSIX-style path relative to the tool root.'''
    relative = path.relative_to(root)
    return relative.as_posix() or '.'


IGNORED_DIRECTORIES = frozenset(
    {
        '.git',
        '.idea',
        '.mypy_cache',
        '.pytest_cache',
        '.ruff_cache',
        '.venv',
        '.vscode',
        '__pycache__',
        'dist',
        'node_modules',
        'target',
    }
)
