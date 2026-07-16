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

当前基线采用 Python 3.12、uv、Typer、pytest 和 Docker。Rich 与 Pydantic 将在 M1 源码开始直接使用时加入直接依赖，SQLite 使用 Python 标准库并在 M4 接入。模型层定义统一的 `ModelClient` 接口，首版只接入一个模型 Provider，后续再扩展多模型或模型路由。

### 本地开发

项目使用 uv 管理 Python、虚拟环境、依赖和锁文件。首次进入仓库后执行：

    uv sync
    uv run pytest
    uv run forge --help

.python-version 将开发环境固定到 Python 3.12，pytest 位于 dev 依赖组中并由 uv 默认同步。CI 或可复现检查应使用：

    uv lock --check
    uv run --frozen pytest

### Docker 基线

Docker 用于复现 ForgeCode CLI 和三个 M0 Fixture 的构建、运行环境。默认公共测试镜像不会复制 `tests/hidden`；评测端需要隐藏测试时，应显式构建各 Fixture Dockerfile 的 `evaluation` 阶段。此处只提供 M0 环境基线，命令权限、资源限制和真正的安全沙箱将在 M3 实现。

安装并启动 Docker Desktop 后，执行完整基线验证：

    python scripts/verify_m0_docker.py

验证脚本会构建所有镜像，检查 `forge --help` 正常退出，并确认三个 Fixture 的基础状态都因预设 Bug 在对应公开测试处失败。Fixture 返回非零退出码是预期结果；脚本还会匹配失败测试名称，避免把依赖缺失或命令不存在误判成成功复现。

也可以分别构建或运行服务：

    docker compose build
    docker compose run --rm forge-cli
    docker compose run --rm python-calculator
    docker compose run --rm typescript-todo
    docker compose run --rm java-order-service

镜像运行阶段默认关闭网络，依赖只在镜像构建阶段下载。API Key 不会写入镜像，也不是 M0 验证所必需的。

### 模型接口基线

首个 Model Provider 确定为 Anthropic。ForgeCode 直接复用官方 Python SDK 的 Message、MessageParam 和 ToolParam 类型，只保留最小异步 ModelClient Protocol，以及调用 AsyncAnthropic.messages.create 的轻量适配器。

模型名称和最大输出 Token 通过适配器构造参数配置，API Key 由 Anthropic SDK 默认从 ANTHROPIC_API_KEY 环境变量读取。CLI 目前尚未调用模型；流式输出、重试和错误映射在 M1 实现。

## 项目结构

```text
forge-code/
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

```text
M0  项目基线与评测样例
 ↓
M1  最小可用 Agent Loop
 ↓
M2  可靠的代码修改与验证闭环
 ↓
M3  权限、安全与代码回滚
 ↓
M4  会话持久化与任务恢复
 ↓
M5  上下文工程与仓库记忆
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
  - [x] 完成 Docker 等其余技术基线配置；
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

### 任务

Model Client：

- [ ] 定义统一的模型请求和响应类型；
- [ ] 支持文本流式输出、Tool Calling 和单次返回多个 Tool Call；
- [ ] 记录输入/输出 Token 和模型调用耗时；
- [ ] 处理超时、限流、格式错误和有限次数重试。

```python
class ModelClient(Protocol):
    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> ModelResponse:
        ...
```

Tool 抽象：

- [ ] 定义统一工具接口，使用 Pydantic 校验输入；
- [ ] 统一返回 `success`、`summary`、`content`、`error` 和 `metadata`；
- [ ] 工具失败时返回结构化错误，不使 Agent 进程崩溃；
- [ ] 实现 `list_directory`、`find_files`、`read_file`、`grep`、`apply_patch`、`run_command`、`git_status` 和 `git_diff`；
- [ ] `read_file` 支持行范围，`grep` 支持路径和文件类型过滤；
- [ ] `run_command` 返回退出码、stdout、stderr 和耗时；
- [ ] `apply_patch` 返回真实修改结果。

Agent Loop 与 CLI：

- [ ] 初始化 System Prompt，将工具 Schema 提供给模型；
- [ ] 执行 Tool Call 并将结果反馈给模型；
- [ ] 支持最大循环次数、最大 Token 预算和 `Ctrl+C` 中断；
- [ ] 检测完全相同的重复工具调用；
- [ ] 保存完整 JSONL 执行轨迹；
- [ ] 支持 `forge` 和 `forge -p 修复当前失败的测试`；
- [ ] 终端显示模型意图、工具及参数摘要、命令退出码、文件修改、最终测试结果和 Token 使用。

### 验收条件

至少完成一个 Fixture 的完整链路：发现失败测试 → 搜索并读取代码 → 修改代码 → 运行测试 → 测试通过 → 输出 Git Diff。

