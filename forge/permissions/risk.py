'''Conservative ToolCall capability and risk classification.'''

from __future__ import annotations

from pathlib import Path
import posixpath
import re
import shlex
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
    r'\b(?:rm|rmdir|del|erase|Remove-Item|git\s+clean)\b'
    r'|\bos\.(?:remove|unlink|rmdir)\s*\('
    r'|\bshutil\.rmtree\s*\('
    r'|\b(?:fs\.)?(?:unlink|rm|rmdir)(?:Sync)?\s*\('
    r'|\.unlink\s*\('
    r'|\.rmdir\s*\(',
    re.IGNORECASE,
)
PRIVILEGE_PATTERN = re.compile(
    r'\b(?:sudo|su|runas|Start-Process\s+[^\n]*-Verb\s+RunAs)\b',
    re.IGNORECASE,
)
DESTRUCTIVE_PATTERN = re.compile(
    r'\bgit\s+(?:checkout|restore|reset|clean)\b',
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
        if (
            tool_call.name == 'remove_directory'
            or (
                tool_call.name == 'apply_patch'
                and _patch_deletes_content(tool_call.arguments.get('patch'))
            )
        ):
            return PermissionRequest(
                tool_call.name,
                'file.delete',
                'high',
                targets,
                'This call immediately deletes the listed files and does not create replacements. Only approve if that exact deletion is intended.',
                _preview(tool_call),
            )
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
    stdin = str(tool_call.arguments.get('stdin', ''))
    process_input = f'{command}\n{stdin}'
    delete_targets, broad_delete = _command_delete_targets(command)
    targets = tuple(dict.fromkeys((*targets, *delete_targets)))
    preview = process_input[:500]
    path_denial = _unsafe_target_reason(targets)
    if path_denial:
        return PermissionRequest(
            tool_call.name, 'file.delete', 'critical', targets,
            path_denial, preview, hard_deny=True,
        )
    if _contains_privilege_command(command) or PRIVILEGE_PATTERN.search(stdin):
        return PermissionRequest(
            tool_call.name,
            'process.privileged',
            'critical',
            targets,
            'Privilege escalation commands are forbidden.',
            preview,
            hard_deny=True,
        )
    if DESTRUCTIVE_PATTERN.search(process_input):
        return PermissionRequest(
            tool_call.name,
            'file.delete',
            'critical',
            targets,
            'The command can discard repository or filesystem state.',
            preview,
            hard_deny=True,
        )
    if broad_delete:
        return PermissionRequest(
            tool_call.name,
            'file.delete',
            'critical',
            targets,
            'The command contains a broad or unresolved recursive deletion target.',
            preview,
            hard_deny=True,
        )
    if DELETE_PATTERN.search(process_input):
        return PermissionRequest(
            tool_call.name,
            'file.delete',
            'high',
            targets,
            'The command deletes files.',
            preview,
        )
    if INSTALL_PATTERN.search(process_input):
        return PermissionRequest(
            tool_call.name,
            'dependency.install',
            'high',
            targets,
            'The command installs or updates dependencies.',
            preview,
        )
    if NETWORK_PATTERN.search(process_input):
        return PermissionRequest(
            tool_call.name,
            'network.access',
            'high',
            targets,
            'The command accesses a network or remote repository.',
            preview,
        )
    return PermissionRequest(
        tool_call.name,
        'process.exec',
        'low',
        targets,
        'Local repository command.',
        preview,
    )


def _contains_privilege_command(command: str) -> bool:
    '''Exempt only simple queries/literal output; unknown execution stays denied.

    Preserve quotes and statement boundaries so interpreter arguments and command
    substitutions cannot acquire the exemption of a harmless neighbouring query.
    Script stdin is deliberately checked separately by the caller.
    '''
    # 只豁免能明确识别的查询和字面输出，未知脚本语义继续保守处理。
    if not PRIVILEGE_PATTERN.search(command):
        return False
    # Literal/query output can become executable input downstream (printf ...
    # | sh, command -v sudo | xargs). Do not exempt pipeline programs.
    if re.search(r'(?<!\|)\|(?!\|)', command) or any(char in command for char in '$`'):
        return True
    try:
        lexer = shlex.shlex(command, posix=False, punctuation_chars=';&|\n')
        lexer.whitespace = ' \t\r'
        lexer.whitespace_split = True
        statements: list[list[str]] = [[]]
        for token in lexer:
            if token and all(char in ';&|\n' for char in token):
                statements.append([])
            else:
                statements[-1].append(token)
    except ValueError:
        return True
    for words in statements:
        text = ' '.join(words)
        if not PRIVILEGE_PATTERN.search(text):
            continue
        # Unknown expansion/redirection may execute code or change semantics.
        if any(char in text for char in '$`<>\\'):
            return True
        query = (len(words) >= 3 and words[:2] in (['command', '-v'], ['command', '-V']))
        literal_output = bool(words and words[0] in {'echo', 'printf'})
        if not (query or literal_output):
            return True
    return False


def _command_delete_targets(command: str) -> tuple[tuple[str, ...], bool]:
    '''Extract explicit rm targets; broad/unresolved recursion stays denied.'''
    targets: list[str] = []
    broad = False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|\n')
        # 换行必须保留为语句边界，否则下一条命令会被误当成 rm 的目标。
        lexer.whitespace = ' \t\r'
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return (), bool(re.search(r'\brm\b', command, re.IGNORECASE))
    if tokens and Path(tokens[0]).name.casefold() in {'sh', 'bash', 'dash', 'zsh', 'cmd', 'cmd.exe', 'powershell', 'pwsh'}:
        for offset, value in enumerate(tokens[1:], start=1):
            if value.casefold() in {'-c', '/c', '-command'} and offset + 1 < len(tokens):
                nested_targets, nested_broad = _command_delete_targets(' '.join(tokens[offset + 1:]))
                targets.extend(nested_targets)
                broad = broad or nested_broad
                break
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if Path(token).name.casefold() != 'rm':
            index += 1
            continue
        index += 1
        recursive = False
        current: list[str] = []
        while index < len(tokens) and not (
            tokens[index] and all(char in ';&|\n' for char in tokens[index])
        ):
            value = tokens[index]
            if value.startswith('-'):
                recursive = recursive or 'r' in value.casefold()
            else:
                current.append(value.replace('\\', '/'))
            index += 1
        targets.extend(current)
        if recursive:
            if not current or any(
                posixpath.normpath(value) in {'/', '.', '..'}
                or any(marker in value for marker in ('*', '?', '$', '%', '~'))
                for value in current
            ):
                broad = True
    return tuple(dict.fromkeys(targets)), broad


def _patch_deletes_content(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return bool(
        re.search(r'^\*\*\* Delete File:', value, re.MULTILINE)
        or re.search(r'^\+\+\+\s+/dev/null(?:\s|$)', value, re.MULTILINE)
        or re.search(r'^deleted file mode\s+', value, re.MULTILINE)
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
        if '.forge' in folded:
            return 'ForgeCode control-plane paths are protected.'
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
