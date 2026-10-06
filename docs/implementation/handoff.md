# ForgeCode V4 交接 — 2026-10-06

- 当前代码完成 F00—F12；下一 F13（任务卡、第 15 / 16 / 20 章已读）。原生平台验收独立 blocked。
- F12 提交父 HEAD：4b87665a6a919f09438796fbdb685243e6fe85d4；接续核对实际 git HEAD。
- F12：EOF / 输出背压停止调度并取消，readonly 实际取消不再调用模型；未知写入不回放。
  预算／批次跳过与 execution_cancelled 区分，保留原 Harness 失败和预算行为。
  migration 003 保存 Agent / grader / environment deadline 和独立 cancel / cleanup 状态。
  Job accounting / Linux subreaper 只处理 live owned handle；严格原生 cleanup unknown 保持 indeterminate。
  固定 helper 执行 run_command / verify；PhaseLifecycle 保留服务到评分结束或 final deadline，评分结果独立。
- F12 最终 unit 124 / portable 92 / regression 956 pass，0 skip；证据
  20261006T153532Z-e789956e / 20261006T153544Z-ed76e3cf / 20261006T154553Z-92330490。
  Linux / Windows native blocked 0：20261006T153751Z-30d364a9 / 20261006T153755Z-48e0813d。
  contracts --check pass。旧 EOF 演示先显式 drain；新 bare EOF 测试必须取消队列和在途。
  旧单测同名收集冲突、四项取消误判已修复，失败证据保留，最终 956 项全过。
  直接在原测试进程重开刚强杀的 WAL 库出现过 disk I/O error；真实新 Engine 恢复测试通过。
  F25 继续 I/O 诊断与对账覆盖；F20/F21 接入 PhaseLifecycle grader，native / 付费实验继续 blocked。
- F00 已推送：3355ee07fd05978cd19284f0460694d10d4d9086，分支 codex/forgecode-v4。
- 实际基线 HEAD：d5c08a3158c764d4e797b2a1e2907e391f6414a8。
  接续时运行 git status / git rev-parse HEAD，以实际提交为准。
- F11 新增早期 file-worker、固定 stdin payload、真实 read/write/patch/search、授权观察、
  expected hash / STALE_FILE、逐文件原子 patch、受控 checkpoint blob 和实际路径身份。
  现有 CLI 默认、公开工具 schema / ToolResult 保留。工具包延迟导入；helper 不读凭证或启 RPC。
  严格模式不执行未迁移 host Hook/MCP；Explore 继承 backend。受限 checkpoint 不走宿主 restore。
- F11 新增 18 项 helper/Harness 行为，unit 117 / portable 79 / regression 936 pass，0 skip。
  证据 20261006T142311Z-40883bdb / 20261006T142329Z-a1f5d82a / 20261006T142628Z-56aaf5ff。
  Linux / Windows native blocked / 0 checks：20261006T142532Z-0939a885 / 20261006T142538Z-16bcd303。
  Shell/helper 一致性、OS TOCTOU 和安装版 Engine 可读授权未验收；strict 仍拒绝未验证能力。
  command/session 监督由 F12 接续，受控 restore 由 F16 接续。未执行付费模型或产生真实成绩。
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
- F04 已推送 980e77be6ac093aef25baebcde316b468a5f07b0。
- F05 实现 python -m forge.engine、17 个真实 RPC handler、Main ACL、严格 JSONL、
  32 项 batch、stderr 隔离、控制/数据有界队列、真实工作调度；握手前不派发任务。
  契约 manifest hash 与发布资产 manifest 分开；scripted fixture 仅 test profile。
  持久 HMAC 游标绑定 store/profile/scope，snapshot 高水位、ACK 后补读、保留期缺口，
  turn.started / cancellation.requested / turn.finished 在真实事务中写入。
  slow-reader 测试实际填满管道后仍受理 start/cancel，不声称大帧可抢占或已完成 UI 验收。
- F05 最新 unit 32 / portable 44 / regression 816 pass，0 skip；证据
  20261006T111751Z-a64edfea / 20261006T111756Z-f5819b93 / 20261006T111902Z-ff8046cd。
  contracts --check pass。早期测试误用已注册 F05 作为失败样例，递归启动验收；
  已停止本任务进程，改用隔离空 registry 的单元断言并重跑。中断运行不计通过。
