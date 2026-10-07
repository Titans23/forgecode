# ForgeCode V4 交接 — 2026-10-07

- F24 已推送 6fb66b0b8b7fc9efee31bb8ded2fd82c19ca099a，远端 SHA 已核对；F25 当前实施中，测试父 HEAD 即此 SHA。
- 当前代码完成 F00—F25；F25 正式 unit 148 / portable 292 / regression 1184 pass，0 skip；development Electron 66 checks pass。
  F25 未推送，正在形成测试后提交；测试父 HEAD 6fb66b0b8b7fc9efee31bb8ded2fd82c19ca099a。
  最终 evidence 20261007T020959Z-85c76830 / 20261007T020954Z-8829811e / 20261007T020953Z-c1790ea7 / 20261007T020920Z-86e44ca9 / 20261007T020306Z-4af36e9d / 20261007T020248Z-da48273d / 20261007T021009Z-35508c5a；dirty f5d2c18ae9f4e914cb1629e79d5b6677890bf0ee59b565029a89f066c1c05fc2。
  migration 014 / committed Journal startup projection / recovery.inspect / readonly recovery UI / unknown cleanup barrier / bounded Main late/action response reconciliation / live parent observation / cursor TTL 与保守预算已实现。
  实际 Main / Engine 强杀后 DB reconciling + unknown，原 action 重查无新 turn；WAL intent/result 与 artifact 完整性据实际文件核查；PID fixture 不清理无关进程。实际事件摘要 evidence/F25-recovery-observations.json，原始测试数据留 .local。
  early desktop 014320、cold import / 500 ms fixture 与测试中资产 hash 拒绝的失败过程见 F25 卡；只有最后固定快照证据计最终 pass。
  packed / native Linux / native Windows 各 blocked 0；actual OS suspend/resume、installed / paid-model / native cleanup 不冒充通过。
  下一满足依赖 F26，卡和规范 19 / 附录 C 已读；CLI 与 Desktop 已共享实际 workspace lock。缺 legacy preview/backup/confirmed import、顶层未知配置诊断、explicit loopback HTTP/SSE 和 HttpTransport。仅做了阅读，F26 未改代码。
- F24 已实现 migration 013 / 人工标注版本与 CAS / 有限建议 / 脱敏候选 / 实际新执行复现核验 / 失败页面与 Main 原生导出。
  专项 14 项、含既有 ZIP 回归 49 项 pass；initial desktop 64 checks 的旧 zoom 失败保留，等待实际布局后重验 64 checks pass。
  正式 unit 147 / portable 268 / regression 1159 pass，全部 0 skip；development desktop 64 checks pass。
  最终 evidence 20261007T010959Z-5c2cc1a6 / 20261007T011016Z-ef2bdeb2 / 20261007T011517Z-8b0f33be / 20261007T011549Z-d546abf4 / 20261007T011550Z-aed347b6 / 20261007T011555Z-727165ad / 20261007T011559Z-8a7b78d1；final dirty 4596b013bb59f887f18caa42d6d5bbbd88423666d7e23a5fef44db2ccda839d2。
  packed / native Linux / native Windows 各 blocked 0 checks；不把离线 fixture 视为公开模型／原生 sandbox 结果。下一 F25：修复 RPC 超时迟到响应并做真实故障对账。
- 提交前只移除 annotations.py 一个空行的尾随空格（逐文本比较确认无其他源码变化），刷新 asset inventory 后补跑 unit 147 / desktop 64 pass：20261007T012542Z-218e0608 / 20261007T012606Z-e13d7eac；dirty 8ba311c8fcf12233a3b4afffc8c986d458d245246f168d3d800ec934ea64d50c。
- F23 已推送 4082833ff7a81f6555e96111239c87ea32b5a854，远端 SHA 已核对；这是 F24 的测试父 HEAD。
- F23 已实现四步不可变实验配置、profile 快照归属、真实分页报告、同题 A/B Trace 与不可比告警、Main 原生文件／隐私确认接线。
  unit 145 / portable 256 / regression 1145，全部 0 skip；Windows 10 development Electron 59 checks pass。
  最终 evidence 20261007T004628Z-9d6e9f79 / 20261007T004659Z-01d191f0 / 20261007T004716Z-11c7a1ea / 20261007T005211Z-485cb317 / 20261007T005211Z-82b55461 / 20261007T005216Z-cdbc3407 / 20261007T005220Z-8a72deb8；final dirty 850c79a0e7244712b36cabfb1c3d9f1f3252d5d84a880dbba2456f653902e030。
  初次 TypeScript preparation 与目录加载时序的失败证据保留；被中断的未完成套件不计通过。
  public model / Docker / F30 spend-policy、native Windows 11 / Ubuntu / installed client / 人工文件对话框仍 blocked。F24 将接真实失败标注、不可变历史与证据绑定复现候选。
