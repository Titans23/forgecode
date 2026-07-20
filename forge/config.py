'''Environment-backed ForgeCode configuration.'''

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


DEFAULT_ANTHROPIC_BASE_URL = 'https://api.anthropic.com'
DEFAULT_MODEL_MAX_TOKENS = 8_192
DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS = 120.0


class ConfigurationError(ValueError):
    '''Raised when ForgeCode model configuration is incomplete or invalid.'''


@dataclass(frozen=True, slots=True)
class ForgeConfig:
    '''Validated configuration used to create the first model client.'''

    api_key: str
    model_id: str
    base_url: str = DEFAULT_ANTHROPIC_BASE_URL
    max_tokens: int = DEFAULT_MODEL_MAX_TOKENS
    context_window: int | None = None
    request_timeout_seconds: float = DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        api_key = self.api_key.strip()
        model_id = self.model_id.strip()
        base_url = self.base_url.strip().rstrip('/')

        if not api_key:
            raise ConfigurationError('ANTHROPIC_API_KEY is not set.')
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
        '''Load Anthropic-compatible settings from environment variables.'''
        if environ is None:
            load_dotenv(dotenv_path=Path.cwd() / '.env', override=False)
            source: Mapping[str, str] = os.environ
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
            api_key=source.get('ANTHROPIC_API_KEY', ''),
            model_id=source.get('MODEL_ID', ''),
            base_url=source.get(
                'ANTHROPIC_BASE_URL',
                DEFAULT_ANTHROPIC_BASE_URL,
            ),
            max_tokens=max_tokens,
            context_window=context_window,
            request_timeout_seconds=request_timeout_seconds,
        )
