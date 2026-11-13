# ForgeCode 1.0.0 使用指南

## 安装与升级

需要 Python 3.12+；源码一键安装会准备 Python 和 uv，但要求已安装 Git、Node.js 22.13+。Windows x64/NTFS 与 Linux x64 在本机构建：

```sh
git clone --branch v1.0.0 https://github.com/Titans23/forgecode.git
cd forgecode
npm run setup
forge --version
```

`setup` 构建 Python wheel、Node 启动器和原生执行资产，再安装全局 `forge`。已有用户配置和会话保留。构建日志在 `dist/package-build.log`；Windows 日常运行不需要 Docker/WSL。Linux 受限命令还取决于可用的 DSH runner；macOS 尚未接入 Forge 的原生控制进程所有权层。

升级标签：`git fetch --tags`、`git checkout <新标签>`、`npm run setup`。跟踪开发版可在 `main` 使用 `git pull --ff-only`。已构建的本平台安装包可直接安装：

```sh
npm install -g ./titans23-forgecode-1.0.0.tgz
```

Python SDK 可在源码目录使用 `uv venv` 和 `uv pip install -e '.[anthropic]'`。完整终端安装为 `uv tool install '.[terminal]'`；wheel 安装需带相应 extras，例如 `uv tool install 'dist/forge_code-1.0.0-py3-none-any.whl[terminal]'`。

| Extra | 用途 |
|---|---|
| `anthropic` / `openai` / `gemini` | 所选模型协议 |
| `cli` / `coding` | 终端界面 / Skills YAML 和平台依赖 |
| `mcp` / `channels` | MCP / 可选消息渠道 |
| `validation` / `observability` | 插件验收 / 可选观测导出 |
| `evaluation` | Harbor 公共基准适配 |
| `terminal` | 默认 coding CLI 的组合依赖 |

基础 SDK 无需 Node、YAML、CLI 或模型 SDK。源码标签与 registry 发布分别管理，不应把标签存在理解为 npm/PyPI 已上传同名版本。

## 模型配置

```sh
forge config setup
forge config setup --from-env .env
forge config set model your-model-id
forge config set base-url https://your-provider-endpoint
forge config set api-key
forge config path
forge config
```

用户模型配置为 `~/.forge/.env`，Windows 对应 `%USERPROFILE%\.forge\.env`。优先级：进程环境 > 项目 `.env` > 用户配置。`--from-env` 只导入模型字段，保留源文件与其他设置；密钥使用隐藏输入，不能通过命令参数传入。`forge config` 不调用模型、不回显密钥。已有 `ANTHROPIC_API_KEY`、`MODEL_ID` 等字段仍兼容。

支持 `anthropic`、`openai_responses`、`openai_chat`、`deepseek`、`gemini`。模型别名放在 `~/.forge/settings.json`，或 `FORGE_DATA_DIR/settings.json`：

```json
{
  "models": {
    "fast": {"provider": "openai_chat", "model": "your-model-id", "api_key_env": "COMPANY_MODEL_KEY", "base_url": "https://gateway.example/v1", "input_modalities": ["text"]}
  },
  "agents": {"assistant": {"base": "core", "role_models": {"main": "fast", "summary": "fast", "worker": "fast"}}}
}
```

`forge config models` 或 `/models` 列出别名；空闲时 `/model fast` 切换，SDK 对应 `await session.set_model('fast')`。切换不调用模型，原 Journal 保留，提供方私有续传状态不跨模型复用。含图像历史要求目标声明图像能力。Chat 网关可配置 `chat_token_field: "max_tokens"`，默认是 `max_completion_tokens`。

默认上下文/输出容量为 262,144/65,536 tokens，可用 `MODEL_CONTEXT_WINDOW`、`MODEL_MAX_TOKENS` 覆盖，实际服务必须支持。压缩按窗口预留与近期历史工作，实际输出额度随可用窗口缩小；这些是单请求容量，不是累计用量预算。

## 终端与会话

在目标项目目录运行 `forge`。默认 coding Agent；`forge --agent core` 启动通用助手，`forge --agent <已配置名称>` 选择组合。

| 操作 | 行为 |
|---|---|
| `Ctrl+O`、`/details` | 展开思考与工具详情；完成后可滚动、搜索 |
| `Ctrl+C`、`Ctrl+D` | 取消当前回合 / 退出；空闲时 Ctrl+C 也可退出 |
| `/resume` | 最近会话优先，标记当前会话 |
| `/history`、`/history all` | 最近 10 轮 / 全部可用历史 |
| `/rename NAME`、`/branch NAME` | 命名 / 分支 |
| `/clear` | 保留旧会话，新会话在首条消息后保存 |
| `/context`、`/compact` | 查看上下文 / 显式压缩 |
| `/permission` | 选择沙盒与审批组合 |
| `/jobs` | 查看、读取或停止本会话后台命令 |