- F06 已完成版本化/限步 ScriptedModel、六个公开 fixture 基础结构、真实读改测
  RPC demo；只能显式 local-trusted，OS 隔离验收仍 blocked，不能导出真实模型成绩。
- F05 已推送 cb724ac5cbd2b874f9d4fdd2f7501905d44c53d5。
- F06 已实现 forge.testing.demo / RpcClient、版本化限步脚本、精确 test 审批、六个实际
  fixture 基础结构。verify --task F06 unit 54 / portable 49 pass、0 skip；证据
  20261006T113646Z-1cb5c7d9 / 20261006T113651Z-324b93bb。完整回归 843 pass、0 skip，
  20261006T113811Z-54d4f9dd；contracts --check pass。验收后提交/push F06。
  早期报告查询/视图和旧脚本兼容失败已修复，失败索引保留。
- 演示 python -m forge.testing.demo [--output-dir 新目录]；真实文件/进程、修复前后
  unittest 结果及 native Journal/SQLite 哈希。scripted/local-trusted，不能作为真实榜单。
  断开响应后 drain，再开启 Engine 用 action.get 和同 ID 重试，确认只有一个 turn/Journal。
- F07 已阅读任务卡、第 10—12 章和锁定 SRT 的 config/schema 类型。allowRead 是 deny
  例外，不能伪装严格读取白名单；需要路径身份/链接/特殊路径拒绝及独立能力要求检查。
- F06 已推送 b5d2b4f5a79d9691e0a94ce430c38375911b4adc。
- F07 已实现 forge/sandbox policy/capabilities/path_policy，规范化/hash/服务前置复核、
  强要求拒绝、真实身份/硬链接/junction/特殊路径、外部 gitdir/commondir 保护。
  capability verification schema 及 3 个生成文件已更新；未探测无 verified 能力。
  unit 96 / portable 56 pass，0 skip，证据 20261006T115350Z-bfd58d91 /
  20261006T115355Z-edb1d80e；contracts --check pass。完整回归 892 pass、0 skip，
  20261006T115517Z-a30f1c2e。Native OS 隔离与动态 shell/file-worker 验收 blocked。
- F07 已推送 0a3c03f6f5e88e50595b1c8c78fca311f4c6c247。
- F08 实现独立 SrtBackend、固定 Node/资产/1413 文件运行库存、六方法 Bridge、受限
  dispatcher、单独 payload stdin、raw byte/增量 UTF-8/输出与 session 配额、有界队列、
  execution hash 幂等与 owner、重复 close。新 command argv 保留空参数；生成契约已同步。
  真实 NODE_OPTIONS 注入测试未执行宿主 loader，输出伪造 RPC 只成为数据 envelope。
  unit 100 / portable 60 / regression 900 pass，0 skip；证据
  20261006T122617Z-467c0ff0 / 20261006T122623Z-252a9f8b / 20261006T122834Z-df2df90c。
  contracts --check / npm run typecheck pass。F08 验收后提交并推送。
- Bridge probe 仅系统/依赖前提，不制造 verified 能力；严格 prepare 在原生边界/清理证据
  未齐时拒绝。SRT reset 为 best effort，native 初始化后 cleanup unknown；不能据
  remaining_processes=0 推断无残留。仅从未初始化/启动的 Bridge close 可确认 clean。
  F09/F10 的受控验证器可调用 initializeNative 底层入口；它不注册 RPC，不接收模型能力。
- Node 路径由 release-lock 固定；安装根/compiled modules 需与 trusted control 根分开，
  不允许工作区覆盖安装目录。prepare 的 control-root 保护会进入冻结 policy hash，
  F11 接入存储/工具时需在创建 policy 前固定这些根。现有 Harness/CLI 入口保持。
- 构建命令 python scripts/build_bridge.py；校验 --check，inventory 不能在测试中自修复。
  packages/contracts 和 sandbox_bridge 编译强制 LF，相关资产清单固定 LF。
  Windows 11/Ubuntu 原生、动态 .env、权限/后代清理和真实模型实验仍 blocked；GHSA 保持。
