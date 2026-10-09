# ForgeCode 合并与减重实施记录

用户于 2026-10-09 批准实施，明确允许验收后正常合入 GitHub `main`。执行顺序：保存增量、统一目录、修复回归、结构减重、完成轻量沙盒、验收合并。产品保留完整研究工作台、CLI/Harness、协议与用户数据。

## 已落地的结构变化

- 删除无调用的 `stop_interactive_session`、`start_interactive_session`、`resume_interactive_session`、`file_read_paths`、`verification_is_current` 与空 `evals/metrics.py`。活动的异步 CLI 生命周期仍保留，明确的兼容接口和研究扩展不删。
- 文件选择与身份检查归入中立的 `forge/file_selection.py`；benchmark 旧入口继续导出兼容名称。
- Store 自身写入恢复事件，解除对 benchmark scheduler 的反向依赖。
- `forge/engine/bootstrap.py` 合并 stdio/Web 的组装；入口准入仍分别校验。
- CI 完成一次构建，后续套件做只读资产清单校验；源码摘要变化拒绝复用，完整覆盖不减少。

## 回归根因

迁移后基线为 Windows 1450 collected / 1445 passed / 5 failed。四项调试文件故障源于内部路径超过 Windows 传统长度，写入被捕获成 omitted 或后续读取失败；私有存储写入/读取复用已有长路径函数，断言保留，并增加真实长路径红绿测试。

HTTP 的短时游标用例在整个任务结束后才开始消费事件，慢机器上先发批次的 ACK 已过期，合法快照恢复越过未确认尾部事件。测试改为像真实 UI 一样在快照轮询期间持续消费/确认，保留 turn.finished、明确过期后的重建和未来事件断言；未延长游标寿命或放宽协议。

## 恢复与当前证据

Windows 原 Git 历史、1331 文件 V4 增量及私有副本保存在 `.local/consolidation-20261009`；`cf432236e721682a45fc83c3112db31c8dc82d00` 是恢复检查点。原 .env、会话、用户配置未提交。WSL 的源码比较没有独有代码修改，平台清单与交接记录保留，并在最终目录重新准备依赖。

当前验收结论集中维护于 [progress](progress.json) 和 [handoff](handoff.md)，平台原始证据在私有 `.local`；公开观察索引不含凭据或原始调试内容。轻量模式及能力含义见 [ADR036](adr/036-consolidation-and-workspace-write.md)。历史 1446 pass 与原生 11 pass / 1 fail / 11 blocked 保持原样。

## 新机与 hosted CI 的后续发现

扩展长度 Journal 路径需要在私有 Harness 边界两侧使用相同的路径写法；修复保留解析后目录约束，并补充扩展路径的外部 Journal 拒绝检查。第一次整合全量结果 1466 pass / 1 fail 保留，后续修复通过单项与定向验证。

第二次全量在三个原有事件用例出现时间窗失败，1464 pass / 3 fail 保留。审批测试的观察窗与原来已冻结的 30 秒 Harness 预算对齐，单次 RPC 仍限 15 秒；响应丢失用例只对故意扣留的首个响应保留 500 毫秒短超时，只读 action.get 使用正常的 15 秒上限。权限、原断言、迟到响应与不重复执行要求保留，产品预算不变。

Hosted Windows 管理员运行器创建目录时可以使用令牌默认 owner group。ACL helper 仅接受当前用户或当前 TokenOwner 所有的新建空目录，实际新增 ACE 仍只给当前用户；外来 owner 被拒绝，既有 ACL 与 owner 保持。依据 [Microsoft TOKEN_OWNER](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-token_owner)。OS 不支持与运行时缺失各自测试，Windows Server 的 portable 通过不会成为 Windows 10 原生证据。

## 当前验收与合入状态

实现提交84a2c30已推送，Windows完整回归1469项通过，Windows/WSL轻量原生各12项通过；受信任push的三平台各unit286/portable438及契约、质量通过。旧目录已归档，用户私有文件摘要复核一致。主线尚未合入：依赖安全仍2high，PR Ubuntu24缺少受信任push专用的系统helper权限。完整源码/平台范围、两轮失败报告及当前门禁见 [本轮证据](evidence/F28-F31-consolidation-observations-20261009.json) 和 [当前交接](handoff.md)。
