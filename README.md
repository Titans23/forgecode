# ForgeCode

> ForgeCode 不是“代码问答工具”，而是一个运行在终端中的通用 Agent Harness：模型负责决策，运行时负责工具、上下文、权限、执行、恢复和评测。

ForgeCode 面向真实代码仓库中的长链路工程任务。它不把模型的一句“已经完成”视为成功，而是通过工具执行、测试反馈、权限控制、变更检查和可复现评测，客观判断任务是否真正完成。

当前项目处于早期开发阶段，但 M1 多步 Agent Loop、M2 工作区与完成门禁、M5 上下文工程的核心能力已经落地。本 README 同时描述当前可用能力、系统边界和从 M0 到 v1.0 的后续路线。

## 项目定位

ForgeCode 的核心职责是为模型提供一套可靠、可恢复、可评测的 Agent 运行时：

```text
用户任务 → 模型决策 → 工具调用与权限判断 → 代码修改与命令执行
        → 测试、构建与结果验证 → 完成判定、轨迹保存与任务恢复
```

第一版聚焦以下任务：

- 修复 Bug；
- 修改代码；
- 补充测试；
- 运行构建；
- 分析测试失败；
- 输出 Git Diff。

第一版暂不实现：

- IDE 插件；
- Web 前端；
- 向量数据库；
- 多 Agent 团队；
- SFT/RL；
- 自动提交和推送代码。

## 设计原则

- **执行结果优先**：以真实工具输出、测试结果和 Git Diff 作为完成依据。
- **模型与运行时解耦**：模型负责推理和决策，运行时负责确定性的执行与约束。
- **结构化失败**：工具、命令和模型调用失败时返回结构化错误，不让 Agent 进程直接崩溃。
- **安全默认值**：仓库外访问、高风险命令、网络和敏感文件默认受限。
- **全程可追踪**：模型响应、工具调用、权限判断、文件修改和验证结果均进入事件轨迹。
- **可恢复而非重来**：当前目标、工作证据和可压缩消息历史分层保存；持久化会话、代码 Checkpoint、回放和回滚继续由 M4/M3 建设。
- **用评测驱动迭代**：每个里程碑都有可复现任务、明确验收条件和量化指标。

## 当前可用能力

- 持续交互的多步 Agent Loop，支持流式文本、多工具调用、结构化错误恢复和 JSONL 轨迹；
- 仓库范围内的读取、搜索、原子写入、分块写入、精确替换、Patch、命令、验证和 Git 工具；
- 基于任务起始快照的工作区版本追踪，内置写入工具可以追踪 Git ignored 目标文件；
- 默认交互模式允许有效 Diff 自然完成；评测或 CI 可以通过 `TaskPolicy(require_verification=True)` 强制当前 revision 的成功验证；
- Edit Recovery 默认累计 5 次失败写入，每次失败后最多允许一次针对性读取，随后只开放写入工具；
- 相同 revision 中已覆盖的文件范围不会重新访问磁盘，也不会再次向模型注入完整源码，只返回短引用；
- 单次用户回合默认累计输入上限为 500,000 Token，达到上限后安全停止；
- WorkingState、廉价压缩、结构化摘要、项目规则、仓库记忆和 `/context`、`/compact`、`/task`、`/memory` 等 Slash Command。

## 技术基线

当前基线采用 Python 3.12、uv、Typer、Rich、Pydantic 和 pytest，并默认直接在用户本机运行，不要求安装 Docker。Rich 用于交互终端，Pydantic 用于工具输入 Schema 和运行时校验。M4 首版采用 append-only JSONL 持久化会话，避免过早引入重复状态源；需要更大规模的检索和统计时，再评估增加 SQLite 索引。模型层定义统一的 `ModelClient` 接口，首版只接入一个模型 Provider，后续再扩展多模型或模型路由。

### 本地开发

项目使用 uv 管理 Python、虚拟环境、依赖和锁文件。首次进入仓库后执行：

    uv sync
    uv run pytest
    uv run forge --help

当前自动化测试基线为 `283 passed`。提交前建议同时运行：

    uv lock --check
    uv run python -m compileall -q forge tests
    uv run pytest -q
    git diff --check

.python-version 将开发环境固定到 Python 3.12，pytest 位于 dev 依赖组中并由 uv 默认同步。CI 或可复现检查应使用：

    uv lock --check
    uv run --frozen pytest

### 本机运行基线

ForgeCode CLI、工具和 Agent Loop 默认直接在当前本机与代码仓库中运行。ForgeCode 不捆绑 Docker，也不要求用户为了启动 Agent 安装容器环境。执行项目任务时，ForgeCode 复用项目本身需要的 Python、Node.js、Java 或其他工具链。

M0 的三个 Fixture 仅用于项目维护者评测。普通用户无需同时安装这些 Fixture 的全部语言工具链。命令审批、路径限制和操作系统级沙箱将在 M3 实现；Docker 如有需要，只作为 M7 Benchmark 的可选执行后端。

### 模型接口基线

首个 Model Provider 确定为 Anthropic，但 SDK 只允许出现在 `forge/runtime/model_client.py` 中，用于创建客户端、发送模型请求和接收流。参考 Anthropic Tool Use 的原始消息格式，ForgeCode 直接使用普通的 `list[dict]` 保存消息和工具 Schema，不再额外包装 `ModelRequest`、`ModelMessage` 等类型。Agent Loop、工具执行、上下文和终端仍由 ForgeCode 自己实现，核心代码不依赖 `anthropic.types`。

模型 ID、API Key 和自定义接口地址从当前目录的 `.env` 读取，最大输出 Token 通过适配器构造参数配置。先复制示例文件：

PowerShell：

    Copy-Item .env.example .env

macOS/Linux：

    cp .env.example .env

然后只在本机编辑 `.env`：

    ANTHROPIC_API_KEY=your-api-key
    MODEL_ID=claude-sonnet-4-6
    MODEL_MAX_TOKENS=8192
    MODEL_CONTEXT_WINDOW=128000
    MODEL_REQUEST_TIMEOUT_SECONDS=120
    ANTHROPIC_BASE_URL=https://api.anthropic.com

完成后检查配置：

    uv run forge config

启动交互式会话：

    uv run forge

启动后可以连续输入消息，每一轮都会携带经过裁剪的相关会话上下文；按 `Ctrl+C` 退出。终端支持直接粘贴包含换行的多行 Prompt，粘贴内容会作为一条完整消息提交。模型文本按照 Provider 的 delta 实时显示。Token 区分最近一次模型请求和当前用户回合累计值，同时显示模型调用次数；如果使用了 Prompt Cache，还会显示缓存读写 Token。交互运行时默认在累计输入达到 500,000 Token 后停止当前用户回合，代码调用方可以通过 `Conversation(max_turn_input_tokens=...)` 调整或禁用这一保险。

