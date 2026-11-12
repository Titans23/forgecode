"""Declarative agent compositions; importing them starts no runtime."""
from dataclasses import dataclass, field, asdict
from typing import Literal
import re

CODING_FEATURES = frozenset({'tasks', 'repository', 'skills', 'hooks', 'mcp', 'checkpoints'})


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
    context_options: dict = field(default_factory=dict)
    max_parallel_tools: int = 10
    sandbox_provider: str | None = 'auto'
    features: tuple[str, ...] | None = None

    def __post_init__(self):
        if self.features is not None:
            if not isinstance(self.features, tuple) or not all(isinstance(item, str) for item in self.features):
                raise ValueError('Features must be a tuple of names')
            unknown = set(self.features) - CODING_FEATURES
            if unknown or self.base == 'core' and self.features:
                raise ValueError('Coding features require the coding base and known feature names')
            if self.completion == 'strict' and not {'tasks', 'repository'}.issubset(self.features):
                raise ValueError('Strict completion requires tasks and repository features')
        if type(self.max_parallel_tools) is not int or self.max_parallel_tools < 1:
            raise ValueError('max_parallel_tools must be a positive integer')
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', self.name):
            raise ValueError('Agent name must be a simple identifier')
        if self.base not in {'coding', 'core'}:
            raise ValueError('Agent base must be coding or core')
        if self.sandbox_provider == 'auto':
            object.__setattr__(self, 'sandbox_provider', 'native' if self.base == 'coding' else None)
        if self.sandbox_provider is not None and (not isinstance(self.sandbox_provider, str)
                or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', self.sandbox_provider)):
            raise ValueError('Sandbox provider must be a registered name or None')
        if self.base == 'core' and self.sandbox_provider is not None:
            raise ValueError('Core agents do not use a sandbox provider')
        if self.completion not in {'normal', 'strict'}:
            raise ValueError('Completion policy must be normal or strict')
        if self.base == 'core' and self.completion == 'strict':
            raise ValueError('Code verification requires a coding composition')
        if not isinstance(self.role_models, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in self.role_models.items()):
            raise ValueError('Role models must map role names to aliases')
        if not isinstance(self.context_options, dict):
            raise ValueError('Context options must be an object')

    @classmethod
    def core(cls, name='core', *, system_prompt='You are a helpful assistant.'):
        return cls(name=name, base='core', system_prompt=system_prompt)

    def as_dict(self):
        return asdict(self)

    @property
    def enabled_features(self) -> frozenset[str]:
        return frozenset(self.features) if self.features is not None else CODING_FEATURES if self.base == 'coding' else frozenset()

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data['plugins'] = tuple(PluginSpec(**{**p, 'requires': tuple(p.get('requires', ()))})
                                for p in data.get('plugins', ()))
        if data.get('tools') is not None:
            data['tools'] = tuple(data['tools'])
        if data.get('features') is not None:
            data['features'] = tuple(data['features'])
        return cls(**data)