- F21 已推送 2a96fc8593b10b2969482e408f42d4bd67151445，远端 SHA 已核对；F22 测试父 HEAD 即此 SHA，提交后用 git HEAD 核对。
- F21：安全 ZIP / private staging / 原始包与冲突隔离 / imported_* 只读表 / profile artifact / Main-only file token / offline report 已实现。
  unit 142 / portable 231 / regression 1117，0 skip；Windows 10 development Electron 44 checks pass。
  最终 evidence 20261006T231423Z-a85f0301 / 20261006T231438Z-2fe8c473 / 20261006T231905Z-de0f6db7 / 20261006T231934Z-9be0bef8 / 20261006T231934Z-6330ec21 / 20261006T231939Z-2971b33e / 20261006T231943Z-2e15c738。
  final dirty 528cd116b097af4ccb7ca6215d4c9a76f2e485e69e4df3c0f2f72afba81d6440，测试父 HEAD f9574492088892b88da3362b37a44b6c81687e37。
  35 项新增行为使用真实文件 / SQLite / RPC；静态手算 synthetic ZIP 为 4 planned / 5 attempts，不是真实模型成绩。
  schema 010 自动备份与旧产物归属回填；原始包 exact bytes 保留，Renderer 不可读原始 private bundle。
  首次 portable 230 pass / 1 fail：旧 F18 fixture 伪降 schema 7 遗留 artifact_profiles；改真实旧 schema 构造后完整重验通过。失败 evidence 保留；已中断旧回归不计 pass。
  packed / native Linux / native Windows 各 blocked 0 checks；原生 file dialog 接线留 F23，无 paid model / Docker / native sandbox 调用。
- F22 已实现真实 execution ID 工具导航、Trace 树／瀑布、stdout/stderr 安全懒加载、上下文版本、证据扫描版本差异、请求账本质量与客户端／Harness 分离时延。
  migration 011 / profile 归属 / filter-bound cursor；运行中 250 ms Journal 投影，首次文本 chunk metadata 及时记录，失败 reconciling 不重跑。
  正式 unit 143 / portable 242 / regression 1129 pass，0 skip；Windows 10 development Electron 52 checks pass。
  最终 evidence 20261006T235241Z-462bb220 / 20261006T235257Z-7e30a489 / 20261006T235747Z-6c832b03 / 20261006T235820Z-ebc91bad / 20261006T235820Z-02ca11f4 / 20261006T235825Z-e0e8fd6e / 20261006T235829Z-17b9f81e。
  final dirty f42654ff778895a1ac8232dd0ce363c11a91cc4a32bb641a78b4e57f70638932；packed / native Linux / native Windows 各 blocked 0 checks。
  focused fixture 修复详见任务卡，下一 F23；客户端实际小集 paid model / Docker 仍 blocked，不能假造公开成绩。