`.env` 已被 Git 忽略，仓库只提交不含真实凭据的 `.env.example`。系统环境变量优先于 `.env` 中的同名配置。`ANTHROPIC_API_KEY` 和 `MODEL_ID` 必填；`ANTHROPIC_BASE_URL` 可以省略，默认使用 `https://api.anthropic.com`。`MODEL_MAX_TOKENS` 可选，默认 `8192`，允许范围为 `1024～32768`。`MODEL_CONTEXT_WINDOW` 可选，必须根据当前 Provider 和模型文档填写，并且大于 `MODEL_MAX_TOKENS`；ForgeCode 不猜测第三方兼容模型的窗口大小。`MODEL_REQUEST_TIMEOUT_SECONDS` 控制流式请求无响应超时，默认 `120` 秒，允许范围为 `10～600` 秒。SDK 内置重试被关闭，由 ForgeCode 统一决定何时安全重试。`forge config` 显示 Model ID、Base URL、最大输出 Token、请求超时、上下文窗口和密钥配置状态，但不会回显 API Key。交互模式的每轮消息都会发起真实 API 请求，可能产生 Provider 费用。

## 项目结构

```text
forge-code/
├── .env.example
├── pyproject.toml
├── README.md
├── forge/
│   ├── cli.py
│   ├── config.py
│   ├── hooks/
│   ├── runtime/
│   ├── tools/
│   ├── permissions/
│   ├── context/
│   ├── sessions/
│   └── prompts/
├── evals/
│   ├── cases/
│   ├── runner.py
│   └── metrics.py
└── tests/
```

## 里程碑总览

里程碑编号表示能力主题，不再强制表示实施顺序。当前优先级是先完成 M2 的真实模型验收，然后优先建设 M5；M3 暂缓实现。

```text
M0  项目基线与评测样例
 ↓
M1  最小可用 Agent Loop
 ↓
M2  可靠的代码修改与验证闭环
 ↓
M5  上下文工程与仓库记忆（下一阶段）
 ↓
M4  会话持久化与任务恢复
 ↓
M3  权限、安全与代码回滚（暂缓）
 ↓
M6  Hooks、MCP 与子 Agent 扩展
 ↓
M7  Benchmark、消融实验与 v1.0
```

## M0：项目基线与评测样例

### 目标

定义 ForgeCode 要解决的问题和系统边界，并准备能够客观判断 Agent 是否成功的可复现任务。此阶段不调用大模型修复 Bug。

### 任务

- [x] 建立独立的 `forge-code` 仓库；
- [x] 在 README 中明确项目定位和系统边界；
- [x] 建立可以通过命令启动的空 CLI；
- [x] 确定首个模型 Provider，并定义统一的 `ModelClient` 接口；
- [x] 配置首版技术栈；
  - [x] 使用 uv 管理 Python、虚拟环境和锁文件；
  - [x] 将 pytest 配置为 dev 测试依赖；
  - [x] 默认使用本机原生执行，不要求 Docker；
  - [x] 支持通过本地 `.env` 配置 Model ID、Base URL 和 API Key；
- [x] 建立 `forge`、`evals` 和 `tests` 基础目录；
- [x] 准备 `python-calculator`、`typescript-todo` 和 `java-order-service` 三个 Fixture 仓库；
  - [x] `python-calculator`：Bug、公开/隐藏测试、任务说明和固定 Commit 已完成；
  - [x] `typescript-todo`：Bug、公开/隐藏测试、任务说明和固定 Commit 已完成；
  - [x] `java-order-service`：Bug、公开/隐藏测试、任务说明和固定 Commit 已完成；
- [x] 为每个 Fixture 固定基础 Commit，设置一个明确 Bug；
- [x] 提供公开测试、正确修复后的隐藏测试和自然语言任务描述；
- [x] 声明构建/测试命令，以及允许修改和禁止修改的路径。

任务格式：

```yaml
id: python-calculator-001
repo: fixtures/python-calculator
base_commit: abc123
task: 修复除数为零时返回错误结果的问题，并补充测试
test_command: pytest
timeout_seconds: 300
forbidden_paths:
  - tests/hidden/
```

### 验收条件

- 能通过命令启动空 CLI；
- 能检查模型配置，且不会回显 API Key；
- 三个 Bug 仓库均可从固定状态复现；
- 每个 Bug 都能通过测试客观判断是否修复；
- README 清晰描述系统能力与暂不支持的范围。

## M1：最小可用 Agent Loop

### 目标

实现第一个可以演示的纵向闭环：

```text
用户任务 → 模型选择工具 → 执行工具 → 返回工具结果 → 模型继续决策
        → 修改代码 → 运行测试 → 输出结果
```

### 当前进度：M1.4 多步 Agent Loop

- [x] 执行 `forge` 后进入持续交互循环，按 `Ctrl+C` 退出；
- [x] 同一会话保留用户和模型消息，使后续请求携带历史上下文；
- [x] 从 `.env` 创建 Anthropic SDK 客户端并调用配置的模型；
- [x] 为每次模型调用注入 ForgeCode System Prompt，避免底层 Provider 身份覆盖产品身份；
- [x] 将模型文本 delta 实时追加到终端；
- [x] 使用 Rich Live 显示会话标题、实时 Markdown 回复和等待状态；
- [x] 根据流式 usage 更新输入、输出、缓存读写和总 Token；
- [x] 交互中每轮模型调用失败时显示错误并继续等待输入；
- [x] 使用 FakeModelClient 覆盖历史上下文、流式事件和 Token 统计测试，不依赖网络与真实 API Key。

M1.1 当前验证 CLI、配置、ModelClient、流式文本、Token usage 与终端渲染链路，不代表完整 Agent Loop 已完成。工具调用循环、轨迹与验证闭环仍属于后续 M1 工作。

M1.2 在保持 Anthropic SDK 请求类型简洁性的同时，将 Provider 流转换为 ForgeCode 统一响应事件：

- [x] 定义 `ToolCall` 以及工具调用开始、参数增量和完成事件；
- [x] 解析 Anthropic `tool_use`、`input_json_delta` 和 `content_block_stop`；
- [x] 按内容块索引独立聚合参数，支持一轮返回多个 Tool Call；
- [x] 工具参数完成后解析并验证为 JSON 对象，协议错误不会产生半成品 `ToolCall`；
- [x] 在解析参数前检查工具名是否属于当前 Schema，未知工具优先返回 `unavailable_tool`；
- [x] 检测 Provider 的 `stop_reason=max_tokens`，区分输出截断与普通 JSON 错误；
- [x] 普通文本输出截断时保留已生成内容并自动续写最多两次，最终合并为完整回答；
- [x] 工具参数截断或其他协议错误最多自动恢复两次，失败响应中的工具一律不执行；
- [x] `TurnResult` 同时支持文本和工具调用，纯工具调用也是合法模型响应；
- [x] 将完成的工具调用保存为 Anthropic `tool_use` 助手消息，为后续执行循环提供上下文。

