# ForgeCode V4 最终开发审查

本报告区分阶段接口实现、产品验收和真实模型实验。F00 已核对真实 CLI/Harness、入口签名及基线；F01—F31 已按依赖推进开发阶段、测试并推送至 `codex/forgecode-v4`，F32 的开发发布审查已完成。完整源码仍有必需代码缺口，不能声明全部 P0 完成。最终 F32 测试、构建及新检出副本的实际结果已写入本目录 evidence 与 progress；GitHub 上传及实际远端矩阵另行核对。

## 三个结论

| 维度 | 结论 | 实际依据与限制 |
|---|---|---|
| 代码实现 | 开发路径可运行；完整 P0 受阻 | 真实 Python Harness/CLI、stdio Engine、Bridge/FileWorker、Electron Main/Preload/Renderer、Journal/SQLite、评测与导出接口已接通。F28 的原生 verified promotion、初始化后完整清理及动态敏感路径一致性，F31 的正式 Harbor RunSpec/策略/可信逐请求账本绑定仍未完成。门禁读取 `remaining_implementation`，任务阶段标 implemented 不会解除代码缺口。 |
| 双平台产品验收 | blocked | 用户当前指定 Windows 10 x64 build 19045；实际开发客户端、frozen Engine、安装目录与 maker smoke 可以运行。Windows 11 workstation、Ubuntu 22.04/24.04 X11/Wayland、原生边界和干净安装/升级/卸载尚无完整证明。没有管理员 setup 授权。签名、项目 LICENSE 和两项高危依赖仍阻止 production。 |
| 真实模型实验 | Windows 受控回归已完成；正式实验 blocked | 用户明确授权配置中的真实模型，金额预算无上限。F31 第二批六 attempt 使用真实 Harness 和独立 Windows unittest，A 3/3、B 2/3，不能声明修复策略提升。正式 Harbor/原生分数为零；Docker daemon 和正式执行绑定仍缺失。费用 unknown。 |

## 实际实现与上游复用

保留现有 `forge` CLI、Conversation/TurnRunner/ToolExecutor、模型 provider、工具接口、SessionJournal 和 benchmark 入口；新增行为调用真实接口，没有以静态页面、假 Shell 返回值或模型 Mock 声称原生验收。

自实现包括共享 contracts 与预编译校验器、私有 Engine/RPC、固定 Main 授权与 workspace/policy、owner-bound 进程生命周期、FileWorker、事件投影及 Trace/请求/评测视图、恢复与写入对账、不可变实验配置、ZIP 导入/脱敏导出、legacy 备份迁移、资源完整性/打包/门禁和评测复算。F32 增加固定资产及公开任务的真实物化入口、独立原子文件发布模块、冷安装与跨路径测试及可执行用户文档。

上游复用 Electron/Chromium、SRT 与其平台 helper、Harbor/公开 Harbor datasets、PyInstaller、Node/CPython、React/AJV/SQLite 等；版本、hash、来源及许可证按实际 lock/SBOM/资产清单记录。复用平台 helper 不等于自行完成全部隔离证明。

自动化 offline demo 使用明确标记的 scripted model，并经过真实 Harness、文件变更、测试进程、Journal 和 SQLite。协议 fixture 与纯逻辑测试保持测试来源，不能替代原生沙盒或真实模型成绩。strict 准备失败时在模型创建前拒绝，不降级为 host/local-trusted。

## 已完成的测试与实测

F32 hosted修正候选a523a7887a238fa7f52bd6be625d71ab4d0625bd／source9f9a924db65db866dcbe3971bfff2b8c74213456331801df8b4bf5ba7e283d1f已完成全量：contracts2、quality4、unit225、portable382、regression1351，全pass且零skip/failure/error。本机freshCIgate通过，87专项通过，31份实际历史/当前报告独立复算。37已有portable证明绑定当前JUnit，110refs有效、156平台证明仍缺。两次派生release索引错误保留在gate_review_evidence_ids，修正后的implementation/release均blocked且0errors；不能将派生gate报告作为它自己的输入证据。最新release为20261007T150635Z-a27c64ba，280条真实代码/平台/发行条件，详见F32-ci-remediation-observations.json。新hosted结果仍待上传实测；上述初次F32结果保留为历史，未将旧安装包重标为本候选重建。

F32首次实际上传dd17e3488103008957ed88bfff764cbe27694a94已核对远端。随后run37636301297四job失败：Ubuntu22/24各unit223pass、portable367pass14fail；Windowsunit223pass、portable379pass2fail；security2high。原始四份ZIP和JUnit/log摘要独立复算，公开索引F32-first-hosted-ci-observations.json。修正候选a523a7887a238fa7f52bd6be625d71ab4d0625bd针对真实启动/资源/平台golden/编码问题；87专项通过，contracts2/quality4/unit225通过，全量portable与regression仍在执行。新的hosted矩阵尚未建立，不宣称CI通过。前述安装包保持此前构建的scope，未把旧二进制改称修正后重建。

F32 最终测试父HEAD79b43a10c7a6b59a1c1fe47ac19441300ca655d6，source bbd5f9a27959a83f651c7208a61b01cc1f4607483e7b7cd4b63eac3c66e571fb：unit223、portable381、regression1348、contracts2、quality4全部通过，零skip/failure/error，fresh本机CIgate通过。21新增行为由实际JUnit统计。首轮1347pass/1Renderer初始化timeout、portable900秒超时无JUnit及frozen预算耗尽均保留，不计最终通过；仅调整host初始化/验证器等待并串行复验，权限断言和10秒请求限制保留。最终22份实际报告独立复算，1份无JUnit历史失败单列。