- F20 测试父 HEAD 12da1f9ab8c021d89f99ae35f796fcde4352eed4；提交／推送 SHA 以实际 git HEAD 核对。
  unit 142 / portable 196 / regression 1082，0 skip；Windows 10 development Electron 44 checks pass。
  最终证据 20261006T222943Z-40ff0fc5 / 20261006T222959Z-e4a89567 / 20261006T223421Z-c303888f / 20261006T223450Z-81b1e3a6 / 20261006T223450Z-f696f399 / 20261006T223455Z-d454cccc / 20261006T223459Z-232a8d30 / 20261006T223508Z-0c033e57。
  final dirty df16b9a3e48f2236455775548e9f38b79c4d55719019d0dcecd0cc6424ee4f56。
  真实官方 Harbor adapter / owned process / immutable grade / patch cache key；冻结 Harness 无额外 repair 或 Harbor retry。
  actual Aider Polyglot 1.0 三题来源 commit f30b14415dd733c83627204bad0af69a89ceb46f，官方 --print-config 3 checks pass；整体 probe blocked，0 model calls / 0 grades。
  Harbor 0.18.0 / Docker client 29.7.2，Linux daemon 缺失；不自动启动管理员服务。Paid approval / F30 spend / native policy reconciliation 仍 blocked。
  packed / native Linux / native Windows 各 blocked 0 checks；生产 executor 不降级、不调用未授权模型；远端 raw journals 不投影可信费用账本。
  F21 实施安全 ZIP quotas / staging / provenance / 原始与脱敏证据 / 原生 profile artifact 归属；导入命名空间只读，离线报告不执行脚本。
- F19 已推送 12da1f9ab8c021d89f99ae35f796fcde4352eed4，远端 HEAD 已核对；F20 当前实施中。
- F19 测试父 HEAD fdbcf50047d290d6c765aaa8ac7b5da77b26b95c；提交／推送 SHA 以实际 git HEAD 核对。
  unit 142 / portable 184 / regression 1070，0 skip；Windows 10 development Electron 44 checks pass。
  最终证据 20261006T215137Z-955dfd02 / 20261006T215153Z-20c452df / 20261006T215603Z-bc7852d1 / 20261006T215634Z-245af063 / 20261006T215634Z-fca4984d / 20261006T215639Z-7dfab335 / 20261006T215643Z-b64762b3。
  final dirty a1117ffe4bc74b91738fe6b07c69d8e3eeee2ec217664f680442140e828616f4。
  原子生成全部 planned trials；同 action 幂等；turn/attempt 单 worker、epoch、真实 heartbeat／deadline／owned process cancel。
  独立 execution／grading／cleanup 三轴；显式基础设施重试、末次授权选择；不可变 grade 和恢复 trace link，无自动副作用回放。
  真实 Harness journal 投影／请求费用去重，手算分母／未知费用／N/A／配置可比性通过；无官方／真实模型成绩。
  首次 portable 曾因旧 RPC capability 断言失败，修正动态目录校验后重验通过；旧 evidence 保留，已中断回归不计通过。
  修正交接 progress 的 F17 缺少对象键；JSON 解析复核，未改变代码测试结论。
  packed / native Linux / native Windows 各 blocked 0 checks；paid API、管理员设置、签名及 G7 仍 blocked。
  F20 实际环境：Harbor 0.18.0 已安装；Docker CLI 29.7.2，但 desktop-linux daemon named pipe 不存在。
  不启动管理员 Docker／付费模型；接既有官方协议、记录独立 grader／环境标签、源码与 patch hash。
- F18 已推送 fdbcf50047d290d6c765aaa8ac7b5da77b26b95c，远端 HEAD 已核对。
- F18 测试父 HEAD 939d6fa0dcd0d7583571521fdef3f08f176a926e；提交 SHA 以实际 git HEAD 核对。
  unit 139 / portable 170 / regression 1053，0 skip；Windows 10 development Electron 44 checks pass。
  最终证据 20261006T211118Z-7289c010 / 20261006T211134Z-d4dc513b / 20261006T211536Z-3bcf5fe6 / 20261006T211605Z-c37e3033 / 20261006T211606Z-ff84a680 / 20261006T211610Z-70274cd5 / 20261006T211615Z-e379a908。
  final dirty babd694a41cbccf99d4abcd0d9ce478ef3f27ca8ef0fd257018189bf124e1abc。
  实际 request 唯一 ledger／冻结价格／Decimal／unknown、角色重试归因、上下文指纹、真实证据失效、显式 metadata OTLP。
  官方 SDK 仅调用受控 loopback provider；不称为真实模型实验。部分 Anthropic usage 未结束时费用 unknown。
  controlled_debug 保存有界脱敏本地模型／工具／上下文副本，导出只含 metadata；出口和磁盘失败不阻断 Agent。
  migration 008 实际回填旧请求，不猜旧价格；原 CLI 与 create_runtime 签名保持。
  packed / native Linux / native Windows 各 blocked 0 checks；G7、管理员设置、签名与 paid API 继续 blocked。
  下一 F19 固定 RunSpec／计划分母／owner epoch／三轴终态；F20 接既有官方 Runner。
