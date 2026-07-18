# ForgeCode

> ForgeCode 不是“代码问答工具”，而是一个运行在终端中的通用 Agent Harness：模型负责决策，运行时负责工具、上下文、权限、执行、恢复和评测。

ForgeCode 面向真实代码仓库中的长链路工程任务。它不把模型的一句“已经完成”视为成功，而是通过工具执行、测试反馈、权限控制、变更检查和可复现评测，客观判断任务是否真正完成。

当前项目处于早期开发阶段。本 README 描述 ForgeCode 的系统边界、首版目标和从 M0 到 v1.0 的实现路线。

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
- **可恢复而非重来**：会话状态和代码 Checkpoint 分开保存，支持恢复、回放和回滚。
- **用评测驱动迭代**：每个里程碑都有可复现任务、明确验收条件和量化指标。

## 技术基线

当前基线采用 Python 3.12、uv、Typer、Rich、Pydantic 和 pytest，并默认直接在用户本机运行，不要求安装 Docker。Rich 用于交互终端，Pydantic 用于工具输入 Schema 和运行时校验，SQLite 使用 Python 标准库并在 M4 接入。模型层定义统一的 `ModelClient` 接口，首版只接入一个模型 Provider，后续再扩展多模型或模型路由。

### 本地开发

项目使用 uv 管理 Python、虚拟环境、依赖和锁文件。首次进入仓库后执行：

    uv sync
    uv run pytest
    uv run forge --help

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
    ANTHROPIC_BASE_URL=https://api.anthropic.com

完成后检查配置：

    uv run forge config

启动交互式会话：

    uv run forge

启动后可以连续输入消息，每一轮都会携带当前会话的历史上下文；按 `Ctrl+C` 退出。终端支持直接粘贴包含换行的多行 Prompt，粘贴内容会作为一条完整消息提交。模型文本按照 Provider 的 delta 实时显示，Token 根据流中实际返回的 usage 事件刷新；如果使用了 Prompt Cache，还会显示缓存读写 Token。

`.env` 已被 Git 忽略，仓库只提交不含真实凭据的 `.env.example`。系统环境变量优先于 `.env` 中的同名配置。`ANTHROPIC_API_KEY` 和 `MODEL_ID` 必填；`ANTHROPIC_BASE_URL` 可以省略，默认使用 `https://api.anthropic.com`。`MODEL_MAX_TOKENS` 可选，默认 `8192`，允许范围为 `1024～32768`。`MODEL_CONTEXT_WINDOW` 可选，必须根据当前 Provider 和模型文档填写，并且大于 `MODEL_MAX_TOKENS`；ForgeCode 不猜测第三方兼容模型的窗口大小。`forge config` 显示 Model ID、Base URL、最大输出 Token、上下文窗口和密钥配置状态，但不会回显 API Key。交互模式的每轮消息都会发起真实 API 请求，可能产生 Provider 费用。

## 项目结构

