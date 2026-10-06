# ADR 004 — 服务调用现有 Harness

状态：implemented；实际提交以 git HEAD 和 evidence 为准。

F00 确认已有 create_runtime、Conversation.stream、TurnRunner 和 ToolExecutor。
应用服务只受理、领取和投影工作项；模型规划、预算、工具 schema 和完成判断继续由原 Harness 执行。

RuntimeBindings 是可选的入口依赖，既有 CLI 不传它时保持原配置、Hook、MCP 和权限模式。
桌面服务传入经过校验的连接配置、CredentialProvider、backend、EventRecorder 和审批回调。
服务不读项目 .env、permissions.json 或 Hook/MCP 配置；连接元数据和预算快照落盘，API key 只从 CredentialProvider 取得。
ForgeConfig repr 不含 API key。辅助 Explore 保留原循环，并接收相同 backend/recorder 和父预算。

ToolExecutor 在已有参数验证、Hook、授权和 Journal intent 之后调用 backend。
LocalTrustedBackend 执行真实工具，但只能用于显式 local-trusted 模式；strict 默认没有就绪 backend 时拒绝受理。
配置保存实际 mode 和 provider/injected 来源，scripted 测试不形成真实模型成绩。

SQLite migration 002 新增连接元数据、不可变预算、会话配置引用和不可变原始终态。
会话创建与 action 在同一事务；turn 受理继续用 F03 的原子事务。响应丢失重试返回原对象。
dispatch 再核验权限、revision、连接和冻结配置；改变或删除连接不能使 queued turn 使用新配置偷偷执行。
续接原 Journal；显式选择另一 provider/model 时按原规则 fork，保留历史。

CLI 和服务的 Conversation.stream 按工作区文件身份持有同一个 OS 锁。
Explore 在原父预算/锁上下文中复用所有权；其他入口无法在该工作区同时执行。
锁不占用工作区文件，路径别名不创造第二个执行身份。

取消 queued 项不会构造模型。running 取消传播至现有模型/工具，保留 Journal；
工具吸收 CancelledError 并返回 indeterminate 后，adapter 停止重规划。
未知工具结果对应 indeterminate，完整 Bridge 清理确认和故障恢复仍由 F12/F25 负责。
投影失败留下 reconciling，禁止把已发生的工具副作用再执行一次。

16 项新集成测试使用 scripted model 和真实 SQLite/Journal/文件工具/进程/OS 锁。
CLI 与服务工具序列、schema 和 stop reason 一致；Engine 没有 terminal input/Rich 输出。
此证据只证明当前 Windows 10 的可移植实现，Windows 11/Ubuntu 原生验收与付费模型实验不计通过。