implementation gate与release gate均blocked，0errors、0incomplete task标签，但明确保留F28/F31必需代码缺口；release当前279条条件，不宣称整体P0完成。详见[F32-development-review-observations.json](evidence/F32-development-review-observations.json)。

F31 冻结源码 `ecb3ee345d41a725e79c1fae9945f4171c111c23315d7ceb6e7131239c8727ec`：unit 215、portable 368、全仓 regression 1327、contracts 2、quality 4，全部通过，零 skipped/failure/error。本机 fresh CI gate 通过；这不替代 GitHub runner 或 native 验收。

F28 实际 frozen Engine 9、packaged desktop 5、installer maker 1、hardened 13、development desktop 66 检查通过。旧产物 `v4-f28-f3c194a-win-x64` 为 unsigned developer-preview，1860 个文件、121 个组件、147 个许可证文件、87 个 frozen Python distribution。F32 已实际重建当前运行时代码：build v4-f32-13bc5fd-win-x64，1860 files／121 components／147 license files，manifest SHA 86b8f9a965d26d0b9e82380d612abba6c1a02aa39182c628f7abc184caa36b36。Engine9、packaged5、maker1、hardened13、desktop66全部通过。Setup.exe为231238144 bytes／SHA 5b67f3a392bda16aa6503ffb2f01157b2178daaee43698e60f2f33f70a4a5643，未系统安装、未签名。196个运行时source inventory摘要已复算，后续测试等待修改未改变它们。

110 项验收映射已核对真实 test refs；F31 acceptance 仍缺 156 项所需平台证明。两次固定性能负载均为 100000 events/10000 spans/100 MiB，元数据中位数额外耗时 8.0771% → 5.2103%，仍超过 5% 目标。不得缩减负载或用 SSR 代替图形帧性能宣称达标。

实际 GitHub F28 run 37622858984 的四个 job 失败：Ubuntu 两组各 213 pass/1 fail（纯文件测试冷导入 Electron 下载提示混入 JSON），Windows Node 18 pass/3 fail（PowerShell 冷启动超时及两个短路径别名比较），security 两项 high。F32 针对真实原因修复，上传后必须核验实际矩阵；远端尚未通过时不报告 CI 已绿。F31实际run37627185380也取回了相同类别失败（Linux各214 pass/1 fail，Windows Node18 pass/3 fail），安全索引为F32-prior-hosted-ci-observations.json。

## 真实请求与评测限制

F28 连接探测实际 1 请求，335 input/10 output token、最终 usage 完整，只证明配置可连接。F31 两批共 10 attempt/83 请求，完整 usage 为 864743 input/15909 output，cache creation/read 为零。请求正文、密钥、URL 端口路径、个人 Journal/SQLite 只保留在私有目录，公开安全索引为 [F31-real-model-observations.json](evidence/F31-real-model-observations.json)。

第一批 4 attempt/33 请求因 tracked development-assets 在执行中更新导致 source_changed，全部保留但不能作为成对结论。第二批从冻结计划重新执行全部六 attempt/50 请求，没有 best-of：三道预先检查过的公开任务，A 3/3、B 2/3；B proverb 内部 completed 但原始 grader 8/8 failure，A transpose 原始 grader 通过但引用验收不足为 partial。三 task-cluster B−A 为 −1/3，seed 20261007、2000 次 bootstrap 的 exploratory 95% 区间 [-1,0]，样本少，不作收益声明。

原始公开 grader 字节独立保留，模型初始输入及项目没有包含 grader/oracle；local-trusted 不提供宿主隔离证明。任务已经预先看过，不是独立 holdout。正式完整 RunSpec trace、官方 Harbor 成绩和 native 证明均为零。预算无上限不表示可以丢弃请求账本，执行仍保留有限模型/工具/时间预算；没有可信账单或价格则费用 unknown。

## 发布资产、用户步骤与恢复

入口见 [开发构建说明](../install/development.md)、[安全状态](../security/status.md) 和 [支持矩阵](../platforms/support-matrix.md)。`release-lock.json` 固定资源来源与完整性，`release-manifest.json` 记录实际构建组、源码 inventory、资产/SBOM 和 developer-preview 状态；安装文件保持私有工作目录，不把密钥、session 或用户数据上传。用户授权上传已测试开发版本到 GitHub，不等于生产发布、签名或系统 setup 授权。

新的检出副本实际执行锁定依赖安装、CLI help/version、doctor、offline demo、固定 Node/SRT 资产恢复及固定 Git 任务物化。当前 Windows 10 上的新源码副本验证不称为干净 Windows 11/Ubuntu 系统安装。候选提交3d4454fb4f67aba92410d49dc2398db564ede68e已在新目录实际完成12条命令；24份原始stdout/stderr摘要、demo原始artifact及三份lock摘要均独立复算通过。固定Node24archive实际新下载，三道pinnedGit任务content/Harbor checksum与预注册一致，模型请求为零；见F32-fresh-source-observations.json。

恢复先读取 progress、任务卡与 handoff，核对当前 HEAD/源码摘要、实际 evidence 和运行进程，继续尚未完成的验证。不得覆盖无关用户变更，也不能把历史通过报告绑定到新的源码。后续必需工作为补原生代码缺口、专用平台验收、性能目标、安全与发行凭据，以及正式 Harbor 执行绑定与 holdout；详情由门禁和 progress 持续列出。
