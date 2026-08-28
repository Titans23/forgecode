明白。你需要的是一条“从程序入口到最终评测结果”的完整源码学习路线，而不是按功能模块零散阅读。

下面这条路线按照 ForgeCode 的真实运行链路设计：

```text
pyproject.toml
→ CLI
→ Terminal UI
→ Runtime 装配
→ Model Client
→ Router
→ Context / Memory
→ Tool Registry
→ Agent Loop
→ Permission / Hooks
→ Workspace / Completion
→ Recovery
→ Session / Checkpoint
→ MCP / Feishu Channel / Skill / Subagent
→ Qwen Tool-call Distillation
→ Local Eval
→ Harbor Benchmark
```

## 阶段 0：项目入口和技术基线

先读：

- [`pyproject.toml`](D:/learn_project/forgecode/pyproject.toml)
- [`README.md`](D:/learn_project/forgecode/README.md:1)
- [`report.md`](D:/learn_project/forgecode/report.md:1)

重点理解：

- `forge = forge.cli:app` 如何成为 CLI 入口
- Python、Typer、Rich、Pydantic、Anthropic、MCP、pytest 各自承担什么职责
- `forge/` 是产品代码，`evals/` 是本地评测，`benchmark/` 是外部 Benchmark 适配
- 项目解决的是“可靠地运行代码 Agent”，不是“训练模型”

先执行：

```powershell
uv sync
uv run forge --help
uv run forge --version
uv run pytest -q
```

产出一页项目地图，至少包含：

```text
CLI
Runtime
Context
Tools
Permissions
Sessions
Extensions
Evaluation
```

## 阶段 1：CLI 参数与启动生命周期

阅读：

- [`forge/cli.py`](D:/learn_project/forgecode/forge/cli.py:42)
- [`forge/config.py`](D:/learn_project/forgecode/forge/config.py:24)
- [`tests/test_cli.py`](D:/learn_project/forgecode/tests/test_cli.py)

先追踪这条链：

```text
forge
→ Typer app
→ main()
→ run_interactive_chat()
→ _run_interactive_chat()
```

重点理解：

- `--continue`、`--resume`、`--fork-session` 的区别
- `forge config` 如何加载 `.env`
- 缺少 API Key 或 Model ID 时如何报告错误
- CLI 为什么统一使用 asyncio
- 为什么 CLI 只负责装配和交互，不负责 Agent 决策

需要能解释：

> 如果用户执行 `uv run forge`，从进程启动到第一次 Prompt 被读取，中间发生了什么？

## 阶段 2：Terminal UI 和事件流

阅读：

- [`forge/terminal.py`](D:/learn_project/forgecode/forge/terminal.py:194)
- [`forge/runtime/events.py`](D:/learn_project/forgecode/forge/runtime/events.py)
- [`forge/runtime/state.py`](D:/learn_project/forgecode/forge/runtime/state.py)
- [`tests/test_terminal.py`](D:/learn_project/forgecode/tests/test_terminal.py)

重点追踪：

```text
TerminalUI.read_prompt()
→ Conversation.stream()
→ ModelTextDelta
→ ToolExecutionStarted
→ ToolExecutionCompleted
→ TurnCompleted
→ StreamingResponseView.render()
```

理解：

- 模型文本为什么可以流式显示
- 工具调用为什么以事件形式显示
- Token 用量如何传回终端
- 权限审批时为什么要暂停流式输出
- `/context`、`/memory`、`/task`、`/permission` 等命令为什么不一定调用模型

这一步的产出是“事件时序图”。

## 阶段 3：Runtime 装配和会话创建

阅读：

- [`forge/cli.py`](D:/learn_project/forgecode/forge/cli.py:629)
- [`forge/tools/__init__.py`](D:/learn_project/forgecode/forge/tools/__init__.py:25)
- [`forge/sessions/store.py`](D:/learn_project/forgecode/forge/sessions/store.py:326)
- [`forge/sessions/checkpoint.py`](D:/learn_project/forgecode/forge/sessions/checkpoint.py:33)

重点理解 `create_session_runtime()`：