M1.2 只完成“模型表达要调用什么工具”的协议层。

M1.3 建立可独立测试的本机工具层：

- [x] 使用 Pydantic 模型生成工具 Schema，并在执行前拒绝未知参数和无效类型；
- [x] 统一使用 `ToolResult(success, summary, content, error, metadata)` 返回结果；
- [x] 参数错误、路径错误、命令失败和工具内部异常均转换为结构化失败；
- [x] 使用 `ToolRegistry` 注册工具、导出确定顺序的 Schema 并处理未知工具名；
- [x] 所有路径型工具以显式仓库根目录为边界，拒绝绝对路径和 `..` 越界；
- [x] 实现 `list_directory`、`find_files`、`read_file`、`grep`、`write_file`、`write_file_chunk`、`replace_text`、`apply_patch`、`run_command`、`verify`、`git_status` 和 `git_diff`；
- [x] `write_file` 使用原子替换创建或覆盖 UTF-8 小文件，内容最多 30000 字符；
- [x] `write_file_chunk` 以最多 30000 字符的有序分块原子扩展大文件，通过 offset 防止错序，并支持最终 SHA-256 完整性校验；
- [x] `replace_text` 只替换恰好出现一次的精确文本，分别返回 `text_not_found` 和 `text_not_unique`；未找到时直接返回最多 2000 字符的最接近当前精确原文，供下一次调用原样复制；
- [x] `apply_patch` 的 Patch 参数硬限制为 30000 字符，超限在执行前返回参数错误；
- [x] `apply_patch` 同时接受标准 Unified Diff 与模型常用的 `*** Begin Patch` 格式；Update、Add、Delete 和多个裸 `@@` Hunk 会先在内存完整验证，再统一通过 `git apply --check` 原子应用；
- [x] Codex 格式补丁匹配失败时会检测误复制的 `read_file` 行号前缀（如 `99 |`），返回 `patch_contains_read_line_numbers` 且不消耗失败写入预算；
- [x] `.forge`、`.git`、`.env` 和私有 `.env.*` 不会被路径工具读取、列出或搜索，公开的 `.env.example` 仍可作为配置说明；
- [x] `list_directory` 接受可选的 `max_results`，避免模型复用分页参数时产生无意义的 Schema 重试；
- [x] 无路径 `git_diff` 超过 30000 字符时要求缩小到具体文件；指定未跟踪 UTF-8 文件时仍能生成可审查的新增文件 Diff；
- [x] `run_command` 返回退出码、stdout、stderr、耗时和超时状态，并拒绝明显的脚本写文件和输出重定向；多行诊断脚本可通过最多 8000 字符的 `stdin` 传入；
- [x] Windows 上的命令明确通过 `cmd.exe` 执行；POSIX `<<` Heredoc 会在启动进程前返回可恢复协议错误，并提示改用 `python -` 或 `node` 配合 `stdin`；
- [x] 工具通过 `read_only`、`workspace_write` 和 `process` Effect 描述工作区影响。

M1.3 的工具已经能够被运行时独立调用，但当时尚未接入交互 CLI。

M1.4 将模型决策和内置工具连接成真正的执行循环：

- [x] 交互 CLI 根据当前工作目录创建默认工具注册表，并把内置执行工具和可选任务工具 Schema 提供给模型；
- [x] 模型返回 Tool Call 后，运行时按响应顺序执行一个或多个工具；
- [x] 将统一 `ToolResult` 序列化为 Anthropic `tool_result` 用户消息，并通过 `is_error` 标记失败；
- [x] 工具执行后继续调用模型；模型无工具调用的文本响应会进入 Completion Gate，`finish_task` 仅作为可选的结构化结束协议；
- [x] 含 Tool Call 的模型文本作为过程说明；不含 Tool Call 的文本作为结束候选并进入 Completion Gate；
- [x] 一次用户请求内累计所有模型调用的输入、输出和缓存 Token；
- [x] 保存完整的 `user → assistant(tool_use) → user(tool_result) → assistant(final)` 会话上下文；
- [x] 终端按事件时间线内联显示模型文本与工具组，并展示工具名称、参数摘要、成功或失败状态、结果摘要及最多 800 字符的失败诊断；
- [x] 默认不限制模型调用次数，但单次用户回合默认限制为累计 500,000 输入 Token；模型完成任务、达到安全上限或用户按 `Ctrl+C` 时结束，调用方仍可显式设置测试调用上限；
- [x] 写入工具没有产生真实最终 Diff 后进入独立 Edit Recovery：只累计失败写入，默认连续 5 次后停止；存在未解决写入失败时暂停全局 Stagnation，避免两套停止机制互相抢占；
- [x] Edit Recovery 每次失败写入后最多开放一次 `read_file` 或 `grep`；读取后下一轮只提供写入工具，未解决失败时不开放无效的完成声明；已覆盖范围的缓存回放只返回短引用而不重复注入源码；
- [x] 未解决的写入失败会同时阻止纯文本完成和 `finish_task` 完成声明；后续真实修改成功后才退出恢复阶段。
- [x] 当前 revision 已有真实 Diff 且满足任务策略后进入 Completion Ready：默认交互模式把验证作为建议，只有显式 `TaskPolicy(require_verification=True)` 才要求当前 revision 的成功验证；读取、搜索和诊断不会再重置独立决策预算；
- [x] Completion Ready 预算耗尽后执行一次无工具的 Finalization Recovery，让模型基于已有 Diff 和可用验证生成最终交付；当前 revision 的明确验证失败、严格策略下验证缺失或过期、存在未解决写入失败或计划仍有未完成步骤时不得进入；
- [x] Completion Gate 的 `git diff --check` 只检查本轮真正修改的路径，不受其他用户预先存在的脏文件影响。

M1.4 已形成可执行的 Agent Loop，并已隔离路径工具可触达的控制面目录与环境文件；完整命令审批、Shell 级敏感路径治理和更强的本机安全限制仍属于 M3。当前 Shell 写入检查只是行为护栏，不是安全沙箱。ForgeCode 当前也没有浏览器、截图或视觉理解能力，网页游戏只能进行源码、命令和测试层面的验证。

### 任务

Model Client：

- [x] 通过普通 Python 消息字典、`ModelClient` 和 Provider 无关的流式事件定义统一模型边界；
- [x] 支持文本流式输出；
- [x] 支持 Tool Calling 和单轮返回多个 Tool Call；
- [x] 读取并显示每轮输入、输出和缓存 Token；
- [ ] 记录模型调用耗时；
- [ ] 处理超时、限流、格式错误和有限次数重试。

```python
class ModelClient(Protocol):
    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        ...
```

Tool 抽象：

