# ForgeCode

ForgeCode 是一个终端 Agent Harness：复用既有 Python 代理循环，负责模型与工具调用、权限、上下文、预算、验证、会话恢复和进程清理。

当前 `main` 提供持续对话终端和真实轻量沙盒，后续开发以此版本为基线。启动 Profile 选择入口组合，Python API 为后续客户端和 SDK 提供结构化事件、结果和取消。评测与后训练接口按需使用。

当前展示主线是可运行、可验证的 Harness：模型调用 → 授权工具执行 → 独立验证 → Journal 恢复，并用冻结任务报告完成率、失败类型、耗时和 token。现有能力、证据边界及下一步见 [T01](docs/implementation/tasks/T01.md) 和 [当前进展](docs/implementation/progress.json)。

Windows 底层写限制复用 DSH，Linux 复用 bubblewrap；ForgeCode 层负责策略接入、执行所有权、验证与生命周期。当前 OS 读取、网络和硬资源隔离尚不完整，能力为 partial，不能表述为自研完整 OS 沙盒。完整强隔离不再是当前求职展示的前置条件；日常运行保持无 Docker 依赖。

目录职责与开发入口见 [当前文档](docs/README.md)；虚拟环境、依赖、构建和测试产物不属于源码。

## 安装与启动

当前版本通过 `forge` 启动终端持续对话。

### 1. 拉取源码，一条命令构建并安装

先安装 Git 和 Node.js 22.13+（包含 npm），然后执行：

```sh
git clone --branch main https://github.com/Titans23/forgecode.git
cd forgecode
npm run setup
```

`npm run setup` 自动准备 uv 和 Python，按锁文件安装构建依赖、编译沙盒、生成 Python wheel 和 npm 包，再安装全局 `forge`。运行时所需的 Git、ripgrep 和 Windows PowerShell 7 也会自动准备，下载校验 SHA-256；直接复用已有 Node。无需手动执行 `uv sync`、`npm ci` 或构建脚本，安装过程不调用模型。构建日志位于 `dist/package-build.log`。Windows x64/NTFS 启动不需要 WSL 或 Docker。

Windows 和 Ubuntu x64 使用同一安装命令，在本机构建对应平台的包。安装与原生沙盒已在 Windows 10 和 WSL2 Ubuntu 26.04 上验证；Ubuntu 22.04 / 24.04 的原生验收仍待完成。Linux 还需要系统 Git、bubblewrap 和可用的 user namespace。

以后更新时，在这个源码目录执行：

```sh
git pull --ff-only
npm run setup
```

这会重新构建并更新 `forge`，保留用户模型配置与会话数据。当前包尚未发布到 npm registry，本流程会安装本地构建产物。源码开发方式见 [终端使用指南](docs/install/terminal-quickstart.md)。

### 2. 配置模型连接

安装会自动创建用户配置 `~/.forge/.env`（Windows 为 `%USERPROFILE%\.forge\.env`）；已有文件保持原样。首次使用运行：

```sh
forge config setup
```

按提示选择协议、模型 ID、服务地址并输入 API Key，密钥输入隐藏。支持 `anthropic`、`openai_responses` 和 `deepseek`。配置一次后可在多个项目使用；创建空文件不会自动获得 API Key，也不会调用模型。

已有 `.env` 时可只导入其中的模型配置；该操作更新用户级模型设置，不复制飞书等其他凭据：

```sh
forge config setup --from-env .env
forge config set model your-model-id
forge config set base-url https://your-provider-endpoint
forge config set api-key
forge config path
```

`set api-key` 使用隐藏输入，不接受命令行密钥参数。更换服务协议请重新运行 `setup`。`forge config init` 可单独创建配置文件，重复运行不会覆盖已有内容。

也可以编辑用户 `.env` 或目标项目 `.env`；兼容 `ANTHROPIC_API_KEY`、`MODEL_ID` 等原有字段。优先级为：进程环境变量 > 项目 `.env` > 用户配置。用户文件中的值按原样读取，升级不会覆盖配置，密钥不要提交到 Git。

### 3. 检查配置并启动

在目标代码项目目录执行：

```powershell
cd D:\path\to\your-project
forge config
forge
```

将示例路径替换为实际项目目录。`forge config` 检查配置，不回显密钥；`forge` 启动持续对话。ForgeCode 安装目录与目标项目分开，避免安装目录保护导致沙盒初始化被拒绝。