```text
创建 SessionStore
→ 创建默认 ToolRegistry
→ 创建 HookManager
→ 创建 MCPClientManager
→ 创建 ModelClient
→ 创建 Router Client
→ 创建 Conversation
→ 创建或恢复 SessionJournal
→ 创建 CheckpointStore
```

分别追踪三种情况：

### 新会话

```text
create_default_registry
→ Conversation
→ SessionStore.create
→ SessionJournal
```

### 恢复会话

```text
SessionStore.open
→ 重放 Journal
→ 恢复 messages
→ 恢复 ActiveTask
→ 恢复 Conversation
```

### Fork 会话

```text
读取旧 Session
→ 创建新 Journal
→ 复制消息和任务状态
→ 可选切换模型
```

需要回答：

> Conversation 为什么不是简单的消息列表，而是整个 Agent 会话的协调器？

## 阶段 4：模型接口和流式协议

阅读：

- [`forge/runtime/model_client.py`](D:/learn_project/forgecode/forge/runtime/model_client.py)
- [`tests/runtime/test_model_client.py`](D:/learn_project/forgecode/tests/runtime/test_model_client.py)
- [`tests/runtime/test_model_boundary.py`](D:/learn_project/forgecode/tests/runtime/test_model_boundary.py)

重点理解：

```text
Anthropic Provider Event
→ ModelTextDelta
→ ModelToolCallDelta
→ ModelToolCallCompleted
→ ModelUsageUpdate
```

重点分析：

- 为什么 Anthropic SDK 只允许出现在 `model_client.py`
- Tool JSON 如何按 block index 增量拼接
- Provider 流式响应如何转换为项目自己的事件
- 空响应、截断 JSON、非法 ToolCall 如何分类
- 哪些错误可以重试，哪些错误不能重试
- 为什么“模型已经输出语义内容后”不应该随意重发请求

需要能解释：

> ForgeCode 如何做到模型 Provider 可替换，而 Agent Loop 不依赖 Anthropic 的具体类型？

## 阶段 5：状态对象和工具协议

阅读：

- [`forge/runtime/state.py`](D:/learn_project/forgecode/forge/runtime/state.py)
- [`forge/tools/base.py`](D:/learn_project/forgecode/forge/tools/base.py:16)
- [`forge/tasks/state.py`](D:/learn_project/forgecode/forge/tasks/state.py)

先搞清楚这些对象：

```text
ToolCall
ToolResult
ToolError
VerificationEvidence
TurnResult
ConversationEvent
ActiveTask
TaskStep
```

然后理解统一工具协议：

```text
模型 ToolCall
→ Pydantic ToolInput
→ Tool.run()
→ Tool.execute()
→ ToolResult
```

重点理解：

- `ToolInput` 为什么禁止额外字段
- `ToolResult` 为什么包含稳定的 error code
- 工具错误为什么不直接抛出 Agent Loop
- `ToolRegistry` 如何注册、导出 Schema、查询 effect、执行工具
- 工具来源 provenance 如何进入审计

这一步是后面读所有工具的基础。

## 阶段 6：语义 Router 和任务状态

阅读：

- [`forge/runtime/router.py`](D:/learn_project/forgecode/forge/runtime/router.py:30)
- [`forge/runtime/intent.py`](D:/learn_project/forgecode/forge/runtime/intent.py)
- [`forge/tasks/manager.py`](D:/learn_project/forgecode/forge/tasks/manager.py:20)
- [`tests/runtime/test_router.py`](D:/learn_project/forgecode/tests/runtime/test_router.py)
- [`tests/tasks/test_task_manager.py`](D:/learn_project/forgecode/tests/tasks/test_task_manager.py)

追踪：

```text
用户 Prompt
→ TurnDecision
→ new_task / continue_task / read_only / change_task
→ ActiveTask
→ Task Scope / Plan
```

重点理解：

- Router 为什么是一次独立的、无工具模型调用
- `TurnDecision` 如何通过 Pydantic 校验语义契约
- 为什么低置信度要变成 `ambiguous`
- `ActiveTask` 如何保存目标、计划、路径作用域、状态和阻塞原因
- 任务计划为什么必须按顺序推进
- 为什么完成步骤必须提供真实执行证据

## 阶段 7：上下文、短期记忆和长期记忆

这是 Harness 的核心，应该花最多时间。

阅读：

