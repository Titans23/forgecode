# ForgeCode 证据分析与优化设计

日期：2026-09-05。分析版本：`390af0b`，分支 `codex/runtime-v2`。

本次工作是代码审查、历史日志分析与离线机制探针，未修改生产代码，未调用模型服务，未启动 benchmark。下述设计尚未实现，探针通过运行不等于产品回归通过。保留原始官方结果，不改写历史 session。

## 1. 判断与优先级

当前最有价值的工作是补齐三条链路：可信测量、可审计的模型响应接收、原始要求到验证证据的追踪。现有单内核方向应保留，不需要再搭一套恢复控制器、第二验证智能体或任务专用解题器。

本轮观察不能支持“只要修协议就能到 5/10”：circuit、MIPS、path 在协议终止前已有真实功能失败。完成证据修复也首先降低错误完成率，并不直接提供正确算法。

建议实现顺序：P0 分类与请求生命周期 → P1 协议拼装及有限安全恢复 → P1 要求与证据关联 → P2 成本和策略诊断。分类与生命周期先做，才能判断后续改动真实效果。

## 2. 最近轮次重新核对

证据根目录：`benchmark/runs/harbor/terminal-bench-2-runtime-v2-390af0b-c6-network-restored-20260905/2026-09-05__14-24-02`。

已逐题读取官方 reward、verifier 输出、kernel final payload，以及 session 中的计划、工具调用/结果、最终消息检查点。官方原始通过 2/10；protein verifier 在 uv bootstrap 阶段失败。当前汇总器重算为 9 scored、2 pass、6 agent failure、1 timeout、0 model failure；最后一个数字不能反映实际终止原因。

| 任务 | raw | 内核状态 | 本次证据结论 |
|---|---:|---|---|
| HTML | 1 | completed | 实际进行了过滤与浏览器 alert 检查；不再只依赖未调用测试函数的脚本 |
| distribution | 1 | completed | 官方通过；保留作为有效数值断言的正例 |
| POV-Ray | 0 | completed | 版本和渲染通过；源码一致性检查失败。两轮渲染均报告 SSIM 0.8731，不能笼统称渲染退化 |
| circuit | 0 | incomplete_tool_call | 前面已发生未完成工具块和空响应，并消耗两次共享纠正机会；已有独立数值检查失败 |
| CompCert | 0 | time_budget_exhausted | 模型约 232 秒、工具约 1560 秒；600 秒依赖安装超时后，再次安装直到整题预算耗尽 |
| MIPS | 0 | empty_model_response | 最后可见验证仍有运行错误；此前工具参数错误也使用同一 protocol_errors 计数 |
| overfull | 0 | completed | 编译与无溢出不是唯一要求；正确提出的允许替换约束始终没有对应的独立执行检查 |
| path | 0 | incomplete_tool_call | 已要求相似度断言，但本地检查更早就因未生成输出而失败；协议问题不能掩盖此功能失败 |
| protein | 0 | completed | verifier 未实际检验产物；Agent 自己明确承认截断与身份要求未满足，却最终被记录为 completed |
| video | 0 | completed | 只验证接口、格式、帧号顺序/范围；没有建立起落事件正确性的独立证据 |

应同时保存三个分母：固定任务数 10、已完成有效 verifier 的任务数 9、排除三项终止性模型/协议故障和一项 verifier 环境故障后的候选有效任务数 6。最后的 6 是按拟议口径推导，不能覆盖原有统计，更不能据此把成绩宣传成更好的通过率。最终验收仍是同版本完整有效十题连续两轮各至少 5/10。

另外，完成任务也可能在途中经历并恢复协议异常，例如 protein。需分别统计终止故障与已恢复事件，不能只从最终 stop_reason 推断全轮服务健康。

## 3. 已确认的代码机制问题

### 3.1 汇总器边读边累计，缺少每题统一分类

`benchmark/harbor/summarize.py` 在读取 kernel payload 之前决定 scored 和 agent failure。payload 循环只补充 timeout 和用量，不处理 empty_model_response、incomplete_tool_call 等。异常分支又先将所有 Harbor exception 计入 infrastructure，连 AgentTimeoutError 也包含在内。

离线临时夹具复现：raw=0、exception=null、kernel=empty_model_response，输出 model_service_failures=0、agent_failures=1、scored_trials=1。

