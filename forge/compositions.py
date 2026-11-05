"""User-selected agent and plugin configuration; never auto-import project code."""
from dataclasses import replace
import json
import os
from pathlib import Path

from forge.agents import AgentDefinition, PluginSpec


def settings_path():
    return Path(os.environ.get('FORGE_DATA_DIR') or Path.home() / '.forge') / 'settings.json'


def read_settings(path=None):
    path = Path(path or settings_path())
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Settings must be an object')
    return value


def update_settings(update, *, path=None):
    path = Path(path or settings_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    from forge.sessions.locking import exclusive_append
    with exclusive_append(path):
        value = read_settings(path)
        update(value)
        temporary = path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def resolve_agent(name, *, settings=None):
    settings = read_settings() if settings is None else settings
    configured = settings.get('agents', {}).get(name)
    if configured is None:
        if name not in {'coding', 'core'}:
            raise ValueError(f'Unknown agent: {name}')
        agent = AgentDefinition() if name == 'coding' else AgentDefinition.core()
        requested = []
    else:
        value = dict(configured)
        requested = value.pop('plugins', [])
        agent = AgentDefinition.from_dict({**value, 'name': name})
    enabled = settings.get('plugins', {})
    specs = []
    for identifier in requested:
        value = enabled.get(identifier)
        if not value or value.get('enabled') is not True or value.get('trusted') is not True:
            raise ValueError(f'Plugin is not enabled and trusted: {identifier}')
        specs.append(PluginSpec(source=value['source'], trusted=True, config=value.get('config', {}),
                                requires=tuple(value.get('requires', ()))))
    return replace(agent, plugins=tuple(specs))