- [x] 定义统一工具接口，使用 Pydantic 校验输入；
- [x] 统一返回 `success`、`summary`、`content`、`error` 和 `metadata`；
- [x] 工具失败时返回结构化错误，不使 Agent 进程崩溃；
- [x] 实现 `list_directory`、`find_files`、`read_file`、`grep`、`write_file`、`write_file_chunk`、`replace_text`、`apply_patch`、`run_command`、`verify`、`git_status` 和 `git_diff`；
- [x] `read_file` 支持行范围，`grep` 支持路径和文件类型过滤；
- [x] `run_command` 返回退出码、stdout、stderr 和耗时；
- [x] `apply_patch` 返回真实修改结果。

Agent Loop 与 CLI：

- [x] 初始化 System Prompt，将工具 Schema 提供给模型；
- [x] 执行 Tool Call 并将结果反馈给模型；
- [x] 默认持续循环直到任务完成，并支持 `Ctrl+C` 中断；测试可显式设置最大循环次数；
- [x] 支持单次用户回合累计输入 Token 上限，默认 500,000，代码调用方可配置或禁用；
- [x] 在相同工作区版本中缓存只读证据；已覆盖的 `read_file` 范围返回短引用，不重复访问磁盘或注入完整源码；
- [x] 普通任务连续 4 次模型调用没有工作区、计划或仓库证据进展时进入恢复检查点，连续 8 次无进展时以 `stuck` 结束；Edit Recovery 使用独立的失败写入预算和一次针对性读取约束；
- [x] 保存完整 JSONL 执行轨迹；
- [x] 支持 `forge` 进入循环交互会话；
- [x] 终端按事件时间线显示模型文本、工具及参数摘要、失败诊断、文件修改、验证结果和 Token 使用。

### 验收条件

至少完成一个 Fixture 的完整链路：发现失败测试 → 搜索并读取代码 → 修改代码 → 运行测试 → 测试通过 → 输出 Git Diff。

- 至少一个 Bug 被真实修复；
- Agent 至少执行一次测试；
- 最终 Diff 非空；
- 全过程有 JSONL 轨迹；
- 无需人工直接指出要修改的文件。

## M2：可靠的代码修改与验证闭环

### 当前进度：核心实现与评测 Runner 完成，等待真实模型验收

### 目标

解决“模型说完成了，但代码实际上不能用”的问题。模型仍负责理解任务、修改代码和选择验证方式；ForgeCode 负责追踪工作区变化、执行验证、保存客观证据，并在证据不足时拒绝结束任务。

M2 不试图替代模型判断代码是否正确，而是建立一条可检查的闭环：

```text
理解任务 → 修改代码 → 检测工作区变化 → 执行验证 → 检查完成条件 → 输出证据
                                      ↑                 │
                                      └── 条件不满足则继续 ┘
```

### 任务

#### M2.1：工作区变化追踪

- [x] 任务开始时记录 Git 状态，把用户已有的未提交修改作为基线；
- [x] 在文件修改工具和 Shell 命令执行后重新检查工作区；
- [x] 记录本次任务实际修改的文件；
- [x] 使用递增的 `workspace_revision` 标记每一次实际代码变化；
- [x] 写入工具执行前记录目标内容指纹，使 Git ignored 文件的创建和修改也能产生任务内 `workspace_revision`；
- [x] 保留用户原有修改，不把它们误认为 Agent 本次产生的修改。

#### M2.2：专用验证工具

- [x] 新增 `verify` 工具，与普通 `run_command` 明确区分；
- [x] 复用现有命令执行能力，记录验证命令、退出码、耗时和执行时的 `workspace_revision`；
- [x] 只有退出码为零的 `verify` 调用才能成为成功验证证据；
- [x] 验证后如果代码再次变化，旧验证自动失效。

#### M2.3：完成检查

- [x] 新增可选的 `finish_task` 结构化结束协议；普通交互可由模型文本响应自然结束，两种路径都经过 Completion Gate；
- [x] 模型准备结束代码任务时，检查是否真的产生了修改；
- [x] 显式严格任务检查当前工作区版本是否有成功的验证证据；默认交互任务不因未调用 `verify` 单独阻塞；
- [x] 当前版本验证失败时拒绝结束；严格策略下没有验证或验证已经过期时也拒绝结束；
- [x] 修改禁止路径或允许范围外的文件时拒绝结束；
- [x] 执行 `git diff HEAD --check`，存在确定性的 Patch 错误时拒绝结束；
- [x] 拒绝结束时把具体原因返回模型，让 Agent Loop 继续工作；
- [x] 连续三次仍不能满足条件时，以 `blocked` 结束并说明原因，避免无限循环；
- [x] 最终结果展示修改文件、验证命令、退出码和耗时。

### 实现要求

- [x] 工作区变化、验证结果和完成检查继续使用现有事件与 JSONL 轨迹记录；
- [x] system prompt 告诉模型修改代码后使用 `verify`，但是否完成由运行时代码判断；
- [x] 为工作区变化、Shell 修改、验证成功与失败、验证过期、越界修改和完成检查编写自动化测试；
- [x] 评测 Runner 从固定 Commit 创建临时 Git 仓库，并在 Agent 运行期间从工作区移除隐藏测试；
- [x] Runner 将 YAML 路径规则传给 `TaskPolicy`，并在 Agent 结束后独立执行构建、公开测试和隐藏测试；
- [x] Runner 保存 JSON 验收报告和 JSONL 轨迹，失败评测同样保留证据；
- [ ] 使用真实模型运行三个 Fixture 并确认全部通过；
- [x] 整个流程直接运行在本机，不要求 Docker。

### 运行评测

评测会调用 `.env` 中配置的真实模型并产生 API 请求。隐藏测试在 Agent 运行期间只保存在 Runner 内存中，不会出现在临时工作区；Agent 结束后才由评测器恢复和执行。

这里提供的是 M2 工作区隔离，不是对恶意 Shell 的操作系统级沙箱。Shell 路径和进程的强制隔离仍由 M3 实现。

```powershell
# 查看可用用例，不调用模型
uv run python evals/runner.py --list

# 运行一个用例
uv run python evals/runner.py --case python-calculator-001

# 依次运行全部用例
uv run python evals/runner.py
```

评测报告写入 `.forge/evals/<case-id>-latest.json`，对应的完整轨迹复制到同一目录。任一 Agent 状态、修改范围、验证、构建、公开测试、隐藏测试或 `git diff HEAD --check` 不满足要求时，该 Case 返回失败，进程最终退出码为 `1`。

### 暂不纳入 M2

- 完整的 `RepositoryProfile` 和仓库记忆放到 M5；
- 权限审批、操作系统沙箱和代码回滚放到 M3；
- 会话恢复和 Checkpoint 持久化放到 M4；
- Hooks、MCP 和子 Agent 放到 M6；
- 时间和费用总预算继续归入 M7；Agent Loop 已提供单回合累计输入 Token 上限。

### 验收条件