第二个夹具只有一题：Harbor RuntimeError、status timed_out=true、kernel time_budget_exhausted，输出 infrastructure=1、agent_failure=1、agent_timeout=1。这是分类重叠，不能将三个类别相加当作三题。

对交接中的“timeout 重复计数”需作精确修正：当前代码已有 `_is_agent_timeout` 守卫，正常 AgentTimeoutError 加 kernel timeout 的组合不应自动声称会计两次。此次确认的是同一 trial 在多类失败中重叠；具体重复 timeout 的旧模式应另补夹具后再下结论。

### 3.2 有恢复，但错误分类、预算与持久化不足

`model_client.py` 已阻止对开始产生文本或工具块的网络失败进行透明重试，并把实际 HTTP attempt 计入统一模型预算。这些机制应保留。

但 `runner.py` 将大部分 ModelProtocolError 统一反馈为“纠正响应格式”，共用 `state.protocol_errors`；工具参数纠正也增加此计数。网络导致的空终止、未完成块、非法参数和真正格式错误因此相互消耗额度。两次纠正不是“两次连续同类失败”，可能被任务很早的其他错误用尽。

对于 partial protocol error，runner 还能提交下一次模型请求；这不同于 SDK 透明重放，而且当前批次尚未执行，因此不能直接断言发生了重复副作用。然而它没有提供完整的接收状态、未完成块清单和持久化接收边界，不足以证明所有分支都满足交接规定的严格恢复条件。

最近十题的 session 事件清单没有 model_call_started/failed/retry 等请求级记录。runner 向消费者 yield 这些事件，但自身没有写入 journal；`Conversation.record_session_event` 主要处理工具与 turn 终止。`TrajectoryRecorder` 有请求日志能力，并不等于 benchmark 实际保存了这些日志。

后果：可以确认协议失败存在，却不能从现有 session 唯一判断供应商漏发事件、兼容网关转换、SDK 拼装还是内核处理问题。反馈文本在 checkpoint/压缩前后也不是可靠的逐请求故障台账。

### 3.3 全局 semantic_output 导致混合响应丢块

`model_client.py` 只有 `not semantic_output` 时才从最终消息恢复内容。离线 FakeStream 构造：流里有一段文本，final message 里另有完整工具块；输出只有文本、usage、response completed，工具块数量为 0。

这是已复现的兼容拼装缺陷。未证明最近 circuit/path 正是此输入形态。另一个边界是 pending_tool_calls 在 final-content reconciliation 前就报错；不能因此直接把缺少 stop 的半个 JSON 拼接后执行，需先设计接收状态与严格一致性检查。

### 3.4 验收要求有来源引用，但没有稳定关联

`TaskManager.plan` 检查 source_quote 是 goal/user_directives 的精确子串，这是有用的来源约束。但它不证明 condition/check 与原文语义一致，也不证明没有漏项。

`acceptance_criteria` 是可随计划替换的字典列表，没有稳定 requirement ID 或证据引用。`CompletionGate` 不读取这些标准；benchmark policy 仅开启几项布尔要求，并没有逐项 required_coverage。

离线探针：`python -c "print(1)"` 返回 0，covers 写 output format，limitations 明确写 required behavior remains unverified，在当前通用验证策略下仍 allowed=true。不能通过解析这段 limitation 的关键词来修复；需要结构化状态。

### 3.5 状态失效与验收义务混在一起

CompletionGate 先按 revision/epoch 过滤全部 evidence，再计算 unresolved failures。普通 process 工具一律增加 epoch，verify 又被整体豁免。

离线探针：同一版本的强检查失败加弱检查成功，completion 被阻止；仅将 epoch 前进、弱检查在新 epoch 成功，completion 就通过。已确认机制漏洞，但未证明某一道失败完全由它引起。

正确区分应是：旧结果不再证明当前状态，但原来的明确验收义务并未消失。也不能把所有曾失败的探索性命令永久变成验收义务，否则会重新制造不可恢复的完成门禁。

## 4. 逐题暴露的不同问题

### overfull：执行阶段漏验收

session sequence 6 已包含“比较原始和最终文本、检查所有替换”的标准。后续 verify covers 都围绕编译、warnings、保护文件哈希。sequence 384 的 awk 仅输出 `token_constraint_check=manual`，limitations 承认不能建立替换合法性。它仍被归为 behavior。

