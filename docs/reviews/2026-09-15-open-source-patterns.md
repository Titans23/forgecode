# 开源 Coding Agent 一手设计核验：DeepSeek Harness、mini-SWE-agent、Pi

访问日期：2026-09-15（Asia/Shanghai）。用途：为 ForgeCode 整体优化提供可核验的设计依据；本次仅静态读取代码和官方文档，没有安装、运行外来项目或启动评测。

## 1. 结论与证据边界

建议采用“小执行内核、明确的能力接口、可替换的策略、可重建的请求记录”。优先重构验收与上下文进入模型的边界，保留已有执行器、日志、权限和进程收尾能力。不建议把 ForgeCode 整体迁移到 TypeScript/Cordis，也不建议因其他项目功能多而一次性加入插件市场、动态热重载和多代理集群。

以下“源码观察”来自固定提交的官方仓库；“迁移建议”是针对 ForgeCode 的设计推论，不是上游项目对 ForgeCode 的验证结果。没有使用上游宣传的 benchmark 分数解释本项目的十题通过率：模型、任务、预算和环境并不相同。

| 项目 | 核验仓库与分支 | 固定提交 | 提交时间（UTC） | 观察限制 |
|---|---|---|---|---|
| DeepSeek Harness | `deepseek-ai/deepseek-harness` / `master` | `c291e7961a515f6d7af9304e7fd1d257929aef26` | 2026-09-10 14:17:09 | 官方明确为 developer preview，接口会破坏兼容 |
| mini-SWE-agent | `SWE-agent/mini-swe-agent` / `main` | `04d809ceab9df28f9adaed044884180159172930` | 2026-09-03 05:05:59 | 核读 DefaultAgent、LocalEnvironment、协议定义；不代表每种 runner/model adapter |
| Pi | `earendil-works/pi` / `main` | `f9bcd351dc3cedf989bc5fc0f8aa012db5737df2` | 2026-09-14 21:58:15 | 原 `badlogic/pi-mono` 官方仓库地址跳转至此；核读 agent core 和 coding-agent 文档、loop 源码 |

