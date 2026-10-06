# ADR 005 — 私有 Engine RPC 与持久补读

状态：implemented；原生/桌面产品验收尚未完成。

Engine 保留唯一 Harness 主循环，RPC 受理事务提交后再由调度器领取工作。stdin/stdout 仅严格 UTF-8 JSONL；诊断去 stderr。握手校验协议、profile 和生成契约内容 hash，资产来源/完整性仍由 release-lock 管理。只广告 17 个已接入实际服务的 handler；测试脚本只能显式用于 test profile，desktop/evaluation 默认 strict，未就绪即阻断。

snapshot 与事件高水位在同一 SQLite 读事务中取得。持久 store secret 签名游标，限制 store generation、profile、scope；保留期变化返回 INVALID_CURSOR / history_gap。订阅只保留一个待 ACK batch，通过 SQLite 补读，事件 ID 不变。跨 scope 游标不能伪造，重复历史事件由客户端按身份去重，UI reducer 留给桌面任务。

输入 128、控制 64、数据 8 项队列有界；writer 只用 OS fd 写，避免解释器退出时缓冲锁死。控制响应优先，但无法抢占已经写入的帧。真实 slow-reader 测试填满输出管道后以只读 SQLite 确认新 turn 派发和取消受理，恢复读端后检查 action 原对象。取消中未证明进程清理的结果仍 indeterminate，不能伪造 clean。

验证：F05 unit 32、portable 44、全回归 816 pass，0 skip，contracts --check pass。证据见任务卡。Windows 10 便携集成不能代替 Windows 11/Ubuntu、Electron UI 或付费模型实验。注册验收任务后发现旧失败样例递归启动 F05，已停止相关进程并改为隔离 registry 断言；中断运行不计通过。