启动参数 `--continue` 恢复当前项目最近会话，`--resume SESSION_ID` 指定会话，`--fork-session` 创建分支。恢复沿用保存的权限；未确认完成的工具不会重放。

运行时可输入调整意见，在下次模型请求前应用；`/follow 任务` 排队到下一轮。`/cancel` 停止当前回合，`/pending`、`/next`、`/discard 输入ID` 管理待办。审批期间输入独占，已有草稿会保留。

状态行区分等待模型、思考、回答、工具参数生成、排队和执行，并显示用时与接收量。“参数生成 / 未执行”不代表文件已经写入。思考只展示模型接口实际提供的可读内容；历史中丢失的思考不能补造。窗口快速缩放、流式预览和最终输出共享屏幕所有权，完成后只保留一次最终回复。

`@path` 可附加文本或图像。输入限制为文本 256 KiB、最多 8 张图、单图 512 KiB、总附件 4 MiB；实际图像支持取决于适配器和模型能力。

## 权限与执行

| 预设 | 沙盒 / 审批 | 作用 |
|---|---|---|
| `danger-full-access`（默认） | full-access / never | 当前用户宿主权限 |
| `workspace-write` | workspace-write / ask | 限定写范围，扩大权限逐次确认 |
| `read-only` | read-only / ask | 默认不修改文件 |

```sh
forge --sandbox workspace-write --write-root ./src --write-root ./tests
forge --sandbox read-only
forge --sandbox danger-full-access --approval supervised
```

普通 coding 不初始化原生沙盒，不运行独立意图路由、强制全盘扫描、自动检查点或完成验收。已有文件编辑须先读取并保持版本一致；这是防止覆盖并发修改的检查。任务显式限制、拒绝规则和插件自身策略继续有效。

内置执行提供方采用 DSH 文件/进程分离机制。文件 helper 执行路径策略，不能称为 OS 隔离；命令、Git 和 verify 在受限模式下由 DSH runner 执行。符号链接/Junction 不导致整个工作区拒绝启动，实际写目标仍按已选范围检查。

`SANDBOX_UNAVAILABLE` 表示该命令未启动。Windows 的 `WRITE_OWNER` 错误表示 DSH 无法设置所需 ACL/完整性标签；可编辑文件不等于有权设置沙盒。默认宿主模式不修改项目 ACL；显式原生 workspace-write 在执行时会按 DSH 机制申请授权。

有审批通道时，工具可为同一操作提供 `sandbox_permissions` 与 `justification` 申请单次扩大权限。仅本次有效，不保存“记住规则”，不改变会话默认值。拒绝、无审批通道或清理不确定时不会自动重试宿主执行。

空闲时使用 `/permission <预设>` 或 `await session.set_permissions(name)`；恢复继承保存值，分支继承，清空回到启动默认值。`/sandbox`、`/approval` 和 `full-access` 别名保留兼容。运行中的后台作业会阻止权限和历史切换。

普通命令默认超时 120 秒，可为长任务显式设置更大的正有限时长。后台任务通过 `run_in_background` 启动，`job_output` 按游标读取，`job_kill` 取消；取消/退出仍清理本会话拥有的进程。Harness 无累计请求、工具、token 或回合时长限制。

需要持续运行的开发服务器可使用 `run_in_background=true, lifetime="session"`；明确设置 `timeout_seconds=null` 表示运行到会话退出，也可设置最大运行秒数。服务启动后仍需检查就绪状态，回答结束后保留服务，`/jobs` 可以停止它。退出会话时清理；运行服务期间不能切换权限或历史。该能力目前用于 Windows/Linux 的 full-access 会话，受限提供方不支持时明确报错。

## SDK 与能力组合

