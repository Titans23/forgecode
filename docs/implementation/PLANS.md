# ForgeCode 开发约定

唯一日常开发目录：D:\projects\forgecode，分支main。读取AGENTS、architecture、progress、handoff及当前任务卡；核对真实Git状态和F00接口，按backlog依赖推进，不创建重复开发目录。

CLI、SDK与评测沿用原Harness。修改前明确实际调用，补充行为测试并运行相关检查与回归。离线测试、原生平台、安装wheel与真实模型分别记录，不伪造或复用不对应源码的报告。

开发准备：uv sync --locked --all-extras --group dev；npm ci --ignore-scripts；uv run --no-sync python scripts/build_bridge.py。验证使用scripts/impl.py verify --suite contracts|quality|regression|terminal-native。

完成时更新progress、任务卡、证据和handoff。失败、警告与资源缺失如实记录。付费模型和管理员系统设置仍需明确授权。

.venv、node_modules、build及forge/_sandbox可重建；.local仅存本轮报告与缓存，均不提交。凭据不进Git也不输出。用户已授权丢弃清理前的旧本地改动和会话，无需备份。
