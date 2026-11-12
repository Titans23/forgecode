# ForgeCode 当前架构

[交互式学习手册](../architecture-guide.html)提供模块讲解、请求流程推演和固定源码版本的阅读入口，可直接在浏览器打开。

## 0. 产品范围

Harness 累计预算配置与检查已移除，包括主回合、路由、摘要和受管子任务的模型次数、工具次数、累计 token 与回合总时长。`RunProfile` 只描述入口和可选路由；`TurnState` / `ObservedModelClient` 记录真实请求与用量，不决定是否继续。单次命令默认 120 秒，可显式延长；模型容量、网络超时、取消、权限及外部评测截止时间独立保留。RPC 不再创建预算配置；旧数据库表和 `budget.consumed` 事件格式仅为历史兼容和用量投影保留，新的 remaining 字段为空，不产生 reserved/limit_reached 事件。RunSpec 为只读归档保留两个可选历史计数字段，执行计划拒绝它们，归档原值、摘要和分数不改写。历史冻结的有预算实验需使用当时的源码，不能由本版本忽略其限制后重放。

当前实施 T03：通用 Python SDK、可组合 coding 能力、可信插件、多模型与可复验交付。旧 T01 结果保留为历史证据。`AgentDefinition.core()` 通过同一 Conversation/TurnRunner/Journal 运行，无需工作区；内部私有存储作用域不是文件工具授权，不启动原生执行器。模型、CLI、MCP 和原生依赖为选装项，完整旧终端安装使用 `[terminal]`。

`PluginHost` 在显式信任后按依赖顺序加载，`PluginContext` 的注册和资源由该会话持有；错误撤销、逆序关闭与既有 runtime_close 共用关闭路径。工具仍通过 ToolExecutor 的参数检查、权限和 Journal 执行。可信插件工具在宿主运行并明确标注不受原生边界覆盖；原生文件工具的标签与限制不变。组合通过 `--agent` 选择，原启动 `--profile` 含义不变。

ProviderRegistry 按选择延迟导入协议适配器。`model_selected` 原子保存可移植消息投影与不含密钥的模型选择，恢复使用最后选择；旧 Journal 保持可读。新模型调用前仍由请求观察器计量，普通切换本身没有请求。摘要与受管子任务按角色配置模型；子任务沿用父权限并向父级汇总用量，关闭模型传输后再释放插件提供方资源。当前 Chat Completions/Gemini 接入由离线协议测试验证，未进行付费云端验收。

`forge.validation` 持有独立验收预期，只向受管子进程传入工具输入与显式 fixture。候选源码按 manifest.files 复制到私有目录，自测和业务探针分开执行；检查前后比较源码与副本，防止测试修改副本后沿用原代码结论。默认环境不传入业务凭据。`HarnessSession.add_completion_check` 与插件 completion check 追加完成条件，不能撤销既有门禁；自然语言开发复用原严格 coding 回合及其修复上限。

接口结果绑定插件代码、SDK 源码和已安装依赖；业务结果另绑定配置与验收。确定性检查不验证角色模型，模型变化不废弃这些检查，也不会产生新模型已实测的结论。ZIP 交付包使用显式源码、标准 SDK wheel、依赖 pins、AgentDefinition 与验收；导入不安装、不执行、不启用。原环境结果仅作来源证据，新环境须重新 check。可信 Python 的宿主能力不因独立进程或这些检查变成恶意插件隔离。

渠道可注册为受管服务，通过 `HarnessSession.service(name)` 使用同一会话的 run/stream/cancel；上下文扩展使用 HookOutcome，验收扩展追加 completion check。插件不能通过这些公开接口移除已有权限。已有显式规则跨回合保留，受管子会话复制父规则；切换会话仍重置会话规则。现代沙盒会话的交互审批只允许本次，不再创建记住规则；旧无 execution_mode 接口保留兼容。

统一 CLI 评测计划位于 `benchmark/harbor/platform.py`，复用既有源码/任务冻结、Harbor runner 与独立 grader；三类对照分开比较 model/plugin/harness 条件。可选价格表复用现有用量标准化和 PriceBook，未知请求保持未知。`benchmark/comparison` 通过 pi 的公开 SDK 执行相同确定性业务探针；不是模型能力或人工效率排名。新 CI 安装用例在无 Node 路径的进程运行通用 SDK，并在第二个 Python 环境导入复验。

持续对话终端与 Python Harness API。terminal/evaluation 启动 Profile 选择入口；`bundles` 是入口描述，不是插件依赖注入系统。SDK 直接调用 API。原生权限由 SessionOptions 单独选择，用量统计不影响权限。

`forge.execution` 定义不含模型凭据的执行请求及后端契约；普通 coding 默认 `danger-full-access`＋`never`，不初始化原生沙盒。显式写入范围、自定义提供方或受限模式继续使用所选边界；原生 PolicyToolBackend 在命令、verify 或 Git 调用前才准备进程后端。可信插件通过 `register_sandbox_provider` 注册，AgentDefinition 显式选择；core 不依赖沙盒。提供方失败不回退，子任务不能更换父提供方，旧会话恢复其保存的权限；会话先关闭模型与执行后端，再释放提供方资源。

文件修改、命令和 Git 查询工具的 `SandboxInput` 共享 DSH 的 `sandbox_permissions` / `justification`。ToolExecutor 在最终参数与任务范围检查后要求单独的 allow_once，已有 allow 规则不能绕过。显式模式下普通内置工具遵循沙盒策略，不使用旧命令/敏感名称启发式作为拒绝依据；工具提供的显式限制和用户 deny 规则继续有效。文件修改通过临时后端完成快照、执行与结果观察，控制字段不进入命令或存储的 verify 定义；原生执行依旧按需准备。清理失败保留 indeterminate；单次授权不会替换默认策略或泄漏给下一次调用。旧的无 execution_mode 接口保留历史兼容语义。