需要的通用能力是可靠的原始输入快照、可执行的变换检查和未验证义务状态，而不是多补一次“请记得检查”的提示。

### video：需求提取阶段已经弱化

sequence 6 曾把“检测跳跃”写为依据视频而非硬编码，但对应 check 本身仅是检查实现并运行示例，仍偏弱。修正精确引用后的 sequence 13 列表主要剩路径、接口、字段、首帧背景；真实事件正确性没有被单独保留。

因此即使简单要求“计划中每项都有 covers”，也可能完成一个遗漏了核心行为的计划。需求提取覆盖审计必须与执行证据审计分开。合成/变形测试可增加反例，但不是实际视频标注的替代证明；不把官方隐藏帧号加入产品。

### protein：非成功声明被拒后落入普通 completed

sequence 218 主动提交 blocked，说明产物截断且不满足完整要求；sequence 222 被拒，理由是没有外部 blocker。最后普通文字再次承认这一限制，却通过通用完成检查。

不能由该 Agent 的论证断言题面真的矛盾，也不能因 verifier 环境失败判断官方正确性。可以确认的是“已明确未满足的要求没有可靠状态”。应允许 failed + structured unresolved requirements 终止，并在 blocked 被拒时提供明确的 failed 路径；普通最终回答须走同一判定结果。

### POV-Ray：来源与构建工作区的区分

前轮官方三个测试通过；本轮仅源码一致性失败，两轮渲染指标相同。本轮轨迹存在原目录内兼容性修补。应继续区分归档变体、提取布局、源码修补三种可能，不仅凭版本横幅判断输入身份，也不注入官方预期哈希。

通用方案是记录下载与提取来源、保留原始输入、在授权条件下独立构建目录执行兼容性变更，最终比较实际交付与公开要求。不是一律禁止修改所有源码，仍以用户授权为准。

### CompCert、MIPS、circuit、path：协议修复后的策略工作

CompCert 工具时间约占总预算 86.6%。依赖安装已出现一次 600 秒超时，最后再次执行同类安装至 turn 超时。普通完成日志无法完整分摊被取消命令时长，需结合 start/end 事件再精算，不能把所有耗时都说成编译。

MIPS、circuit、path 已有可执行检查给出真实失败。建议保存“失败性质—最小反例—实现假设—下一次区分性实验”，用小范围性质测试定位，再做集成检查。不要用增加 120/240/1800 预算替代诊断，也不要内置题名解法。

## 5. 目标架构

唯一主循环继续由 TurnRunner 持有。新增的是事实结构与纯判定函数，不新增执行路径。

| 现有模块 | 拟议调整 | 不承担的职责 |
|---|---|---|
| ModelClient | 每请求/每块接收状态、严格拼装、结构化故障事实 | 不执行工具、不判断任务完成 |
| TurnRunner / TurnState | 单一恢复决策、统一预算、请求生命周期先持久化后通知 | 不解释文件权限或具体题意 |
| ToolExecutor | 保留统一执行链，补齐工具结果和环境观察事实 | 不判“做够了”，不自动重放 |
| TaskState / EvidenceLedger | 稳定要求、检查身份、证据关系、未解决状态 | 不通过模型计划升级权限 |
| CompletionGate | 对明确合同做 reconciliation，返回结构化缺口 | 不从总结关键词或命令名称推断语义成功 |
| ContextManager | 从账本生成紧凑视图，保留来源与未解决项 | 不拥有第二份验收/恢复状态 |
| summarize | 读取统一 TrialAssessment 后聚合 | 不在多个循环分支增减分类计数 |

## 6. P0：先建立可信测量

新增纯函数 `assess_trial()`，先收集所有观察，再产出一份记录：

```text
TrialAssessment
  trial_id, attempt_id, schema_version
  raw_reward, reward_present
  verifier_state: passed | failed | environment_failed | unknown | not_run
  agent_stop_reason, protocol_failure_kind, observed_faults[]
  primary_outcome: pass | agent_failure | agent_timeout |
                   model_protocol_failure | verifier_environment_failure |
                   infrastructure_failure | unfinished | unknown
  evaluation_eligible, exclusion_reasons[], evidence_refs[]
```

primary_outcome 每题唯一；observed_faults 可多项，但明确是事件/标签，不与题数相加。模型协议终止和产物功能失败可以并存于两个维度。出现矛盾时保留 conflict/unknown，不猜一个解释覆盖原始事实。

