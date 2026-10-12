# ForgeCode

ForgeCode 是一个终端 Agent Harness：复用既有 Python 代理循环，负责模型与工具调用、权限、上下文、预算、验证、会话恢复和进程清理。

当前 `main` 提供持续对话终端和真实轻量沙盒，后续开发以此版本为基线。启动 Profile 选择入口组合，Python API 为后续客户端和 SDK 提供结构化事件、结果和取消。评测与后训练接口按需使用。

日常开发目录统一为 `D:\projects\forgecode`。目录职责与开发入口见 [当前文档](docs/README.md)；虚拟环境、依赖、构建和测试产物不属于源码。

## 安装

准备 Python 3.12+、Node 22+、Git、ripgrep；Windows 还需要 PowerShell 7/NTFS，Linux 需要 bubblewrap 与可用的非特权 user namespace。

```powershell
git clone --branch main https://github.com/Titans23/forgecode.git forgecode-terminal
cd forgecode-terminal
uv sync --locked
npm ci --ignore-scripts
uv run --no-sync python scripts/build_bridge.py
uv tool install --editable .
```

在另一个项目目录配置模型连接，再运行：

```text
forge config
forge
forge --continue
forge --resume SESSION_ID
```

默认使用 workspace-write 沙盒；初始化失败拒绝任务。明确的权限范围、完整访问、审批、wheel 安装及使用限制见 [终端使用指南](docs/install/terminal-quickstart.md)。

## 入口组合

| Profile | Bundle | 入口 |
|---|---|---|
| terminal（默认） | core + terminal | 终端持续对话 |
| evaluation | core + evaluation | 既有评测目录和 runner |
| Python API | core | 客户端、SDK 与外部编排直接调用 |

```text
forge --show-profile
forge --profile evaluation list --json
forge --sandbox read-only
forge --sandbox workspace-write --write-root ./src
forge --sandbox full-access --approval supervised
```

`forge.api.HarnessSession` 提供 `open/stream/run/cancel/close`，沿用原 `ConversationEvent` 和 `TurnResult`；`event_dict` 输出版本化 JSON。会话事实来自 Journal，活动执行事实来自 Conversation/TurnState。核心不依赖终端文本解析。

默认依赖只加载终端 Harness 和沙盒。Harbor 使用 `forge-code[evaluation]`，飞书兼容命令使用 `forge-code[channels]`。当前版本移除了 Electron、React、Vite 和运行中的 Web 客户端。

## 评测、后训练与验证

保留 `evals.runner`、`benchmark` 的官方协议适配、RunSpec/ResultBundle、独立 grader 与可复现信息。后训练复用 `extensions.qwen_tool_distillation` 的模型/工具轨迹、rollout、反馈与奖励、版本化 SFT/DPO 导出及数据检查；训练框架只由显式功能加载。

```text
uv sync --locked --all-extras --group dev
uv run --no-sync python scripts/impl.py verify --suite regression
uv run --no-sync python scripts/impl.py verify --suite terminal-native
npm run test:unit
```

确定性测试、原生平台、安装 wheel 与真实模型实验分别记录。当前范围及证据见 [T01](docs/implementation/tasks/T01.md)、[progress](docs/implementation/progress.json) 和 [handoff](docs/implementation/handoff.md)。终端版本已按用户要求快进更新到 main；真实模型预算与管理员安装需要另行授权。
