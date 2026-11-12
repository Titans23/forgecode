'''Dependency-free per-execution process output binding, also used by helpers.'''
from contextvars import ContextVar
from collections.abc import Callable

process_output: ContextVar[Callable[[str, bytes], None] | None] = ContextVar('forge_process_output', default=None)
