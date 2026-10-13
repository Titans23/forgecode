'''Environment-backed ForgeCode configuration.'''

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from dotenv import dotenv_values, load_dotenv, set_key


DEFAULT_ANTHROPIC_BASE_URL = 'https://api.anthropic.com'
DEFAULT_MODEL_MAX_TOKENS = 8_192
DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS = 120.0
FORGECODE_ENV_PATH = Path.home() / '.forge' / '.env'
USER_CONFIG_TEMPLATE = (
    '# ForgeCode user configuration. Run: forge config setup\n'
    '# Priority: process environment > project .env > this file.\n'
    '# FORGE_PROVIDER=anthropic\n'
    '# FORGE_MODEL=your-model-id\n'
    '# FORGE_API_KEY=your-api-key\n'
    '# FORGE_BASE_URL=https://your-provider-endpoint\n'
)
USER_MODEL_FIELDS = {
    'FORGE_PROVIDER', 'FORGE_MODEL', 'FORGE_API_KEY', 'FORGE_BASE_URL',
    'FORGE_REASONING_EFFORT', 'MODEL_MAX_TOKENS', 'MODEL_CONTEXT_WINDOW',
    'MODEL_REQUEST_TIMEOUT_SECONDS',
}


class ConfigurationError(ValueError):
    '''Raised when ForgeCode model configuration is incomplete or invalid.'''


