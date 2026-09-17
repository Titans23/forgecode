'''Shared classification for verification commands and their evidence quality.'''

from __future__ import annotations

import re
import shlex
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
    # A quoted Python heredoc is one program; its source is not shell syntax.
    # Restrict recognition to literal delimiters and a final closing line.
    lines = command.strip().splitlines()
    if len(lines) >= 3:
        header = re.fullmatch(r"(python(?:3(?:\.\d+)?)? -)\s*<<\s*(['\"])([A-Za-z_][A-Za-z_0-9]*)\2", lines[0])
        if header and lines[-1] == header[3] and header[3] not in lines[1:-1]:
            return 'behavior'
    if _NEGATIVE_VERIFICATION.search(command):
        return 'negative'
    # A compound shell command has a provider- and shell-dependent exit-status
    # contract.  In particular, ``failing-test; echo OK`` exits zero while the
    # substantive command failed.  Keep such evidence visible, but do not let
    # the final exit code claim positive task coverage automatically.
    if has_unsafe_shell_chain(command):
        return 'unknown'
    return verification_kind(command)


def has_unsafe_shell_chain(command: str) -> bool:
    '''Detect exit-masking operators outside quoted program source.'''
    # Quoting hides program punctuation, but not the semantics of a shell
    # interpreter's command argument: sh -c 'false; true' still masks failure.
    try:
        words = shlex.split(command)
    except ValueError:
        return True
    if words:
        executable = words[0].replace('\\', '/').rsplit('/', 1)[-1].lower()
        if executable in {'sh', 'bash', 'zsh', 'dash', 'cmd', 'cmd.exe', 'powershell', 'powershell.exe', 'pwsh', 'pwsh.exe'}:
            for index, word in enumerate(words[1:], start=1):
                if word.lower() in {'-c', '/c', '-command'} and index + 1 < len(words):
                    if has_unsafe_shell_chain(' '.join(words[index + 1:])):
                        return True
                    break
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == '\\' and quote != "'":
            escaped = True
            index += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {'\"', "'"}:
            quote = char
            index += 1
            continue
        pair = command[index:index + 2]
        if char in {';', '\n', '\r', '|'} or (char == '&' and pair != '&&'):
            return True
        index += 2 if pair == '&&' else 1
    return False


def is_task_verification_command(command: str) -> bool:
    '''Return whether a command exercises the requested task.'''
    return verification_quality(command) == 'behavior'


def is_positive_verification_command(command: str) -> bool:
    '''Return whether a command can positively establish task correctness.'''
    return verification_quality(command) == 'behavior'


def completion_summary_has_unresolved_claims(summary: str) -> bool:
    '''Deprecated compatibility helper: prose is never a completion gate.'''
    return False
