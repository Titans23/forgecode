"""Declarative agent compositions; importing them starts no runtime."""
from dataclasses import dataclass, field, asdict
from typing import Literal
import re


@dataclass(frozen=True, slots=True)
class PluginSpec:
    source: str
    trusted: bool = False
    config: dict = field(default_factory=dict)
    requires: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.source, str) or not self.source:
            raise ValueError('Plugin source is required')
        if type(self.trusted) is not bool or not isinstance(self.config, dict):
            raise ValueError('Plugin trust must be boolean and config must be an object')


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    name: str = 'coding'
    base: Literal['coding', 'core'] = 'coding'
    system_prompt: str | None = None
    tools: tuple[str, ...] | None = None
    plugins: tuple[PluginSpec, ...] = ()
    role_models: dict[str, str] = field(default_factory=dict)
    completion: Literal['normal', 'strict'] = 'normal'

    def __post_init__(self):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', self.name):
            raise ValueError('Agent name must be a simple identifier')
        if self.base not in {'coding', 'core'}:
            raise ValueError('Agent base must be coding or core')
        if self.completion not in {'normal', 'strict'}:
            raise ValueError('Completion policy must be normal or strict')
        if self.base == 'core' and self.completion == 'strict':
            raise ValueError('Code verification requires a coding composition')

    @classmethod
    def core(cls, name='core', *, system_prompt='You are a helpful assistant.'):
        return cls(name=name, base='core', system_prompt=system_prompt)

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data['plugins'] = tuple(PluginSpec(**{**p, 'requires': tuple(p.get('requires', ()))})
                                for p in data.get('plugins', ()))
        if data.get('tools') is not None:
            data['tools'] = tuple(data['tools'])
        return cls(**data)