`permissions.presets` 用一张表描述工作区、只读和完全访问预设，遵循 DSH permission-presets 的“沙盒＋审批”组合方式；当前预设从真实取值推导，非匹配组合为 custom，不另存一份权限状态。CLI `/permission` 与 SDK `set_permissions` 共用 `select_session_mode`：只在空闲时准备新提供方，确认旧资源清理后用一条 `permissions_changed` 保存两项值，再同步 Executor、observer、Explore 与子任务默认值；仅审批变化时复用后端，重复选择不追加事件。SessionState 同时读取新组合事件和旧分项事件；恢复、分支、清空保持原语义。Journal 失败或取消不发布新权限。核心 Agent 不加载沙盒；插件/MCP 的独立 JSON Schema 工具不被误当作内置 Pydantic 授权输入。

ToolExecutor 独占单次授权生命周期：获批后 `open_approved`，在该后端完成快照、执行及观察，最后 `close_approved`。移除重复的 execute_approved 分支；不改变会话默认，不静默回退。显式 deny、取消、清理未知和原生平台条件仍是独立边界。旧 `/sandbox`、`/approval` 和审批名称只作为兼容/高级接口保留。

T01 已保留能力盘点、冻结任务、失败恢复和 CLI/API 演示证据；T03 在此基础上实现可扩展底座。完整跨平台 OS 强隔离仍是未完成扩展，不代表原沙盒目标已完成。保持无 Docker 的日常运行和现有权限/取消边界，不新增训练扩展或未经授权的模型调用。

## 1. 目录与调用链

工具批次通过 `runtime/tool_batch.py` 在模型顺序上运行有界滚动池，默认最多 10 个执行体，`AgentDefinition.max_parallel_tools=1` 恢复串行。仅显式并发声明允许重叠，未声明、非法参数、分类器异常、单次提权均独占；Hook 改参后重新分类。Executor 通过同一 prepare/dispatch/finish 路径执行，授权、前置 Hook 与后置结算保持顺序，工作体并行；ContextVar 延续各调用自己的观测身份。正文响应完整校验后才派发工具。普通失败继续，拒绝/取消/不确定结果停止补充并收齐已启动调用。消费者关闭也保留真实已启动结果，不补造未执行事实。

FileWorkerClient 的每个请求拥有独立 helper 和清理任务，原生输出按 execution_id 分流，关闭时先取消并收齐请求。CLI 分别显示排队、运行、执行完待归档和完成状态及单次耗时；给模型的结果仍按调用顺序。插件注册 `concurrency_safe=True` 或纯参数分类函数，只声明调度能力，不提升权限。默认开放文件/搜索/图片及 Git 查询的并行；前台命令和目录操作独占，读取组与修改组之间保留屏障。

内置 write_file、write_file_chunk、replace_text 和 apply_patch 显式声明文件修改能力。Executor 在原有快照、执行、版本检查和有序结算外持有 `FileMutationLocks` 租约；同后端或无后端同 Registry 的调用共享协调器。目标键包括解析后的规范路径、已有文件身份及缺失父目录，多文件补丁使用现有补丁解析器的所有目标并一次预留全部目标，等待更早的相交操作；不同文件可重叠，同文件/别名等待前一操作结算。取消线程写入先等实际写入结束，才释放租约；它不是任意外部进程的文件锁，也不增加权限或提供跨进程互斥。

`run_command.run_in_background` 在同一参数校验和单次授权之后交给会话 `CommandJobs`。启动调用只确认受理，命令仍由原执行后端运行；临时授权后端一直归该作业持有，到结束/取消才关闭，不改变默认权限。`job_output` 用游标与最多 60 秒等待读取有界增量输出，`job_kill` 只取消本会话作业。作业同时运行数复用 Agent 的并发上限，超出的排队，不施加累计预算。前台与后台保留相同命令超时；只读 job_output 不推进环境 epoch，命令启动和结束会失效旧环境证据。运行中作业阻止验证/完成声明及权限、历史切换；同模式选择保持幂等，模型选择仍可在空闲回合进行。

后台输出通过无依赖 ContextVar 传递，helper 可选 JSONL 输出帧绑定 request_id，原生外层继续绑定 execution_id；默认单响应协议兼容。Windows 匿名管道用 read1 读取已刷新的数据。Job 保留有界输出、源回合 ID 和终态 Journal，跨进程恢复不会重放命令。取消回合收齐该回合创建的作业，关闭会话收齐全部所属作业；历史切换只在无运行作业时重新绑定并清空活句柄。CLI 的 `/jobs` 提供不调用模型的列表、输出和停止入口。不同后台命令及文件编辑的依赖由调用方明确排序，文件锁不覆盖任意 shell 程序的内部写入。

| 目录 | 当前职责 |
|---|---|
| forge/cli.py、launch.py、api.py | 终端、入口组合和程序调用 |
| forge/protocol.py、tool_types.py、messages.py | 无产品层依赖的协议、结果和文字/图片输入契约 |
| forge/runtime、context、tools、sessions | Harness、上下文、工具和Journal事实 |
| forge/sandbox、permissions、sandbox_bridge | 授权及Windows DSH/Linux bubblewrap执行 |
| forge/application、engine | CLI、RPC和评测实际使用的服务与状态投影 |
| benchmark、evals、extensions | 评测适配、任务和后训练导出 |
| contracts、packages/contracts | 维护源与TS契约校验器 |
| scripts、tests、docs | 构建、真实验证和当前说明 |

forge.launch选择forge.cli或benchmark.cli。CLI和forge.api共用open_runtime，调用runtime.factory.create_runtime → Conversation.stream → TurnRunner → ToolExecutor → 原生工具。Journal记录会话副作用；Engine负责受理、调度与投影，不复制Journal事实。

`create_runtime` 是唯一运行时装配函数，新建与恢复使用同一构造路径；项目模型配置读取传入的 root，不隐式读取其他工作目录。可选渠道/MCP 组合位于 `forge/integrations.py`。旧 `forge.application.models` 保留协议导入兼容。