- F17 已推送 939d6fa0dcd0d7583571521fdef3f08f176a926e，远端 HEAD 已核对。
- F17：真实 Journal observation / fsync、受理根 trace、模型→上下文→工具→验证父子关系、Explore／摘要／重试归因。
  来源 ID/sequence/hash 冲突隔离；外部 import: 命名空间；重复回放不执行、不增 ledger。
  migration 007；恢复创建新 trace link 旧 trace，unknown 副作用仍不自动重跑。
  已实现事件目录留 trial/attempt/client 未接入项 contract_only；grader 是真实回调 fixture，F19—F21 接续正式协议。
  unit 127 / portable 156 / regression 1026，0 skip；Windows 10 development Electron 44 checks pass。
  证据 20261006T195728Z-806a10fc / 20261006T195743Z-1fa9ce8a / 20261006T195925Z-29d74dae / 20261006T200005Z-56bfc334。
  packaged blocked / 0：20261006T195955Z-97a1025e；native Linux / Windows blocked / 0：
  20261006T195956Z-d6a34afe / 20261006T200000Z-05e1019e。native Bridge child execution tracing 未验收；不制造 verified caps。
  F17 测试父 HEAD bc27f9780b179097d0e2bb9f732e93748ce3709c；final dirty 0fbdf33ae33b98b252644e79b82f76c3f0aa7156280eff4a71221c4ddc2f75b0。
  F18 继续 raw / normalized usage、冻结价格与 unknown 成本、上下文版本／配对、证据失效、有界异步 OTLP。
- F16 已推送 bc27f9780b179097d0e2bb9f732e93748ce3709c，远端 HEAD 已核对；F17 测试父 HEAD 即此提交。
- F16：真实项目文件、会话提交／流式消息／取消、immutable dirty 基线、Diff 和 guarded reverse patch 预览。
  migration 006；session+action / turn+action 同事务；snapshot 签名事件边界、sequence 去重、10000 有界列表。
  valid Hook/MCP/.env 不执行；Git 禁 fsmonitor / hooks 并限制父仓库查找。原 CLI / create_runtime 签名不变。
  unit 127 / portable 139 / regression 1009，0 skip；Windows 10 development Electron 44 checks pass。
  证据 20261006T191227Z-fdf08b04 / 20261006T191242Z-960d04de / 20261006T191601Z-c1e273e9 / 20261006T191654Z-94a8d24d。
  packaged blocked / 0：20261006T191629Z-b8802a4c；native Linux / Windows blocked / 0：
  20261006T191645Z-824876b1 / 20261006T191650Z-e24e7d15。人工原生 IME/对话框、安装版和 paid API 仍 blocked。
  早期桌面选择检查失败保留，最终实际展开面板后全部通过。final dirty dca27cb8ed3cc9567d26c41fbee24f7dcad2a8109cd5c5deeff18afbaff1f5b3。
  F16 测试父 HEAD 7d40c3e8f26c17c590463380defb9cf72e855913；提交 SHA 以实际 git HEAD 核对。
