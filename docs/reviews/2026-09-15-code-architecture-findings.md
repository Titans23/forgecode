# 当前工作树架构评审：事实、推断与迁移边界

日期：2026-09-15。范围是当前工作树，包含 9 月 14 日尚未提交的合同继承、重复完成保护和截止时间适配。此次只读核查代码并运行一项不调用模型、不启动进程任务的内存级离线复现；没有运行评测、全量测试或修改运行时代码。

结论：应保留单一 TurnRunner、ToolExecutor、权限边界、追加日志和隔离评测控制器。需要重构的是要求/检查/运行证据的生命周期，以及模型协议和上下文投影边界。当前问题不足以证明需要把执行内核替换成多代理系统。以下优先级是建议实施顺序，不表示每项都已证明导致本轮失分。

## F1 · P1：模型提供的测量与判据仍被用作完成硬门禁，结构可校验不等于正确性可判定

**事实。** `OutputCheck` 的期望值、要求 ID、来源文本和 `source_ref` 都由工具请求提供；实际值从同一请求指定程序的 stdout 最后一行 JSON 获取。`asserted_requirement_ids` 只要求存在 ID 和非空 `expected_source`。完成门禁主要检查要求 ID 是否在成功证据中、是否存在 `check_signature`、命令是否被正则判断为行为检查。

证据：[verification_checks.py:21](D:/projects/forgecode/forge/tools/verification_checks.py:21)、[verify.py:125](D:/projects/forgecode/forge/tools/verify.py:125)、[completion.py:164](D:/projects/forgecode/forge/runtime/completion.py:164)、[verification.py:111](D:/projects/forgecode/forge/runtime/verification.py:111)。

**推断。** 即使合同继承完全正确，模型仍可以无意中用“输出形状正确”来代表“行为正确”，或用自己生成的测量程序验证自己的实现。`source_ref` 目前是普通字符串，不是运行时解析并核对过的原文锚点或文件哈希。更多必填字段不能补足独立判据。

**建议。** 为 Requirement 和 CheckSpec 增加来源等级：用户/调用方约束、仓库已有测试、外部参考、模型提出检查。允许模型检查作为工作证据，但不能将其统一解释为独立验收。终止、交付候选产物、内部验证结论与官方评分应分开存储。不能通过删除历史失败或放松官方测试提高成绩。

## F2 · P1：验收解释只能追加，缺少用户更正与模型假设撤回的正规版本语义

**事实。** `_merge_acceptance` 验证引用是用户 goal/directives 的子串；同一 `source_quote + clause_id` 保留首次解释，不允许更新；不同 clause 可以继续追加。`continue_active` 追加用户指令，保留已有 acceptance；完成时逐一要求所有 acceptance 当前通过。

证据：[manager.py:44](D:/projects/forgecode/forge/tasks/manager.py:44)、[manager.py:114](D:/projects/forgecode/forge/tasks/manager.py:114)、[state.py:11](D:/projects/forgecode/forge/tasks/state.py:11)、[completion.py:164](D:/projects/forgecode/forge/runtime/completion.py:164)。

**推断。** 模型第一次对原文的错误理解可能成为不可撤回义务；用户明确修改要求后也缺少 superseded/retracted 状态。新 clause_id 解决不了冲突，只会增加必过项。这与防止模型偷降阈值是两个不同的问题。

**建议。** 区分原始要求与可修订解释。用户更正形成有来源事件的 RequirementRevision；模型假设可撤回但必须保留原因。调用方固定要求不允许模型撤销。避免让“第一版模型解释永远不可改”代替权限与来源检查。

## F3 · P1：最新重复完成保护仍可被相同检查的新运行 ID 重置

**事实与离线复现。** `_observe_completion_rejection` 从检查集合排除了运行 ID，却将完整 `reasons` 纳入指纹。完成门禁的失败原因包含最新 `verification_id`。本次用四次相同命令、相同 cwd、相同退出码、相同工作区和检查，仅改变运行 ID 的内存记录复现：四次 `unchanged_completion_rejections` 均为 1，`terminal` 均为 None。

证据：[runner.py:590](D:/projects/forgecode/forge/runtime/runner.py:590)、[completion.py:261](D:/projects/forgecode/forge/runtime/completion.py:261)、[completion.py:432](D:/projects/forgecode/forge/runtime/completion.py:432)。

复现结果：

```text
identical_failed_check_attempt=1 unchanged_rejections=1 terminal=None
identical_failed_check_attempt=2 unchanged_rejections=1 terminal=None
identical_failed_check_attempt=3 unchanged_rejections=1 terminal=None
identical_failed_check_attempt=4 unchanged_rejections=1 terminal=None
```

这不证明最新评测每道题实际触发了该路径，但证明“仅改变验证运行 ID 不重置”的设计意图尚未实现。另有 inspection 缺少证据、验收注册失败、blocked 缺少外部证据等拒绝分支，没有经过这一计数入口。