DeepSeek 官方产品页链接到上述仓库；其开发预览和能力可组合声明可由[官网](https://deepseek.com/harness/en/)与[仓库 README](https://github.com/deepseek-ai/deepseek-harness/blob/c291e7961a515f6d7af9304e7fd1d257929aef26/README.md)交叉核对。其核心 loop 和 session 并不是无法替换的特权内核；Cordis 负责插件生命周期，Agent 能力由插件组合。

## 2. DeepSeek Harness：值得借鉴的是生命周期和状态边界

### 2.1 能力接口分为 Definition、Provider、Consumer

DSH 的能力边界不是“每个目录放一个 plugin”。它区分服务定义、具体后端、调用该能力的消费者。例如 `ctx.llm` 是模型接口，DeepSeek/Pi-AI/replay 是后端；loop 和 compaction 是消费者。文件系统和进程后端共同构成执行环境，使文件工具、shell 和 LSP 能一致地使用同一个远程环境。[能力依赖图](https://github.com/deepseek-ai/deepseek-harness/blob/c291e7961a515f6d7af9304e7fd1d257929aef26/docs/capability-seams.md)

**ForgeCode 迁移建议：** 从现有 `forge/runtime/factory.py` 出发，先引入窄接口 `ModelGateway`、`ExecutionEnvironment`、`SessionLog`、`ContextProjector`、`CompletionPolicy`。组装函数负责注入依赖；loop 消费接口。Host/Sandbox 是执行能力配置，benchmark/interactive 是任务策略配置，应避免合并成单个万能 profile。已有 `forge/runtime/profile.py` 应继续表达宿主和沙箱的差异。

**不要照搬：** DSH 的整个运行时插件树、配置覆盖语义、跨端 UI 和热重载。如果 ForgeCode 暂时只有一个后端，不必为每个辅助函数建立插件接口；以真实替换需求和测试边界为依据拆分。

### 2.2 Session 是模型输入的事实来源，不只是事后日志

`Session.append()` 保存类型化事实；`deriveMessages()` 从已提交日志投影模型历史。`request/header` 保存非历史请求参数与工具 schema，模型可见 system/user/assistant/tool 内容来自 surface 事件。压缩通过 replacement 事件改变可见投影，历史事实仍保留。[Session 设计](https://github.com/deepseek-ai/deepseek-harness/blob/c291e7961a515f6d7af9304e7fd1d257929aef26/packages/core/session/README.md)

源码中的 invariant companion 在 `llm/stream` 前检查 loop 生成的请求是否冻结，并比对实际 messages 与 `deriveMessages()`，以及模型参数/tools 与已记录 header。由此，“日志能精确还原实际请求”成为可执行约束，而不是文档愿望。该检查属于可挂载的 invariant companion，不能据此假设所有部署都启用它。[请求重建 invariant 源码](https://github.com/deepseek-ai/deepseek-harness/blob/c291e7961a515f6d7af9304e7fd1d257929aef26/packages/core/agent-loop/src/invariant.ts)

**ForgeCode 迁移建议：** 当前已有 `forge/sessions/store.py`、模型调用事件、message checkpoint 和 context_compacted；应保留这些资产，增加统一的 `RequestSnapshot`：模型路由、原始输入来源、最终 system、可见 messages、schema 清单/版本、压缩边界、使用量与终止结果。记录最终请求的稳定摘要，离线回放逐次重建并校验摘要。不要把“保存了一份聊天记录”视为已经证明所有 hook 注入、任务约束和 schema 都被记录。

建议优先记录这几个来源类别：用户原文、仓库说明、工具输出、hook 注入、压缩摘要、运行时预算、验收提示。它们均可进入模型，但具有不同来源和更新规则；不能把模型生成的摘要升级成用户原始要求。

### 2.3 Durable event 与 live event 分开

DSH 区分持久 session 事实、运行中 `agent/*` 通知，以及 `tools/*` 等能力扩展事件。一个 step 是模型请求加其工具调用；turn 可以包含多个 step。输入统一经过 inbox，注入上下文可以等到下一次激活才消费。请求准备、接纳输入、发送和持久化结算都有明确边界。[整体架构与事件流](https://github.com/deepseek-ai/deepseek-harness/blob/c291e7961a515f6d7af9304e7fd1d257929aef26/docs/architecture.md)

**ForgeCode 迁移建议：** 把事件划分为三个 Python 数据类型族，不要让 UI 消息兼做权威状态：

- `SessionFact`：请求、工具调用/结果、产物引用、证据、压缩、终止；可回放。
- `RuntimeSignal`：流式增量、心跳、取消、当前等待阶段；可丢弃重建。
- `PolicyDecision`：准许/拒绝、重试/继续/停止、理由与引用；影响运行时必须写入事实日志。

用户看到“运行结束”、控制器退出、产物提交、内部验证通过和官方评分，分别对应不同事实，不应压成一个 `completed` 布尔值。

### 2.4 调度：并发执行不等于乱序状态提交

`tool-calls.ts` 将独占工具视为屏障；仅声明可并行的调用进入有上限的滚动池。派发可重叠，策略处理和持久结果按模型原始顺序提交。每个调用在开始前重新检查执行类别。取消停止补充新调用并等待已启动调用结束；未派发的调用记录 `ABORTED_BEFORE_DISPATCH` 结果，保留协议配对。调度器内部异常不伪造“已执行”的工具结果。[调度源码](https://github.com/deepseek-ai/deepseek-harness/blob/c291e7961a515f6d7af9304e7fd1d257929aef26/packages/core/agent-loop/src/tool-calls.ts)

**ForgeCode 迁移建议：** 可以在现有 `forge/runtime/executor.py` 前增加 `ToolBatchScheduler`，第一版只允许明确无副作用的独立读取并行。修改、测试、构建和同一工作目录中的 shell 默认串行；测试也可能写缓存或产物，不能仅按工具名认为只读。框架并发上限与 Harbor 的题目并发上限是两种预算，不得混淆。

该能力不是当前提升十题正确性的最高优先级；先修正终止和验收再考虑，以免并发掩盖状态问题。

### 2.5 取消与生命周期必须保留所有权

DSH 的 Agent 创建是有回滚边界的事务：先完成 session/agent/scoped context 和 setup，再发布；失败则撤销未发布资源。resume 先取得 session 的写句柄，避免同一 session 并发写入。关闭顺序包含 stop-and-drain、关闭持久化、卸载 scope，再解绑对象。loop 不内置 turn budget，预算须由外部策略扩展提供。[Loop 生命周期](https://github.com/deepseek-ai/deepseek-harness/blob/c291e7961a515f6d7af9304e7fd1d257929aef26/packages/core/agent-loop/README.md)

**ForgeCode 迁移建议：** 保留当前 deadline、controller、Windows Job 与子进程清理机制。把 `run_id → trial_id → request_id/tool_call_id → process/job_id` 的所有权链记录清楚；取消先停止新工作，再等待/终止已有工作，最后确认日志和结果已结算。不要因为借鉴 DSH 而删除本项目现有预算限制。

### 2.6 “Scheduling”要区分三种事情

DSH 的工具批次调度、后台 jobs、定时提醒不是同一服务。`schedule` 是同会话的持久提醒：状态来源是 `schedule/change` 日志，读写决策经过 flush；到期仅由活跃根 Agent 在空闲维护阶段投递，关闭的会话保留 overdue，不能保证外部通知或冷启动执行。[Schedule 设计](https://github.com/deepseek-ai/deepseek-harness/blob/c291e7961a515f6d7af9304e7fd1d257929aef26/packages/schedule/schedule/README.md)

**ForgeCode 迁移建议：** 长时间运行命令可以以后抽成 `JobHandle`，但不要把 benchmark controller 心跳、用户提醒和 agent 工具调度放进同一循环。控制器观察到 PID 存活只能证明进程存在；应同时保留 last_request、last_tool、等待网络/等待进程/等待用户等阶段信息。

### 2.7 子代理接口有用，多代理并不自动可靠

DSH 子代理分一次性与可继续两类，并通过 provider 能力声明拒绝不支持的选项；子代理发布前由 provider 负责回滚，发布后由调用方负责关闭。官方同时列出没有持久父级 mailbox、跨进程 residency 协调和 accepted-but-unlogged 消息恢复等限制。[Subagent 合同与限制](https://github.com/deepseek-ai/deepseek-harness/blob/c291e7961a515f6d7af9304e7fd1d257929aef26/packages/subagent/subagent/README.md)

**ForgeCode 迁移建议：** 以后引入子代理，应先用于只读定位、独立审查或单文件独占任务，明确输入快照、输出证据和父级验收。没有独立 oracle 时，让第二个模型复述第一个模型的自测，不会变成正确性证明。当前历史失败不足以证明必须改成多代理主架构。

## 3. mini-SWE-agent：用最小可用循环检验复杂度是否必要

`DefaultAgent` 的主路径是 query → environment.execute → observation；Model、Environment、Agent 有独立协议。step/cost/wall-time 是运行预算，`FormatError` 是可恢复的交互错误，格式错误连续计数在一次正常 step 后清零；每次循环的 finally 保存轨迹。[DefaultAgent 源码](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py)、[协议定义](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/__init__.py)

本次核读的 `LocalEnvironment` 通过特定 shell 输出标记触发 `Submitted`，提交本身不做答案正确性证明。LocalEnvironment 同时把命令输出和退出码反馈给模型，POSIX 超时会结束进程组。[环境源码](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/local.py)

可迁移的是三个思想：

1. **提交与正确性分离。** ForgeCode 可以接受 `submit_candidate` 并结束任务，同时记录 evidence_status=unverified/failed/passed；不要让模型为取得“允许结束”重复构造复杂合同。
2. **局部错误局部恢复。** 同类无效工具参数连续失败才加重纠偏；正常进展重置连续计数。全局预算仍独立保留。
3. **保留最小策略做对照。** 在相同执行器、模型、日志和预算下，建立 simple 策略与 evidence-guided 策略，用离线回放和之后获准的实验确定新增门禁是否产生收益。

不要照搬：单一 shell 标记可能与普通输出混淆；ForgeCode 已有类型化 finish 工具，不必退回文本哨兵。mini 的本地执行/轨迹存储也不是 ForgeCode 的权限、崩溃恢复和 Windows 子进程治理替代品。精简是用来减少模型负担，而不是取消生产环境所需的保障。

## 4. Pi：上下文、模型协议和交互入口分别演进

Pi 区分 `AgentMessage` 和实际 LLM message。源码在发请求前先执行 `transformContext`，再 `convertToLlm`；停止钩子 `shouldStopAfterTurn` 在一轮模型和工具完成后运行。steering 与 follow-up 分队列处理，后者在正常工作将结束时消费。[Agent core 接口](https://github.com/earendil-works/pi/blob/f9bcd351dc3cedf989bc5fc0f8aa012db5737df2/packages/agent/README.md)、[Loop 源码](https://github.com/earendil-works/pi/blob/f9bcd351dc3cedf989bc5fc0f8aa012db5737df2/packages/agent/src/agent-loop.ts)

**ForgeCode 迁移建议：** `forge/context/manager.py`/`compactor.py` 负责选取和压缩上下文，`model_client.py` 只负责 provider 协议。不要让请求重试重新运行有副作用的上下文构建，也不要让 provider adapter 决定任务是否完成。借鉴 Pi 的 transform seam 时，补上 DSH 式“最终投影可重建”约束，避免 hook 隐式改写请求后无审计。

Pi 的压缩保存 `CompactionEntry`，引用 `firstKeptEntryId`；原历史保留，下一轮使用摘要与保留区间。压缩点避免拆开工具调用和结果，累积跟踪读写文件，并记录摘要模型的 usage。触发点包括工具批次完成、下一模型请求开始前。[压缩设计](https://github.com/earendil-works/pi/blob/f9bcd351dc3cedf989bc5fc0f8aa012db5737df2/packages/coding-agent/docs/compaction.md)

**ForgeCode 迁移建议：** 不需要从零重写当前 compactor。应审计：摘要是否保留任务原文引用、当前产物与文件版本、最新失败、下一步、仍未验证事项；裁剪工具输出是否留下可读取的原文路径；压缩调用是否计入预算。固定的 token 阈值不应照搬，应按实际模型上下文窗口和输出保留量配置。

Pi 的 JSONL session 通过 id/parentId 表达分支，区分流中的 pending 与已持久化 terminal message。[Session 格式](https://github.com/earendil-works/pi/blob/f9bcd351dc3cedf989bc5fc0f8aa012db5737df2/packages/coding-agent/docs/session-format.md) 对 ForgeCode，近期更值得采用的是稳定 entry 身份和压缩/分支来源，而非立即迁移为整棵会话树。

Pi 默认围绕 read/write/edit/bash，并通过扩展承载额外工作流；其官方理念刻意不内置若干其他产品中的功能。[Coding-agent 设计取向](https://github.com/earendil-works/pi/blob/f9bcd351dc3cedf989bc5fc0f8aa012db5737df2/packages/coding-agent/README.md) 这支持“功能可选、核心可理解”，但不支持删掉 ForgeCode 已需使用的 MCP、权限或渠道功能。应把非必需能力从模型默认工具面和核心控制流中移出，而不是从产品中删除。

## 5. 对 ForgeCode 的具体落点

以下是本次已观察到的现有文件与建议调整边界，不表示这些文件中的能力全部缺失。

| 现有位置 | 保留资产 | 目标调整 | 可离线验证的约束 |
|---|---|---|---|
| `forge/runtime/factory.py` | 统一入口组装 | 注入 ModelGateway、ContextProjector、CompletionPolicy；先内部接口化 | CLI/benchmark/渠道入口采用相同执行语义 |
| `forge/runtime/agent_loop.py`、`runner.py` | 会话与执行循环 | 剥离请求投影、任务完成策略、产品命令；循环只负责阶段推进 | 相同输入事实/策略产生相同派发决策 |
| `forge/sessions/store.py`、`trajectory.py` | JSONL、checkpoint、工具与模型记录 | 明确版本化事实/投影；请求快照与实际发送可核对 | 重建 messages/schema/route 摘要一致；不伪造丢失请求 |
| `forge/context/manager.py`、`compactor.py` | 工具裁剪、摘要、预算估计 | 完整来源和压缩引用；约束/最新失败不因摘要消失 | 工具配对完整、原始依据可追踪、摘要 usage 入账 |
| `forge/runtime/completion.py`、`check_contracts.py` | 稳定 source_ref、继承断言、拒绝差异 | Candidate 提交与证据评估分离；降低模型手工合同协议负担 | 不靠重复 finish 维持“未验证”；放松阈值不能变成通过 |
| `forge/runtime/executor.py`、`forge/tools/base.py` | 参数验证、权限、hook、审计 | 局部恢复；必要时才加工具批次 scheduler | 取消后不新派发；结果有明确 call_id；有副作用工具不默认并发 |
| `forge/runtime/model_budget.py`、benchmark controller | 限额、deadline、退出与心跳 | 一份有效 deadline 向下传播；阶段等待与 budget settlement | 内层 deadline 不晚于外层收尾点；重试不重放已执行副作用 |

## 6. 建议先做的设计实验（本次不执行评测）

1. **请求重建回放。** 选历史网络重试、压缩、finish 拒绝和 deadline 轨迹，列出每次真实模型输入及来源；缺失字段明确标为 unknown。
2. **终止策略回放。** 相同工具结果分别通过现有门禁与 candidate/evidence 分离策略，统计拒绝次数、错误原因和预计省下的无效模型调用。回放只能证明控制决策变化，不能证明答案会更正确。
3. **证据合同迁移。** 从现有 `source_ref` 与 `inherit_checks_from` 演进为运行时创建的 checker ID/version；模型引用 ID 即可，保留用户要求和数值阈值，不要求重新抄完整断言。
4. **上下文负担审计。** 分开统计 task policy、schema、历史工具输出、失败恢复与真正产物相关的内容；以历史请求还原为依据减少重复材料。
5. **再决定是否需要更深架构迁移。** 若离线证据证明边界清晰但模型仍不会解题，应改工具反馈和问题分解策略；若大量失败是阶段错乱、丢失状态、错误重试，则优先完成 session/loop 的结构拆分。

这些上游设计共同支持“让模型把精力用于产物，把一致性放在运行时”，但没有任何一个项目能替代本项目对任务正确性、真实产物和评测环境的独立验证。
