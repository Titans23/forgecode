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
VerificationQuality = Literal[
    'structural',
    'behavior',
    'negative',
    'unknown',
]

_SHELL_CHAIN = re.compile(r'(?:;|\|\||(?<!\|)\|(?!\|))')

_UNRESOLVED_COMPLETION_SUMMARY = re.compile(
    r'(?:\b(?:not|never)\s+(?:fully\s+)?(?:verified|complete|fixed|satisfied)\b|'
    r'\b(?:still|unresolved)\b|'
    r'\b(?:remain(?:s|ing)?)\s+(?:unresolved|unfixed|a\s+defect|a\s+warning|'
    r'a\s+failure|an?\s+error|an?\s+issue|a\s+problem|a\s+gap)\b|'
    r'\bnot\s+(?:yet\s+)?(?:working|passing|resolved)\b|'
    r'\b(?:cannot|can\s*not|unable|couldn\s*not)\s+(?:verify|confirm|complete|'
    r'fix|resolve|satisfy|pass|finish)\b|'
    r'\b(?:warning|error)s?\s+(?:remain|still|persist))',
    re.IGNORECASE,
)


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
    '''Classify evidence strength without trusting shell-chain exit masking.'''
    if _NEGATIVE_VERIFICATION.search(command):
        return 'negative'
    # A compound shell command has a provider- and shell-dependent exit-status
    # contract.  In particular, ``failing-test; echo OK`` exits zero while the
    # substantive command failed.  Keep such evidence visible, but do not let
    # the final exit code claim positive task coverage automatically.
    if _SHELL_CHAIN.search(command):
        return 'unknown'
    return verification_kind(command)


def is_task_verification_command(command: str) -> bool:
    '''Return whether a command exercises the requested task.'''
    return verification_quality(command) == 'behavior'


def is_positive_verification_command(command: str) -> bool:
    '''Return whether a command can positively establish task correctness.'''
    return verification_quality(command) == 'behavior'


def completion_summary_has_unresolved_claims(summary: str) -> bool:
    '''Detect a completed declaration that admits its own unresolved defect.'''
    return bool(_UNRESOLVED_COMPLETION_SUMMARY.search(summary))
