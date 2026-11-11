"""DSH-style named permissions over sandbox scope and approval policy."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermissionPreset:
    name: str
    sandbox: str
    approval: str
    label: str
    description: str


PERMISSION_PRESETS = (
    PermissionPreset('workspace-write', 'workspace-write', 'ask', '工作区',
        '工作区内操作；扩大权限时逐次询问，不改变会话模式。'),
    PermissionPreset('read-only', 'read-only', 'ask', '只读',
        '默认不允许修改；需要写入或宿主执行时逐次询问。'),
    PermissionPreset('danger-full-access', 'full-access', 'never', '完全访问',
        '本会话使用宿主权限，不受工作区沙盒限制，不询问扩大权限。'),
)


def permission_preset(name: str) -> PermissionPreset:
    name = 'danger-full-access' if name == 'full-access' else name
    for preset in PERMISSION_PRESETS:
        if preset.name == name:
            return preset
    raise ValueError('Choose workspace-write, read-only or danger-full-access.')


def current_permission(manager) -> PermissionPreset | None:
    return next((preset for preset in PERMISSION_PRESETS
        if (preset.sandbox, preset.approval) == (manager.execution_mode, manager.mode)), None)


def describe_permissions(manager) -> str:
    preset = current_permission(manager)
    lines = ([f'权限：{preset.label} ({preset.name})', preset.description] if preset else
             ['权限：自定义组合', f'沙盒：{manager.execution_mode or "未配置"}；审批策略：{manager.mode}'])
    counts = (len(manager.user_rules), len(manager.project_rules), len(manager.session_rules))
    if any(counts):
        lines.append(f'附加规则：用户 {counts[0]}，项目 {counts[1]}，会话 {counts[2]}。')
    return '\n'.join(lines)