- [x] ForgeCode 能发现模型和 Shell 命令修改了哪些文件；
- [x] 当前版本验证失败时系统能够拒绝结束；严格策略还能拒绝没有验证或验证已过期的完成声明；
- [x] 修改禁止路径或允许范围外文件时，系统能够拒绝结束；
- [x] 用户原有的未提交修改不会被覆盖或误认为本次 Agent 修改；
- [ ] 三个 Fixture 均在干净副本中完成公开测试和隐藏测试验收；
- [x] 最终结果能够列出修改文件和验证依据，并留下可回放的 JSONL 轨迹。

## M3：权限、安全与代码回滚

### 当前状态：暂缓实现

M3 的规划继续保留，但当前不进入开发。M5 可以先在固定 Fixture 和用户明确授信的本机仓库中推进；涉及不可信仓库、高风险命令和操作系统级隔离的使用场景，仍必须等待 M3。

### 目标

把 ForgeCode 从“能够运行”提升为“能够在真实仓库中谨慎运行”。权限决定操作是否允许，原生操作系统沙箱限制 Shell 及其子进程，不要求 Docker。

### 任务

权限模式与规则：

- [ ] `plan`：只读和分析，不修改文件；
- [ ] `supervised`：代码编辑和高风险命令需要确认；
- [ ] `auto`：低风险行为自动执行，高风险行为仍需确认；
- [ ] 支持 `allow`、`ask` 和 `deny`，并定义规则优先级；
- [ ] 支持用户级、项目级和会话级规则；
- [ ] 支持“允许一次”“本会话允许”“当前项目允许”“拒绝”和“拒绝并告知 Agent 原因”。

文件安全：

- [ ] 标准化所有路径，阻止 `../` 和软链接路径逃逸；
- [ ] 默认禁止访问仓库外路径；
- [ ] 默认保护 `.env`、SSH 私钥、云凭证、浏览器凭证和 `.git` 内部文件；
- [ ] 限制单次修改文件数和 Patch 大小。

Shell 安全：

- [ ] 设置命令超时和 stdout/stderr 长度限制；
- [ ] 清理敏感环境变量并禁止 `sudo`；
- [ ] 网络访问、依赖安装和文件删除单独审批；
- [ ] 支持查询和终止后台进程；
- [ ] 限制 CPU、内存和进程数量。

Checkpoint：

- [ ] Agent 第一次修改前保存工作区基线；
- [ ] 保存并保护用户原有的未提交修改；
- [ ] 每轮重要修改创建 Checkpoint；
- [ ] `/undo` 恢复最近一次 Agent 修改；
- [ ] `/rewind` 恢复指定任务节点；
- [ ] 不依赖 `git reset --hard`；
- [ ] 区分用户修改和 Agent 修改；
- [ ] 单独检测 Shell 命令造成的文件变化。

### 验收条件

- 读取 `../.ssh/id_rsa` 的尝试被拒绝；
- 读取 `.env` 的尝试被拒绝或要求审批；
- 危险删除命令被拦截，超时命令能够被终止；
- Agent 修改后可以通过 `/undo` 恢复；
- 用户原有未提交代码不会丢失；
- 每次审批都有审计日志。

达到 M3 后，ForgeCode 才进入不可信或高风险大型仓库的试用阶段。

## M4：会话持久化与任务恢复

### 当前状态：已完成

### 目标

参考 Claude Code 的公开会话模型，让任务不会因为进程退出而丢失，并明确区分两个相互独立的能力：

- **会话恢复**：持久化对话、工具调用与结果、任务状态和上下文压缩结果；
- **文件 Checkpoint**：保存 ForgeCode 文件编辑工具修改前的内容，用于会话级撤销，不替代 Git。

M4 首版优先保证可靠恢复和不重复执行工具，不尝试自动恢复整个 Git 工作区。

### 任务

会话存储采用 append-only JSONL 作为唯一事实来源：

- [x] 在用户目录下按项目保存会话：`~/.forge/projects/<project-key>/sessions/<session-id>.jsonl`，不污染任务仓库和 Git 状态；
- [x] 记录 `SessionStarted`、`UserMessage`、`AssistantMessage`、`ToolRequested`、`ToolStarted`、`ToolResult`、`TaskState`、`ContextCompacted`、`TurnCompleted`、`SessionStopped` 和 `SessionResumed`；
- [x] 每个事件包含 UUID、父事件 UUID、会话 ID、递增序号和时间戳；
- [x] 完整保存恢复所需的模型消息、工具参数和工具结果，保证 `tool_use` 与 `tool_result` 协议配对合法；
- [x] 大型工具输出单独保存，事件中只保留引用；
- [x] 流式增量不逐 Token 持久化，只在完整消息形成后写入可恢复记录；
- [x] 原始历史持续保留，压缩后通过父事件链选择实际发送给模型的有效消息；
- [x] 写入采用追加、刷新和尾部损坏容错，事件不被静默覆盖。

恢复语义：

- [x] `--continue` 选择当前项目最近的可恢复会话，`--resume` 按 ID 或名称选择会话；
- [x] 从最后一个完整、协议合法的事件边界恢复，忽略崩溃留下的不完整 JSONL 尾部或消息片段；
- [x] 恢复完整对话、模型配置、活动任务、上下文摘要和最近验证结果；活动任务的单轮计时、Token 和恢复次数重新计数；
- [x] 已有 `ToolResult` 的工具绝不重放；只有 `ToolStarted` 而没有结果的工具标记为结果未知；
- [x] 结果未知的读取工具可以重新执行，写入、命令和其他有副作用工具必须先检查工作区并由用户决定；
- [x] 危险权限和临时审批不随会话自动恢复；
- [x] 会话分支复制有效消息链并生成新会话 ID，原会话保持不变。

文件 Checkpoint：

- [x] 每个用户提示建立一个 Checkpoint，并在 `write_file`、`apply_patch`、`replace_text`、`replace_line_range` 和分块写入首次修改目标文件前保存原内容；
- [x] 使用 SHA-256 内容寻址 Blob 保存原文件，记录文件是否原本存在；
- [x] 回滚时恢复被修改文件，并删除由 ForgeCode 编辑工具新建的文件；
- [x] 不把 Checkpoint 宣称为 Git 或系统级快照；Shell 命令、外部程序、符号链接和并发会话造成的修改仅检测并警告，首版不自动回滚；
- [x] 恢复前校验目标文件当前哈希，发现用户或外部修改时停止覆盖并请求确认；
- [x] 为 Checkpoint 设置数量和时间保留策略，清理不再被引用的 Blob。

索引与命令：