分别输出 raw_pass_count/expected_task_count、verifier_scored_count、eligible_trial_count、eligible_pass_count。raw reward=1 即使伴随协议故障也保留 raw=1，但不得作为有效验收通过。Agent 自身超时仍可作为有效失败；供应商故障与 verifier bootstrap 失败使该轮不满足完整有效轮条件。

增加轮次完整性字段：冻结源码 digest、任务集合 digest、模型和预算配置、expected=10、实际唯一任务、重复/缺失、全部 verifier 是否完成。不能只用目录数为 10 推断完整轮。

迁移保留旧 pass_at_1/pass_at_2 等字段，但标记 legacy 并明确语义，不悄悄更换历史分母。统计读取日志按 attempt/turn/event ID 去重，不按 forgecode*.txt 的文件数累加。旧日志缺少身份时输出不确定性。

第一阶段不增加模型请求，成本主要为本地日志处理；不修改官方 verifier 或 reward。

## 7. P1：模型接收与安全恢复

每次实际 HTTP attempt 建立结构化接收记录，包含 request_id、stage、attempt、provider message ID、终止标记是否观察到、stop_reason、usage 是否已知、已输出文本长度、各工具块的 start/argument/stop 状态、已持久化/已分发状态、错误类型、恢复决定及原因。

内核保存脱敏元数据；不默认记录凭据、完整网络头或所有原始 SSE 内容。原始缺陷流可用合成夹具复现。

拼装按 content block index + tool-call ID 去重，不用一个 semantic_output 标志决定是否恢复全消息。正常完整响应才允许检查 final 内容与流块是否一致；已有块与 final 冲突时明确报错。工具缺少完成边界、JSON 不完整、结束原因缺失时不猜参数，不执行。流中有文本而 final 多一个完整工具块的兼容恢复，必须满足完整响应和严格一致性契约。

恢复矩阵：

| 状态 | 拟议行为 |
|---|---|
| 无 assistant 输出、无完整或部分工具调用、无执行副作用的 transient transport/empty | 可有限重试；每次计入同一个模型和时间预算 |
| 正常终止但无语义内容 | 记录 empty_response；仅在上述安全条件成立时有限恢复 |
| 缺少终止标记且无内容 | 标记 stream termination missing，不能与正常空回答混为一谈 |
| 已产生任何部分文本或工具调用 | 不透明重试、不自动重放；持久化部分状态并报告失败/需检查 |
| 同批已有完整调用但另一块不完整 | 保持整批未执行，明确取消完整项，保留未完成项诊断 |
| 工具已开始且结果不确定 | indeterminate，先检查现状，不重放 |
| 参数/工具协议错误 | 保留最多两次纠正，与 transport/empty 的计数分离，仍由统一预算约束 |
| 输出达到 max_tokens | 作为输出预算事件处理，不冒充 server error；既有文本续写与工具截断分别测试 |

不扩大默认重试次数。优先重分类并落实安全条件，避免把上限拆成多个可以叠加放大的恢复循环。纠正计数的生命周期写入 TurnState，不能由成功工具随意清零。

本阶段首先提高归因与恢复安全。对已有 partial 的严格处理可能减少继续机会，必须如实报告这一代价，不能承诺通过率必升。

## 8. P1：要求到证据的关联

采用三个独立对象，先作为现有 TaskState/EvidenceLedger 的版本化字段，不急于新增工具族或服务：

```text
Requirement
  id, source_message_id, source_span, source_hash, original_quote
  origin: caller_contract | model_proposal
  kind: artifact | behavior | numeric | preservation | transformation
  mandatory, interpretation_status, supersedes

CheckSpec
  id, version, requirement_ids[], command/cwd/stdin fingerprints
  assertion_signature, expected_source_refs[], input_baseline_refs[]
  role: acceptance | diagnostic

EvidenceLink
  check_id/version, execution_id, requirement_id
  actual_result_ref, revision, environment_epoch
  assertion_result, freshness, provenance, limitations
```

requirement ID 只负责关系完整性，不能证明语义覆盖。固定的 Caller contract 才提供强约束；模型提取项先记录为 proposal。精确引用只证明出自原文，不能把模型的 condition、阈值、授权路径或“题面矛盾”判断自动升级为事实。