- [`forge/context/manager.py`](D:/learn_project/forgecode/forge/context/manager.py:177)
- [`forge/context/working.py`](D:/learn_project/forgecode/forge/context/working.py:103)
- [`forge/context/compactor.py`](D:/learn_project/forgecode/forge/context/compactor.py:41)
- [`forge/context/repository.py`](D:/learn_project/forgecode/forge/context/repository.py:34)
- `tests/context/`

建立这张关系：

```text
Conversation History
  └─ 原始会话消息

WorkingState
  └─ 当前 revision 的文件/搜索/失败证据

ActiveTask
  └─ 当前目标、计划和修改范围

MemoryStore
  └─ 跨任务的项目长期知识

SessionJournal
  └─ 用于恢复的完整事件记录
```

重点阅读三条链。

### WorkingState 链

```text
read_file / grep / find_files
→ WorkingState.observe()
→ 记录证据和范围
→ 重复读取时缓存或重放
→ 文件修改后只失效受影响路径
```

### Cheap Compaction 链

```text
ContextManager.prepare()
→ persist_large_tool_results()
→ snip_middle_messages()
→ shorten_old_tool_results()
→ 生成模型可见的上下文副本
```

### Full Compaction 链

```text
上下文达到阈值
→ 保存完整 transcript
→ 调用模型生成 JSON 摘要
→ 保留目标、约束、失败、验证、下一步
→ 恢复最近消息和任务相关文件证据
```

需要能回答：

- 为什么不能只保留最近几条消息？
- 为什么大型工具结果要外置保存？
- 为什么 `tool_use/tool_result` 必须原子保留？
- 为什么压缩摘要不能完全信任模型？
- 为什么长期记忆采用 Markdown，而不是向量数据库？
- 为什么长期记忆需要按当前 Query 选择？
- 为什么 Session Journal 不是长期记忆？

## 阶段 8：具体工具实现

阅读顺序：

1. [`forge/tools/filesystem.py`](D:/learn_project/forgecode/forge/tools/filesystem.py)
2. [`forge/tools/search.py`](D:/learn_project/forgecode/forge/tools/search.py)
3. [`forge/tools/git.py`](D:/learn_project/forgecode/forge/tools/git.py)
4. [`forge/tools/patch.py`](D:/learn_project/forgecode/forge/tools/patch.py:59)
5. [`forge/tools/shell.py`](D:/learn_project/forgecode/forge/tools/shell.py)
6. [`forge/tools/verify.py`](D:/learn_project/forgecode/forge/tools/verify.py:43)
7. [`forge/tools/finish.py`](D:/learn_project/forgecode/forge/tools/finish.py)

不要按文件数量学习，而是按工具类别学习：

### 读取类

```text
list_directory
read_file
grep
find_files
git_status
git_diff
```

### 修改类

```text
write_file
write_file_chunk
replace_text
apply_patch
create_directory
remove_directory
```

### 执行和验证类

```text
run_command
verify
finish_task
```

重点理解：

- 文件工具为什么有大小、行数和覆盖边界
- Patch 为什么要在修改前完整预检
- `replace_text` 为什么要求唯一匹配
- `verify` 为什么拒绝普通文件读取命令
- `finish_task` 为什么不是普通的文本回复

## 阶段 9：真正阅读 Agent Loop

现在才读：

- [`forge/runtime/agent_loop.py`](D:/learn_project/forgecode/forge/runtime/agent_loop.py:280)

不要从头读到尾，按以下顺序：

### 9.1 初始化

读 `Conversation.__init__`：

```text
ModelClient
ToolRegistry
ContextManager
TaskManager
PermissionManager
WorkspaceTracker
CompletionGate
SessionJournal
CheckpointStore
```

### 9.2 一轮任务的开始

读 `Conversation.stream()` 前半部分：

```text
session_start
→ route
→ start/continue task
→ reset WorkingState
→ create workspace baseline
→ create checkpoint
→ record user message
```

### 9.3 请求构造

读：

- `_system_prompt_with_task`
- `_request_system_prompt`
- `_tool_definitions`
- `_permission_filtered_tools`
- `_read_only_tools`
- `_mutation_action_tools`

理解：

