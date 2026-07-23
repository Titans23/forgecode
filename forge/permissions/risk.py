'''Conservative ToolCall capability and risk classification.'''

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from forge.permissions.policy import PermissionRequest
from forge.runtime.state import ToolCall


NETWORK_PATTERN = re.compile(
    r'\b(?:curl|wget|Invoke-WebRequest|iwr|ssh|scp|git\s+(?:clone|fetch|pull|push))\b',
    re.IGNORECASE,
)
INSTALL_PATTERN = re.compile(
    r'\b(?:pip|pip3|uv|npm|pnpm|yarn|cargo|gem|apt|apt-get|brew|winget|choco)\s+'
    r'(?:install|add|sync|update)\b',
    re.IGNORECASE,
)
DELETE_PATTERN = re.compile(
    r'\b(?:rm|rmdir|del|erase|Remove-Item|git\s+clean)\b',
    re.IGNORECASE,
)
PRIVILEGE_PATTERN = re.compile(
    r'\b(?:sudo|su|runas|Start-Process\s+[^\n]*-Verb\s+RunAs)\b',
    re.IGNORECASE,
)
DESTRUCTIVE_PATTERN = re.compile(
    r'\bgit\s+(?:checkout|restore|reset|clean)\b|\brm\s+-[^\n]*r[^\n]*f\b',
    re.IGNORECASE,
)
PATCH_PATH_PATTERN = re.compile(
    r'^\*\*\* (?:Update|Add|Delete) File: (.+)$', re.MULTILINE
)
SENSITIVE_NAMES = {
    '.env',
    'id_rsa',
    'id_ed25519',
    'credentials',
    'credentials.json',
    'known_hosts',
}


def classify_tool_call(tool_call: ToolCall, effect: str | None) -> PermissionRequest:
    '''Convert one final ToolCall into a normalized permission request.'''
    arguments = tool_call.arguments
    targets = _targets(arguments)
    path_denial = _unsafe_target_reason(targets)
    if path_denial:
        return PermissionRequest(
            tool_call.name,
            _capability(effect),
            'critical',
            targets,
            path_denial,
            hard_deny=True,
        )

    if effect == 'process':
        return _classify_process(tool_call, targets)
    if effect == 'workspace_write':
        return PermissionRequest(
            tool_call.name,
            'file.write',
            'low',
            targets,
            'The tool can modify repository files.',
            _preview(tool_call),
        )
    return PermissionRequest(
        tool_call.name,
        'file.read' if targets else 'repository.read',
        'low',
        targets,
        'Read-only repository operation.',
        _preview(tool_call),
    )


def _classify_process(
    tool_call: ToolCall,
    targets: tuple[str, ...],
) -> PermissionRequest:
    command = str(tool_call.arguments.get('command', ''))
    if PRIVILEGE_PATTERN.search(command):
        return PermissionRequest(
            tool_call.name,
            'process.privileged',
            'critical',
            targets,
            'Privilege escalation commands are forbidden.',
            command[:500],
            hard_deny=True,
        )
    if DESTRUCTIVE_PATTERN.search(command):
        return PermissionRequest(
            tool_call.name,
            'file.delete',
            'critical',
            targets,
            'The command can discard repository or filesystem state.',
            command[:500],
            hard_deny=True,
        )
    if DELETE_PATTERN.search(command):
        return PermissionRequest(
            tool_call.name,
            'file.delete',
            'high',
            targets,
            'The command deletes files.',
            command[:500],
        )
    if INSTALL_PATTERN.search(command):
        return PermissionRequest(
            tool_call.name,
            'dependency.install',
            'high',
            targets,
            'The command installs or updates dependencies.',
            command[:500],
        )
    if NETWORK_PATTERN.search(command):
        return PermissionRequest(
            tool_call.name,
            'network.access',
            'high',
            targets,
            'The command accesses a network or remote repository.',
            command[:500],
        )
    return PermissionRequest(
        tool_call.name,
        'process.exec',
        'low',
        targets,
        'Local repository command.',
        command[:500],
    )


def _capability(effect: str | None) -> str:
    if effect == 'workspace_write':
        return 'file.write'
    if effect == 'process':
        return 'process.exec'
    return 'file.read'


def _targets(arguments: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ('path', 'cwd'):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            values.append(value.replace('\\', '/'))
    patch = arguments.get('patch')
    if isinstance(patch, str):
        values.extend(
            match.strip().replace('\\', '/')
            for match in PATCH_PATH_PATTERN.findall(patch)
        )
    return tuple(dict.fromkeys(values))


def _unsafe_target_reason(targets: tuple[str, ...]) -> str:
    for value in targets:
        path = Path(value)
        folded = {part.casefold() for part in path.parts}
        if path.is_absolute() or '..' in path.parts:
            return 'Repository path escape attempts are forbidden.'
        if '.git' in folded or '.forge' in folded:
            return 'ForgeCode and Git control-plane paths are protected.'
        if any(
            name in SENSITIVE_NAMES
            or name.startswith('.env.') and name != '.env.example'
            for name in folded
        ):
            return 'Credential and secret files are protected.'
    return ''


def _preview(tool_call: ToolCall) -> str:
    safe = {
        key: value
        for key, value in tool_call.arguments.items()
        if key not in {'content', 'patch', 'stdin', 'new_text', 'old_text'}
    }
    return str(safe)[:500]