- F08 已推送 0cabc1873caa83bdb096f4532a4c5707196aa736。
- F09 新增只读系统/项目 Doctor、固定 bwrap 真实前提探针、受控 Linux native fixture、
  namespace/start tick/父子链绑定的保守取消；不按名称或孤立 PID 杀进程。
  真实 canary 包括动态 .env、外部合成文件、代理变量删除、直连、Unix socket、嵌套进程。
  动态敏感路径若可读必须 fail；SRT 初始化后 session cleanup 仍 unknown，不制造 verified。
- F09 unit 110 / portable 60 / regression 910 pass，0 skip：
  20261006T125123Z-88d982fc / 20261006T125133Z-3780e12c / 20261006T125856Z-b7b52c5d。
  sandbox-linux 真实入口 blocked / 0 checks，20261006T125256Z-c6d9de11；Windows 10
  不满足 Ubuntu 22.04/24.04，doctor exit 2、task 聚合 exit 1。不是空集合 pass。
  早一轮回归误把非 test_ 的 native verifier 路径纳入 case_ids；已修复并重跑，旧证据保留。
- Ubuntu 内核/隔离、C10 显式受控公共 HTTP endpoint、F12/F25 完整 session cleanup 尚缺。
  当前 runtime inventory 1415 文件；构建和 --check 均成功。F09 验收后提交/push。
- F10 已审计实际 srt-win --help 与上游 install/status/ACL API：诊断只读，install 真 UAC；
  重复 install 可能更新共用用户凭据，不能把 repair 当无影响动作；不得自动 uninstall /
  force / acl recover。现无只读共享 ACL holder 列表，未知共享活动需保守阻断。
  此审计没有执行管理员 setup，没有 Windows 11 原生通过证据；F10 尚未写代码。
- F09 已推送 cc2df6c99012bc7ee8cd21147fcccd4408479d8a。
- F10 正在实施：固定 Main setup broker / 三 action Bridge launch、实际上游 UAC API、
  保守共享 repair 阻断、OS Known Folder worker + OS lock + unresolved marker、真实 Windows
  Doctor / NTFS / helper 状态、双平台共同 native canary。没有执行管理员 setup。
  首轮 unit 116 / portable 61 / native Windows blocked 0；证据
  20261006T132815Z-f8ebdcb8 / 20261006T132833Z-f342b184 / 20261006T133034Z-632dd5ea。
  实际 raw srt-win user 有额外 wrapper / snake_case，Python Doctor 映射已修正并新增单测；
  上述旧报告的账户字段遗漏不能作为最终诊断证据。需重跑最终 task + regression 后推送。
  此前回归 20261006T133122Z-68006520 为 917 pass，是映射修正前启动；不作为最终 source 验收。
  最新局部 doctor 测试 12 pass；setup blocked 退出码 0 的问题已修复为 2，stdout 完整写出。
  接续先核对 git status；F10 改动未提交，用户原有 untracked 仍保留。F11 卡 / 第 9、11 章
  已读并盘点现有工具 IO、Tracker / Checkpoint；F11 尚未写代码。
- F10 最终代码验收 unit 117 / portable 61 / regression 918 pass，0 skip：
  20261006T134127Z-76e53bec / 20261006T134145Z-5e720e59 / 20261006T134442Z-98283708。
  Windows native 20261006T134352Z-ad842262、Linux native 20261006T134433Z-be4553ee
  均 blocked / 0 checks / 底层 exit 2。实际 raw status 映射已修复：SRT 账户 / credential
  缺失；BFE cannot-read、pwsh7 缺失、NTFS。没有自动 setup / force / recover / CA 安装。
  首次 setup 需账号/group/credential/marker 全缺；cannot-read 不单独阻止明确确认的 UAC。
  所有共享安装 repair 均保守 blocked；正常 task 不提权。Main 仅编译，F13/F14 接入 GUI。
  Worker 锁/未解决 grants marker 真实测试；全 Job/ACL 清理 F12/F25 仍需原生证明。
  inventory 1418 文件已实际构建/--check。F10 验收后提交/push，再开始 F11。
- F10 已推送 d17c361e147e07109c3f74761cd7df7954fad5b9；F11 进入开发。