通用 `ModelClient` 协议与模型请求／流协议错误归属 `runtime.state`；健康状态、路由和 Runner 直接使用这些契约，不依赖 Anthropic 实现来获取类型。`model_client` 保留同对象导出兼容，负责实际 Anthropic 传输；健康状态不再反向导入它。

Anthropic 适配器按 DSH Messages 的续传机制拼接 signature_delta，并在响应完整通过协议检查后，将原始有序 content（含 thinking、redacted_thinking、text、tool_use）存入 ModelProviderState。同模型后续请求与恢复会话使用该内容，切换模型/提供方不复用签名；中断、截断、协议错误不发布可续传状态。上下文估算只计算实际发送的内容，摘要只读取可移植消息，不重复输入原生内容与不透明签名。旧会话已经丢失的思考无法补造。

模型传输默认最多重试两次，SDK 内部重试关闭。Anthropic、Chat/Responses 和 Gemini 共用退避计算：连接、超时、限流及服务端故障仅在尚未发布语义输出时自动重试；收到正文、思考或工具调用后不透明重放。有效 Retry-After（秒、HTTP 日期或 retry-after-ms）优先，超过 60 秒则停止自动重试，不缩短服务端等待；缺失或无效时采用带抖动的指数退避。等待可取消，重试事件显示下一次尝试编号和时长。此策略与 DSH 的普通模式遵循同一机制，默认次数与等待上限不宣称相同。Anthropic 超时按下一条语义事件的等待计算，持续产出的长流可超过该总时长。

Anthropic 传输使用 SDK 的原始事件接口，工具 JSON 在内容块结束后才解析，避免高层流助手提前解析不完整参数。无效参数或不可用工具可触发至多两次显式格式纠正；整条响应通过校验前不执行工具，先前已解析的调用记录为未执行。传输中断、缺少终止标记和内容冲突不会因为已显示部分输出而透明重放。纠正请求保留原统计与限制。

流式正文和工具参数直接交给消费者，高频诊断事件按同一请求/内容块合并，在达到 16 KiB 或持续接收满一秒时落盘；块结束、请求终态、重试、异常、取消和消费者关闭均排空缓冲。签名及完整响应仍在既有状态中保存。授权、工具 intent/终态和会话事实继续同步 fsync。硬退出可能丢失尚未落盘的最后一批诊断分片，不能据其缺失推断工具执行；这与 DSH 的实时流和持久化结算分离原则一致，不移植其存储框架。

观测投影复用观测层 ID 生成函数，不反向导入持久化模块；工具发现直接使用公共错误契约，不依赖沙盒启动器。持久化仍在同一事务内调用投影，启动器仍负责执行前的完整准入检查。

禁用可信扩展时，工厂不导入 MCP SDK 或读取 MCP 配置；启用路径仍使用原有权限与关闭流程。工具只通过已选择的 backend 或 registry 执行，不保留无调用方的 operation 注入路径。

Registry 的无副作用预校验委托给具体工具：内置工具使用 Pydantic，MCP 使用远端 JSON Schema；执行入口仍再次校验。MCP 不再由占位输入模型决定合法参数，授权和审计继续使用实际调用参数。

模块边界以高内聚、低耦合为目标：`runner` 从 `state` 读取消息契约、从 `verification` 读取验证结果转换，不在运行时反向导入 `Conversation`；旧 helper 导入名保留兼容。会话恢复、分支和清空共用一处 Journal／权限／MCP／Checkpoint 绑定。评测配置归属校验由 `EvaluationService` 负责；查询显式接收 service/events/artifacts，恢复只接收 service；CLI 的无参通知命令使用固定映射。

`application/sessions.py` 统一会话操作、消息和快照视图，事件传输层复用其返回结构；安全工具输出属于 `ObservationViews` 的查询方法。独立模块用于真实边界：SessionInbox 负责输入状态，Journal 负责持久化，Engine 负责 RPC 受理与恢复，不能以目录大小代替职责判断。仅依赖开发夹具的 RPC 演示位于 `scripts/harness_rpc_demo.py`，不再作为 `forge` 运行包的一部分。

会话展示消息以 SQLite 记录为序号和配额的唯一依据，在同一事务内计算、追加并发布事件；不在写入器中复制计数。配额仍按字符计算，包含空字符，快照使用同一统计函数。评测视图与失败标注显式接收实际服务、游标和产物依赖，不持有整个 RPC 路由对象；profile 归属、幂等动作与证据权限仍由原服务检查。

Hooks 保留匹配、JSON 协议及 stdout/stderr 合计 1 MB 硬限额，进程树生命周期统一由 `LocalProcessOwner` 管理。正常退出、超时、取消和输出超限都清理所属后代；清理未知不能返回 allow。Hook 命令使用既有受管进程的环境清理规则；这不增加沙盒能力。

