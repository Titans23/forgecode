# ADR 016 — 工作区内容观察与只读恢复预览

项目授权 revision 保持配置 CAS 的含义；migration 006 单独记录内容 revision、有限变更历史、不可变 turn baseline 和消息序号。
原 Harness 的工作区 OS 锁取得后调用可选基线回调；CLI 默认为空，Explore 的锁与预算继承规则不改变。
基线由真实文件字节生成，受控 artifact 保存内容；无模型循环副本或静态 Diff。

检查最多 20000 个对象、5 秒、16 MiB 小文件字节。文本上限 128 KiB；二进制／大文件／超额有明确分类。
PathPolicy 拒绝逃逸、敏感文件、链接、硬链接及 Engine 数据目录。文件读取核对打开句柄身份、读前后状态，
小文件还比对完整 SHA-256；大文件按需读取 256 KiB 上限。扫描不完整时 history_gap 为真，不据此执行恢复。
分页 cursor 绑定 workspace、内容 revision 和查询范围，外部修改使旧 cursor／预览失败；不改变项目执行授权。

原有 dirty 来自真实 Git status；禁用 fsmonitor／hooks／global/system config／可选写锁，
GIT_CEILING_DIRECTORIES 阻止无 Git 的项目误读父仓库。读取有时间和输出上限，不可用时明确报告。
本轮变化相对当前工作树基线区分新增、删除、修改、唯一 hash 匹配重命名，保持原始 CRLF 和末尾换行。
反向 patch 包含当前 hash 和内容版本，只有审查预览；本任务不提供写入或 reset/clean/checkout 操作。
手工应用需用户自行审查并遵守文件前置条件与同一工作区写入锁，客户端不声称已恢复。

普通 GUI 会话由 Main-only 操作提供固定 deny-direct 策略与 16 model / 64 tool / 300 秒预算。
策略和预算预配置后，会话／turn 与对应 durable action 在同一事务受理；同 ID 重试不创建额外任务。
严格生产 readiness 不满足时拒绝发送，脚本模型只用于明确的 development test profile。

显示消息来源于真实 Harness 事件；known credential 跨 delta 脱敏，工具卡仅保留名称／状态。
每 turn 最多 10000 条／1048576 个字符，单条 2048 字符，超额显示限制提示；原 Journal 保持原有权威性。
session snapshot 事务绑定消息、终态与事件 high watermark／签名 cursor；Renderer 重载先读快照，不调用 submit。
UI 按 turn + sequence 去重；文件、会话、消息与事件列表使用固定行窗口，不导入 Electron。

诊断／安装接线复用 F10 Main broker：固定 Node、Bridge 和完整依赖 hash，Renderer 无 argv／路径／环境输入。
目录／授权／setup 使用同一原生操作互斥与当前 sender 重查；实际管理员安装、共享 setup repair 和 installed assets 验收独立受限。
Windows 10 的 development Electron／DOM IME 与缩放检查不替代 Windows 11 或 Ubuntu 的 native/hardened 验收。
