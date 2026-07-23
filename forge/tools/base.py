'''Shared tool contracts, validation, registry, and repository boundaries.'''

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from forge.permissions.policy import PermissionRequest


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


def _validation_location(error: dict[str, Any]) -> str:
    '''Render one Pydantic error location for a model-facing diagnostic.'''
    location = error.get('loc', ())
    return '.'.join(str(part) for part in location) or 'arguments'


def _argument_at_location(
    arguments: Mapping[str, Any],
    location: tuple[Any, ...],
) -> Any:
    '''Return the original value named by a Pydantic error location.'''
    value: Any = arguments
    for part in location:
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        elif (
            isinstance(value, (list, tuple))
            and isinstance(part, int)
            and 0 <= part < len(value)
        ):
            value = value[part]
        else:
            return None
    return value


def _validation_problem(
    error: dict[str, Any],
    arguments: Mapping[str, Any],
) -> str:
    '''Turn one Pydantic error into a concise actionable explanation.'''
    location = _validation_location(error)
    error_type = error.get('type')
    context = error.get('ctx') or {}
    value = _argument_at_location(
        arguments,
        tuple(error.get('loc', ())),
    )
    if error_type == 'missing':
        return f'`{location}` is required but missing'
    if error_type == 'extra_forbidden':
        return f'`{location}` is not an allowed argument'
    if error_type == 'string_too_long':
        maximum = context.get('max_length', 'the configured limit')
        actual = len(value) if isinstance(value, str) else 'unknown'
        return (
            f'`{location}` has {actual} characters; maximum is {maximum}'
        )
    if error_type == 'string_too_short':
        minimum = context.get('min_length', 'the configured minimum')
        actual = len(value) if isinstance(value, str) else 'unknown'
        return (
            f'`{location}` has {actual} characters; minimum is {minimum}'
        )
    message = error.get('msg', 'invalid value')
    return f'`{location}`: {message}'


def _validation_recovery_hint(
    tool_name: str,
    errors: list[dict[str, Any]],
) -> str:
    '''Give the model a concrete next action for common schema failures.'''
    error_types = {str(error.get('type')) for error in errors}
    if 'string_too_long' in error_types:
        if tool_name == 'write_file':
            return (
                'Create a smaller new-file skeleton with write_file, then '
                'extend it through multiple focused apply_patch calls.'
            )
        if tool_name == 'apply_patch':
            return 'Split the change into multiple focused patches.'
        if tool_name == 'replace_text':
            return 'Split the replacement into multiple smaller edits.'
        return 'Split the value across multiple supported tool calls.'
    if 'extra_forbidden' in error_types:
        return 'Remove arguments that are not in the allowed field list.'
    if 'missing' in error_types:
        return 'Add every required argument before retrying.'
    return 'Correct the listed values before retrying.'


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
    def provenance(self) -> dict[str, Any]:
        return {'source': 'builtin'}

    @property
    def definition(self) -> dict[str, Any]:
        '''Return a plain tool schema understood by the model adapter.'''
        return {
            'name': self.name,
            'description': self.description,
            'input_schema': self.input_model.model_json_schema(),
        }

    def permission_request(
        self,
        arguments: Mapping[str, Any],
    ) -> PermissionRequest | None:
        '''Optionally provide a conservative tool-specific permission request.'''
        del arguments
        return None

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
            validation_errors = error.errors(
                include_url=False,
                include_input=False,
            )
            problems = '; '.join(
                _validation_problem(item, arguments)
                for item in validation_errors[:5]
            )
            if len(validation_errors) > 5:
                problems += (
                    f'; and {len(validation_errors) - 5} more problem(s)'
                )
            recovery_hint = _validation_recovery_hint(
                self.name,
                validation_errors,
            )
            separator = ', '
            empty = 'none'
            return ToolResult.fail(
                'invalid_arguments',
                (
                    f'Invalid arguments for tool {self.name}. '
                    f'Allowed arguments: {separator.join(allowed) or empty}. '
                    f'Required arguments: '
                    f'{separator.join(required) or empty}. '
                    f'Problems: {problems}. Recovery: {recovery_hint}'
                ),
                details={
                    'allowed_arguments': allowed,
                    'required_arguments': required,
                    'unknown_arguments': unknown,
                    'validation_errors': validation_errors,
                    'recovery_hint': recovery_hint,
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
        hidden_tools: Iterable[str] = (),
    ) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        self._hidden_tools = frozenset(hidden_tools)
        self.workspace_tracker = workspace_tracker
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool[Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f'Duplicate tool name: {tool.name}')
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        '''Remove a dynamically registered tool if it exists.'''
        self._tools.pop(name, None)

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [
            tool.definition
            for name, tool in self._tools.items()
            if name not in self._hidden_tools
        ]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def definition(self, name: str) -> dict[str, Any] | None:
        '''Return one registered schema, including a phase-hidden fallback.'''
        tool = self._tools.get(name)
        return None if tool is None else tool.definition

    def effect(self, name: str) -> ToolEffect | None:
        tool = self._tools.get(name)
        return None if tool is None else tool.effect

    def provenance(self, name: str) -> dict[str, Any]:
        tool = self._tools.get(name)
        return {} if tool is None else tool.provenance

    def permission_request(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> PermissionRequest | None:
        tool = self._tools.get(name)
        if tool is None:
            return None
        return tool.permission_request(arguments)

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
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ToolExecutionError(
            'path_outside_repository',
            f'Path is outside the repository: {raw_path}',
        ) from error
    if is_repository_path_protected(relative):
        raise ToolExecutionError(
            'protected_path',
            f'Path is protected from repository tools: {raw_path}',
            details={'path': raw_path},
        )
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


CONTROL_PLANE_DIRECTORIES = frozenset({'.forge', '.git'})
PUBLIC_ENV_FILES = frozenset({'.env.example'})


def is_repository_path_protected(path: Path) -> bool:
    '''Return whether a repository-relative path is control or secret state.'''
    for part in path.parts:
        name = part.casefold()
        if name in CONTROL_PLANE_DIRECTORIES:
            return True
        if name == '.env':
            return True
        if name.startswith('.env.') and name not in PUBLIC_ENV_FILES:
            return True
    return False


IGNORED_DIRECTORIES = frozenset(
    {
        '.forge',
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