```text
当前任务状态
+ 当前 Working Evidence
+ 当前权限模式
+ 当前恢复阶段
→ 本次模型请求的 System Prompt 和工具表
```

### 9.4 工具执行管线

重点看 report 指出的工具执行区域：

[`agent_loop.py`](D:/learn_project/forgecode/forge/runtime/agent_loop.py:1379)

完整顺序是：

```text
PreToolUse Hook
→ 阶段工具检查
→ ToolCall 参数检查
→ 保护性语义检查
→ PermissionManager
→ Task Scope
→ Checkpoint
→ ToolRegistry.execute
→ AfterFileEdit / PostToolUse Hook
→ WorkspaceTracker.refresh
→ WorkingState.observe
→ TaskManager 更新
→ Verification 更新
```

### 9.5 完成判定

读：

- `_finish_rejection_reasons`
- `finish_task`
- `CompletionGate`
- `render_completion_ready_context`

核心问题：

> 为什么模型返回一句“已经完成”仍然不能结束任务？

## 阶段 10：权限、安全和 Hooks

阅读：

- [`forge/permissions/risk.py`](D:/learn_project/forgecode/forge/permissions/risk.py)
- [`forge/permissions/policy.py`](D:/learn_project/forgecode/forge/permissions/policy.py:85)
- [`forge/permissions/approval.py`](D:/learn_project/forgecode/forge/permissions/approval.py)
- [`forge/hooks/manager.py`](D:/learn_project/forgecode/forge/hooks/manager.py)
- [`forge/hooks/runner.py`](D:/learn_project/forgecode/forge/hooks/runner.py:36)
- `tests/hooks/`
- `tests/mcp/test_permissions.py`

理解三种权限模式：

```text
plan
  → 只允许读取

supervised
  → 有副作用的操作需要审批

auto
  → 低风险自动执行，高风险仍需审批
```

重点理解：

- 风险依据最终 ToolCall，而不是用户自然语言
- 用户级、项目级、会话级规则如何合并
- Hook 如何在模型、工具、编辑、验证和 Session 生命周期中介入
- 为什么事后 Hook 失败不能回滚已经发生的文件修改
- 当前为什么还不是 OS 级 Sandbox

## 阶段 11：Workspace Tracker 和 Completion Gate

阅读：

- [`forge/runtime/workspace.py`](D:/learn_project/forgecode/forge/runtime/workspace.py:25)
- [`forge/runtime/completion.py`](D:/learn_project/forgecode/forge/runtime/completion.py:33)
- [`tests/runtime/test_workspace_completion.py`](D:/learn_project/forgecode/tests/runtime/test_workspace_completion.py)

重点追踪：

```text
begin_turn()
→ baseline snapshot
→ watch_paths()
→ 工具修改文件
→ refresh()
→ workspace_revision += 1
→ changed_paths
→ verify evidence
→ CompletionGate.evaluate()
```

Completion Gate 检查：

- 是否有真实 Diff
- 是否修改了禁止路径
- 是否越出允许范围
- 是否通过验证
- 验证是否属于当前 revision
- 是否通过 `git diff --check`

需要能解释：

> 为什么旧 revision 的测试结果不能证明当前代码是正确的？

## 阶段 12：恢复机制和失败状态机

重点看：

- [`forge/runtime/agent_loop.py`](D:/learn_project/forgecode/forge/runtime/agent_loop.py:2500)
- [`forge/runtime/agent_loop.py`](D:/learn_project/forgecode/forge/runtime/agent_loop.py:3519)
- `tests/runtime/test_m2_agent_loop.py`
- `tests/runtime/test_agent_loop.py`

按错误类型学习：

```text
Tool Protocol Recovery
  → 工具名、参数或 JSON 错误

Mutation Recovery
  → Patch、replace、write 失败

Verification Recovery
  → 测试、构建、类型检查失败

Dependency Recovery
  → tsc、vite 等工具缺失

Stagnation Recovery
  → 多轮没有新证据或真实修改

Finalization Recovery
  → 门禁已满足但模型还在继续调用工具
```

重点理解：

> ForgeCode 不是“失败就 retry”，而是根据失败语义改变下一轮允许的工具和上下文。

## 阶段 13：Session、Checkpoint 和 Trajectory

阅读：

