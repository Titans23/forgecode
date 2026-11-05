"""Declarative agent compositions; importing them starts no runtime."""
from dataclasses import dataclass
from typing import Literal
import re


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    name: str = 'coding'
    base: Literal['coding', 'core'] = 'coding'
    system_prompt: str | None = None
    tools: tuple[str, ...] | None = None

    def __post_init__(self):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', self.name):
            raise ValueError('Agent name must be a simple identifier')
        if self.base not in {'coding', 'core'}:
            raise ValueError('Agent base must be coding or core')

    @classmethod
    def core(cls, name='core', *, system_prompt='You are a helpful assistant.'):
        return cls(name=name, base='core', system_prompt=system_prompt)