如果在 `D:\learn_project\forgecode` 安装仓库内直接执行 `forge`，可能出现 `POLICY_DENIED: Symlink or reparse link paths are unsupported`。`npm ci` 为本仓库的 workspace 包创建了 `node_modules/@forgecode` 目录链接，当前沙盒拒绝符号链接和 Windows reparse point。请切换到另一个目标项目目录后启动；删除这些依赖链接也不能解决安装目录与工作区重叠的问题。

遇到 `SANDBOX_UNAVAILABLE` 时，先执行 `forge sandbox --json` 查看 `runtime` 的检查结果。npm 安装会自动提供其管理的工具；Ubuntu 需检查系统 bubblewrap 和 user namespace，源码安装需检查构建文件。只读诊断的整体 `status` 在尚未验证工作区时可为 `blocked`，实际工作区边界在启动会话时验证。

Windows 目标项目还需要当前用户能修改目录权限和所有者（“完全控制”包含这些权限）。只有“修改”权限的目录可能在原生边界检查时被拒绝；安装环境完成不代表任意目标目录都已授权。

恢复已有会话：

```powershell
forge --continue
forge --resume SESSION_ID
```

`--continue` 恢复当前项目的上次会话，`--resume` 指定会话 ID；清除会话数据后需要重新开始。默认使用 workspace-write 沙盒；初始化失败拒绝任务。其他模型连接、明确的权限范围、完整访问、审批、wheel 安装及使用限制见 [终端使用指南](docs/install/terminal-quickstart.md)。

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

本地 `evals.runner` 与 CLI/API 共用 HarnessSession、模型配置、预算、权限和资源关闭，Agent 与独立判分默认走原生 workspace-write，不可用时不会回退。`python -m evals.runner --list` 只列任务、不调用模型；实际运行读取用户模型配置。案例可冻结 `max_model_calls`、`max_tool_calls`、`max_turn_seconds` 和 `intent_routing`。每个 run_id 保留结果及 Journal/trajectory，只有 `passed=true` 算完整成功，latest 文件不重复计数。现有三个自建夹具用于工程回归，不是实际开源仓库 Coding Agent 能力分数。

```text
uv sync --locked --all-extras --group dev
uv run --no-sync python scripts/impl.py verify --suite regression
uv run --no-sync python scripts/impl.py verify --suite terminal-native
npm run test:unit
```

真实仓库预检使用 [六任务清单](experiments/native-swe-lite.json)：Flask、pytest、Sphinx 各两个固定提交，来自公开 SWE-bench Lite。Windows 原生适配不等于官方 SWE-bench 分数，也不是未见任务；选择、排除理由、依赖版本和完整测试映射均冻结。以下命令只获取官方数据并验证原始失败、参考修复及全部规定回归项，不调用模型，不要求 Docker：

```text
uv run --no-sync --with pyarrow==19.0.1 python scripts/prepare_native_swe.py preflight
```

解释器、原始补丁和预检日志保存在 `.local/implementation/real-repo-preflight`。可用 `--case <清单ID>` 选择预检项。Agent 通过共享 HarnessSession 读取问题和全新的 base 源码；任务依赖在同一所属临时根的独立目录中。Agent 关闭后，判分器在另一份源码及依赖中重放候选修改、施加冻结测试补丁。全部规定参数必须执行，缺项、跳过、xfail、收集或清理失败都不通过。

有界模型实验使用 [冻结计划](experiments/native-swe-lite-plan.json)，只读取项目 `.env` 的模型配置。先无调用校验，再在干净、已验证提交上显式执行：

```text
uv run --no-sync python -m evals.native_swe_campaign
uv run --no-sync --with pyarrow==19.0.1 python -m evals.native_swe_campaign --execute
```

计划为六任务各两次、串行、每槽最多 18 次请求、总上限 216，零自动重试。限流、超时、流中断或清理未确认立即停止。恢复须显式 `--execute --resume`，仅执行未开始的槽；中断且未完成核对的槽不会重放。结果默认在 `.local/implementation/native-swe-live`，所有尝试计入完成率，成功任务费用单列。可用 `--replay-outcome <已保存结果.json> --output <新目录>` 在全新判分环境复核候选修改，不调用模型、不改变原失败记录。局部原生边界不保证宿主读取/网络保密，Journal 访问审计有此限制。

确定性测试、原生平台、安装 wheel 与真实模型实验分别验证。运行报告保存在已忽略的 `.local/implementation`，不提交到文档目录。开发约定见 [AGENTS.md](AGENTS.md)，当前待办见 [progress.json](docs/implementation/progress.json)。真实模型预算与管理员安装需要另行授权。