- [`forge/sessions/store.py`](D:/learn_project/forgecode/forge/sessions/store.py:60)
- [`forge/sessions/checkpoint.py`](D:/learn_project/forgecode/forge/sessions/checkpoint.py:33)
- [`forge/sessions/trajectory.py`](D:/learn_project/forgecode/forge/sessions/trajectory.py:49)
- `tests/sessions/`

分别理解：

### Session Journal

保存：

```text
用户消息
模型消息
Tool Started / Completed
Router
Permission
Hook
Compaction
Checkpoint
TurnCompleted
```

### Checkpoint Store

保存：

```text
修改前文件状态
修改后文件状态
文件内容 Blob
```

支持：

```text
/undo
/rewind
```

### Trajectory

保存面向分析的轻量轨迹：

```text
事件类型
工具摘要
成功状态
Token 用量
最终状态
```

重点理解：

- Journal 为什么 append-only
- 为什么恢复时不能自动重放未完成的副作用工具
- 为什么 Session 需要 durable head
- 为什么两个恢复进程不能同时追加
- 为什么大 Payload 不能全部直接塞进 Journal

## 阶段 14：MCP、飞书办公、Skill 和 Explore Agent

核心主线理解完后，再读扩展：

- [`forge/mcp/config.py`](D:/learn_project/forgecode/forge/mcp/config.py)
- [`forge/mcp/manager.py`](D:/learn_project/forgecode/forge/mcp/manager.py:44)
- [`forge/mcp/tool.py`](D:/learn_project/forgecode/forge/mcp/tool.py)
- [`forge/channels/config.py`](D:/learn_project/forgecode/forge/channels/config.py)
- [`forge/channels/gateway.py`](D:/learn_project/forgecode/forge/channels/gateway.py)
- [`forge/channels/feishu.py`](D:/learn_project/forgecode/forge/channels/feishu.py)
- [`forge/office/feishu.py`](D:/learn_project/forgecode/forge/office/feishu.py)
- [`forge/office/mcp_server.py`](D:/learn_project/forgecode/forge/office/mcp_server.py)
- [`forge/skills/manager.py`](D:/learn_project/forgecode/forge/skills/manager.py)
- [`forge/subagents/explore.py`](D:/learn_project/forgecode/forge/subagents/explore.py:144)
- `tests/mcp/`
- `tests/channels/`、`tests/office/`
- `tests/skills/`
- `tests/subagents/`

理解它们如何接入主系统：

```text
MCP / Skill / Explore Agent
→ ToolRegistry
→ PermissionManager
→ ToolResult
→ WorkingState / Context
```

特别注意：

- MCP 工具不是特殊旁路，而是进入统一权限和审计管线
- Explore Agent 使用隔离的只读上下文
- Skill 主要注入工作说明，不会绕过工具权限和 Completion Gate

飞书办公链路要单独看两层：`forge/channels/` 负责官方 WebSocket 消息、白名单、@ 提及过滤、事件去重、按聊天隔离 Session 和审批卡片；`forge/office/` 负责一个本地 stdio MCP sidecar，只暴露文档读取/创建/更新和消息发送四个窄范围工具。运行时会把已配置且凭据齐全的飞书 Channel 转成 `office-<channel>` MCP Server，并让工具继续经过统一的 `ToolRegistry`、`PermissionManager` 和审计管线。

建议实际走一遍：

```powershell
uv run forge feishu setup       # 首次运行：按终端验证码私聊机器人完成配对
uv run forge integrations
uv run forge gateway --channel feishu-main
```

重点验证：启用 Channel 必须有用户或群聊白名单；凭据只能来自环境变量；高风险的文档写入和群发需要原始请求者对参数哈希一致的单次审批；飞书文档更新必须带读取时的 revision，网络结果未知时不能自动重发。

## 阶段 15：Qwen 工具调用蒸馏扩展

阅读：

- [`extensions/qwen_tool_distillation.py`](D:/learn_project/forgecode/extensions/qwen_tool_distillation.py)
- [`tests/extensions/test_qwen_tool_distillation.py`](D:/learn_project/forgecode/tests/extensions/test_qwen_tool_distillation.py)

这条线是可选的模型工程扩展，不是核心 Agent Loop 的隐式分支。先理解四个边界：