对于当前自然语言 benchmark，公开任务合同的结构化提取仍有语义不确定性：先在同一智能体的既有首轮中记录候选要求，再对原文段落做覆盖审计，显式列出未映射/解释不确定项；不新增默认 verifier 智能体。没有依据的映射只能标为 unknown，不能伪装为 deterministic verified。要做强阻止，入口必须显式启用相应合同策略并接受这种未验证状态的处理规则。

计划改写不得删除已建立的合同要求。要求更新以 supersedes 记录来源和理由；原始用户要求只有新的用户/入口合同能改变。步骤标题和 scopes 仍不影响执行授权。

reconciliation 对每项返回 verified / failed / stale / unverified / interpretation_unknown。当前有效证据、未过期断言、可靠来源齐备时才支持对应 CheckSpec 的结论；程序化检查的正确性仍需独立评审，不能称为通用语义证明。

明确必需且未满足的要求不能靠 weak covers、limitations 或普通自然语言总结消除。普通最终回答与 finish_task 使用同一 reconciliation；failed 可保留产物和解释，不要求先跑成功验证。blocked 仍需外部证据；被拒后明确提示可用 failed，不自动改为 completed。

### 证据失效和检查修正

revision/epoch 变化使旧证明 stale，验收义务保留为待重验；诊断命令失败只保留诊断，不一律阻止完成。不要通过取消 epoch 失效来掩盖义务丢失。

CheckSpec 与执行签名分开：当前精确命令签名可防弱检查清除强失败，但检查脚本修错也会改变签名。允许有来源、可审计的 check version/supersedes，断言条件保持或加强才能替代；阈值降低、比较对象改变必须重新审查依据。没有证据时保留 unresolved，避免“弱化即修复”。

overfull 真实轨迹中曾手工抄错预期文件哈希。建议让内核按明确输入契约生成不可变 baseline ref，由验证引用；不让模型反复复写长哈希。输入读取内容和终端行号展示必须区分。

### 环境追踪

先保持对未知 process 的保守失效，不急于实现复杂环境推断；由上述义务账本消除失效后遗忘要求的问题。后续再用受信工具效果和运行时环境观测细化 invalidation，不能信任模型自报 read_only，也不能让 verify 因名称而豁免真实依赖安装影响。安装后的本次检查可绑定新 epoch，其他旧验证应变 stale。

## 9. P2：成本与调试策略

最近累计输入从 12,805,195 增至 15,209,058，约 +18.8%；输出从 147,720 增至 173,731，约 +17.6%；模型调用 +13.9%，工具请求 +15.2%。这些不是完整账单，而且两轮有效性与通过题目不同，不能据此估计单个补丁的因果收益。

请求生命周期补齐后，分别统计 transport retry、协议纠正、完成拒绝、压缩、普通规划、工具执行和环境准备。长命令被取消也保留开始时间及耗时下界，避免仅合计成功日志漏掉最贵操作。

ContextManager 从同一账本派生紧凑视图：原始要求索引、当前缺口、最新反例、已排除假设、检查来源、余下预算。已通过要求只显示短摘要，失败保留诊断引用；不在系统提示重复塞入整份证据，不用按消息条数裁剪。

对构建任务记录关键路径与可观察里程碑，开始长安装前利用已知耗时及剩余时间判断是否值得推进，并为最终验证留出明确估算时间。最初作为可解释规划信息，不新增强制阶段门禁或硬编码百分比分配。

失败复盘键逐步从 tool_name:error_code 改为标准化诊断、检查身份和环境指纹；“三个 command_failed”通常不是同一根因。提示仅帮助规划，不隐藏工具、不封锁命令。

## 10. 实施批次与回归门槛

| 批次 | 修改范围 | 必须先复现的负例 | 完成标准 |
|---|---|---|---|
| A | summarize + benchmark tests | 空响应算代码失败；单题多类重叠；raw reward 与故障并存 | 每题唯一主分类；原始 reward 不变；旧轮可重算且差异可解释 |
| B | journal、model budget、runner 事件 | 无消费者时请求失败未持久化；重试 ID/用量归属不清 | 每实际 attempt 有唯一生命周期；失败/取消可追踪；CLI/飞书/MCP/benchmark 同内核 |
| C | model_client + runner recovery | 混合 final 丢工具块；半块 EOF；缺终止；计数串扰 | 不漏/不重复分发；partial 不重放；每次恢复受预算约束 |
| D | TaskState、EvidenceLedger、CompletionGate、verify | 弱检查完成；计划漏项；epoch 后义务消失；blocked 后普通 completed | 来源与 ID 稳定；缺口显式；旧会话可读；无权限扩大 |
| E | context 视图与时间诊断 | 压缩后丢义务/反例；取消命令成本缺失 | 状态从账本恢复，上下文可控；策略有可验证实验依据 |