```text
forge-code/
├── .env.example
├── pyproject.toml
├── README.md
├── forge/
│   ├── cli.py
│   ├── config.py
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
- [x] 协议错误和输出截断最多自动恢复两次，失败响应中的工具一律不执行；
- [x] `TurnResult` 同时支持文本和工具调用，纯工具调用也是合法模型响应；
- [x] 将完成的工具调用保存为 Anthropic `tool_use` 助手消息，为后续执行循环提供上下文。

M1.2 只完成“模型表达要调用什么工具”的协议层。

M1.3 建立可独立测试的本机工具层：

- [x] 使用 Pydantic 模型生成工具 Schema，并在执行前拒绝未知参数和无效类型；
- [x] 统一使用 `ToolResult(success, summary, content, error, metadata)` 返回结果；
- [x] 参数错误、路径错误、命令失败和工具内部异常均转换为结构化失败；
- [x] 使用 `ToolRegistry` 注册工具、导出确定顺序的 Schema 并处理未知工具名；
- [x] 所有路径型工具以显式仓库根目录为边界，拒绝绝对路径和 `..` 越界；
- [x] 实现 `list_directory`、`find_files`、`read_file`、`grep`、`write_file`、`replace_text`、`apply_patch`、`run_command`、`verify`、`git_status` 和 `git_diff`；
- [x] `write_file` 使用原子替换创建或覆盖 UTF-8 小文件，内容最多 8000 字符；
- [x] `replace_text` 只替换恰好出现一次的精确文本，新旧文本分别最多 8000 字符；
- [x] `apply_patch` 的 Patch 参数硬限制为 8000 字符，超限在执行前返回参数错误；
- [x] `apply_patch` 先运行 `git apply --check`，成功后真实修改文件并返回工作区状态；
- [x] `run_command` 返回退出码、stdout、stderr、耗时和超时状态，并拒绝明显的脚本写文件和输出重定向；
- [x] 工具通过 `read_only`、`workspace_write` 和 `process` Effect 描述工作区影响。

M1.3 的工具已经能够被运行时独立调用，但当时尚未接入交互 CLI。

M1.4 将模型决策和内置工具连接成真正的执行循环：

- [x] 交互 CLI 根据当前工作目录创建默认工具注册表，并把 11 个工具 Schema 提供给模型；
- [x] 模型返回 Tool Call 后，运行时按响应顺序执行一个或多个工具；
- [x] 将统一 `ToolResult` 序列化为 Anthropic `tool_result` 用户消息，并通过 `is_error` 标记失败；
- [x] 工具成功或失败后再次调用模型，直到模型不再请求工具并返回最终文本；
- [x] 一次用户请求内累计所有模型调用的输入、输出和缓存 Token；
- [x] 保存完整的 `user → assistant(tool_use) → user(tool_result) → assistant(final)` 会话上下文；
- [x] 终端按事件时间线内联显示模型文本与工具组，并展示工具名称、参数摘要、成功或失败状态和结果摘要；
- [x] 默认最多执行 100 次模型调用，超过上限时安全停止。

M1.4 已形成可执行的 Agent Loop，但命令审批、敏感路径治理和更强的本机安全限制仍属于 M3；当前 Shell 写入检查只是行为护栏，不是安全沙箱。ForgeCode 当前也没有浏览器、截图或视觉理解能力，网页游戏只能进行源码、命令和测试层面的验证。重复工具调用检测仍是完整 M1 的后续任务。

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
- [x] 实现 `list_directory`、`find_files`、`read_file`、`grep`、`write_file`、`replace_text`、`apply_patch`、`run_command`、`verify`、`git_status` 和 `git_diff`；
- [x] `read_file` 支持行范围，`grep` 支持路径和文件类型过滤；
- [x] `run_command` 返回退出码、stdout、stderr 和耗时；
- [x] `apply_patch` 返回真实修改结果。

Agent Loop 与 CLI：

- [x] 初始化 System Prompt，将工具 Schema 提供给模型；
- [x] 执行 Tool Call 并将结果反馈给模型；
- [x] 支持最大循环次数和 `Ctrl+C` 中断；
- [ ] 支持最大 Token 预算；
- [ ] 检测完全相同的重复工具调用；
- [ ] 保存完整 JSONL 执行轨迹；
- [x] 支持 `forge` 进入循环交互会话；
- [ ] 终端显示模型意图、工具及参数摘要、命令退出码、文件修改、最终测试结果和 Token 使用。

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
- [x] 保留用户原有修改，不把它们误认为 Agent 本次产生的修改。

#### M2.2：专用验证工具

- [x] 新增 `verify` 工具，与普通 `run_command` 明确区分；
- [x] 复用现有命令执行能力，记录验证命令、退出码、耗时和执行时的 `workspace_revision`；
- [x] 只有退出码为零的 `verify` 调用才能成为成功验证证据；
- [x] 验证后如果代码再次变化，旧验证自动失效。

#### M2.3：完成检查

- [x] 模型准备结束代码任务时，检查是否真的产生了修改；
- [x] 检查当前工作区版本是否有成功的验证证据；
- [x] 测试失败、没有验证或验证已经过期时拒绝结束；
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
- Token 预算和重复工具调用检测暂不实现。

### 验收条件

- [x] ForgeCode 能发现模型和 Shell 命令修改了哪些文件；
- [x] 测试失败、没有验证或验证已过期时，系统能够拒绝结束；
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

### 目标

让任务不会因为进程退出而丢失，并明确区分“会话恢复”和“代码 Checkpoint”。

### 任务

事件模型采用 append-only 设计：

- [ ] 实现 `SessionStarted`、`UserMessageReceived` 和 `ModelResponded`；
- [ ] 实现 `ToolRequested`、`PermissionEvaluated` 和 `ToolCompleted`；
- [ ] 实现 `FileModified`、`VerificationExecuted` 和 `CheckpointCreated`；
- [ ] 实现 `ContextCompacted`、`TaskCompleted` 和 `SessionStopped`；
- [ ] 每个事件包含时间、会话 ID 和序号；
- [ ] 大型工具输出单独保存，事件中只保留引用；
- [ ] 敏感值自动脱敏，事件只追加且不被静默覆盖。

存储与命令：

- [ ] SQLite 保存会话元数据、任务状态和指标；
- [ ] JSONL 保存完整事件轨迹；
- [ ] `files/` 保存大日志、Patch 和快照；
- [ ] 支持 `forge --continue`、`forge --resume <id>` 和 `forge sessions`；
- [ ] 支持 `/rename`、`/history`、`/status`、`/diff`、`/replay` 和 `/branch`。

恢复校验：

- [ ] 检查当前工作目录、Git 仓库、基础 Commit、当前 Branch 和文件哈希；
- [ ] 检测用户是否在外部修改代码；
- [ ] 不一致时允许选择使用当前文件、恢复 Checkpoint、创建新分支或取消恢复。

### 验收条件

- 任务运行中强制结束进程后，可通过 `forge --continue` 恢复；
- 不重复执行已经成功的工具调用；
- 可以查看恢复前的计划、修改和测试结果；
- 可以从旧会话创建新分支尝试不同方案。

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
- [x] 中段历史超过预算后裁剪，并把 `tool_use` 与对应 `tool_result` 作为不可拆分单元；
- [x] 旧工具结果缩为占位符，保留最近 3 个完整结果；
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

`/context` 使用字符数近似估算 Token，并分别展示 System Prompt、仓库规则/相关 Memory、工具 Schema 和已提交会话历史。预计剩余量按照下面的方式计算：

```text
预计剩余 = MODEL_CONTEXT_WINDOW - 预计输入 - MODEL_MAX_TOKENS
```

自动压缩使用相同的请求口径：System Prompt、仓库规则/相关 Memory、工具 Schema、廉价压缩后的消息历史以及 `MODEL_MAX_TOKENS` 预留输出之和，达到 `MODEL_CONTEXT_WINDOW` 的 80% 时生成结构化任务摘要。未配置模型窗口时，使用 120,000 个历史字符作为兜底阈值；Provider 仍明确报告上下文溢出时，会强制压缩并恢复请求一次。

工具结果已经包含在会话历史中，只单独显示大小而不会重复计入总量。统计基于最近一次模型请求快照，不包含用户下一条尚未输入的 Prompt。由于不同模型的分词方式不同，Provider 返回的真实 `input_tokens` 仍是请求完成后的最终依据；未配置 `MODEL_CONTEXT_WINDOW` 时，ForgeCode 会把剩余量显示为 `unavailable`。

## M6：Hooks、MCP 与子 Agent 扩展

### 目标

在核心运行时稳定后建立扩展机制，并保证扩展能力仍受统一的权限、事件和评测体系管理。

### 6.1 Hooks

生命周期事件：

- [ ] `SessionStart`
- [ ] `BeforeModelCall`
- [ ] `PreToolUse`
- [ ] `PostToolUse`
- [ ] `BeforeFileEdit`
- [ ] `AfterFileEdit`
- [ ] `BeforeCompact`
- [ ] `AfterVerification`
- [ ] `SessionEnd`

Hook 能够放行或拒绝操作、修改工具参数、注入额外上下文、执行外部命令并记录审计事件。计划覆盖编辑 Python 后格式化、禁止修改特定目录、测试通过后通知和提交前安全扫描等场景。

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