- [x] 使用可重建的 `index.json` 加速会话列表，JSONL 始终是事实来源；首版不引入 SQLite 双重状态；
- [x] 支持 `forge --continue`、`forge --resume [<id-or-name>]`、`forge sessions` 和 `--fork-session`；
- [x] 支持 `/resume`、`/rename`、`/history`、`/status`、`/branch`、`/rewind`、`/compact` 和 `/clear`；
- [x] 输入 `/resume` 打开非全屏内联会话选择器并原地切换上下文，支持方向键、Enter、Esc 和 Ctrl+C；Slash 补全支持会话命令，并在 `/resume ` 后动态补全当前项目的会话名和 ID；
- [x] `/rewind [checkpoint-id] [code|conversation|both]` 可分别选择恢复对话、恢复代码或同时恢复两者。

### 验收条件

- 任务运行中强制结束进程后，可通过 `forge --continue` 恢复；
- 不重复执行已经成功的工具调用；
- JSONL 尾部损坏或模型流式响应中断时，仍能恢复到最后一个合法边界；
- 可以查看恢复前的计划、修改、测试结果和上下文压缩摘要；
- `/rewind` 能恢复 ForgeCode 编辑工具造成的文件修改，并在外部文件变化时拒绝静默覆盖；
- 可以从旧会话创建新会话分支尝试不同方案，原会话保持不变。

## M5：上下文工程与仓库记忆

### 当前状态：核心能力已完成

M5 已实现本机仓库中的上下文统计、分级压缩、结构化摘要、项目规则加载和持久化仓库记忆，不扩大现有权限边界。真实模型下的大规模策略对比继续归入 M7 Benchmark。

### 目标

解决长任务中上下文持续膨胀、Agent 遗忘任务目标、重复读取文件和重复执行命令的问题。

### 已实现

上下文分层：

```text
System Layer
├── 行为规则
├── 工具定义
└── 安全边界

Repository Layer
├── 项目指令
├── 仓库画像
└── 架构摘要

Task Layer
├── 目标
├── 计划
├── 已确认事实
├── 待解决问题
└── 验证状态

Working Layer
├── 最近文件
├── 最近工具调用
├── 当前错误
└── 当前 Patch
```

上下文统计与廉价压缩：

- [x] `/context` 显示消息数、字符数、工具结果字符数和近似 Token；
- [x] 单个大型工具结果写入 `.forge/context/tool-results/`，模型上下文只保留路径、哈希、大小和预览；
- [x] 历史超过预算后只保留最近 12 条消息，不再固定保留会话开头，并把 `tool_use` 与对应 `tool_result` 作为不可拆分单元；
- [x] 旧工具结果缩为占位符，保留最近 3 个完整结果；结构化文件证据保留规范副本，缓存命中的重复读取只保留短引用；
- [x] 每一次模型调用前重新计算请求上下文和自动压缩阈值，而不只在用户回合开始时检查；
- [x] 最近消息窗口之外额外保留最多 4 个关键 `read_file` 证据单元，总预算 100 KB，避免模型知道“读过”却看不到源码；
- [x] 临时工具输出和压缩前转录不提交到 Git。

结构化任务摘要：

```json
{
  "goal": "",
  "constraints": [],
  "findings": [],
  "modified_files": [],
  "failed_attempts": [],
  "verification": [],
  "open_questions": [],
  "next_action": ""
}
```

- [x] 廉价压缩仍超限时，请求模型生成固定字段的 JSON 摘要；
- [x] 摘要前把完整会话保存为 `.forge/context/transcripts/*.jsonl`；
- [x] 摘要保留目标、约束、发现、修改文件、失败尝试、验证、未决问题和下一步；
- [x] `/compact` 支持手动压缩；配置模型窗口后，预计输入与预留输出达到窗口的 80% 时自动压缩；
- [x] 未配置模型窗口时，继续使用 120,000 字符作为自动压缩兜底阈值；
- [x] 自动摘要连续失败 3 次后熔断；Provider 明确报告上下文超限时只恢复重试一次。

项目指令与仓库记忆：

- [x] 加载根目录 `AGENTS.md`、`FORGE.md` 和 `.forge/rules/*.md`；
- [x] 记忆使用 `.forge/memory/MEMORY.md` 索引和带 YAML frontmatter 的独立 Markdown 文件；
- [x] 支持 `user`、`feedback`、`project`、`reference` 四类记忆；
- [x] 使用确定性关键词相关度选择最多 5 条记忆，单条最多 4 KB、总计最多 20 KB；
- [x] 相关记忆仅注入当前模型请求，不写入会话历史；
- [x] `/remember name | content`、`/memory list/show/forget/rebuild/consolidate` 提供可解释的管理入口；
- [x] 用户输入“记住：...”时自动保存显式事实；疑似 API Key、Token、密码和私钥的内容拒绝写入；
- [x] 记忆达到 10 条后自动整理完全重复的内容并重建索引。

当前目标与可选任务计划：

- [x] 每次用户请求都会建立内存中的 `ActiveTask`，并把原始目标注入该轮每一次模型请求；
- [x] `ActiveTask` 独立于可压缩的消息历史，长工具链和上下文压缩不会丢失当前目标；
- [x] 简单问答、单次读取和小修改不创建计划文件；
- [x] 复杂任务可由模型调用 `task_plan` 创建步骤，并用 `task_update` 记录当前步骤和验证依据；
- [x] 只有显式计划的复杂任务写入 `.forge/tasks/`，可通过任务历史查看和恢复；
- [x] Completion Gate 拒绝结束时会同时重申原始目标，避免模型回到更早的会话问题；
- [x] 工具参数错误会返回允许参数、必填参数和未知参数，帮助模型直接修正下一次调用。

结构化工作证据与只读任务收敛：

- [x] `WorkingState` 独立记录已读取文件、工作区版本、总行数、已覆盖行区间、目录列表和近期失败；
- [x] WorkingState 能识别相同文件和 revision 中已覆盖的行范围；相邻或重叠片段可以按行合并，重复请求只返回覆盖范围短引用，不重新访问磁盘或重复注入源码；
- [x] 文件发生变化并产生新的 `workspace_revision` 后允许重新读取；
- [x] 新文件、未覆盖行、搜索结果、工作区变化、计划推进和验证结果才算有效进展；空 Git 结果以及已读取文件被 `find_files` 再次发现不会重复计为进展；
- [x] 纯读取任务同样受无进展保护，不再依赖是否尝试修改文件；
- [x] 普通无进展任务进入恢复检查点时保留完整工具集；Edit Recovery 每次失败写入后仅开放一次 `read_file` 或 `grep`，随后只保留能产生真实 revision 的写入工具；
- [x] 写入失败后的恢复预算独立于只读证据新颖性；缓存回放不注入源码，模型不能再通过改变读取范围、搜索词或目录参数无限延长失败编辑；
- [x] 工具参数/Schema 错误使用独立的协议恢复反馈，不计入任务语义停滞；
- [x] 当前修改和当前验证满足 Completion Gate 后使用独立收敛计数；新的只读证据不能再把已完成候选无限延长，已审阅的缓存 Diff 也不能重复续命；
- [x] Anthropic 兼容接口遗漏流式 Delta 时从最终消息恢复文本或 Tool Call；真正的空响应作为 `empty_model_response` 自动恢复，而不是立即结束任务；
- [x] Provider 的 `stop_reason` 写入 JSONL 轨迹，便于区分正常结束、工具调用、Token 截断和兼容接口异常；
- [x] 区分 `blocked` 与 `stuck`：前者只表示需要用户或外部条件，后者表示 Agent 行为循环；
- [x] 被阻塞或卡住的任务收到后续指令时保留原始目标，避免“你直接修复”覆盖问题上下文。