重点测试矩阵：

- 分类：server_error/stream_interrupted/empty/incomplete × raw 0/1/缺失；有效 verifier 与 bootstrap 失败；异常和状态同时报告 timeout；未结束 job；重复日志；损坏 JSON；unknown 不伪装失败归因。
- 流：无输出、仅 usage、text-only、tool-only、混合 stream/final、重复 block、冲突 ID/参数、截断 JSON、缺 block stop、缺 message stop、已完整一个工具再断流、取消发生在持久化/通知之间。成功执行计数必须精确，不只检查返回事件名称。
- 验收：格式成功不能覆盖行为失败；强检查未跑；阈值没 assert；同命令重跑；检查修错与弱化分别处理；epoch/revision 变化；计划替换；原文引用与解释不一致；两个不同要求使用同一标签；不依赖 summary 中的 still/failed 等词。
- 兼容：旧 schema 无 requirement/check ID；resume 后 stale 证据；CLI、飞书、MCP、Skills、Hooks、checkpoint/rollback；普通问答不被代码合同阻止；真实失败仍可终止为 failed。

实现每批先让失败测试在旧代码上复现，再做最小修复并跑相关测试。冻结候选前执行 Windows/Linux 全量测试、compileall、diff-check、统一入口契约审查。权限/环境无法完成的测试必须记录 blocked，不能写成通过。本次没有执行这套全量门槛，因为还没有生产改动。

## 11. 后续评测规则与完成定义

固定十题、并发 6、模型 gpt-5.6-luna、120 模型调用、240 工具调用、1800 秒及既定数据集摘要保持不变。先通过离线回放与架构回归，写出候选审查报告；只有用户明确要求时才启动下一轮全量评测。

同版本完整有效十题首次达到至少 5/10 后冻结候选，立即进行同十题确认轮。两轮均至少 5/10 才完成。不得拼接不同轮次的通过题，不得通过排除故障题提高展示分数，不得将 protocol fix 或回归测试通过当作解题达标。

阶段成功的领先指标分别是：分类准确率、请求生命周期完整性、重复执行为零、明确要求漏验收下降、错误 completed 下降、相同性质错误的诊断成本下降。最终结果仍由官方完整评测决定。

## 12. 本次离线探针记录与证据入口

探针只使用临时目录、FakeStream 和现有纯判定逻辑，无网络/模型请求。观察值：

```text
PROBE_MODEL: model_service_failures=0, agent_failures=1, scored_trials=1
PROBE_MULTI_CATEGORY: one trial -> infrastructure=1, agent_failure=1, agent_timeout=1
PROBE_WEAK: allowed=True
PROBE_CURRENT_FAILURE: allowed=False
PROBE_EPOCH_FAILURE: allowed=True
PROBE_MIXED_FALLBACK: ModelTextDelta, ModelUsageUpdate, ModelResponseCompleted; tools=0
```

现有代码入口：

- `benchmark/harbor/summarize.py:130`：分散分类；`:232`：payload 仅补 timeout。
- `forge/runtime/model_client.py:201`：透明重试的 output 边界；`:311`、`:430`：全局 semantic_output 与 fallback。
- `forge/runtime/runner.py:258`：请求事件；`:301`、`:530`：共享协议计数；`:481`：completion；`:508`：finish 声明。
- `forge/runtime/completion.py:118`：current evidence；`:133`：过滤后计算失败；`:184`：声明覆盖要求。
- `forge/tasks/manager.py:123`：source_quote；`:169`：计划验收列表替换。
- `forge/runtime/executor.py:260`：普通 process epoch 与 verify 例外。
- `forge/sessions/trajectory.py:81`：独立 recorder 具有的日志能力，不代表所有入口已接入。

历史解释依据仍保留在 `2026-09-04-candidate-evidence-audit.md`、`2026-09-04-baseline-stop-and-driver-repair.md` 与交接文件。本次未编辑 `.tmp/`、用户原有审计文档或交接文件。