**建议。** 门禁返回 `reason_code + obligation_id + state_fingerprint` 等结构化理由；展示文本独立生成。无进展计数使用语义事实摘要，不使用随机运行 ID 或自然语言。所有完成拒绝通过同一决策入口；有新诊断不等于有进展，不能只靠多执行一个探针重置保护。

## F4 · P1：任务要求跨 turn 保留，证据账本却仅存活于一个 turn

**事实。** 每次 `Conversation.stream` 创建新的 TurnRunner 和 TurnState；EvidenceLedger 默认空。会话恢复返回 messages、active_task 和 indeterminate_tools，不返回验证账本。虽然终止日志中保存了 verification_history，SessionStore 的回放只恢复状态及变更路径，没有重新构建证据；合同继承只查当前 turn 的 `state.evidence.verification`。WorkspaceTracker 的 revision 和 environment_epoch 也在每个 turn 重置为零。

证据：[agent_loop.py:297](D:/projects/forgecode/forge/runtime/agent_loop.py:297)、[runner.py:35](D:/projects/forgecode/forge/runtime/runner.py:35)、[turn_state.py:114](D:/projects/forgecode/forge/runtime/turn_state.py:114)、[store.py:53](D:/projects/forgecode/forge/sessions/store.py:53)、[store.py:748](D:/projects/forgecode/forge/sessions/store.py:748)、[runner.py:417](D:/projects/forgecode/forge/runtime/runner.py:417)、[workspace.py:39](D:/projects/forgecode/forge/runtime/workspace.py:39)。

**推断。** 用户“继续”或恢复会话后，原要求仍在，但旧验证 ID 无法用于继承。成功证据必须重做，历史失败的硬义务反而可能不再参与计算。日志有记录不等于运行时能恢复事实。这是长期 coding-agent 会话的重要架构缺口；单 turn benchmark 不足以覆盖它。

**建议。** 将 Requirement/CheckSpec/CheckRun 放到 task/session 级日志与投影中，Run 与 turn_id 关联。恢复保留事实与未解决义务，成功证据按依赖指纹重新判断是否新鲜。禁止把上个 turn 的 revision=0 直接等同于本 turn 的 revision=0。迁移旧记录应标记 freshness unknown，而非假定有效或删除。

## F5 · P1：环境失效按工具名处理，既有过度失效也有漏失效

**事实。** 所有 `effect == process` 且工具名不是 verify 的执行都会递增全局环境 epoch，不管命令是否只是读状态；verify 运行任意被授权命令，却不增加该 epoch。所有当前验证要求 workspace_revision 与 environment_epoch 完全一致。Git 快照默认忽略 ignored files，只有显式 watch 的路径额外捕获。

证据：[executor.py:264](D:/projects/forgecode/forge/runtime/executor.py:264)、[completion.py:126](D:/projects/forgecode/forge/runtime/completion.py:126)、[verify.py:96](D:/projects/forgecode/forge/tools/verify.py:96)、[workspace.py:158](D:/projects/forgecode/forge/runtime/workspace.py:158)。

**推断。** `run_command` 只读诊断也能让已有通过证据失效；verify 内若改变依赖、忽略目录或工作区外允许目标，旧证据又可能继续被认为当前。与完成保护叠加时，空环境探针还可能重置“无进展”。不能仅把 verify 也全局递增，因为连续独立验证又会互相使证据过期。

**建议。** 分离过程能力、已观测副作用和证据依赖。逐步引入文件集/输入哈希/环境配置版本的 DependencyFingerprint；未知副作用保守失效，可信只读检查不使无关证据过期。初期按工具和命令能力标注粗粒度依赖，避免立即建设复杂全量依赖追踪器。

## F6 · P2：provider-neutral 停留在 Protocol 和流事件层，消息与工具定义仍是 Anthropic 形状

**事实。** `ModelClient` 接受未约束 dict 列表。工具定义使用 `input_schema`；runner 构建 assistant `tool_use` 和 user `tool_result` 块；上下文计量、原子组和文件证据提取直接匹配这些字段。factory 固定构造 AnthropicModelClient，并把这些消息原样传给 Anthropic SDK。

证据：[model_client.py:93](D:/projects/forgecode/forge/runtime/model_client.py:93)、[factory.py:84](D:/projects/forgecode/forge/runtime/factory.py:84)、[tools/base.py:270](D:/projects/forgecode/forge/tools/base.py:270)、[agent_loop.py:902](D:/projects/forgecode/forge/runtime/agent_loop.py:902)、[agent_loop.py:925](D:/projects/forgecode/forge/runtime/agent_loop.py:925)、[compactor.py:702](D:/projects/forgecode/forge/context/compactor.py:702)。

**推断。** 换 model_id 只能证明网关接受这个模型名，不能证明不同提供方的工具、思考、缓存和截断语义被正确适配。没有证据说明本轮零分由协议差异直接引起，但当前结构会使后续多提供方支持侵入 runner、context 和 session。

**建议。** 先定义内部 Message/ToolCall/ToolResult/ToolSpec 类型及适配器能力描述，provider adapter 负责渲染协议和归一化结果。先为现有 Anthropic 形状提供无行为变化的兼容适配器，再添加其他协议；保留原始响应审计。旧会话读取需有版本转换，不能一次性重写历史日志。