最小 SDK 示例见 [README](../../README.md#python-api-与-agent-组合)。`HarnessSession` 提供 `open/stream/run/cancel/close`、队列输入、模型切换和插件服务，流事件与最终 `TurnResult` 独立于终端。

`AgentDefinition.core()` 不创建仓库、任务、检查点和命令作业。coding 的 `features` 可选 `tasks`、`repository`（规则与记忆）、`skills`、`hooks`、`mcp`、`checkpoints`；空数组全部关闭。`tools` 选择具体工具，`max_parallel_tools` 默认为 10，可设 1。只允许显式声明安全的调用重叠，同文件编辑通过目标身份锁协调。

`completion="strict"` 需要 tasks/repository 能力；普通 `completed` 仅表示回合完成，不是独立验收通过。插件可用 `register_completion_check` 追加完成条件。`sandbox_provider` 可选内置或可信插件提供方；core 不依赖沙盒，受管子 Agent 不得扩大父权限。

## 插件开发与交付

插件使用 `activate(ctx, config)` 和 `forge-plugin.json`；`files` 明确交付源码、测试和元数据。`ctx.register_tool` 注册 JSON Schema 工具；`ToolContract` 不要求工作区路径。还可注册服务、模型/执行提供方、Hooks、完成检查和受管子任务。

```sh
forge plugin init ./hello --name hello
forge plugin check ./hello --cases ./acceptance.json --trust
forge plugin enable ./hello --trust
forge plugin list
forge plugin disable hello
```

`--trust` 表示允许宿主 Python 代码。检查和导入不自动启用插件。启用后在用户 settings 的 `agents` 中选择插件 ID，例如 `{"agents":{"notes":{"base":"core","plugins":["hello"]}}}`，再运行 `forge --agent notes`。保留其他既有配置。

独立验收文件示例：

```json
{"cases":[{"id":"greeting","tool":"greet","arguments":{"name":"Ada"},"expect":{"content":"Hello, Ada!"}}]}
```

自然语言开发先准备固定验收，再显式调用模型：

```sh
forge plugin develop "开发一个问候工具" --directory ./greeter --cases ./acceptance.json
forge plugin develop "开发一个问候工具" --directory ./greeter --cases ./acceptance.json --execute
```

不带 `--execute` 只生成计划/骨架。执行默认使用严格 coding 与 workspace-write，验收预期由控制器持有；完成后不自动启用。接口检查、独立业务结果与模型表现分别记录，不能把插件检查当作恶意代码隔离认证。

交付 Agent 时使用与验收源码匹配的 SDK wheel：

```sh
uv build --wheel
forge agent check ./agent.json --cases ./acceptance.json --trust --output ./validation.json
forge agent export ./agent.json ./delivery.zip --cases ./acceptance.json --receipt ./validation.json --sdk-wheel dist/forge_code-1.0.0-py3-none-any.whl
forge agent import ./delivery.zip ./imported
```

在导入目录安装 `requirements.lock`，配置环境变量引用，重新 `agent check`；旧回执只是来源证明，环境或绑定变化会使它失效。示例包括 [笔记](../../examples/agents/notes)、[报销](../../examples/agents/expenses)、[子任务](../../examples/agents/coordinator)和[内部 HTTP](../../examples/agents/internal_http)。

## 评测与轨迹

公共基准见 [benchmark 指南](../../benchmark/README.md)。默认官方任务镜像、单次尝试、独立 grader；数据集运行默认 `completion-policy=normal`，需要 Forge 门禁时显式选 strict。历史冻结条件不静默改写。

```sh
forge eval list --json
forge eval plan ./evaluation.json --output ./frozen-plan
forge eval run --plan ./frozen-plan
forge eval run --plan ./frozen-plan --execute --output ./evaluation-run
forge eval report ./evaluation-run
forge eval export ./evaluation-run --format atif
forge eval export ./evaluation-run --format openai
```

运行实际模型或公共基准会产生费用；范围和环境准备须由调用方授权。`evals.runner` 的本地夹具默认原生 workspace-write，与普通 coding 的默认宿主模式不同。

Journal 保存私有完整事实。导出 ATIF / OpenAI messages JSONL 会脱敏，并保留失败、取消、未完成请求和未知用量；SDK 对应 `journal_to_atif(path)`、`journal_to_openai(path)`。新会话/恢复事件记录 Forge 版本；旧记录缺少版本时保持 unknown，跨版本会话标记 mixed。导出器版本与记录版本分开。

项目不提供蒸馏、SFT/DPO、tokenizer 或训练执行。公开基准输出保持 evaluation-only，轨迹可交给外部分析工具，导出本身不证明适合训练。

## 开发与验证

日常在 `main` 开发。安装开发依赖并编译桥：

```sh
uv sync --locked --all-extras --group dev
npm ci --ignore-scripts
uv run --no-sync python scripts/build_bridge.py
uv run --no-sync python scripts/impl.py audit
```

修改时运行相关行为测试；稳定后运行对应回归与质量/契约检查，提交推送并核对 CI。文档变更检查引用、示例和一致性。日志写入 `.local/implementation`，历史 test count 不证明当前版本通过。

```sh
uv run --no-sync python scripts/ci_checks.py --suite quality --output .local/implementation/quality.json
uv run --no-sync python scripts/ci_checks.py --suite contracts --output .local/implementation/contracts.json
uv run --no-sync python scripts/check_sdk_install.py --output .local/implementation/new-sdk-check
```

### 5 分钟 Harness 演示

`uv run --no-sync python examples/harness_demo.py --output <新目录>` 使用脚本模型、真实工具和已选原生机制演示修复、证据失效与 Journal 重建；它不调用付费模型。运行前需要相应原生条件，缺失时保持未验证，不临时修改项目 ACL。旧真实实验、冻结输入和贡献边界见 [T01](../implementation/tasks/T01.md)。

发布采用 `v1.0.0` 注释标签；Python、npm 启动器和本仓库 Node workspace 包保持产品版本一致。插件 API v1、Journal v2、ATIF v1.7 与共享契约 v1 是独立格式版本，不能机械替换为产品版本。