- 至少一个 Bug 被真实修复；
- Agent 至少执行一次测试；
- 最终 Diff 非空；
- 全过程有 JSONL 轨迹；
- 无需人工直接指出要修改的文件。

## M2：可靠的代码修改与验证闭环

### 目标

解决“模型说完成了，但代码实际上不能用”的问题，让验证成为任务完成的必要条件。

### 任务

Repository Discovery：

- [ ] 启动时识别 Git 根目录、当前 Git 状态和文件树；
- [ ] 识别主要语言、README、项目指令、构建系统、测试框架和 CI 配置；
- [ ] 识别 `package.json`、`pyproject.toml`、`pom.xml`、`build.gradle`、`go.mod`、`Cargo.toml`、`Makefile`、`Dockerfile` 和 `.github/workflows/*`；
- [ ] 生成包含语言、构建文件、可能的测试/检查命令、指令文件和 Git 状态的 `RepositoryProfile`。

错误分类：

- [ ] 区分命令不存在、环境或依赖缺失、编译失败、测试断言失败、测试超时和权限失败；
- [ ] 区分 Patch 应用失败和 Agent 引入回归；
- [ ] 将长错误输出压缩为包含分类、命令、失败测试、关键行和退出码的结构化摘要。

Completion Gate：

- [ ] 检查是否产生代码修改、运行验证命令且验证成功；
- [ ] 检查未处理的工具错误、Git Diff 和禁止路径修改；
- [ ] 未执行测试时要求模型继续验证；
- [ ] 测试失败时拒绝将任务标记为成功；
- [ ] 允许声明无法完成，但必须给出原因；
- [ ] 最终状态区分 `completed`、`partially_completed`、`blocked` 和 `failed`。

Patch 质量检查：

- [ ] 检查修改文件数和 Diff 行数；
- [ ] 检查是否删除或弱化测试；
- [ ] 检查大范围无关修改、新增依赖和生成文件修改；
- [ ] 检查未跟踪的大文件。

### 验收条件

- 在三个 Fixture 中至少成功完成两个；
- 测试全部通过，且未修改隐藏测试；
- Agent 至少经历一次真实执行反馈；
- 模型声明完成但测试失败时，系统能够拒绝结束；
- 最终报告列出明确的验证依据；
- 开始记录任务成功率、测试通过率、假完成率、工具调用次数、重复工具调用次数和平均 Token。

## M3：权限、安全与代码回滚

### 目标

把 ForgeCode 从“能够运行”提升为“能够在真实仓库中谨慎运行”。权限决定操作是否允许，沙箱在操作系统层限制 Shell 及其子进程。

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

达到 M3 后，ForgeCode 才进入较大真实仓库的试用阶段。

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

### 目标

解决长任务中上下文持续膨胀、Agent 遗忘任务目标、重复读取文件和重复执行命令的问题。

### 任务

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

Token Budget：

- [ ] 统计各类上下文的 Token；
- [ ] 分别限制文件、日志、历史和工具描述的预算；
- [ ] 避免重复注入相同文件，并按行范围加载文件；
- [ ] 命令输出只保留开头、结尾、错误附近和结构化摘要；
- [ ] 旧 Diff 只保留摘要与引用；
- [ ] 文件更新后使上下文中的旧版本失效。

结构化 Task Memory：

```json
{
  goal: ",
  plan: [],
  confirmed_facts: [],
  open_questions: [],
  modified_files: [],
  failed_attempts: [],
  verification: {},
  next_action: "
}
```

- [ ] 压缩时强制保留目标、失败尝试、未解决问题和验证状态；
- [ ] 压缩后重新注入项目规则；
- [ ] 实现 `/context` 和 `/compact`；
- [ ] 支持自动压缩。

项目指令与仓库记忆：

- [ ] 支持 `AGENTS.md`、`FORGE.md`、`.forge/rules/*.md` 和 `.forge/memory.md`；
- [ ] 启动时加载根目录规则，按需加载子目录规则；
- [ ] 支持按路径设置规则 Scope，并检测冲突规则；
- [ ] 分离项目规则与个人规则；
- [ ] Memory 可以查看、编辑和删除；
- [ ] 项目指令作为模型上下文，强制安全限制仍由权限系统或 Hook 执行。

### 验收条件

设计一个至少需要十几轮工具调用的固定任务，对比以下三种策略：

- 无压缩；
- 普通历史摘要；
- 结构化 Task Memory。

记录成功率、Token 消耗、重复文件读取、重复命令调用、压缩后目标遗忘次数和平均完成轮数。这组实验将作为 M5 的核心成果。

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
- [ ] 每次运行创建独立目录或容器；
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
