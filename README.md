# ForgeCode

ForgeCode 是一个 Python Agent Harness：复用同一代理循环，负责模型与工具调用、权限、上下文、预算、验证、会话恢复和进程清理。首版扩展流程连接自然语言插件开发、独立验收、模型/配置对照和可复验交付；当前验证状态见 [progress.json](docs/implementation/progress.json)。

纯 SDK 使用 `SessionOptions(agent=AgentDefinition.core(), data_root=...)`，可在没有工作区和 Node 的环境运行。按需安装 `forge-code[anthropic]` 或 `forge-code[openai]`；完整终端环境为 `forge-code[terminal]`。源码开发继续使用锁文件及全部 extras。

`forge plugin init/develop/check`、`forge agent check/export/import` 的用法见 [定制指南](docs/install/terminal-quickstart.md)；
`forge eval plan/run/compare` 与 pi 确定性对照见 [评测说明](benchmark/README.md)。插件必须显式信任/启用；检查不默认调用付费模型，
也不认证恶意 Python 插件安全。pi 已有扩展与模型能力，此处差异是默认集成的交付流程，尚无真实模型或人工效率优势结论。

ForgeCode 自有代码采用 [MIT](LICENSE)；[第三方声明](THIRD_PARTY_NOTICES.md) 保留依赖、参考项目和基准的原有权利。

当前 `main` 提供持续对话终端和真实轻量沙盒，后续开发以此版本为基线。启动 Profile 选择入口组合，Python API 为后续客户端和 SDK 提供结构化事件、结果和取消。评测与后训练接口按需使用。

