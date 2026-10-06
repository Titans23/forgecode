# ForgeCode V4 交接 — 2026-10-06

- 当前完成 F00—F04；下一任务 F05（已阅读 tasks/F05.md、第 8 章和对应 RPC schema）。
- F00 已推送：3355ee07fd05978cd19284f0460694d10d4d9086，分支 codex/forgecode-v4。
- 实际基线 HEAD：d5c08a3158c764d4e797b2a1e2907e391f6414a8。
  接续时运行 git status / git rev-parse HEAD，以实际提交为准。
- 实施包原件在 update_implementation_pack/forgecode-implementation-v4，
  工作规范在 docs/implementation；原件未修改。
- 用户原有 untracked：.forge/、build/、三张 architecture 图、svg_probe.txt、
  update_implementation_pack/；保持原样，不加入本次提交。
- 已实现 scripts/impl.py audit [--write]、doctor --scope development
  [--check-network]、status、verify --task F00 / --suite audit / --suite regression。
  异目录定位、AST 签名核验、缺工具／网络 blocked、JUnit 判定、真实 evidence 索引。
- 基线 powershell -NoProfile -File scripts/test.ps1 -q --tb=short：exit 0，740 passed。
- 新行为 verify --task F00：exit 0，7 passed，evidence 20261006T091942Z-d39f0b28。
- 回归 verify --suite regression：exit 0，747 passed、0 skipped，
  evidence 20261006T092001Z-c7f13bd9；日志和 JUnit 留在 .local/implementation。
- 两次旧 evidence 采集因扫描缓存太慢被取消；已修正并重跑，不计通过。
- 工具：项目 Python 3.12.13，Node 22.17.1、npm 10.9.2、uv 0.12.5，
  Git 2.52.0.windows.1。gh / pip / py 不可用；uv pip 可用。
- 本机 Windows 10 build 19045。Windows 11、原生 Ubuntu、GUI 安装版、签名、
  SRT 管理员 setup 和付费模型预算均未验收／未授权，不冒充 pass。
- 用户授权每个开发版本测试后上传 GitHub；未授权付费模型实验、管理员 setup。
- F01 完成 npm workspaces、package-lock / uv.lock / resolved release-lock、资产
  loader、固定 Node 和 Python 运行时、PyInstaller onedir / Forge Vite 真构建探针。
  uv sync --group desktop-build --link-mode=copy 和官方 npm ci 成功。构建与安装串行。
- F01 unit：9 passed（内含 4 个真实 Node 行为断言），20261006T094338Z-9ee68b20。
  回归：748 passed，20261006T093957Z-8e28ebc0；新增固定 Python 拒绝行为另有 unit 证据。
  开发 smoke 6 checks pass，20261006T094341Z-a9e7179e；实际 packaged 验收 exit 2
  / blocked，因为当前 Windows 10 不属于声明平台。npm run typecheck 通过。
- 一次误并行 npm ci 占用 native 模块导致失败 20261006T093844Z-5d88174b，已重装重验。
- GHSA-86w9-cpqp-85rv：node-forge 1.4.0 尚无已发布补丁，SRT 0.0.78 依赖它。
  security=blocked；node packaging/verify-release.mjs --release 拒绝生产发布。
  不运行 npm audit fix 的旧 SRT 降级；不自造密码学补丁。详见 ADR 001。
- F01 已推送 629b8d612a0b0587144429522561a0dd0f26bcbb。F02 提交父 HEAD 即此提交。
- F02 共享契约已实现：48 个 Engine / 6 个 Bridge / 41 个事件 payload，配置快照、
  Policy / RunSpec / Bundle；严格 UTF-8 JSON、canonical hash、权限、生成漂移检查。
  contract_only 不作为可调用能力；后续服务/Bridge/观测任务接入真实 handler。
- F02 verify --task：unit 32 pass，portable 1 pass；同判定 310 个结构样本与
  原始 seed/边界、99 个通道请求。evidence 20261006T100851Z-a7ee0375 / 20261006T100856Z-8425af60。
  最终回归 773 pass / 0 skip，20261006T100851Z-a837e8f2；types / contracts check pass。
- F02 新命令 contracts [--check]、gate --name implementation/release、suite/case 注册。
  尚未实现的原生/desktop/live verifier 返回失败，required skip/零测试不能 pass。
- F02 已推送 6c2d10f4d68cae884993b92525459271a938780e。F03 提交父 HEAD 即此提交。
- F03 完成真实 SQLite migration、OS owner lock、WAL/FULL、FK/唯一/不可变约束、
  事务受理、snapshot、CAS/epoch/对账、Journal metadata 投影/冲突隔离、Backup API、
  原子 artifact 和单项/attempt/diagnostic 配额。未迁移用户 .forge。
- 修复原 SessionJournal：append/fsync 成功后再加入 started/completed 去重集合。
  否则首次写入失败后同 ID 重试可不记录 intent/result；真实命令测试已复现并验证修复。
- F03 unit 32 pass / portable 17 pass（16 个存储行为），证据
  20261006T102610Z-94aab17c / 20261006T102615Z-0f141cab；最终回归 789 pass / 0 skip，
  20261006T102610Z-698a0127；contracts/typecheck pass。N02/N06 portable pass，
  D30 Windows 11/Ubuntu 原生验收仍 blocked，不能用 Windows 10 代替。
- 旧 owner 项重启后 reconciling，不能领取新任务；mark_indeterminate 只保留未知结果，
  完整清理/继续策略留给 F12/F25。Journal 回放仅元数据，不重跑工具、不编造费用。
- F03 后续修正迁移 CRLF/LF 校验差异，锁文件和生成契约固定 LF；新增真实迁移重开测试。
  最新 unit 32 / portable 18 / regression 790 pass，0 skip；证据
  20261006T103414Z-2a9980f6 / 20261006T103419Z-f3e3cf9a / 20261006T103452Z-166d145d。
- F04 复用 create_runtime/Conversation/TurnRunner，增加 backend/recorder/approval 注入、
  服务化 create/start/cancel/snapshot、真实权限/连接前置校验；保留CLI入口和原始终态映射。
  strict sandbox 尚未就绪时明确阻断，测试可使用显式 scripted profile，不产生成绩。
- G0 pass；G7 blocked；其他门禁未运行。保留 Python Harness、CLI、MCP、
  Hook、Explore、Feishu 和已有 Harbor runner，不复制或重写主循环。
- F03 换行符修复已推送 4e851013f2dfc3021787f9dce35d0bc7346cb7de。
- F04 实现 ApplicationServices / HarnessAdapter / session views / RuntimeBindings；
  真实 create/start/cancel/snapshot、冻结连接/预算、凭证内存注入、backend/recorder/审批回调。
  migration 002 保留原数据，服务不读项目 .env、Hook/MCP 或权限配置。CLI 默认兼容。
  工作区文件身份 OS 锁由 CLI/服务共用，Explore 继承 backend/recorder/父预算。
  原 Journal/完成契约不变；真实进程取消未知结果标 indeterminate，停止后续模型调用。
  16 新行为测试，最新 unit 32 / portable 34 pass，0 skip：
  20261006T105304Z-1b46b663 / 20261006T105310Z-27975238。
  全量回归 806 pass、0 skip，20261006T105339Z-c4dd0804。
  strict 未就绪明确拒绝。原生 Windows 11/Ubuntu 和真实模型实验仍未验收。
