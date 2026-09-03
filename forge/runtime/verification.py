'''Shared classification for verification commands and their evidence quality.'''

from __future__ import annotations

import re
from typing import Literal


_NON_TASK_VERIFICATION = re.compile(
    r'^(?:git\s+(?:status|diff(?:\s+--check)?|log)\b|'
    r'python(?:\d+(?:\.\d+)?)?\s+-m\s+(?:py_compile|compileall)\b|'
    r'(?:node(?:\.exe)?\s+(?:--check|-c)|'
    r'(?:bash|sh|zsh)(?:\.exe)?\s+-n|'
    r'ruby(?:\.exe)?\s+-c|'
    r'perl(?:\.exe)?\s+-c|'
    r'php(?:\.exe)?\s+-l)\b|'
    r'(?:test\s+-(?:e|f|d)|find|ls|dir|cat|type|head|tail|wc|stat)\b|'
    r'(?:echo|printf|pwd|true|:)\b)',
    re.IGNORECASE,
)

_NEGATIVE_VERIFICATION = re.compile(
    r'(?:\bset\s*\+e\b|'
    r'\b(?:status|rc|code|exit_code)\s*=\s*\$\?\b|'
    r'\btest\s+[^;&|\n]*\s+(?:-ne|!=|not\s+equal)\s+0\b|'
    r'\bassert\s+[^;&|\n]*(?:!=|is\s+not)\s+0\b|'
    r'(?:^|[;&|])\s*true\s*$)',
    re.IGNORECASE,
)

VerificationKind = Literal['structural', 'behavior']
VerificationQuality = Literal['structural', 'behavior', 'negative']


def verification_kind(command: str) -> VerificationKind:
    '''Classify whether a command exercises behavior or only structure.'''
    segments = re.split(r'\s*(?:&&|\|\||[;|])\s*', command.strip())
    return (
        'behavior'
        if any(
            segment and not _NON_TASK_VERIFICATION.match(segment.strip())
            for segment in segments
        )
        else 'structural'
    )


def verification_quality(command: str) -> VerificationQuality:
    '''Classify evidence strength, including intentionally inverted checks.'''
    if _NEGATIVE_VERIFICATION.search(command):
        return 'negative'
    return verification_kind(command)


def is_task_verification_command(command: str) -> bool:
    '''Return whether a command exercises the requested task.'''
    return verification_kind(command) == 'behavior'


def is_positive_verification_command(command: str) -> bool:
    '''Return whether a command can positively establish task correctness.'''
    return verification_quality(command) == 'behavior'
