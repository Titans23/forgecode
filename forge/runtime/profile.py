'''Explicit execution profiles for host and disposable runtimes.'''

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ExecutionProfileName = Literal['host', 'sandbox']


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    '''Capabilities that differ between a user's host and a disposable task.'''

    name: ExecutionProfileName = 'host'
    allow_command_file_writes: bool = False

    @classmethod
    def host(cls) -> ExecutionProfile:
        '''Return the conservative profile for a user's real machine.'''
        return cls(name='host', allow_command_file_writes=False)

    @classmethod
    def sandbox(cls) -> ExecutionProfile:
        '''Return the disposable profile used by isolated benchmark tasks.'''
        return cls(name='sandbox', allow_command_file_writes=True)