- F15 已推送 7d40c3e8f26c17c590463380defb9cf72e855913；F16 测试父 HEAD 即此提交。
- F15 测试父 HEAD e612f360be14dc2a198d65f936b88a7ffd336941；本次提交 SHA 以实际 git HEAD 核对。
  Main fixed Electron safeStorage helper / DPAPI、Linux 保护分类和内存模式、私有 Engine 注入、确认连接与显式联网测试。
  Renderer 无密钥读回；设置页清空输入，绑定 canonical endpoint / revision，锁定／删除／变更立即撤销并取消。
  锁定 Electron Win bootstrap stdin 是 EOF；辅助进程实际读取继承 fd 0。无 Node exe fallback / 自造 crypto。
  migration 005 独立测试结果，immutable accepted actions 不 UPDATE；action.get 查询实际观察。
  慢网络测试最多 16 个 owned async requests，真实 RPC 撤销／EOF 及时取消，batch 保持成员顺序。
  unit 126 / portable 128 / regression 997，0 skip；Windows 10 development Electron 29 checks pass。
  20261006T181011Z-29e85a98 / 20261006T181026Z-879caa5b / 20261006T181340Z-690e66f5 / 20261006T181449Z-45ec1b68。
  packaged blocked / 0：20261006T181358Z-6ef85083；native Linux / Windows blocked / 0：
  20261006T181437Z-202e38e3 / 20261006T181443Z-9ce797db。final dirty 14ab3eda6e9f1e99bc47402b15f58ea88219b5e748a914cbd27a3ac86a40e4ef。
  actual Linux keyring、原生人工确认、F28 installed / F24 bundle / F27 diagnostics export 和 paid API 未验收。
  F16 接入当前契约-only workspace.files / read_file / changes，内容 revision 与授权 revision 分开；
  session / message / Diff 复用真实 Harness / Journal / 写入锁，10000 项有界、IME、reload 不重开 turn。
- F14 已推送 e612f360be14dc2a198d65f936b88a7ffd336941；F15 测试父 HEAD 即此提交。
- F14 测试父 HEAD c094171f9ce175d8914a777110a3975346474a2e，提交 SHA 以实际 git HEAD 核对。
- F14：命名 IPC、可信窗口／frame／origin／schema；原生目录选择与高风险拒绝／仅此次。
  Engine 持久 pending、最终参数／policy／实际 workspace / environment 绑定、短期 nonce / CAS 一次消费。
  真实删除、文件／参数变化拒绝、伪造／过期／重试、取消与 Engine 重启测试；等待不挂起 RPC reader。
  migration 004 增加不可变审批详情与 nonce hash；两个 prepare 方法扩展 catalog 至 50。
  重复定义会被 generator 拒绝；workspace.authorize 旧 contract-only payload 已升级。
  unit 126 / portable 109 / regression 976，0 skip；Windows 10 Electron development 21 checks pass。
  20261006T171100Z-46d571f9 / 20261006T171115Z-dc7c34c1 / 20261006T171412Z-ce51f277 / 20261006T171509Z-18a7199e。
  desktop-packaged blocked / 0：20261006T171429Z-f16c25e3；native Linux / Windows blocked / 0：
  20261006T171457Z-102cd590 / 20261006T171503Z-7e896f69。手工原生批准／拒绝选择与安装版未验收。
  F15 继续 Main 私有凭证与连接；F16 设置接线既有 fixed setup broker，不自动管理员安装。
- F13 已推送 c094171f9ce175d8914a777110a3975346474a2e；F14 测试父 HEAD 即此提交。
- F12 已推送 8875bfbaf9c14cce9834cb1e93f596a1055403b8；F13 本次提交的测试父 HEAD 即此提交。
- F13：真实安全 Electron 窗口、固定 Preload、动态 DesktopTransport、固定 Engine 资产／私有 stdio Supervisor。
  实际修复 demo、reload / Renderer crash 保持 Engine/turn、第二实例、obsolete 参数、OS 无 TCP listener、退出 cleanup confirmed。
  unit 125 / portable 97 / regression 962，0 skip；desktop-development 10 checks pass。
  20261006T162646Z-4fe55ae3 / 20261006T162701Z-1b15650e / 20261006T163202Z-24e933d4 / 20261006T163212Z-4433d05b。
  frozen assembly 尚缺，desktop-packaged blocked / 0：20261006T162951Z-e58dd5f3；实际 Forge prePackage 拒绝缺资源。
  Native Linux / Windows blocked / 0：20261006T163223Z-4a291216 / 20261006T163229Z-e28151f4。
  Windows 10 GUI 是 development scope；Windows 11/Ubuntu/X11/Wayland、native close dialog choices 与发布仍未验收。
  正常 start 使用 provider/strict；离线 demo 仅可信开发脚本 explicit scripted/local-trusted，不产真实模型成绩。
  build_desktop.py --check 不修复 inventory；npm start 无开发 HTTP server；包加载绝不退回源码 Python。
  Main-only credential/setup/approval 接线由 F14/F15继续，完整 frozen/hardened package 由 F28完成。
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