当前展示主线是可运行、可验证的 Harness：模型调用 → 授权工具执行 → 验证门禁 → Journal 状态重建；评测另用独立 grader 判分，并用冻结任务报告完成率、失败类型、耗时和 token。共享预算、验证证据与副作用生命周期的调用链见 [当前架构](docs/implementation/architecture.md#4-核心调用链与证明边界)，现有证据及范围见 [T01](docs/implementation/tasks/T01.md) 和 [当前进展](docs/implementation/progress.json)。

Windows/Linux 底层写限制及子进程范围复用 DSH 发布的 sandbox-local/subprocess-local；Linux 由 DSH 选择 bubblewrap 或 Landlock。ForgeCode 负责工具协议、会话、预算和验证。当前 OS 读取、网络和硬资源隔离尚不完整，能力为 partial，不能表述为自研完整 OS 沙盒；日常运行无 Docker 依赖。

目录职责与开发入口见 [当前文档](docs/README.md)；虚拟环境、依赖、构建和测试产物不属于源码。

## 安装与启动

当前版本通过 `forge` 启动终端持续对话。

输入后立即显示动态等待状态和用时；模型提供的思考默认折叠为一行，工具调用显示紧凑摘要。运行中按 `Ctrl+O` 展开/收起预览，完成后按 `Ctrl+O` 或输入 `/details` 查看最近一轮的完整思考、工具参数和输出，支持滚动与搜索。`/resume` 会标记当前会话，包括尚未提交首条消息的新会话。

权限统一从 `/permission` 选择：默认“工作区”，需要扩大权限时逐次确认；“只读”默认禁止修改；“完全访问”在本会话使用宿主权限。一次批准不会切换会话模式。Windows 原生沙盒仍受目录权限约束，失败不会自动降级为宿主执行。

### 1. 拉取源码，一条命令构建并安装

先安装 Git 和 Node.js 22.13+（包含 npm），然后执行：

```sh
git clone --branch main https://github.com/Titans23/forgecode.git
cd forgecode
npm run setup
```

`npm run setup` 自动准备 uv 和 Python，按锁文件安装构建依赖、编译沙盒、生成 Python wheel 和 npm 包，再安装全局 `forge`。运行时所需的 Git、ripgrep 和 Windows PowerShell 7 也会自动准备，下载校验 SHA-256；直接复用已有 Node。无需手动执行 `uv sync`、`npm ci` 或构建脚本，安装过程不调用模型。构建日志位于 `dist/package-build.log`。Windows x64/NTFS 启动不需要 WSL 或 Docker。

Windows 和 Ubuntu x64 使用同一安装命令，在本机构建对应平台的包。历史安装与原生验收覆盖 Windows 10 和 WSL2 Ubuntu 26.04；当前 DSH 会话适配的原生验证范围见 progress.json。Linux 需具备可用的 bubblewrap/user namespace 或 Landlock；macOS 尚未接入 Forge 的控制进程所有权层。

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

将示例路径替换为实际项目目录。`forge config` 检查配置，不回显密钥；在目标项目目录运行 `forge` 启动持续对话。

新建普通 coding 会话默认使用 `danger-full-access`＋`never`：文件和命令使用当前用户的宿主权限，不初始化原生沙盒或修改项目 ACL。普通回合直接请求主模型，不做独立意图分类、全盘基线扫描或强制完成验收；仅记录实际工具目标的变化。需要隔离时使用 `forge --sandbox workspace-write` 或 `/permission workspace-write`，文件写入限于工作区和平台临时目录，命令按需使用 DSH 原生边界。显式写入范围和自定义沙盒提供方仍采用其受限模式。

遇到 `SANDBOX_UNAVAILABLE` 时，先执行 `forge sandbox --json` 查看 `runtime` 的检查结果。源码安装需检查构建文件，Linux 由 DSH 检测可用 runner。只读诊断可为 `blocked`；成功打开 CLI 不代表 OS 隔离已经验证。缺少 Git、rg 或 PowerShell 只影响需要它们的工具，不阻止其他命令。

选择 Windows 原生沙盒时，目标目录仍须满足 DSH 的授权条件，包括设置低完整性标签所需的 WRITE_OWNER。不足时命令不会启动；命令、verify、Git 和文件修改工具可用 `sandbox_permissions` 与 `justification` 请求单次更宽权限。受限模式默认 `ask`，完全访问默认 `never`，后者拒绝需要审批的请求；显式禁止规则仍有效。一次批准不会改变会话模式，也不会自动修复项目 ACL。

会话空闲时可用 `/sandbox read-only|workspace-write|danger-full-access` 切换沙盒，`/approval ask|never` 切换审批；`/permission workspace-write` 和 `/permission danger-full-access` 分别选择 workspace-write＋ask、full-access＋never。选择保存在当前会话中，恢复时重放；分支继承，`/clear` 回到启动默认值，不改全局配置。SDK 对应 `await session.set_sandbox(mode)`。

恢复已有会话：

```powershell
forge --continue
forge --resume SESSION_ID
```

`--continue` 恢复当前项目的上次会话，`--resume` 指定会话 ID；恢复保留该会话保存的权限，不把旧会话自动改为完全访问。其他模型连接、权限范围、审批、wheel 安装及使用限制见 [终端使用指南](docs/install/terminal-quickstart.md)。

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

npm 终端安装包含内置原生沙盒提供方，默认 coding Agent 使用它；通用 core Agent 不加载沙盒，其他组合可显式设置 `sandbox_provider: null` 使用宿主执行。可信插件可注册自己的提供方。Harbor 使用 `forge-code[evaluation]`，飞书兼容命令使用 `forge-code[channels]`。当前版本移除了 Electron、React、Vite 和运行中的 Web 客户端。

源码开发环境准备好后，可用公开示例演示真实原生工具、验证过期、同回合预算内修复和 Journal 只读重建，无需模型密钥：

```text
uv run --no-sync python examples/harness_demo.py --output .local/implementation/api-demo-001
```

输出目录必须是新目录。预期为 `completed`、共享请求计数 6/8、`cleanup.state=clean`、`fixture_retained=false`，并生成 `events.jsonl` 和 `summary.json`。模型响应和 usage 是明确的脚本夹具，provider 调用为零，不代表真实模型完成率。具体演示、历史候选 CLI 复核及恢复边界见 [5 分钟演示](docs/install/terminal-quickstart.md#5-分钟-harness-演示)。

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

当前证据分开封存：原始 12 槽完整完成 0/12，请求 129 次；后续两个已见任务验证 0/2，请求 34 次；缓存修复与解释器指引后的同题验证仍为 0/2，请求 28 次。最新 Sphinx 候选通过原 6 项及补充 18 项公开回归，但后续命令令验证过期，默认未启用预算内修复反馈，因此 Agent 仍 partial。之前候选的 3 项公开回归和缓存超时也保留记录。没有成功任务时，成功耗时/token 统计为空。各组版本、分母、尝试成本及证据摘要见 [progress.json](docs/implementation/progress.json)，不能据此宣称稳定提速或未见任务泛化。

确定性测试、原生平台、安装 wheel 与真实模型实验分别验证。运行报告保存在已忽略的 `.local/implementation`，不提交到文档目录。开发约定见 [AGENTS.md](AGENTS.md)，当前待办见 [progress.json](docs/implementation/progress.json)。真实模型预算与管理员安装需要另行授权。