参考 [DSH 的执行层日志职责](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/packages/core/tools/src/batch.ts)与 [Pi 的核心循环／请求转换边界](https://github.com/earendil-works/pi/blob/a92283056d915c758799fcd904948ff0cb42628c/packages/agent/src/agent-loop.ts)：Executor 记录工具生命周期，Runner 在发布事件前记录终态与异常，CLI／渠道只消费事件，不重复写 Journal。Conversation 的手动记录接口保留兼容。沿用本项目已有权限、持久化和取消边界，不据代码删减宣称模型性能提升。

`evals.runner` 同样使用 `HarnessSession`，通过共享配置解析与模型工厂选适配器，复用用量统计、权限、TaskPolicy、Journal 和关闭路径，不再直接创建 Conversation/Anthropic 客户端。默认采用原生 workspace-write；不可用即失败，full-access 必须显式选择。初始化中途失败时，API 也关闭已经创建的模型客户端和后端。

夹具的 setup、public/hidden verifier 和 diff check 是评测器独立触发的命令，走同一已选择执行边界，独立于 Agent 的模型/工具用量统计，受各自命令时限约束。隐藏测试在 Agent 回合结束后恢复，拒绝链接目标；不能把这描述成 partial OS 边界下的绝对保密保证。`EvalOutcome.passed` 才是最终验收结果，Agent 声称完成不等于成功。每次尝试保留 run_id 报告和 Journal/trajectory；latest 只是同一尝试的便捷视图，不应重复计数。取消也保存失败记录；缺少终态聚合用量时留 null。清理未知时拒绝成功并保留工作区，避免在活跃进程可能存在时删除它。

真实仓库任务冻结在 `experiments/native-swe-lite.json`，目前是公开 SWE-bench Lite 的 Windows 原生适配预检，不是官方成绩或模型完成率。`scripts/prepare_native_swe.py` 校验官方数据/源码摘要，使用任务独立解释器和依赖，先施加测试补丁复现失败，再只施加参考修复验证全部规定回归。`evals/swe_lite_grader.py` 校验冻结的原标签→全部实际 node ID 映射，拒绝缩小参数覆盖、跳过、xfail 和收集错误；命令生命周期复用 LocalProcessOwner，不证明 OS 强隔离。

参考工作区、补丁、原始日志和 `runner-source.json` 只属于评测控制器；后者不是可运行 EvalCase。`evals.native_swe` 复用 HarnessSession/runner：所属夹具根中分开源码和依赖，清理未知时一并保留；Agent 结束后保存带摘要的增删改候选，在新副本施加测试补丁并独立判分。Journal 审计可见的答案来源访问，发现后标为 invalid；当前原生读取/网络继承宿主，因此输入分离不等于绝对保密。`evals.native_swe_campaign` 使用既有 OS 锁和 Journal，持久化槽位准入，串行执行冻结计划，禁用提供方重试，停止后只允许显式续跑未开始槽。预算耗尽不算成功，成功费用单列，未知用量不填零；候选可以不调用模型独立重放。历史预算和模型固定在 `experiments/native-swe-lite-plan.json`；当前入口拒绝这些旧预算字段，重放需使用当时冻结的源码。

| 状态/资源 | 唯一责任方 |
|---|---|
| 已提交消息、持久化句柄、验证历史、待处理输入 | Conversation 直接拥有；消息与验证列表保持原地更新，SessionInbox 随当前 Journal 绑定 |
| 当前回合用量、待执行调用、临时模型响应 | TurnRunner / TurnState |
| 模型客户端和扩展连接 | Conversation 的幂等 runtime_close |
| 文件/进程后端 | 创建它的会话或 ApplicationServices；Explore 明确借用，不关闭父后端 |
| 执行事实 | fsync Journal；SQLite 是受理/调度状态及可重建的事实投影 |

观测记录器维护运行时关联和当前证据。请求终结后释放输入关联（重试保留），压缩与验证终态写入成功后释放临时数据；证据失效事件提交成功后移除当前证据，不再同时维护历史证据副本与失效集合。历史和恢复依据仍来自 Journal；Trace／工具关联继续供其消费者读取。

`WorkingState` 只维护模型提示需要的路径、行覆盖范围和近期失败，不存工具原文、结果缓存或未使用的重放／去重决策。读取缓存仅由 `ToolExecutor` 按文件指纹管理；历史消息中的旧缓存元数据仍可读取。任务上下文在请求时由 TaskManager 直接生成，不保存只写不读的副本。

验证相关依赖单向流动：`state` 定义证据和报告值；`check_contracts` 统一检查身份、断言继承及失败消除规则；`delivery` 生成需求诊断和交付报告；`completion` 执行完成门禁。报告和契约规则不反向导入门禁，原 helper 和报告类型导入名保留兼容。`tasks` 的状态、计划操作和显式计划存储仍各有用途；计划完成不能替代执行证据，Journal 继续负责会话恢复事实。

压缩摘要只存在于已提交消息，由 Journal 保存并由轻量裁剪保留，不另设可重新注入的摘要副本。清空、切换会话或回退对话历史时，同步清除压缩失败计数、访问规则范围、工作观察和待注入 Hook 提示；项目长期记忆保留。保留原历史的分支、仅代码回退和当前会话 resume 不清除这些上下文。

上下文默认 262,144 tokens，单请求输出上限 65,536 tokens。压缩采用 [Pi 的预算方式](https://github.com/earendil-works/pi/blob/e65b3b88c08412bb8ffbd21fb5dc5756064a6e5a/packages/coding-agent/src/core/compaction/compaction.ts)：ForgeCode 将总预留提高为 `reserve_tokens=49,152`、`keep_recent_tokens=20,000`，完整可见请求超过 `窗口 − reserve_tokens` 时触发，当前为 212,992 tokens（81.25%）。先裁剪大工具结果再测量；只摘要旧前缀，近期原文按 20,000 tokens 保留，边界向前扩展以保持工具调用／结果配对。摘要后复测，至多两次尝试，仍超限则明确失败；每次请求计入共享用量统计，溢出恢复仍仅一次。固定指令过大或无安全摘要范围时不发摘要请求。小窗口需显式降低预留和近期保留预算。

为同时保留 64K 输出上限，ForgeCode 在发送普通请求和摘要请求时按 `min(配置输出上限, floor(窗口 × 0.99) − 估算输入)` 计算实际输出额度，1% 用于协议开销及估算余量；没有输出空间时直接报告上下文超限。三个提供方适配器及上下文统计共用此计算，不修改客户端配置或权限。此动态额度是 ForgeCode 的容量适配，不是 Pi 的默认参数；本地估算不能代替提供方的精确 token 计数。

大工具结果采用 [DSH pruner 的字符规则](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/packages/compaction/compaction-tool-result-pruner/src/config.ts)：每个结果的文本超过 8,192 个 Unicode 码点时保留头 4,096、尾 1,024 和中部裁剪标记，跨文本块累计，图片块保持原位；不再按结果年龄二次缩短，也不按所有工具结果的字符总量裁剪。沿用 ForgeCode 的结果归档和受限读取句柄，因此可见结果额外包含少量归档元数据。用户原话、摘要和工具配对继续受保护，完整原文保存在现有 artifact／Journal／压缩前 transcript；不移植 DSH 的插件体系。手动压缩和溢出恢复保留最新完整消息单元，不采用自动压缩的 20,000 tokens 尾部预算。

`scripts/check_architecture.py` 防止公共契约反向依赖产品模块、核心依赖 CLI/旧应用协议模块、沙盒导入 Conversation 实现、会话视图反向依赖事件传输层，以及验证值／报告／规则反向依赖完成门禁。工具路径解析独立于循环。兼容参数 repeated_tool_limit/stagnation 等已无运行时效果，自定义值会发出弃用警告；回合累计预算也已移除。

## 2. 保留接口与能力边界

python -m forge.engine的stdio RPC及原main/renderer角色保留兼容；当前不交付Electron或Web客户端。未来客户端接入API/RPC。test_refs必须指向实际测试，生成文件从contracts/v1产生。

workspace-write 的文件工具提供可信代码内的路径写范围检查；进程工具复用发布的 DSH sandbox-local/subprocess-local 0.2.1-alpha.1。每个原生会话持有一个私有 JSONL 控制进程，DSH 负责 runner 选择、授权和子进程范围；Forge 负责协议、用量统计和清理记录。CLI 启动不创建项目 ACL，普通命令不跑 Forge 前置写入探针；实际 confinement 失败拒绝该调用并保留会话，不回退宿主。文件结果标记 `*-file-policy`、sandbox_covered=false。evals.runner 在模型请求前显式 probe_process，只有该探针通过才记录已测能力。full-access 须显式选择。离线、原生、wheel、模型和训练分别验收。

内置提供方按 DSH 文件效果策略记录 execution_mode：读取继承宿主，写入按实际解析目标检查；workspace-write 只授予已选范围，read-only 拒绝修改，danger-full-access/原 full-access 取消工作区写入围栏。不再按凭据文件名、安装目录重叠、命令关键字或模型推断的只读意图叠加拒绝；显式调用方拒绝规则、任务范围、环境凭据过滤与清理契约保留。符号链接、Junction 和普通硬链接不触发整个工作区的启动拒绝；实际目标的身份重检保留。无 execution_mode 的旧接口保留兼容策略。

工作区扫描记录链接本身的身份和目标摘要，不遍历其外部目录；明确工具目标另行观察，链接变化会改变工作区证据。普通链接不再令整个基线失效；扫描限额、超时和实际读取失败仍阻断缺失证据的完成声明。完全访问的外部文件检查点由已授权 helper 提供内容，只有显式宿主会话可创建外部目标条目；宿主直接恢复受限检查点仍被拒绝。Windows/硬链接等剩余边界保持 partial，不据路径校验宣称强隔离。

Windows workspace-write 使用 DSH 持有的会话级私有临时目录，workspace standing grant 按 DSH 语义保留；read-only 不增加临时写权限。Linux 由 DSH 选择 bubblewrap 或 Landlock。显式多个/空 write_roots 复用 DSH runner 与授权组件构造限定范围，不先授予整个工作区。子进程范围确认退出后才撤销临时授权，清理不确定仍报告 unknown。原生宿主目前接入 Windows/Linux；DSH 的 macOS Seatbelt 尚未接入 Forge 控制进程所有权层。

沙盒保持原生轻量路线，安装和日常运行不依赖 Docker。试验性的 Docker 执行层、镜像构建入口及相关 CI 已移除。现有 DSH/bubblewrap 仍只提供已验证的部分隔离；`strict` 在完整能力具备前继续拒绝准入。后续原生能力审计、网络白名单、资源配额和完整 CLI/SDK 验收见 T01，不能用历史容器测试证明原生能力。与运行平台无关的 `workspace_transfer.py` 保留有界输入快照、敏感路径过滤及冲突检查回写，供原生隔离工作区使用；它本身不构成 OS 沙盒。单文件替换原子化，多文件应用不是文件系统事务，I/O中断明确返回已应用路径及 INDETERMINATE，不自动重放。

Windows 的 ACL 授予和撤销在 Win32 调用处使用扩展路径形式，避免长控制目录中的临时授权失败；策略路径比较及 SID 派生仍使用原规范路径。环境变量和应用路径 API 不全局重写，例如 Node `mkdtemp` 自身处理长路径时仍需使用其支持的扩展路径形式。

Windows 控制进程目录放在 OS 解析的 LocalAppData/ForgeCode/native 下，按会话 UUID 独立持有；DSH 临时目录建立在该会话的 scratch 下，产物目录保留定位文件和失败诊断。只有子进程范围、临时授权及所属目录均确认清理后才发布 clean，未知清理保留现场；不修改系统 TEMP，也不清理其它会话。

原生命令使用原解释器，不创建 Forge 私有 venv，不修改 Python mkdir/临时目录语义或注入 PYTEST_ADDOPTS。Windows CPython 的 0700 与受限令牌兼容问题按 DSH 原生行为暴露，需调用方选择适合该模式的缓存配置；不能把此限制表述为已解决。

显式全盘观察复用 Git 改动路径与授权文件指纹，并枚举普通 ignored 文件；`.gitignore` 不作为安全白名单，已声明的运行缓存仍排除。Git 配置包含过滤器、include 或工作树重定向时使用有界扫描；输出截断、超时和观察缺失不算完整。严格验收中的不完整基线仍可阻止完成；普通 coding 未请求全盘盘点时，CompletionReport.workspace_tracking_complete 为 None，不把目标观察误报为全盘扫描失败或成功。受限观察不启用宿主 Git diff 子进程。输出生产者在有界队列成功入队后让出调度，消费者缺失、缓慢或配额耗尽仍报告丢弃。
普通 coding 的 WorkspaceTracker 只观察工具目标，不执行全盘基线或全盘刷新，命令仅推进环境版本并复查已观察目标；变更列表不宣称完整。没有显式 TaskPolicy 时不创建 CompletionGate 或自动检查点；工具失败保留为事实，不再自动阻止模型结束回合。strict Agent、显式 TaskPolicy 及插件完成检查继续执行调用方约束；严格扫描的时间限制覆盖单文件哈希读取，不完整基线不会靠后续扫描补造前像。RunProfile 与 RuntimeBindings 的 intent_routing 默认 False，工厂不再额外创建路由模型；显式 True 仅保留旧调用兼容。

文件写入、替换和补丁移除固定字符上限、单块覆盖禁令、目录名称推断及强制 JSON 校验。FileToolBackend 保存模型读取或写入得到的目标指纹；内部检查点不代表模型已读取。未观察的目标只能新建，现有文件须先读，外部修改使旧指纹失效。普通 snapshot 只传版本信息，显式检查点才包含内容；原有传输、检查点、取消和清理边界保留。

主模型的系统指令保持稳定；任务、仓库上下文、工作证据、权限状态和 Hook 补充放在请求末尾的 `[ForgeCode current context]` 用户消息中，明确来源且不提升仓库文本权限。该消息只存在于请求副本，不把过期状态累积进持久历史；压缩预算包含它的大小。这样状态变化不再使整个历史的缓存前缀失效；最近请求尾部、工具 schema 变化和压缩仍可能造成缓存未命中，实际命中以提供方计量为准。显式沙盒模式下，意图分类不再隐藏进程或写入工具，真实权限、调用方规则和任务约束仍由 Executor 执行；旧无 execution_mode 的只读意图限制保留兼容。

真实任务的固定 Python 环境与 Harness 控制解释器分开。NativeTaskAdapter 在公开任务文本中给出 Task Python 路径及 pytest 调用方式，所有 Python/导入检查应使用它；不修改内部 helper 解释器、不自动重写命令或全局 PATH。已见任务的解释器导入对照是零模型证据，不代表模型会遵循新指引或完成率已改善。

## 3. 开发与验证

验证器修正可使用 `verify(check_id=原检查ID, command=修正命令, revision_reason=原因)`；检查 ID 和执行记录 ID 均可引用，原断言、需求和工作目录自动继承。仍拒绝降低阈值、替换来源或改变工作目录；没有结构化断言的旧检查不能通过改写内联命令自动消除失败记录。绝对路径解释器的 compileall/py_compile 也只算结构检查；评测提示允许在禁止修改测试时读取、运行已有公开测试。EvalCase 可显式启用已有 require_task_verification/require_positive_verification 门禁，实际策略记录在运行配置；默认不升级日常任务，功能验收仍依赖行为检查及独立判分。

`view_image` 通过现有文件授权边界读取 PNG/JPEG/GIF/WebP（最多 512 KiB），向 Anthropic/Responses 传递原生图像内容；DeepSeek 文本适配器不提供该工具。Journal 保存原始图像消息，工具文本裁剪不删除图片；被摘要前缀中的旧图片转成文字占位，保留尾部中的图片继续发送。图片上下文预算使用估算，实际 token 以服务端用量为准。

API 接受字符串或类型明确的 text/image blocks，CLI 的 @path 支持文字和图像；输入限制为文字 256 KiB、最多 8 张图、单图 512 KiB、总附件 4 MiB。MCP 图片进入相同内容链路，音频仍不支持原生输入。适配器传输能力与具体模型能力分开，未知模型模态保持未知。

每轮开始时加载项目根目录 AGENTS.md、FORGE.md 和 `.forge/rules/*.md`，以及相关项目记忆。工具访问路径及显式 cwd 会激活其祖先目录的 AGENTS.md/FORGE.md，按从浅到深的顺序注入下一次请求；未访问的兄弟目录不加载。若写入或进程工具涉及模型尚未收到的新规则，则返回规则更新提示，待下一次模型请求后再尝试；同一批中先读后写也不能绕过此检查。规则文件沿用链接限制和大小预算，工具权限及用户任务范围检查保持独立。

待处理输入先记为 input_queued；steering 只在请求边界进入上下文，user_message 同时记录消费 ID。follow_up 等待下一回合。取消将未消费 steering 转为待办，不自动重放工具；恢复从 Journal 重建队列。运行中不得切换会话历史。

工具批次按模型索引顺序执行；普通命令失败、确定已清理的超时和参数错误作为该工具的结果返回，后续调用继续独立校验与授权。权限/Hook/任务范围拒绝、取消和清理不确定仍停止待执行尾部。与 DSH 一样区分普通工具错误和调度中止，但仍保留 Python Harness 的顺序调度。实时 Engine 投影只消费当前写入者已 fsync 的新增记录，结束与恢复仍全量检查。上下文压缩沿用完整请求大小与输出预留的固定估算阈值；真实 token 用量独立记录，不再校准下一次压缩阈值。

`tools.base` 负责工具协议、输入校验与注册；路径解析、保护路径、访问守卫和目标提取统一归属 `tools.paths`，base 的旧路径导出指向同一对象。授权前校验与执行入口校验各自保留。Explore 复用 Conversation，固定只读工具集限制能力，汇总父回合用量并借用后端；结构化报告限制进入父上下文的内容，不另设子代理调度框架。

交互新会话延迟到首轮输入才持久化，API 和评测仍使用原有即时会话生命周期。
终端生成预览与输入共用 prompt_toolkit 的差量渲染，按实际终端行宽截取预览，停止输入读取后只打印一次最终 Rich Markdown；不再叠加 Rich Live 的定时重绘。重定向和 dumb 终端直接输出文本增量，取消/异常保留已到达的文本与未确认工具状态。

正在生成的预览和输入使用同一个 PromptSession 的临时全屏缓冲区，缩放只重排临时内容，不将旧预览卷入普通滚动历史。暂停审批、结束或取消会退出临时缓冲区，恢复原会话记录，再由原流程打印最终内容；空闲输入仍为普通行内提示符。草稿、光标位置与详情展开状态由原输入会话保留，不清空主屏幕历史。
交互提示使用同一 renderer 的 120ms 刷新显示等待动画和用时；运行中的输入在结束时擦除并保留草稿，避免把空提示符留在回答中。ModelReasoningDelta 单独传递提供方的可读思考/摘要，Runner 不将其拼入答案，已显示的思考也会关闭透明重试边界；原 ModelProviderState 继续独立持有不透明续传状态。默认思考单行、工具摘要，Ctrl+O 展开有界预览；完成后的详情窗口复用 prompt_toolkit，保留完整当前回合内容、滚动和搜索，返回时恢复输入，不执行工具。切换会话清除旧详情；轨迹仍跳过高频文本/思考增量。

预览按实际终端宽高、自动补全菜单预留行和草稿折行计算空间，长工具记录不能挤掉状态行。状态区显示当前请求/工具耗时、本轮耗时、当前请求实际收到的 UTF-8 KB；连续五秒没有新的文本、思考或参数时显示无新内容的时间，不将字节数估算成 token 或声称工具已执行。模型思考强度和输出额度沿用配置，动画不代表服务端持续产出。

旧 Engine RPC 的单次授权绑定完整工作区版本。普通 coding 未启用全盘观察时，只在请求此类授权时创建独立观察快照，不覆盖本轮目标基线；批准前仍重查目录、参数、环境代次、nonce 与策略。CLI 的现代单次沙盒授权不因此增加全盘扫描。
恢复选择器按更新时间倒序排列已保存会话并标记当前项，包含延迟保存的新会话；选择当前项是无副作用操作，不为展示创建空 Journal。审批使用无编号 RadioList，单次授权仍不能记为会话规则。启动卡片展示模型、工作区与快捷入口；动态计时标为本轮累计。
CLI 消费 ModelToolCallArgumentsDelta，按请求内工具索引累计 UTF-8 参数字节，显示 KB 与“未执行”；计数在新模型请求重置，不把未完成的参数标成已执行，也不额外保存参数正文。
原生 runner 与初始化阶段的 SANDBOX_UNAVAILABLE 共用恢复提示，明确命令未启动及单次授权路径。Executor 只在可信失败结果确认 command_started=false 时记为 rejected，并跳过环境版本推进与执行后扫描；不自动切换宿主、不修改 ACL，也不缓存故障来禁止后续显式操作。
恢复展示从 Journal 原始消息重建，独立于压缩后的模型上下文；默认最近 10 轮，支持完整历史。

模型配置由 forge.config 读取与保存。npm 安装只创建缺失的用户 `.forge/.env`；`forge config setup/set` 写入用户文件，`forge config` 检查进程、项目和用户配置合并后的有效值。密钥隐藏输入，写入不覆盖其他配置条目；配置命令不调用模型。

公开评测通过 benchmark CLI 冻结实验后调用 Harbor 0.18.0：固定 Terminal-Bench 2.0 或 SWE-bench Verified 的内容摘要、显式任务范围、源码和依赖锁。模型复用 ForgeConfig；任务默认单并发、一次、零自动重试。运行保存在用户 evaluation 数据目录，官方 verifier 负责判分，既有 RPC/RunSpec 路径保留其未完成项阻断。

公开评测默认直接使用官方任务镜像，在容器内安装 Harness，不创建预热镜像；操作流程见 [评测指南](../../benchmark/README.md#terminal-bench-20直接使用官方镜像)。仅在用户明确要求预热时，`--prepare-only` 才在模型执行前缓存已安装 Harness 的派生 Docker 镜像，断网检查 Python 导入并记录官方基础镜像、源码和安装镜像身份。`--prepared-images` 在完整清单通过校验后启动，保持官方任务内容及资源限制。Git 基线在模型调用前确认，判分前差异引用固定基线；空工作区和模型自行提交均可采集，失败保留具体诊断。

准备阶段还缓存 Terminal-Bench 的固定版本 uv 官方安装脚本、安装包及可解析的 uvx 判分依赖，并断网验证依赖启动。缓存冻结为镜像层，准备好的任务使用各自容器的写时复制缓存，不挂载共享可写缓存。判分阶段仅将该安装脚本的精确下载请求导向缓存，安装包使用本地 URL，uv 禁止联网；官方测试脚本与断言不改写。无法解析的依赖命令在准备阶段明确失败，不宣称已缓存。任务自身的系统包和业务数据下载不属于此缓存。补丁采集使用独立临时 Git 索引，不修改或解锁 Agent 的工作索引，失败同时保存合并输出与 stderr。

Journal 是执行事实源；ATIF 和 SFT 都是离线投影。版本比较核对冻结配置与主机采集的 Docker 镜像/资源证据，缺失证据保留未知。公开测试任务隔离于训练数据；SFT 复用 Qwen 转换与 loss mask，独立检查来源、许可、划分、重叠、成功验收和本地 tokenizer。当前训练导出只完成接口，尚无真实训练数据集。

日常工作区为 D:\projects\forgecode 的 main，只维护当前说明和任务。历史通过和旧授权不能证明当前能力。虚拟环境、npm依赖、沙盒构建和.local报告均可重建且不提交。

开发时只跑新增行为、失败用例与相关模块，稳定后运行一次相关回归以及 contracts/quality，再推送并检查 CI。
仅公共核心变化或未覆盖风险触发全量回归，不在 Windows、WSL 和 CI 默认重复全量；原生验收按阶段需求执行。
CI 的 unit 自动发现非 native/fixture 的功能测试目录，portable 覆盖实现集成与协议测试；有测试收录检查防止遗漏。quality 同时运行 Ruff、公共输入/结果类型的 mypy 和架构依赖检查。根据 JUnit 耗时减少重复准备，保留关键边界测试。
离线存储性能基准：`uv run --no-sync python scripts/benchmark_architecture.py --records 100 --output .local/implementation/architecture-performance.json`。RunProfile.intent_routing=True 可显式启用兼容路由作对照，不改变权限。真实模型消融必须固定任务、模型、预算和环境后另行授权。
清理不授权付费模型、管理员设置或生产签名。

每个独立验收点完成相关检查后单独 commit、push，并报告 SHA/链接、验证范围和实际 CI 状态。源码存在、离线脚本模型通过、真实原生测试和真实模型收益是不同证据；完整完成率包含所有已尝试任务，预算耗尽、取消及基础设施失败不能算成功。成功任务成本与双成功配对另列，不用删掉失败后的均值代表总体改进。当前 P3 七个双成功配对的 token -3.4%、耗时 +1.6% 未证明稳定收益，不能摘取前者作为简历性能结论。

EvalCase 可显式设置已有 max_delivery_repairs（默认 0，最多 2），NativeTaskAdapter 将其传给同一个 TaskPolicy 并记录实际配置。完成被拒后，既有 TurnRunner 可在本回合反馈不同缺口；相同缺口重复、修复次数耗尽仍终止。它不放宽验证断言或新鲜度，也不提供进行中回合的跨进程续跑；CLI --resume 开启新回合。

评测失败复查后的运行路径：`run_command` 根据同一 ExecutionProfile 生成当前能力说明；宿主仍禁止脚本写文件，评测容器可在原授权范围内生成数据和构建产物，编辑工具的单次字符上限不是命令生成文件的大小限制。

参考 [DSH 的请求压力裁剪与重新测量](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/packages/compaction/compaction-basic/src/index.ts)，自动压缩先按轻量裁剪后的完整请求计量，不因未发送的历史单独触发摘要；裁剪仍保留原始用户约束。固定提示本身过大时不调用摘要，无缩减收益的摘要保留原历史并计入已有三次失败上限。原始 Journal、工具配对、失败检查及用量统计继续保留。固定提示和当前任务去除重复内容，不引入新的压缩服务或插件系统。

公开 benchmark 的官方 verifier 是任务通过与否的最终依据；内部仍要求当前版本的实际正向行为验证及覆盖说明，但不强制第二套模型自拟需求 ID／JSON 断言协议。其他调用方显式设置的严格验收契约不变。启用行为验证和交付修复时，无已观察外部阻塞的首次 partial/failed 可收到一次行动性诊断；它占用原有修复次数，重复失败、已给过反馈或 blocked 不因此重试。取消仍沿用原停止与清理路径。

## 4. 核心调用链与证明边界

| 调用链 | 机制与取舍 | 代码和现有验证入口 |
|---|---|---|
| Agent Loop → 工具/Explore → 压缩 | TurnRunner 有序准备/结算，可并行工具的执行体按滚动池重叠，独占调用形成屏障。普通失败继续；取消、拒绝和不确定清理收齐已启动调用并记录未执行尾部。ContextVar 传入父 TurnState；支持请求观察协议的提供方按实际尝试记录用量，旧嵌入客户端按 stream 调用计数。子代理用量向父级累计，路由和摘要也计入。Explore 借用同一后端但不关闭它。压缩计算完整请求及输出预留，保存原始约束并成对保留 tool_use/result；这是用量与协议正确性，不是稳定提速结论。 | [TurnRunner._batch](../../forge/runtime/runner.py#L489)、[ObservedModelClient.stream](../../forge/runtime/model_observation.py#L101)、[TurnState](../../forge/runtime/turn_state.py#L138)、[Explore](../../forge/subagents/explore.py#L198)、[压缩阈值](../../forge/context/manager.py#L473)；[共享用量](../../tests/runtime/test_shared_usage.py#L10)、[实际读取/压缩夹具](../../tests/implementation/integration/test_harness_regressions.py#L94) |
| verify → check ledger → CompletionGate → 同回合反馈 | 文件 revision 与环境 epoch 同时匹配才是当前证据；普通进程执行保守地推进 epoch。检查按 ID 保存定义，修检查器必须继承原断言、需求和 cwd，不能改阈值冲掉失败。完成被拒后最多反馈两个不同缺口，仍在同一回合记录用量；行为检查和独立 grader 是不同层。 | [环境失效](../../forge/runtime/executor.py#L300)、[检查继承](../../forge/runtime/check_contracts.py#L62)、[完成门禁](../../forge/runtime/completion.py#L142)、[有界反馈](../../forge/runtime/runner.py#L768)；[不可削弱断言](../../tests/runtime/test_check_contracts.py#L57)、[公开原生演示](../../examples/harness_demo.py#L61) |
| 规则激活 → 授权 → Journal intent → 执行/取消 → 状态重建 | 访问路径激活祖先规则；新规则尚未进入模型请求时，写入/进程先被拒绝，同批先读后写也不能绕过。统一执行器先授权；需要文件 checkpoint 时捕获 before image，intent fsync 后才执行。取消后的写入/进程结果可为 indeterminate。重建列出未匹配 intent，不自动重放；关闭只释放自己拥有的客户端和后端。 | [祖先规则](../../forge/context/repository.py#L191)、[请求边界](../../forge/runtime/runner.py#L622)、[授权/intent](../../forge/runtime/executor.py#L192)、[Journal fsync](../../forge/sessions/store.py#L381)、[重建未知工具](../../forge/sessions/store.py#L985)、[幂等关闭](../../forge/runtime/agent_loop.py#L795)；[未知副作用恢复](../../tests/implementation/integration/test_recovery.py#L55)、[Explore 后父后端仍可用](../../tests/implementation/integration/test_file_worker.py#L69) |

上述 TurnRunner、验证契约、Journal 与评测接入是仓库代码；Anthropic/OpenAI SDK 提供传输，Pydantic 提供输入校验，DSH/bubblewrap 提供已声明的 OS 写边界。已有 Python Agent 循环、SDK 和 OS 后端的复用与项目层修改分开说明，不把模型、完整 OS 沙盒或训练框架归为本轮自研。

测试入口说明覆盖内容，实际验收版本与报告以 progress.json 为准。脚本模型、真实本地进程/HTTP 夹具、原生平台验收和真实付费模型分别标注；只读恢复不等于原 Agent 进行中回合的跨进程续跑。
