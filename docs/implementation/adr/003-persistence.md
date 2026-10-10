# ADR 003 — 单写入者、受理事务与 Journal 投影

2026-10-06，F03，依据主规范第 7 章与附录 B.2。SQLite 管业务受理、状态和查询投影，原 SessionJournal 保留工具副作用事实，不创建第二份执行事实。

Store 使用本地数据目录、OS 持有句柄锁、WAL、foreign_keys、busy_timeout 和 synchronous=FULL。迁移按连续版本与 SHA-256 校验，可靠事务执行 SQL；避免 executescript 的隐式 commit。已有数据迁移前使用 SQLite Backup API，备份显式关闭连接、刷新文件后原子发布。新 schema 或迁移失败进入只读 DB 诊断，保留一致备份和错误记录。[SQLite Backup API](https://www.sqlite.org/backup.html)

profile/method/action 唯一；参数 canonical hash 不同返回 IDEMPOTENCY_CONFLICT。配置快照、turn、work item、action、accepted event 在同一事务提交。回复丢失后重试复用原 turn。受理不启动任何模型或命令；权限、连接、预算和 sandbox readiness 由 F04 服务在受理前验证。

SQLite 保存配置快照与引用，不把生成 ID 当作未落盘的虚构快照。未知 budget 分量使用 null，已知数值仍为整数。单个 active work item 有唯一约束；CAS 领取绑定当前 epoch。重启时旧 owner 项进入 reconciling，禁止新领取；只能明确保留 indeterminate，不能自行重跑或用 PID 杀进程。真正清理/恢复由 F12/F25 接入。

JournalProjector 复用 SessionStore 的有效链与 payload hash 读取，按 source sequence 事务更新 events/offset。journal.projected 仅包含原始类型、ID 和内容 hash，不伪造缺失的 execution ID、usage、评分或完整 Trace；F17 再接入真实语义事件。同 event ID/source seq 不同 body 进入冲突索引，不覆盖首条事实。投影失败补读不执行工具。

新增真实故障测试发现 SessionJournal 在 append 成功前更新去重集合。一次 intent 写入失败后恢复路径，同 call ID 会跳过 intent。已将 started/completed 标记移到 append/fsync 成功之后；新测试使用真实 RunCommandTool 与文件障碍，证明拒绝执行及恢复后的 intent/result 完整。

artifact 先受控临时文件、fsync、原子 rename，再事务公布 metadata 引用；默认 100 MiB 单项、1 GiB attempt、200 MiB diagnostic 安全预算，关联 attempt 总量来自已保存 size。失败可能留下无引用文件，不能为了清理而删除可能已提交的事实；F25 按所有权/引用清理 orphan。Linux 还 fsync 父目录，Windows 文件 fsync/原子替换已在本机执行，断电设备验收未运行。

新数据仅在测试临时目录创建，未迁移用户现有 .forge。Windows 10 可移植层结果不能替代 Windows 11/Ubuntu 原生 D30 验收，二者维持 blocked。未运行任何付费模型、系统 setup 或签名操作。
