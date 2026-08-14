# ForgeCode 技术实现报告

> 本报告基于当前仓库源码进行静态审阅，覆盖 `forge/` 下全部产品代码、`evals/` 评测框架、项目配置以及 `tests/` 的测试组织与用例索引。`play/` 是 ForgeCode 的真实任务验收场，不属于产品运行时实现，因此只作为端到端验证背景，不作为架构模块展开。

## 1. 项目目标与实现边界

ForgeCode 是一个运行在终端中的 Agent Harness。它不是把用户问题直接转发给模型的聊天壳，而是在模型和本地仓库之间加入一层可验证、可恢复、可审计的执行运行时。项目元数据将命令入口注册为 `forge = forge.cli:app`，运行环境为 Python 3.12，核心依赖包括 Anthropic SDK、Pydantic、Typer、Rich、prompt-toolkit 与 MCP SDK，见 [`pyproject.toml`](pyproject.toml#L5) 和 [`pyproject.toml`](pyproject.toml#L21)。

系统的职责边界可以概括为：

```text
用户输入
  → 语义路由（判断对话、读取、变更、继续任务及验证需求）
  → 主模型决策（回答或生成结构化 ToolCall）
  → 阶段工具过滤、权限与 Hook
  → 本地工具/MCP 工具执行
  → 工作区 revision、任务状态与验证证据更新
  → Completion Gate 判断是否允许完成
  → 会话、轨迹和 Checkpoint 持久化
```

关键设计不是“尽量让模型成功”，而是把模型不应拥有的决定权留在确定性运行时中：模型不能绕过仓库路径边界，不能把旧 revision 的测试当作当前代码的证据，不能用一句“完成了”代替真实 Diff，也不能自动重放结果未知的外部副作用。

## 2. 总体架构

### 2.1 模块分层

| 层次 | 主要目录/文件 | 职责 |
| --- | --- | --- |
| 终端与装配 | `forge/cli.py`、`forge/terminal.py` | CLI 生命周期、Slash Command、流式渲染、运行时依赖装配 |
| Agent 运行时 | `forge/runtime/` | Agent Loop、模型边界、意图路由、工作区追踪、完成门禁、事件模型 |
| 工具系统 | `forge/tools/` | 文件、搜索、Patch、Shell、Git、验证、任务与完成声明 |
| 任务系统 | `forge/tasks/` | ActiveTask、计划步骤、作用域、进度证据和持久化 |
| 上下文系统 | `forge/context/` | WorkingState、历史压缩、仓库规则、长期记忆和请求预算 |
| 安全扩展 | `forge/permissions/`、`forge/hooks/` | 风险分类、审批规则、生命周期 Hook 与审计 |
| 会话恢复 | `forge/sessions/` | append-only Journal、会话 DAG、文件 Checkpoint、轨迹 |
| 外部能力 | `forge/mcp/`、`forge/subagents/` | MCP Client、动态工具注册、隔离的只读 Explore Agent |
| 客观评测 | `evals/`、`tests/` | 临时 Git 工作区、隐藏测试、指标统计和自动化回归 |

系统的中心是 [`Conversation`](forge/runtime/agent_loop.py#L91)。它持有模型客户端、消息、工具注册表、任务管理器、上下文管理器、权限管理器、工作区追踪器、完成门禁、会话 Journal、Checkpoint、Hooks 与 MCP Manager。换言之，`Conversation` 不是单纯的聊天历史对象，而是一次可恢复工程会话的协调器。

### 2.2 启动与依赖装配

Typer 回调在 [`forge/cli.py`](forge/cli.py#L58) 解析 `--continue`、`--resume` 和 `--fork-session`，随后进入长期存活的 asyncio 事件循环。交互循环在 [`forge/cli.py`](forge/cli.py#L171) 处理普通 Prompt 和 `/context`、`/compact`、`/task`、`/status`、`/resume`、`/branch`、`/permission`、`/undo`、`/rewind`、`/memory`、`/mcp` 等控制命令。

真正的运行时装配位于 [`create_session_runtime`](forge/cli.py#L600)：

1. 创建默认 `ToolRegistry`；
2. 从用户和项目配置加载 Hooks、MCP Server；
3. 创建或恢复 `SessionJournal` 与 `CheckpointStore`；
4. 使用完整输出预算构造主模型客户端；
5. 使用 600 Token 输出预算构造独立的路由客户端；
6. 将恢复的消息、ActiveTask、模型 ID 和会话组件注入 `Conversation`。

终端事件转发在 [`render_streamed_turn`](forge/cli.py#L546)：模型文本、Token 用量、工具开始/结束、完成被拒绝和最终 TurnResult 都使用强类型事件传递，避免 UI 从日志文本反向解析状态。事件类型集中定义在 [`forge/runtime/state.py`](forge/runtime/state.py#L12)。

## 3. 模型边界与语义路由

### 3.1 Provider 隔离

Anthropic SDK 被限制在 [`forge/runtime/model_client.py`](forge/runtime/model_client.py#L1)。其余运行时代码只依赖 `ModelClient` 抽象以及普通 `list[dict]` 消息，不依赖 Provider 的具体类型。这一点由 `tests/runtime/test_model_boundary.py` 专门约束。

`AnthropicModelClient` 在 [`forge/runtime/model_client.py`](forge/runtime/model_client.py#L107) 完成以下工作：

- 将配置显式传入 SDK，并关闭 SDK 自带重试，避免双层重试；
- 把 Provider 流事件转换为 ForgeCode 的文本、工具调用和用量事件；
- 按 tool block index 聚合并解析增量 JSON；
- 在执行前检查工具是否真的包含在本次请求的工具表中；
- 对 `max_tokens` 截断、未闭合工具 JSON、空响应、兼容接口缺失 delta 等情况分类；
- 仅在尚未产生语义输出时重试临时连接、限流与 5xx 错误，避免文本或副作用可能已经发生后重复请求；
- 当兼容 Provider 没有流式 delta 时，从最终 message 恢复内容，相关兜底见 [`final_content_events`](forge/runtime/model_client.py#L441)；
- 将 Provider 异常归一为 retryable/non-retryable，分类和退避分别见 [`classify_provider_error`](forge/runtime/model_client.py#L484) 与 [`retry_delay_seconds`](forge/runtime/model_client.py#L512)。

默认模型配置由 [`ForgeConfig.from_env`](forge/config.py#L72) 从当前目录 `.env` 与环境变量读取；API Key、模型 ID、Base URL、最大输出、上下文窗口和请求超时都经过显式校验。密钥不会由 `forge config` 回显。

### 3.2 模型驱动的意图路由

项目刻意删除了基于关键词的自然语言路由，原则写在 [`forge/runtime/intent.py`](forge/runtime/intent.py#L1)：中文或英文措辞不应靠正则决定任务语义。`ModelIntentRouter` 在 [`forge/runtime/router.py`](forge/runtime/router.py#L79) 通过一次工具关闭的短模型调用，输出结构化 `TurnDecision`，包括：

- intent：conversation、task_query、read_only、new_task、continue_task、change_task 或 ambiguous；
- 与现有任务的关系；
- 是否要求工作区变更；
- 是否要求执行验证；
- 置信度和原始诊断。

路由请求只携带当前 Prompt、ActiveTask 摘要及最近四条消息；低于 0.65 的结果、无效 JSON、引用不存在任务等情况会 fail closed 为 ambiguous。路由结果和原始响应被写入 Session Journal，便于区分“模型判断错”“工具调用错”和“运行时拒绝”三类故障。

## 4. Agent Loop 的实现

### 4.1 一轮任务的初始化

主循环从 [`Conversation.stream`](forge/runtime/agent_loop.py#L261) 开始。一次用户回合会依次：

1. 启动 Session Hook；
2. 调用语义路由器；
3. 新建、继续或保留 ActiveTask；
4. 为本轮建立不可变工作区基线与文件 Checkpoint；
5. 组装 Repository、Task、Working、Permission 和 Verification 上下文；
6. 进入有界的模型—工具循环。

复杂计划会扩展保险预算：计划不少于 4 步、步骤文本很长或目标很长时，默认从 80 次模型调用/120 次工具调用扩展到 160/320，并启用 2,000,000 累计输入 Token 上限，见 [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L377)。这不是目标预算，而是防止长任务无限消耗的终止保险。

### 4.2 分阶段工具可见性

ForgeCode 会依据任务和恢复状态，为每次模型请求重新生成工具表，核心分支位于 [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L534)。典型阶段包括：

- 对话/只读：只提供无副作用的读取工具；
- Task Query：只提供 `task_get`，随后关闭工具进行状态综合；
- Planning Checkpoint：只提供 `task_plan` 与 `finish_task`；
- Mutation Read：只允许针对失败目标的 `read_file`/`grep`；
- Mutation Action：只允许文件修改或诚实终止；
- Verification Fix：允许最小读取、编辑、命令与验证；
- Dependency Recovery：只允许 `run_command`；
- Dependency Verification：只允许 `verify`；
- Finalization Recovery：不再提供工具，只生成交付说明。

每个 ToolCall 执行前还会检查它是否在本次请求中声明；阶段外调用返回 `tool_not_available_in_phase`，见 [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L1447)。这能阻止模型沿用旧上下文中的工具能力，但也意味着“checkpoint 标志”和“工具过滤结果”必须严格一致。当前已知的缺失依赖恢复问题正发生在这个交界：提示要求 `run_command`，但某次长任务的下一请求没有实际暴露它。

### 4.3 ToolCall 的执行管线

每个 ToolCall 在 [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L1379) 进入统一管线：

```text
PreToolUse Hook
  → 阶段可用性与协议检查
  → placeholder/delete-only/task-input 等语义护栏
  → PermissionRequest 与审批
  → ActiveTask 路径作用域检查
  → 写前 Checkpoint
  → ToolRegistry.execute
  → AfterFileEdit/PostToolUse Hook
  → WorkspaceTracker.refresh
  → Task/WorkingState/Verification 更新
```

其中的重要防护包括：

- 对同 revision、同参数的失败调用拒绝原样重复，签名生成见 [`tool_call_signature`](forge/runtime/agent_loop.py#L4549)；
- 已成功的相同工作区写入或删除在同一回合内按幂等成功处理，不重复执行副作用；
- 实现/重构任务的 delete-only 响应被拒绝，必须同批提供建设性修改；
- `task.md`、`AGENTS.md` 和显式任务根目录不会在实现任务中被误删，见 [`protected_task_input_delete`](forge/runtime/agent_loop.py#L5153)；
- `temp`、`probe`、`noop`、`placeholder` 等明显伪进度写入被拒绝，见 [`obvious_probe_write`](forge/runtime/agent_loop.py#L5435)；
- 一个批次中的写入改变 revision 后，后续依赖旧文件状态的调用会延迟到下一次模型决策。

### 4.4 失败恢复状态机

Agent Loop 不是笼统地“失败就重试”，而是维护相互独立的恢复计数和 checkpoint 标志。

**模型协议恢复。** 未知工具、Schema 错误、截断工具 JSON、把 JSON 参数打印成普通文本等不会被误认为仓库任务失败。运行时把精确错误和当前工具名单反馈给模型，并要求更小的调用；反馈生成见 [`build_protocol_recovery_feedback`](forge/runtime/agent_loop.py#L5524)。

**编辑恢复。** `apply_patch` 上下文不匹配、`replace_text` 不唯一、写入已有文件等会记录工具、错误码、目标和有界诊断。连续两次上下文类失败后，先开放一次目标读取，再进入只允许更正编辑的阶段；核心记账见 [`mutation_failure_record`](forge/runtime/agent_loop.py#L5051) 与 [`render_mutation_recovery_context`](forge/runtime/agent_loop.py#L5222)。真实 workspace revision 会清除旧失败债务，防止“已经修好却仍被旧失败卡死”。

**验证恢复。** 验证失败的 stdout/stderr 是主证据，下一阶段只允许读取错误涉及的文件或进行最小修复。`typecheck` 和 `build` 会形成类别化验证债务，较弱的命令不能覆盖它；分类见 [`verification_obligation`](forge/runtime/agent_loop.py#L5175)。源码改变后，证据因 revision 变化而失效，必须重跑原验证。

**依赖恢复。** `tsc`、`vite`、eslint、vitest 等项目声明的工具若“command not found”，由 [`verification_missing_dependency`](forge/runtime/agent_loop.py#L5185) 识别，随后应进入 `run_command` 安装、原命令复验的闭环。相应反馈构造在 [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L4912)。

**停滞恢复。** `WorkingState` 区分新证据、任务状态推进、真实写入和无进展调用；达到 warning 时要求改变策略，达到 limit 时依次尝试计划、行动或最终综合，而不是无限扫描。主逻辑位于 [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L3084) 到 [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L3438)。

### 4.5 完成协议

变更任务不能以普通文本结束。模型必须单独调用 `finish_task`，其输入声明 task_kind、completed/blocked、summary 和 blocked reasons，工具定义见 [`forge/tools/finish.py`](forge/tools/finish.py#L19)。运行时再用客观证据复核声明，拒绝原因汇总在 [`Conversation._finish_rejection_reasons`](forge/runtime/agent_loop.py#L3519)。

当所有确定性门禁满足但模型继续诊断时，运行时最多给出有限次 completion decision；随后进入无工具的 Finalization Recovery，避免再次调用工具导致循环，见 [`render_completion_ready_context`](forge/runtime/agent_loop.py#L4994) 与 [`build_finalization_recovery_feedback`](forge/runtime/agent_loop.py#L5021)。

## 5. 工具系统

### 5.1 统一 Tool 协议

所有工具继承 [`Tool`](forge/tools/base.py#L187)，输入继承 Pydantic `ToolInput` 并设置 `extra='forbid'`。调用成功或失败均返回 [`ToolResult`](forge/tools/base.py#L32)，失败包含稳定 error code、面向模型的恢复消息、details 与 metadata，而不是把异常直接抛穿 Agent Loop。

[`ToolRegistry`](forge/tools/base.py#L288) 负责注册、Schema 导出、effect/provenance 查询和执行。路径解析函数 [`resolve_repository_path`](forge/tools/base.py#L370) 将访问约束在仓库根目录，拒绝绝对路径、`..` 逃逸、符号链接逃逸以及 `.git`、`.forge`、敏感 `.env` 等控制面目标。

默认注册表在 [`forge/tools/__init__.py`](forge/tools/__init__.py#L20) 组装。`write_file_chunk` 与 `replace_text` 默认可作为恢复工具被动态暴露，而不必常驻所有模型请求，从而降低 Schema Token 与误用概率。

### 5.2 文件与搜索工具

[`forge/tools/filesystem.py`](forge/tools/filesystem.py#L30) 定义单次编辑 30,000 字符、读取 400 行、分块文件 1,000,000 字符等硬边界。

- `list_directory` 只列直接子项，目录优先排序；
- `create_directory` 只用于真正需要保留的空目录，已有目录返回可恢复诊断；
- `remove_directory` 支持清空内容或递归删除，但拒绝仓库根、控制面与含链接的目录，并产生高风险权限请求；
- `read_file` 返回带行号内容、SHA-256 和续读位置；
- `write_file` 面向新文件或空占位符，绝不直接覆盖非空文件；
- `write_file_chunk` 使用精确 offset、可选最终 SHA-256 与原子写入协议；
- `replace_text` 要求 old_text 在当前文件中唯一出现，保留 CRLF，并在失败时返回接近的当前片段帮助修正。

搜索层在 [`forge/tools/search.py`](forge/tools/search.py#L25) 遍历非生成目录、排除链接与控制面；`find_files` 明确只返回文件，`grep` 支持正则/字面量、扩展名过滤和结果上限。

### 5.3 Patch、Shell、Git 与 Verify

`apply_patch` 位于 [`forge/tools/patch.py`](forge/tools/patch.py#L59)，兼容 Codex `*** Begin Patch` envelope 和标准 unified diff。它会预解析整个补丁、复用仓库路径安全、检查复制自 `read_file` 的行号、空 hunk、缺失上下文、歧义上下文和 CRLF，并在完整验证通过前不修改任何文件。

`run_command` 位于 [`forge/tools/shell.py`](forge/tools/shell.py#L207)。它并非任意 Shell 后门：仓库文件读写应走专用工具，危险 Git、目录写入、重定向绕过和 Windows POSIX heredoc 会被识别；子进程使用清理后的环境，输出有界，超时会终止进程树。命令的网络、安装、删除、提权等风险会转成 PermissionRequest。

Git 工具在 [`forge/tools/git.py`](forge/tools/git.py#L34) 提供有界 log、status 和 diff。对于未跟踪 UTF-8 文件，`git_diff` 会自行生成可分页 diff；大输出通过 offset 与 SHA-256 防止页间内容漂移，相关渲染在 [`forge/tools/git.py`](forge/tools/git.py#L472)。

`verify` 在 [`forge/tools/verify.py`](forge/tools/verify.py#L43) 只把测试、构建、lint、类型/语法检查或 `git diff --check` 记为完成证据。版本查询、目录查看和只读 Git 命令可以执行但标记为 inspection-only；文件读取、写入和目录修改则被拒绝。每份成功/失败证据都绑定命令、cwd、退出码、耗时、超时状态与 workspace revision。

## 6. 工作区追踪与 Completion Gate

[`WorkspaceTracker`](forge/runtime/workspace.py#L25) 在每轮开始时拍摄任务局部文件系统快照，而不是简单读取 `git status`。它会：

- 记录文件内容哈希、目录和符号链接状态；
- 主动 watch 工具即将写入的 ignored 路径；
- 排除 `.forge/context`、memory、tasks、trajectories 等运行时自生成状态；
- 只把相对本轮基线的新变化计入 changed paths；
- 在继续同一持久任务时，仅携带仍然 dirty 的历史任务路径；
- 每次状态变化递增 `revision`。

因此，用户进入回合前已有的脏文件不会冒充 Agent 本轮成果，Agent 写完又还原到基线也不会算作完成。

[`CompletionGate`](forge/runtime/completion.py#L33) 的检查顺序是：真实变更、验证是否要求且是否成功、验证 revision 是否当前、允许/禁止路径、任务局部 Patch 格式。对 tracked 文件逐路径执行 `git diff --check`；对 untracked 文件使用 `git diff --no-index --check`。它刻意只做机械事实判断，不代替模型判断功能语义是否满足。

## 7. 任务与计划系统

`ActiveTask` 和 `TaskStep` 是不可变数据对象，定义于 [`forge/tasks/state.py`](forge/tasks/state.py#L21)。[`TaskManager`](forge/tasks/manager.py#L20) 负责：

- 启动新目标或继续当前目标；
- 从用户措辞推导保守的路径 scope hints，入口见 [`infer_goal_scope`](forge/tasks/manager.py#L480)；
- 建立 2～20 步的顺序计划；
- 强制只推进当前步骤，完成步骤必须带非空、有效的执行证据；
- 观察 workspace paths，但只持久化任务作用域内的路径；
- 区分 completed、blocked 与 stuck；
- 将当前目标、最新指令、计划、阻塞原因和作用域注入系统上下文。

只有显式计划任务写入 `.forge/tasks`，简单任务保留在内存中，见 [`forge/tasks/store.py`](forge/tasks/store.py#L16)。任务工具在 [`forge/tools/task.py`](forge/tools/task.py#L22) 实现 `task_get`、`task_plan` 与 `task_update`，对重复建计划、重复更新已完成步骤、越过当前步骤和仅用失败说明作为完成证据均作结构化处理。

## 8. 上下文工程

ForgeCode 将上下文分为四层：

1. **System**：产品身份、平台规则、工具可用性和权限模式；系统提示位于 [`forge/prompts/system.md`](forge/prompts/system.md#L1)。
2. **Repository**：`AGENTS.md`、`FORGE.md`、`.forge/rules/*.md` 与查询相关记忆，见 [`RepositoryContext`](forge/context/repository.py#L183)。
3. **Task**：ActiveTask 的稳定目标、步骤、范围、变更路径和状态。
4. **Working**：当前 revision 已读范围、搜索结果、错误和外部阻塞证据，见 [`WorkingState`](forge/context/working.py#L103)。

WorkingState 缓存最多 400 行的读取片段，合并相邻/重叠范围；同 revision 的重复读取可以短路或重放，文件变化时只失效相关路径，而不是清空全部证据。它还限制注入系统提示的证据体积，避免长任务把源码反复塞回上下文。

压缩算法位于 [`forge/context/compactor.py`](forge/context/compactor.py#L1)：先把大型工具结果保存到 `.forge/context/tool-results`，再缩短旧结果，同时保持 `tool_use` 与 `tool_result` 原子配对，避免破坏 Provider 协议。结构化摘要保留当前目标、约束、完成工作、失败、验证、恢复文件和最近消息。

[`ContextManager.compact_history`](forge/context/manager.py#L289) 在每次模型请求前根据完整请求层估算 Token；达到配置窗口比例或字符回退阈值时触发摘要。摘要前的完整历史以内容哈希命名保存为 JSONL，可用于恢复。连续三次摘要失败会打开 fuse，防止每轮重复花费失败的摘要调用。

长期记忆由 [`MemoryStore`](forge/context/repository.py#L34) 保存为可审阅 Markdown，按名称、描述和内容词项相关性选取；中英文均支持简单词项匹配。写入前用模式拒绝 API Key、token、password 和私钥，十条以上自动去重并重建索引。

## 9. 权限、安全与 Hooks

### 9.1 权限模型

[`PermissionManager`](forge/permissions/policy.py#L85) 支持 plan、supervised、auto 三种模式，并合并用户级、项目级和会话级规则：

- plan：从模型请求中直接隐藏所有 effectful 工具；
- supervised：有副作用操作交给终端审批；
- auto：自动放行低风险操作，高风险和硬拒绝仍需决策。

风险分类在 [`forge/permissions/risk.py`](forge/permissions/risk.py#L35) 基于最终 ToolCall 和工具 effect，而不是从用户自然语言猜测。文件删除、递归操作、包安装、网络、提权、危险 Git、外部 MCP 调用等被映射为具体 capability 与 risk。审批结果和规则来源写入会话审计；删除类授权不会保存成无限制通配符。

当前边界是应用层保护而非 OS Sandbox：路径解析、环境清理、命令策略和交互审批能降低误操作，但尚未提供独立容器、CPU/内存/进程数和原生网络的强隔离。

### 9.2 Hook 生命周期

Hook 模型定义在 [`forge/hooks/models.py`](forge/hooks/models.py#L1)，配置由用户与项目设置合并。Hook Manager 在 SessionStart/End、BeforeModelCall、BeforeCompact、Pre/PostToolUse、Before/AfterFileEdit、AfterVerification 等节点执行。

Hook 子进程实现位于 [`forge/hooks/runner.py`](forge/hooks/runner.py#L36)：事件以 JSON stdin 和受控环境变量传入；命令使用 argv 而非 Shell；总输出上限 1 MB；有超时；退出码 2 表示 deny；标准输出可返回 updated_arguments、additional_context 和 reason。阻塞型 Hook 失败采用 fail closed，事后 Hook 的失败只审计、不能回滚已发生动作。

## 10. 会话、Checkpoint 与轨迹

### 10.1 Append-only Session Journal

[`SessionJournal`](forge/sessions/store.py#L60) 将用户消息、模型消息、工具开始/结束、路由、权限、Hook、压缩、Checkpoint 和 TurnCompleted 追加为 JSONL。大 payload 独立存为带 SHA-256 的 artifact，Journal 只保存引用。

[`SessionStore`](forge/sessions/store.py#L326) 在恢复时重放事件并重建消息和 ActiveTask。它只恢复完整的 assistant tool_use + user tool_result 原子对；只有 started 而没有 completed 的工具被标记 indeterminate，绝不自动重放。尾部半写 JSON 行可忽略，中间损坏则拒绝恢复。

会话分支形成合法 DAG。每次追加前校验 writer 持有的 durable head，过期进程不能继续向已被其他分支推进的 Session 写入，从而避免两个恢复进程静默制造分叉。分支、恢复、清空及模型切换的接线位于 [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L4366)。

### 10.2 文件 Checkpoint 与回退

[`CheckpointStore`](forge/sessions/checkpoint.py#L34) 在工作区写入前捕获原始状态，将内容按哈希去重存入 blob；写后保存状态。恢复前会比较当前内容和记录的 after 状态，若文件被外部修改则拒绝覆盖。它支持恢复修改文件、新文件和删除的目录树，并通过 `/undo`、`/rewind [id] [code|conversation|both]` 暴露给用户，调用入口见 [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L4238)。

[`TrajectoryRecorder`](forge/sessions/trajectory.py#L49) 记录面向分析的轻量 JSONL 轨迹，保留事件类型、ToolCall 参数摘要、成功状态、用量和完成结果，但不会把大型工具内容原样复制进去。

## 11. MCP 与 Explore Agent

MCP 配置从用户级 `~/.forge/mcp.json` 和项目 `.mcp.json` 合并，项目同名项覆盖用户配置；配置解析位于 [`forge/mcp/config.py`](forge/mcp/config.py#L65)，支持 stdio 和 Streamable HTTP，并展开 `${VAR}` 环境变量。

[`MCPClientManager`](forge/mcp/manager.py#L44) 在需要时连接 Server、分页获取工具、将远程工具标准化为 `mcp__server__tool` 并动态注册。工具列表变化时会刷新 Registry；调用通过同一 PermissionManager；来源、Server 和远程工具名进入审计。若连接在调用后丢失，返回 `mcp_result_unknown`，明确说明远端操作可能已完成且不会自动重放，关键逻辑见 [`forge/mcp/manager.py`](forge/mcp/manager.py#L278)。

[`ExploreRepositoryTool`](forge/subagents/explore.py#L144) 是隔离的只读子 Agent。它使用独立消息历史、独立 Token/调用预算和只读工具注册表，提示位于 [`forge/prompts/explore.md`](forge/prompts/explore.md#L1)。返回值必须通过结构化 `ExploreReport` 校验，包括 findings、relevant_files、suggested_edit_points、current_excerpt 和 unanswered_questions；原始探索链不会进入父 Agent 上下文，只交接有界报告。

## 12. 自动化测试与真实评测

### 12.1 测试结构

`tests/` 当前包含 34 个 Python 文件、383 个显式 `test_` 函数；参数化展开后的仓库基线是 406 项。测试不是只覆盖 happy path，而是围绕运行时不变量组织：

- `tests/runtime/test_agent_loop.py`：流式循环、多工具顺序、预算、重复调用、协议恢复、权限和上下文；
- `tests/runtime/test_m2_agent_loop.py`：真实 Diff、编辑恢复、验证债务、停滞、finish_task、依赖与作用域；
- `tests/runtime/test_workspace_completion.py`：基线、ignored/untracked 路径、revision 和 Completion Gate；
- `tests/tools/`：路径逃逸、CRLF、原子写、Patch 全量预检、Shell 绕过、Git diff 分页和 Verify 分类；
- `tests/sessions/`：半写 Journal、未知副作用、分支恢复、并发头、Checkpoint 冲突；
- `tests/context/`：工具对配对、范围缓存、压缩阈值、摘要 fuse、记忆密钥过滤；
- `tests/hooks/` 与 `tests/mcp/`：真实子进程/stdio/HTTP 集成、超时、动态工具刷新和结果未知语义；
- `tests/subagents/test_explore.py`：只读能力、结构化报告和父上下文隔离。

这些测试大量使用 fake model 稳定复现轨迹边界，其价值是精确验证状态机；但它们不能代替真实 Provider 在超长工具参数、流事件差异和策略选择上的表现。

### 12.2 Eval Harness

[`evals/runner.py`](evals/runner.py#L35) 定义 YAML `EvalCase`，当前包含 Python calculator、TypeScript todo 和 Java order service 三个跨语言场景。每次运行会：

1. 从固定 Git commit 用 `git archive` 解包到临时目录；
2. 把隐藏测试移出 Agent 可见工作区；
3. 建立新的 Git baseline；
4. 用 `TaskPolicy` 限定 allowed/forbidden paths，并要求变更和验证；
5. 运行 Agent；
6. 由评测器独立运行 build、公开测试、`git diff HEAD --check`；
7. 恢复隐藏测试并执行；
8. 把 outcome 与 trajectory 保存到工作区外。

客观验收条件位于 [`acceptance_reasons`](evals/runner.py#L366)：Agent 状态必须 completed、Diff 非空、具备成功 Verify 证据、无越界路径，而且构建、公开测试、隐藏测试和 diff check 均通过。当前 [`evals/metrics.py`](evals/metrics.py#L1) 仍是空占位文件，尚未实现跨运行的成功率、成本和恢复次数聚合；现阶段的结果输出由 `EvalOutcome` JSON 与 JSONL trajectory 承担。

### 12.3 当前实战状态

仓库 README 记录了一次真实 `gpt-5.4-mini` 端到端验收：从只保留规格的 `play/` 目录生成 Vite + TypeScript + Phaser 项目，恢复大 Patch、精确替换和首次编译失败，最终 typecheck、build 与 HTTP 资源加载通过。与此同时，另一次未安装依赖的长任务暴露出 Dependency Recovery 的阶段工具可见性缺陷。这说明当前体系已经能正确阻止虚假完成，但“安全地失败”与“自动恢复并完成”仍是两个不同成熟度指标。

## 13. 关键不变量与工程取舍

### 13.1 已落实的不变量

1. 所有仓库路径必须经过统一边界解析；
2. 变更任务必须产生相对本轮或同一持久任务的真实 Diff；
3. 验证证据只属于执行后的精确 workspace revision；
4. 旧的 typecheck/build 失败不能被弱验证掩盖；
5. 工具只能在本次模型请求声明的阶段执行；
6. 已发生但结果未知的外部副作用绝不自动重放；
7. 写入前必须能够建立恢复 Checkpoint；
8. 完成声明必须与工具、任务、Diff 和验证证据一致；
9. 会话只能从合法 durable head 继续追加；
10. 大型工具结果和历史压缩不能破坏 tool_use/tool_result 配对。

### 13.2 主要取舍

**严格工具协议提高安全性，也增加模型纠错负担。** `write_file` 不覆盖、Patch 要完整上下文、Verify 不允许读文件，这些约束能防止数据破坏和伪验证；但弱模型若不能根据 error.details 换用正确工具，就会产生用户观察到的高失败率。因此恢复反馈、工具阶段和默认暴露集合是系统体验的核心，而非辅助文案。

**Agent Loop 目前过度集中。** `agent_loop.py` 超过 5,500 行，工具阶段、恢复债务、任务推进、完成协议和会话接口都集中在 `Conversation`。优点是状态转换可见且共享局部变量；缺点是布尔 checkpoint 组合容易发生优先级错误，Dependency Recovery 工具缺失就是典型风险。后续宜将其拆为显式枚举状态、统一 Transition 对象和可表驱动测试的 phase policy。

**双模型调用提高语义泛化但增加成本。** 每个普通回合先路由再调用主模型，避免中文关键词规则，却增加一次调用和 Token。当前通过路由小输出预算、最近四条消息和失败关闭控制成本。

**本地直接执行易用但隔离有限。** 当前适合可信仓库和受监督操作；面向不可信代码时，需要在 Tool 层下增加真正的 Sandbox Backend，而不是继续扩展 Shell 正则。

## 14. 后续改进建议

1. **把恢复阶段改成单一状态对象。** 用 `Phase(kind, allowed_tools, required_action, exit_condition)` 取代十余个布尔 checkpoint，模型请求工具表和提示从同一对象生成，消除“提示说可用、Schema 未暴露”的不一致。
2. **加入阶段一致性断言。** 每次调用模型前校验反馈中点名的工具均存在于 request tools；Dependency Recovery 必须断言 `run_command`，Post-install 必须断言 `verify`。
3. **把 Agent Loop 拆成执行器与策略器。** 将 request preparation、tool batch executor、recovery reducer、completion coordinator 分离，保留一个纯状态 reducer，便于对所有转移做属性测试。
4. **记录失败率的语义指标。** 区分 Provider、Schema、phase rejection、permission、mutation、verification、stagnation，而不是统一显示“工具执行存在失败”；用户可看到哪些是自动纠正的协议事件，哪些真正影响交付。
5. **扩充真实 Provider 回归矩阵。** 在费用受控下覆盖 Windows/POSIX、空项目/脏仓库、依赖缺失、大文件、长计划、会话恢复和 MCP 结果未知，并统计完成率、无效调用率、输入 Token 与恢复次数。
6. **增加浏览器验收适配层。** Web/game 项目目前只能验证构建和 HTTP 文件加载；若要验证“敌人是否出现”等视觉语义，需要受控浏览器、截图和可查询页面状态工具。

## 15. 关键代码导航

| 主题 | 入口 |
| --- | --- |
| CLI 启动 | [`forge/cli.py`](forge/cli.py#L58) |
| 运行时装配 | [`forge/cli.py`](forge/cli.py#L600) |
| Agent 主循环 | [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L261) |
| 阶段工具选择 | [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L534) |
| 工具执行管线 | [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L1379) |
| 编辑/停滞恢复 | [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L2551) |
| 完成复核 | [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py#L3519) |
| 模型流适配 | [`forge/runtime/model_client.py`](forge/runtime/model_client.py#L107) |
| 语义路由 | [`forge/runtime/router.py`](forge/runtime/router.py#L79) |
| 工具抽象/注册表 | [`forge/tools/base.py`](forge/tools/base.py#L187) |
| 文件工具 | [`forge/tools/filesystem.py`](forge/tools/filesystem.py#L40) |
| Patch | [`forge/tools/patch.py`](forge/tools/patch.py#L59) |
| Shell | [`forge/tools/shell.py`](forge/tools/shell.py#L207) |
| Verify | [`forge/tools/verify.py`](forge/tools/verify.py#L43) |
| Workspace revision | [`forge/runtime/workspace.py`](forge/runtime/workspace.py#L25) |
| Completion Gate | [`forge/runtime/completion.py`](forge/runtime/completion.py#L33) |
| Task Manager | [`forge/tasks/manager.py`](forge/tasks/manager.py#L20) |
| WorkingState | [`forge/context/working.py`](forge/context/working.py#L103) |
| 历史压缩 | [`forge/context/manager.py`](forge/context/manager.py#L289) |
| 权限管理 | [`forge/permissions/policy.py`](forge/permissions/policy.py#L85) |
| Hook 执行 | [`forge/hooks/runner.py`](forge/hooks/runner.py#L36) |
| Session Journal | [`forge/sessions/store.py`](forge/sessions/store.py#L60) |
| Checkpoint | [`forge/sessions/checkpoint.py`](forge/sessions/checkpoint.py#L34) |
| MCP Manager | [`forge/mcp/manager.py`](forge/mcp/manager.py#L44) |
| Explore Agent | [`forge/subagents/explore.py`](forge/subagents/explore.py#L144) |
| Eval Runner | [`evals/runner.py`](evals/runner.py#L286) |

## 16. 总结

ForgeCode 已经实现了一个完整的本地工程 Agent 运行时：模型负责语义判断与行动选择，运行时负责边界、证据、状态、恢复和审计。项目最有价值的部分不是工具数量，而是围绕 `workspace_revision`、结构化 ToolResult、ActiveTask、Completion Gate、append-only Session 与有界恢复建立的一组可验证不变量。

当前最大技术风险也很明确：核心循环的恢复状态过多且集中，容易出现提示、工具 Schema 与退出条件之间的不一致。下一阶段最有效的工作不是继续添加更多特例，而是把这些隐含布尔状态收敛为显式 phase state machine，并用真实 Provider 场景持续测量“最终完成率、自动恢复率和单位成功任务 Token”，从而让 ForgeCode 从“能够安全阻止错误”进一步成长为“能够稳定完成复杂任务”。

## 17. Aider Polyglot / Harbor 真实评测基线

### 17.1 评测结论

ForgeCode 使用真实模型 `gpt-5.6-luna` 完成了 Aider Polyglot 的完整 225 题 Harbor 运行。最终 reward 为 **211/225（93.78%）**；其中 1 题属于 Harbor 基础设施错误（`RewardFileNotFoundError`），按 224 道可计分代码题计算，最终准确率为 **211/224（94.20%）**。

该协议允许一次 verifier-feedback repair：严格首轮通过为 **97/224（43.30%）**，另有 **114** 道题在反馈后修复通过。因此首轮 pass@1 与允许修复后的最终代码正确率必须分别报告。

### 17.2 运行配置与可复核证据

| 项目 | 配置/结果 |
| --- | --- |
| 基准 | Aider Polyglot / Harbor |
| 题目数 | 225 |
| 模型 | `gpt-5.6-luna` |
| Harbor / Docker | `0.18.0` / `29.7.2` |
| 数据集 commit | `f30b14415dd733c83627204bad0af69a89ceb46f` |
| 并发 / 最大重试 | 1 / 3 |
| 运行目录 | `benchmark/runs/harbor/full-225-luna-rerun/2026-08-12__11-53-03` |
| 汇总 JSON | `benchmark/runs/harbor/full-225-luna-rerun/summary.json` |
| 模型调用 / 工具调用 | 3,488 / 3,840 |
| 输入 / 输出 token | 42,090,702 / 885,602 |

按语言统计的最终准确率为：C++ **24/25（96.00%）**、Go **37/39（94.87%）**、JavaScript **49/49（100%）**、Java **39/47（82.98%）**、Python **32/34（94.12%）**、Rust **30/30（100%）**。Java 的工程布局、构建工具和 API 发现仍是主要改进方向。

### 17.3 版本与复测边界

`v0.1.1` 标签保留上述 211/225 评测基线；当前 `main` 的回滚提交 `5f74fdc` 恢复了该基线行为。之后启动但未完成的 `full-225-luna-postfix` 运行不计入上述分数，也不作为版本质量结论。后续若修改恢复状态机、任务作用域或验证器，应在同一数据集 commit 和 Harbor 配置下重新运行完整 225 题，并同时报告最终 reward、严格首轮 pass@1、基础设施错误数和遥测开销。