1. `QwenModelClient` 把 OpenAI-compatible 流式响应转换回 ForgeCode 的 `ModelStreamEvent`，处理并行工具调用、增量 JSON、不可用工具、截断和空响应；
2. `RecordingModelClient` 在 `ModelClient.stream` 边界记录已经过上下文压缩和阶段工具过滤的真实请求，因此样本包含动态 system、消息历史和当前工具 Schema；
3. `TraceRecorder` 以 append-only JSONL 记录 model request/response、tool result、workspace revision、验证和完成结果，版本分别是 `forgecode-qwen-distillation/v1` 与 `forgecode-qwen-sft/v1`；
4. `clean_episode` 在导出前校验公开来源、许可证、密钥、工具 Schema/结果配对、权限合规、执行验证和 Completion Gate，`build_sft_rows` 只保留未截断且可独立验证的 next-action 样本。

之后再读 `build_dpo_row`、`ForgeCodeRolloutEnvironment` 和 `write_training_assets`：它们分别提供安全优先的偏好样本、隔离 Git worktree 的有限 rollout，以及 SFT/DPO/GRPO 的 ms-swift recipe 和 vLLM/SGLang 服务命令。扩展默认关闭 thinking，训练和推理依赖需要单独安装，不应把生成的 recipe 或未验证轨迹当作核心产品配置。

可执行的最小检查：

```powershell
uv run python -m extensions.qwen_tool_distillation preflight
uv run python -m extensions.qwen_tool_distillation write-recipes distill/recipes
uv run python -m extensions.qwen_tool_distillation build-sft data/teacher.jsonl data/train.jsonl
```

## 阶段 16：本地 Eval Harness

阅读顺序：

- [`evals/cases/python-calculator-001.yaml`](D:/learn_project/forgecode/evals/cases/python-calculator-001.yaml)
- [`evals/cases/typescript-todo-001.yaml`](D:/learn_project/forgecode/evals/cases/typescript-todo-001.yaml)
- [`evals/cases/java-order-service-001.yaml`](D:/learn_project/forgecode/evals/cases/java-order-service-001.yaml)
- [`evals/runner.py`](D:/learn_project/forgecode/evals/runner.py:36)
- [`tests/evals/test_runner.py`](D:/learn_project/forgecode/tests/evals/test_runner.py)

完整流程：

```text
读取 YAML EvalCase
→ 从固定 Git Commit 解包 Fixture
→ 移出 hidden tests
→ 初始化新的 Git baseline
→ 构造 allowed / forbidden paths
→ 启动 Conversation
→ Agent 修改代码
→ Agent 内部 verify
→ 外部 build
→ 外部 public tests
→ git diff --check
→ 恢复 hidden tests
→ 外部 hidden tests
→ 生成 EvalOutcome 和 trajectory
```

关键点：

> Eval Runner 不相信 Agent 自己说完成，而是在 Agent 之外重新执行构建、公开测试、隐藏测试和 Diff 检查。

重点看：

- `PreparedFixture`
- `build_agent_prompt`
- `run_agent`
- `run_case`
- `acceptance_reasons`

运行方式可以先用：

```powershell
uv run python -m evals.runner --list
uv run python -m evals.runner --case python-calculator-001
```

同时理解当前边界：

- [`evals/metrics.py`](D:/learn_project/forgecode/evals/metrics.py) 目前仍是占位文件
- 本地 Eval 有独立的逐案例结果和轨迹
- 跨运行的成本、恢复次数、成功率聚合还不是完整模块

## 阶段 17：Harbor Benchmark 与能力矩阵

外部评测不应只看 Aider Polyglot。先阅读：

- [`benchmark/README.md`](benchmark/README.md)
- [`benchmark/harbor/README.md`](benchmark/harbor/README.md)
- `benchmark/catalog.py`
- `benchmark/cli.py`
- `benchmark/harbor/forgecode_agent.py`
- `benchmark/harbor/run_dataset.py`
- `benchmark/harbor/run_aider.py`
- `benchmark/harbor/run_swebench.py`
- `benchmark/harbor/run_terminal.py`
- `benchmark/harbor/summarize.py`
- `benchmark/harbor/aider_feedback_plugin.py`

先查看当前能力矩阵：

