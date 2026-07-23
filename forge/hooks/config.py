'''Load user and repository lifecycle hook settings.'''

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from forge.hooks.models import HookSettings


class HookConfigurationError(ValueError):
    '''Raised when a hook settings file is invalid.'''


def load_hook_settings(
    root: Path,
    *,
    user_settings_path: Path | None = None,
) -> HookSettings:
    '''Merge user hooks first and project hooks second.'''
    resolved_user = (
        user_settings_path
        if user_settings_path is not None
        else Path.home() / '.forge' / 'settings.json'
    )
    paths = (resolved_user, root.resolve() / '.forge' / 'settings.json')
    merged: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        if not path.is_file():
            continue
        data = _read_settings(path)
        hooks = data.get('hooks', {})
        if not isinstance(hooks, dict):
            raise HookConfigurationError(
                f'{path}: hooks must be a JSON object.'
            )
        for event_name, specifications in hooks.items():
            if not isinstance(specifications, list):
                raise HookConfigurationError(
                    f'{path}: hooks.{event_name} must be a JSON array.'
                )
            merged.setdefault(str(event_name), []).extend(specifications)
    try:
        settings = HookSettings.model_validate({'hooks': merged})
    except ValidationError as error:
        raise HookConfigurationError(
            f'Invalid ForgeCode hook settings: {error}'
        ) from error
    identifiers: set[str] = set()
    for hooks in settings.hooks.values():
        for hook in hooks:
            if hook.id in identifiers:
                raise HookConfigurationError(
                    f'Duplicate hook id after settings merge: {hook.id}'
                )
            identifiers.add(hook.id)
    return settings


def _read_settings(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HookConfigurationError(
            f'Could not read hook settings {path}: {error}'
        ) from error
    if not isinstance(data, dict):
        raise HookConfigurationError(
            f'{path}: top-level settings must be a JSON object.'
        )
    return data