当前有意保持简单：不使用向量数据库、Embedding、全局/云端记忆或额外子 Agent。项目规则是模型上下文，不代替 M3 的强制权限控制。

### 验收条件

- [x] 自动测试构造 20 轮工具调用，压缩后所有 `tool_use` 与 `tool_result` 仍严格配对；
- [x] 大型工具输出可从落盘文件恢复，原始会话对象不被廉价压缩修改；
- [x] 结构化摘要保留目标、限制、修改文件、失败尝试和验证依据；
- [x] 新建 MemoryStore（模拟进程重启）后仍能读取已有记忆；
- [x] 无关记忆不会注入当前请求，敏感内容不会落盘；
- [x] 全部功能使用 FakeModelClient 和临时仓库测试，不依赖真实 API。

### Slash Command

ForgeCode 在交互终端中提供 Slash Command，用于执行不需要交给模型处理的本地操作。

输入 `/` 后，终端会显示可用命令、参数格式和中文说明。继续输入命令前缀可以过滤候选，例如输入 `/mem` 只显示 Memory 相关命令。使用上下方向键选择候选，通过 Tab 或 Enter 完成补全。普通自然语言输入不会触发命令菜单，也不会受到补全功能影响。

| 命令 | 作用 | 是否调用模型 |
| --- | --- | --- |
| `/context` | 查看 System、仓库上下文、工具 Schema、历史、预留输出和预计剩余窗口 | 否 |
| `/compact` | 立即把当前会话压缩为结构化任务摘要 | 是 |
| `/task` | 查看当前目标、状态和可选计划进度 | 否 |
| `/task history` | 列出 `.forge/tasks/` 中保存的复杂任务 | 否 |
| `/task resume task-id` | 恢复一个已保存的复杂任务 | 否 |
| `/remember name \| content` | 将一条知识写入当前仓库的持久化记忆 | 否 |
| `/memory list` | 列出当前仓库中的全部记忆 | 否 |
| `/memory show name` | 查看指定记忆的描述和完整内容 | 否 |
| `/memory forget name` | 删除指定记忆并更新索引 | 否 |
| `/memory rebuild` | 根据记忆文件重新生成 `MEMORY.md` 索引 | 否 |
| `/memory consolidate` | 删除内容完全相同的重复记忆并重建索引 | 否 |

使用示例：

```text
/context
/compact
/remember testing | 项目测试命令是 uv run pytest
/memory show testing
/memory forget testing
```

`/remember` 使用竖线分隔记忆名称和内容。记忆保存在当前仓库的 `.forge/memory/` 下，可以直接通过 Markdown 文件检查和编辑。疑似包含 API Key、Token、密码或私钥的内容会被拒绝写入。

`/context` 使用字符数近似估算 Token，同时展示本地保存的完整历史，以及经过大型工具结果落盘、旧工具结果缩短和中间消息裁剪后的实际请求历史。System Prompt、仓库规则/相关 Memory 和工具 Schema 会计入实际请求。预计剩余量按照下面的方式计算：

```text
预计剩余 = MODEL_CONTEXT_WINDOW - 预计输入 - MODEL_MAX_TOKENS
```

自动压缩在 Agent Loop 的每一次模型调用前使用相同的请求口径重新检查：System Prompt、ActiveTask、WorkingState、仓库规则/相关 Memory、工具 Schema、廉价压缩后的消息历史以及 `MODEL_MAX_TOKENS` 预留输出之和，达到 `MODEL_CONTEXT_WINDOW` 的 80% 时生成结构化任务摘要。未配置模型窗口时，使用 120,000 个历史字符作为兜底阈值；Provider 仍明确报告上下文溢出时，会强制压缩并恢复请求一次。

利用率按照“预计输入 + 预留输出”占总窗口的比例显示，与 80% 自动压缩阈值使用同一口径。工具结果已经包含在历史中，只单独显示大小而不会重复计入总量。统计不包含用户下一条尚未输入的 Prompt。终端中的 `last request` 是最近一次模型调用的真实 usage，`turn cumulative` 是当前用户回合所有模型调用的累计消耗，不能把累计值误认为单次上下文大小；累计输入达到默认 500,000 Token 后，Agent Loop 会停止当前回合。由于不同模型的分词方式不同，Provider 返回的真实 `input_tokens` 仍是请求完成后的最终依据；未配置 `MODEL_CONTEXT_WINDOW` 时，ForgeCode 会把剩余量显示为 `unavailable`。

## M6：Hooks、MCP 与子 Agent 扩展

### 目标

在核心运行时稳定后建立扩展机制，并保证扩展能力仍受统一的权限、事件和评测体系管理。

### 6.1 Hooks

生命周期事件：

- [x] `SessionStart`
- [x] `BeforeModelCall`
- [x] `PreToolUse`
- [x] `PostToolUse`
- [x] `BeforeFileEdit`
- [x] `AfterFileEdit`
- [x] `BeforeCompact`
- [x] `AfterVerification`
- [x] `SessionEnd`

Hook 配置从用户级 `~/.forge/settings.json` 和项目级 `.forge/settings.json` 加载，按用户配置在前、项目配置在后的顺序合并。每个 Hook 使用可选的工具和仓库相对路径 Glob 进行匹配；命令必须写成参数数组，并通过无 Shell 的子进程执行。命令在仓库根目录运行，通过 stdin 接收事件 JSON，同时可以读取 `FORGE_HOOK_EVENT`、`FORGE_SESSION_ID`、`FORGE_TOOL_NAME` 和 `FORGE_FILE_PATH` 环境变量。`{event}`、`{session_id}`、`{tool}` 和 `{path}` 可以作为完整命令参数中的占位符。

```json
{
  "hooks": {
    "AfterFileEdit": [
      {
        "id": "format-python",
        "matcher": {"paths": ["**/*.py"]},
        "command": ["uv", "run", "ruff", "format", "{path}"],
        "timeout_seconds": 30
      }
    ]
  }
}
```

退出码 `0` 表示放行，退出码 `2` 表示明确拒绝，其余非零退出码表示执行错误。Hook 也可以在 stdout 返回 JSON，通过 `decision` 放行或拒绝，通过 `updated_arguments` 修改工具参数，通过 `additional_context` 注入上下文，并用 `reason` 解释决策。