```powershell
uv run python -m benchmark list
```

当前三条可执行路径分别是：

```text
Aider Polyglot
  → 多语言仓库代码编辑

SWE-bench Verified
  → 真实 GitHub Issue 的仓库级修复

Terminal-Bench 2
  → 通用终端任务、脚本、环境和工具协作
```

对应入口是 `benchmark.harbor.run_aider`、
`benchmark.harbor.run_swebench` 和 `benchmark.harbor.run_terminal`。三者都
使用 Harbor 的隔离任务环境和独立 verifier；只有 Aider Polyglot 使用仓库中
现有的一次 feedback repair。

理解 Harbor Benchmark 和本地 Eval 的区别：

| 本地 Eval | Harbor Benchmark |
|---|---|
| ForgeCode 自己控制 Runner | Harbor 负责隔离执行和评分 |
| 3 个固定案例 | Aider、SWE-bench、Terminal-Bench 等外部数据集 |
| 本地恢复 hidden tests | Docker / Harbor 独立 verifier |
| 主要验证系统不变量 | 验证跨语言、仓库级和终端任务表现 |

通用 Benchmark 流程：

```text
Harbor Dataset
→ 隔离任务工作区
→ ForgeCode 非交互 Agent
→ 独立 Verifier
→ reward
→ pass rate / repair / token / tool-call 统计
```

协议差异必须单独记录：

- Aider Polyglot：多语言代码编辑，共 225 题；允许一次 verifier feedback repair
- SWE-bench Verified：真实 GitHub Issue 修复；单次尝试，不使用 Aider repair 插件
- Terminal-Bench 2：终端、脚本、环境和工具协作；单次尝试
- `pass@1`：第一次尝试即通过
- `pass@2`：Aider 历史指标，表示允许一次 repair 后通过；不能套用到不允许 repair 的基准
- infrastructure error：环境、镜像、网络或 Provider 错误
- code failure：执行环境正常，但 Agent 没有完成任务

阅读汇总代码时，重点区分：

- `scored_trials`：真正进入评分分母的任务数
- `final_pass_rate`：按该基准正式协议计算的最终通过率
- `pass_at_1_rate`：首次尝试通过率
- `infrastructure_failures`：不应与代码失败混为一谈的基础设施错误
- token/tool telemetry：成本和行为统计，不等于正确率

当前仓库文档中有多组不同时间的测试和 Benchmark 数字，简历或报告中不要直接混用。每次结果至少记录：

```text
运行时间
ForgeCode 代码版本
模型和 Provider
Benchmark 名称与数据集 commit
Harbor / Docker 版本
并发数
重试次数
是否允许 feedback repair
scored trials
基础设施错误数
pass@1 或首次通过率
最终通过率 / reward
Token 和工具调用统计
```

BFCL V4 可以作为未来的工具调用基准，但需要先完成 ToolRegistry 到外部
tool schema/state machine 的适配；OSWorld 需要 GUI、桌面应用和视觉输入，
不能用当前终端 Agent 的分数替代。

## 最终你要形成的完整讲解

你最终应该能够从下面这句话开始，连续讲 5～10 分钟：

> 用户从 CLI 输入 Prompt 后，Typer 启动交互会话，CLI 装配 ModelClient、Router、ToolRegistry、ContextManager、PermissionManager、SessionJournal 和 CheckpointStore。Router 先判断任务类型，TaskManager 创建或恢复 ActiveTask。随后 ContextManager 组合 System、Repository、Task、Working 和压缩后的历史上下文，向模型提供当前阶段允许的工具。模型生成 ToolCall 后，运行时依次执行协议检查、权限审批、任务作用域、Checkpoint 和工具执行，再通过 WorkspaceTracker、WorkingState 和 VerificationEvidence 更新状态。只有真实 Diff、当前 revision 的验证证据、合法路径和 `finish_task` 声明全部满足时，Completion Gate 才允许完成。整个过程通过 Journal、Checkpoint 和 Trajectory 持久化，最后由本地 Eval 或 Harbor 的独立测试和 verifier 重新判定结果。

如果你能独立讲清楚这条链路，就不只是“会用一个 Vibe Coding 项目”，而是已经真正理解了 Agent Harness 的实现。