## F7 · P2：子代理有局部预算，却没有纳入父级总预算与调用审计

**事实。** 默认工具注册 ExploreRepositoryTool；它内部创建独立 Conversation 和局部模型/输入预算，用 metadata 返回调用量。父 runner 记录该次工具请求，但未将这些 metadata 的模型调用/token 加入父 TurnState。子 BudgetedModelClient 把 contextvar observer 设成子 state 的观察器，因此父预算观察器不会自动累计子请求。

证据：[tools/__init__.py:60](D:/projects/forgecode/forge/tools/__init__.py:60)、[explore.py:193](D:/projects/forgecode/forge/subagents/explore.py:193)、[explore.py:213](D:/projects/forgecode/forge/subagents/explore.py:213)、[runner.py:435](D:/projects/forgecode/forge/runtime/runner.py:435)、[model_budget.py:72](D:/projects/forgecode/forge/runtime/model_budget.py:72)。

**推断。** 启用探索子代理时，“整轮模型预算120次”和 token 统计未必代表包含子代理的总调用。不是本次已证明失分原因，但若整体设计直接扩展多代理会放大预算与追踪缺口。

**建议。** 先引入根级 BudgetAccount 与子任务配额保留/归还，request 带 task_id、parent_request_id、stage；局部和全局额度同时检查。暂保留只读探索，只有证据显示独立子任务能降低主上下文成本时再扩展写入子代理。

## F8 · P2：上下文摘要保存自然语言验证结论，缺少可重建的证据投影

**事实。** 压缩摘要的 verification、failed_attempts 都是字符串数组，没有要求保留 CheckSpec ID、最新失败/成功 Run ID 或依赖指纹。恢复策略保留最近消息与文件阅读片段；runner 的 system 注入 acceptance 文本，但不注入结构化当前检查清单。压缩计量使用字符数/4 的通用估算。

证据：[compactor.py:75](D:/projects/forgecode/forge/context/compactor.py:75)、[compactor.py:131](D:/projects/forgecode/forge/context/compactor.py:131)、[runner.py:195](D:/projects/forgecode/forge/runtime/runner.py:195)、[manager.py:175](D:/projects/forgecode/forge/context/manager.py:175)。

**推断。** 历史工具片段归档后，模型可能知道“检查失败过”却不再知道如何准确引用和修复；再次 finish 才收到冗长拒绝。不应把模型摘要当成账本恢复。上下文长度与成本问题需要请求级实际测量，不能仅从总 input token 推断压缩失败。

**建议。** 生成短小确定性的 ActiveTaskProjection：活跃要求、最近产物、未解决检查 ID、可查询证据入口、预算。模型摘要只保存推理假设和下一步，机器事实从日志重建。保留现有大输出归档、工具调用/结果原子组和原始用户指令锚点。将稳定系统前缀与动态状态分块，以便提供方适配器管理缓存；收益需实测。

## F9 · P2：截止时间已同步，但通过全局修改 Harbor 私有方法实现

**事实。** DeadlinePlugin 在 on_job_start 改写类级 `Trial._run_agent_phase`，读取 kwargs 的 timeout_sec；on_job_end 才按当前 wrapper 身份恢复。这是私有方法的全局 monkeypatch。当前 run 每个独立进程运行一个 job，可限制其影响，但不构成稳定的第三方接口。

证据：[deadline_plugin.py:8](D:/projects/forgecode/benchmark/harbor/deadline_plugin.py:8)、[forgecode_agent.py:102](D:/projects/forgecode/benchmark/harbor/forgecode_agent.py:102)。

**建议。** 尽可能用正式 task/phase 配置接口传递绝对 deadline；未有可用 API 时把补丁封装在兼容层，锁定受测 Harbor 版本、启动校验签名与参数、缺失 deadline 明确报配置错误，并在 finally 清理。不要把评测平台适配渗入核心完成状态机。

## 迁移边界与风险

1. 保留单模型/工具循环和统一执行器；不要同时替换模型协议、存储与完成规则，否则无法归因。
2. 第一阶段新增结构化 CompletionReason、修复 F3，并以已有真实日志离线回放确认状态机差异；不调用模型。
3. 第二阶段引入只读 TaskEvidenceProjection 与稳定合同 ID，双写旧字段和新事件，逐步切换 gate，保留历史 Run。
4. 第三阶段引入依赖新鲜度和有来源的要求修订；旧记录采用 unknown freshness，需要复验，不批量宣称已通过。
5. provider canonical IR 独立迁移，先保持现有模型行为。工具 schema、上下文选择和回复渲染均需在适配测试中覆盖。
6. 子代理扩展放到全局预算与 request lineage 完成之后。当前证据不支持为提高通过率立即新增多写入代理。

优先防止两种错误迁移：把模型自测包装成独立真值；为了避免过严门禁，直接把旧失败清空或把 finished 等同于 correct。最终收益必须通过后续受控评测衡量；本次未启动新评测。
