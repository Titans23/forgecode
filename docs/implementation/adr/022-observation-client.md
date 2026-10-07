# ADR022 — 真实观测客户端与安全懒加载

运行观测页通过固定 DesktopTransport / Preload / Main 命名 IPC 调用既有 Engine 查询；新增 observability.output / timings，spans 支持实际 execution ID 定位，events 支持有限目录过滤。过滤、profile、store generation 与分页游标绑定。默认页面不是 MockTransport，不解析工具 stdout 为状态或费用。

ToolExecutionCompleted 从当前可信 JournalRecorder 的 branch + tool_call ID 获取真实 Scope.execution_id；Started 尚无 Scope 时明确 null，完成决定根 Span 也不伪造 execution。migration 011 为 turn_messages 增加可空关联，turn_profiles 从已提交 start/submit action 证明旧归属；未知或多义归属不猜测。观测和输出只读当前 profile 的交互 turn 或 evaluation run。

Journal 原始事实 fsync 后才投影。运行中以至多每 250 ms 的定时扫描与工具/终态边界投影更新视图，sequence 未增加时不重读；事件 ID / source sequence 保持原有幂等机制，投影失败进入既有 reconciling 路径，不重新执行工具。首次 SDK 文本 chunk 及时形成 metadata 事实；后续动画仍可合并。历史 Journal 最终完整校验；本任务没有声明大型历史 Journal 扫描的性能上限，F25 继续 I/O 与恢复诊断。

tool.finished 保留受控调试 capture 的身份/完整性引用。只保存并懒加载真实 stdout/stderr，read_file 内容、参数脚本、模型/上下文原始调试载荷没有读取接口。私有路径由已提交 turn/native/execution/trace 身份推导；拒绝链接、超量、变更 hash 和不匹配身份。派生脱敏流作为 profile-owned metadata artifact 保存，通过 <=256 KiB 分块读取；原始 capture 每次重核，缓存不能掩盖源丢失。默认 metadata 模式明确 capture_disabled，不制造输出成功。

Trace 树/瀑布以实际单调时钟和父子身份构建，每 Trace 独立时间基准；不将并行 Span 耗时求和。列表每页至多 100，内存至多 10000，DOM 虚拟化；父节点尚未加载、历史裁剪、查询失败、游标失效分别显示。历史分页期间费用/时延继续刷新，Trace 等集合保留分页；手动重新核对回到第一页。

上下文仅显示版本、估算器、原始消息 fingerprints、工具配对和确定性的原始约束消息存在检查；不从摘要相似度断言语义完整。证据分别显示 Harness workspace revision 和 Engine 内容扫描 revision/hash 对照，两者不混为一个计数器。当前环境未经实际核查时显示 unverified_current；内部验证不等于独立 grader。

usage 每次读取唯一请求账本的完整 Decimal 汇总，替换客户端视图而不累加事件或父 Span。展示 known / estimated / unknown、质量、价格快照与 exporter 缺口。Engine 受理到终态的时间只在同 owner epoch 且单调边界完整时计算；历史缺失或跨所有权时未知。Harness wall_seconds 来自真实预算终态事实。用户点击到收到回执及客户端读取首文本在当前 UI 测量，含 400 ms 轮询延迟，重载后未保留则明确未采集；不称服务器内部首 token 时间。

验证包含真实 Harness / SQLite / Journal / 文件 / RPC 数据、标准 stale-evidence fixture 的 unittest 与实际外部 invalidate.py、实际 React 万行 Trace、开发 Electron 的工具导航/安全输出/证据版本/重载费用。脚本模型仅用于离线行为；Windows 11 / Ubuntu / frozen installed Engine / native dialogs / paid model 验收独立 blocked。原完成策略和 create_runtime / CLI 签名保持，严格完成约束归 F30。