```json
{
  "decision": "allow",
  "updated_arguments": {"path": "src/app.py"},
  "additional_context": "该文件由代码生成器维护。",
  "reason": ""
}
```

`PreToolUse` 和 `BeforeFileEdit` 修改后的参数会重新经过工具参数校验、重复调用检查、Checkpoint 和实际执行；拒绝会转成模型可见的 `hook_denied` 工具结果。`SessionStart` 或工具后置 Hook 注入的上下文只进入下一次模型请求，`BeforeModelCall` 注入的内容只进入当前请求，不会污染持久化的原始消息历史。

操作前 Hook 在超时、非法 JSON、启动失败或非零错误退出时采用 fail-closed；操作后 Hook 无法撤销已经完成的操作，因此记录错误但保持原操作结果。命令输出总量限制为 1 MB，审计预览限制为 2000 个字符。每次执行都会以 `hook_execution` 事件写入当前 Session JSONL，记录事件、耗时、决策、退出码、参数是否修改和有限输出预览，不保存完整模型上下文或完整工具参数。

项目 Hook 等同于在本机执行项目提供的程序。只应在可信仓库中启用或提交 `.forge/settings.json`，并避免在 Hook 输出中打印密钥和其他敏感信息。

已覆盖编辑 Python 后格式化、禁止修改特定目录、测试通过后通知，以及通过匹配 `finish_task` 在完成前运行安全扫描等场景。

### 6.2 MCP Client

ForgeCode 将作为 MCP Host 管理 MCP Client，而不是把 MCP 简化成普通 HTTP 调用。

- [ ] 实现 MCP Client Manager；
- [ ] 支持 stdio Transport 和 Streamable HTTP；
- [ ] 支持初始化、能力协商、`tools/list` 和 `tools/call`；
- [ ] 支持 Tool 变化通知、超时和断线恢复；
- [ ] 将 MCP Tool 映射到统一 Tool Registry；
- [ ] 让 MCP 工具经过 ForgeCode 权限系统；
- [ ] 在日志中标记工具来源；
- [ ] 首版只连接一个本地示例 Server；
- [ ] 首版不自行实现 OAuth 和完整的远程认证体系。

### 6.3 Explore Subagent

首个子 Agent 只负责只读仓库探索，可使用 `list_directory`、`find_files`、`grep`、`read_file` 和 `git_log`。

- [ ] 使用独立上下文和独立 Token Budget；
- [ ] 设置最大执行轮数；
- [ ] 仅拥有只读权限，不能修改文件；
- [ ] 只返回相关文件、调用路径、根因假设、建议修改点和不确定问题的结构化摘要；
- [ ] 由主 Agent 决定是否采纳结论；
- [ ] 比较启用前后的主 Agent 上下文消耗。

### 验收条件

- Hook 能在编辑后自动触发，并能阻止一个禁止操作；
- 至少接入一个 MCP Server，且其工具接受统一权限治理；
- Explore Agent 能完成只读仓库调查；
- 主 Agent 上下文 Token 明显少于直接探索方案；
- 加入扩展能力时不需要大幅修改 Agent Loop。

## M7：Benchmark、消融实验与 v1.0

### 目标

把 ForgeCode 从功能 Demo 发展为有可复现数据支撑的工程项目。

### 评测顺序

```text
3 个 Fixture
→ 10 个自建任务
→ 30～50 个固定任务
→ 少量 SWE-bench Lite
→ 有能力后再继续扩展
```

### Benchmark Harness

- [ ] 每个任务固定基础 Commit；
- [ ] 每次运行创建独立工作目录；
- [ ] 提供可选 Docker 执行后端，但不作为 ForgeCode 安装或运行依赖；
- [ ] 默认关闭网络；
- [ ] 设置时间、Token 和费用上限；
- [ ] Agent 无法查看隐藏测试，结束后再注入隐藏测试；
- [ ] 保存最终 Patch、完整事件轨迹和容器日志；
- [ ] 同一任务支持重复运行；
- [ ] 自动分类失败原因；
- [ ] 支持多个模型或策略配置。

结果格式：

```json
{
  task_id: ",
  resolved: true,
  public_tests_passed: true,
  hidden_tests_passed: true,
  iterations: 12,
  tool_calls: 31,
  repeated_tool_calls: 2,
  input_tokens: 45000,
  output_tokens: 8200,
  duration_seconds: 260,
  permission_prompts: 3,
  failure_category: null
}
```

### 核心指标

效果指标：

- Task Success Rate；
- Hidden Test Pass Rate；
- 编译成功率；
- 回归失败率；
- 假完成率；
- Pass@1。

效率指标：

- 平均工具调用次数和重复工具调用率；
- 平均迭代次数；
- Token 消耗、完成时间和模型成本。

安全指标：

- 危险操作尝试数和拦截率；
- 权限误拦截率；
- 仓库外访问次数；
- Checkpoint 恢复成功率。

### 消融实验

| 对比 | 主要验证内容 |
| --- | --- |
| 无 Completion Gate vs 有 Gate | 是否降低假完成率 |
| 完整历史 vs 结构化 Memory | 是否降低 Token 消耗和重复探索 |
| 全量工具 vs 动态工具加载 | 是否降低工具描述 Token 和误调用 |
| 无 Explore Agent vs 有 Explore Agent | 是否降低主上下文占用 |
| 无 Checkpoint vs 有 Checkpoint | 失败恢复能力 |
| 无项目规则 vs 有项目规则 | 规则遵循率 |
| 单模型 vs 模型路由 | 成本与成功率权衡 |

至少完成其中四组消融实验。

### v1.0 验收条件

- [ ] 能在多个真实仓库中工作；
- [ ] 能完成 Bug 修复和测试验证；
- [ ] 具备权限和沙箱边界；
- [ ] 支持 Resume、Undo 和 Replay；
- [ ] 具备结构化上下文管理；
- [ ] 支持 Hooks；
- [ ] 支持至少一种 MCP Transport；
- [ ] 支持一个只读 Explore Agent；
- [ ] 拥有 30 个以上固定评测任务；
- [ ] 完成至少四组消融实验；
- [ ] README 中的所有性能数字均可复现；
- [ ] 提供完整架构图和演示录像。

## 成功标准

ForgeCode 的成功不以“模型输出了一个看起来合理的答案”衡量，而以以下事实为依据：

1. Agent 是否理解并遵守任务边界；
2. 是否对真实仓库产生必要且受控的修改；
3. 是否执行了构建、测试或其他验证命令；
4. 验证是否通过，且没有修改禁止路径或弱化测试；
5. 最终 Patch、执行轨迹和评测结果是否可以复现；
6. 遇到失败、中断或高风险操作时，系统是否能够安全停止、恢复或回滚。

在这些条件有证据支持之前，ForgeCode 不应把任务标记为成功。