def model_environment(project_env: Path | None = None,
                      overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Merge the shared user/project/process/explicit model configuration."""
    project_env = project_env or Path.cwd() / '.env'
    layers = [read_user_config(),
              dotenv_values(project_env) if project_env.is_file() else {},
              dict(os.environ), dict(overrides or {})]
    source = {key: value for layer in layers for key, value in layer.items() if value is not None}
    provider = source.get('FORGE_PROVIDER', 'anthropic')
    aliases = [('FORGE_API_KEY', {'anthropic': 'ANTHROPIC_API_KEY', 'openai_responses': 'OPENAI_API_KEY',
                                 'deepseek': 'DEEPSEEK_API_KEY'}.get(provider)),
               ('FORGE_MODEL', 'MODEL_ID'),
               ('FORGE_BASE_URL', {'anthropic': 'ANTHROPIC_BASE_URL', 'openai_responses': 'OPENAI_BASE_URL',
                                  'deepseek': 'DEEPSEEK_BASE_URL'}.get(provider))]
    for canonical, specific in aliases:
        for layer in reversed(layers):
            if any(name in layer and layer[name] is not None for name in (canonical, specific)):
                source.pop(specific, None)
                source[canonical] = layer.get(canonical) or layer.get(specific) or ''
                break
    return source


def initialize_user_config() -> bool:
    """Create an empty user configuration once, without touching existing settings."""
    path = FORGECODE_ENV_PATH
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not path.is_file():
            raise ConfigurationError('The user configuration path is not a file.')
        return False
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(USER_CONFIG_TEMPLATE)
    return True


def read_user_config() -> dict[str, str]:
    if not FORGECODE_ENV_PATH.is_file():
        return {}
    return {key: value for key, value in dotenv_values(FORGECODE_ENV_PATH, interpolate=False).items()
            if value is not None}


def save_user_config(updates: Mapping[str, str]) -> None:
    """Replace only requested model fields, preserving other entries and comments."""
    if not updates or not updates.keys() <= USER_MODEL_FIELDS:
        raise ConfigurationError('Unsupported user model setting.')
    if any(any(char in value for char in '\r\n\0') for value in updates.values()):
        raise ConfigurationError('Model settings must contain a single line without NUL characters.')
    path = FORGECODE_ENV_PATH
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = path.read_text(encoding='utf-8') if path.exists() else USER_CONFIG_TEMPLATE
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        for key, value in updates.items():
            set_key(temporary, key, value.replace('\\', '\\\\'), quote_mode='always', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class ForgeConfig:
    '''Validated configuration used to create the first model client.'''

    api_key: str = field(repr=False)
    model_id: str
    base_url: str | None = None
    max_tokens: int = DEFAULT_MODEL_MAX_TOKENS
    context_window: int | None = None
    request_timeout_seconds: float = DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS
    provider: str = 'anthropic'
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        if self.provider not in {'anthropic', 'openai_responses', 'deepseek'}:
            raise ConfigurationError('provider must be anthropic, openai_responses, or deepseek')
        api_key = self.api_key.strip()
        model_id = self.model_id.strip()
        default_url = {'anthropic': DEFAULT_ANTHROPIC_BASE_URL,
                       'openai_responses': 'https://api.openai.com/v1',
                       'deepseek': 'https://api.deepseek.com'}[self.provider]
        base_url = (default_url if self.base_url is None else self.base_url).strip().rstrip('/')

        if not api_key:
            key_name = {'anthropic': 'ANTHROPIC_API_KEY', 'openai_responses': 'OPENAI_API_KEY',
                        'deepseek': 'DEEPSEEK_API_KEY'}[self.provider]
            raise ConfigurationError(f'{key_name} is not set.')
        if not model_id:
            raise ConfigurationError('MODEL_ID is not set.')
        if not 1_024 <= self.max_tokens <= 32_768:
            raise ConfigurationError(
                'MODEL_MAX_TOKENS must be between 1024 and 32768.'
            )
        if self.context_window is not None:
            if not 4_096 <= self.context_window <= 2_000_000:
                raise ConfigurationError(
                    'MODEL_CONTEXT_WINDOW must be between 4096 and 2000000.'
                )
            if self.context_window <= self.max_tokens:
                raise ConfigurationError(
                    'MODEL_CONTEXT_WINDOW must be greater than '
                    'MODEL_MAX_TOKENS.'
                )
        if not 10 <= self.request_timeout_seconds <= 600:
            raise ConfigurationError(
                'MODEL_REQUEST_TIMEOUT_SECONDS must be between 10 and 600.'
            )

        parsed_url = urlsplit(base_url)
        if parsed_url.scheme not in {'http', 'https'} or not parsed_url.netloc:
            raise ConfigurationError(
                'ANTHROPIC_BASE_URL must be an absolute http(s) URL.'
            )

        object.__setattr__(self, 'api_key', api_key)
        object.__setattr__(self, 'model_id', model_id)
        object.__setattr__(self, 'base_url', base_url)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> ForgeConfig:
        '''Load settings from the environment or the nearest supported .env.'''
        if environ is None:
            project_env = Path.cwd() / '.env'
            source = model_environment(project_env)
            # Hooks/channels retain the existing project dotenv environment behavior.
            if project_env.is_file():
                load_dotenv(dotenv_path=project_env, override=False)
        else:
            source = environ

        raw_max_tokens = source.get(
            'MODEL_MAX_TOKENS',
            str(DEFAULT_MODEL_MAX_TOKENS),
        )
        try:
            max_tokens = int(raw_max_tokens)
        except ValueError as error:
            raise ConfigurationError(
                'MODEL_MAX_TOKENS must be an integer.'
            ) from error
        raw_context_window = source.get('MODEL_CONTEXT_WINDOW', '').strip()
        try:
            context_window = (
                int(raw_context_window) if raw_context_window else None
            )
        except ValueError as error:
            raise ConfigurationError(
                'MODEL_CONTEXT_WINDOW must be an integer.'
            ) from error
        raw_request_timeout = source.get(
            'MODEL_REQUEST_TIMEOUT_SECONDS',
            str(DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS),
        )
        try:
            request_timeout_seconds = float(raw_request_timeout)
        except ValueError as error:
            raise ConfigurationError(
                'MODEL_REQUEST_TIMEOUT_SECONDS must be a number.'
            ) from error

        return cls(
            provider=(provider := source.get('FORGE_PROVIDER', 'anthropic')),
            api_key=source.get('FORGE_API_KEY') or source.get({
                'anthropic': 'ANTHROPIC_API_KEY', 'openai_responses': 'OPENAI_API_KEY',
                'deepseek': 'DEEPSEEK_API_KEY'}.get(provider, ''), ''),
            model_id=source.get('FORGE_MODEL') or source.get('MODEL_ID', ''),
            base_url=source.get('FORGE_BASE_URL') or source.get({
                'anthropic': 'ANTHROPIC_BASE_URL', 'openai_responses': 'OPENAI_BASE_URL',
                'deepseek': 'DEEPSEEK_BASE_URL'}.get(provider, ''), {
                'anthropic': DEFAULT_ANTHROPIC_BASE_URL, 'openai_responses': 'https://api.openai.com/v1',
                'deepseek': 'https://api.deepseek.com'}.get(provider, '')),
            reasoning_effort=source.get('FORGE_REASONING_EFFORT') or None,
            max_tokens=max_tokens,
            context_window=context_window,
            request_timeout_seconds=request_timeout_seconds,
        )


def warn_unknown_config_fields(path: Path, value: Mapping[str, object], supported: set[str]) -> None:
    """Keep legacy values intact and report only ignored field names."""
    unknown = sorted(set(value) - supported)
    if unknown:
        import warnings
        names = ', '.join(repr(name[:64]) for name in unknown[:16])
        warnings.warn(f'{path.name}: unsupported fields {names}; remove them or use supported fields '
                      f"{', '.join(sorted(supported))}. Values were ignored and the file was preserved.",
                      UserWarning, stacklevel=2)
