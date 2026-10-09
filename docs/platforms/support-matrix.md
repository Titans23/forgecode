# ForgeCode V4 平台支持与验收状态

2026-10-09 新增用户明确选择的桌面 `local-trusted`，用于当前用户权限下的日常执行，UI 持续标明无 OS 隔离。默认 strict、原生验收和 F31 官方环境要求保留。入口见[桌面指南](../install/desktop-quickstart.md)，实现与测试范围见 [ADR035](../implementation/adr/035-optional-desktop-local-execution.md) 和[本轮证据](../implementation/evidence/F28-desktop-local-mode-observations.json)。Linux 代码接入不等于 Linux 安装版已实测。

2026-10-08 追加交付条件：按用户要求，正式支持的 Windows10／Ubuntu22.04、24.04 默认桌面环境必须仅经 ForgeCode 安装／首次使用流程获得可用原生沙盒。Windows 随包供应核心运行时与工具，Ubuntu 安装流程自动解析系统依赖并配置本应用所需策略；允许正常系统授权，禁止要求用户手工补组件或执行配置命令来通过标准验收。此项已写入 [方案](../implementation/sandbox-redesign.md) 和 F28 S1/S4，**实现及干净系统验证待完成**；项目 SDK 和独立 F31 Docker 评测仍另行准备。

2026-10-08 实施补充：[沙盒改进方案](../implementation/sandbox-redesign.md) 的 S1 已接入私有 PowerShell 7.6.6、MinGit 2.56.0.2、ripgrep 15.2.0，实际私有目录启动通过；Ubuntu deb 依赖与 AppArmor 安装配置已有代码，待目标系统验证。Windows 首次使用调用固定 setup，完整能力准入仍未完成。详见 [S1 证据](../implementation/evidence/F28-sandbox-s1-observations.json)，以下历史观测不自动覆盖本轮代码。

截至 2026-10-08，必需 Windows 验收采用本机 Windows 10 x64 build19045。用户随后明确授权本机管理员配置，并将 F31 评测移到另一台 WSL/Docker 电脑；自动拉取 GitHub 并接续的入口见 `docs/experiments/F31-wsl-handoff.md`。Ubuntu 原生桌面验收独立，既有模型授权有效，各项仍需实际环境证据。

| 环境 | 已有实际结果 | 产品验收 |
|---|---|---|
| Windows 10 x64 build 19045（本机，必需验收目标） | 私有工具、固定 setup、实际包目录初始化成功；F 构建原生 11 pass / 1 fail / 11 blocked，真实读写、payload 只读、中文路径/退出码和部分网络拒绝通过 | 动态新建 `.env` 可读，原生报告为 fail；完整清理与 verified 准入仍未通过。最终报告及限定恢复见 S1 索引，不能记作可用 strict 沙盒 |
| Ubuntu 22.04 x64 X11／Wayland | Linux adapter、deb 构建基线约束、受控 Bridge/FileWorker 与打包入口 | blocked：没有专用原生／图形验收设备 |
| Ubuntu 24.04 x64 X11／Wayland | 同上；Linux 包必须在较老 Ubuntu 22.04 构建 | blocked：没有专用原生／图形验收设备 |
| GitHub Ubuntu 22.04／24.04、Windows Server2025 portable jobs | 已上传64032fe；实际run37723899477三平台各contracts2/quality4/unit226/portable418通过，0skip/failure/error，三个freshCIgate通过，每平台16项新增行为及四artifact/四日志/六JUnit复算通过。获批Electron helper设置仍只适用于指定Ubuntu24 push job；security实际audit仍2high/exit2 | hosted portable通过，不建立原生产品能力；整体workflow仍failure |
| macOS／Windows Server／WSL | 无产品支持声明；Server 不满足 Windows workstation 判定 | unsupported |

每个 strict turn 现在创建独立实际 SRT Bridge 和 FileWorker，配置／prepared policy hash 必须一致，准备成功才创建模型。准备失败记录真实 owner-bound cleanup，不自动改用 local-trusted。当前 capability verification 尚不提供可提升为 verified 的完整原生证明，初始化后的边界／后代清理也未完成原生验收，因此 strict 执行继续拒绝。此处既有环境限制，也有尚待原生实现验证的代码限制，不能称为已可用原生沙盒。

既有 Windows 11 运行路径保留，但不再是必需验收平台，也不能替代指定 Windows 10 证据。详见 ADR033；历史报告保留其实际 OS、源码和状态。

本轮 Windows10 同机隔离回归1398、contracts2/quality4通过，零失败／跳过；合并当前工作区后定向21项与真实 Electron72项通过。主工作区验收重算仍有156项必需平台证明 blocked。全部源码范围、原生前提与七份报告复算见 `docs/implementation/evidence/F28-windows10-acceptance-observations.json`；当前结果不更新旧安装包证明。

2026-10-08关闭边界续作source9c0b2502已在当前Windows10完成contracts2/quality4/unit225/portable403/regression1372，0skip/failure/error；关闭后及排队请求禁止启动，并发关闭/取消共享一次清理。证据F28-admission-observations.json。随后e6bda835真实hosted三平台通过，见F28-admission-hosted-observations.json；本轮结果不更新旧安装包证明，也不提升原生能力。

F31本轮source457c5613在同一Windows10环境通过contracts2/quality4/unit226/portable418/regression1388，七份正式报告复算；显式金额政策、冻结网络一致性和账本归属校验均有新增行为实证。live-eval实际检查及156必需原生平台证明仍blocked，本轮未重建安装包或运行新模型。本轮64032fe随后取得三平台实际hosted通过；证据F31-budget-observations.json及F31-budget-hosted-observations.json。

开发演示明确使用离线 scripted model 与 local-trusted，经过实际 Harness、文件修改、测试命令、Journal、SQLite、Main/Preload/UI。它不证明隔离能力或模型成绩。

F28 两次完整固定负载均为 100000 条事件、10000 个 span、100 MiB 输出。元数据中位数额外耗时从 8.0771% 降为 5.2103%，仍超过 5% 目标；这一项继续 blocked，保留两次原始报告。复测 source Engine 冷启动中位数 3.2679 秒／热 RPC 0.76535 毫秒，SQLite 投影 55.6679 秒，实际 React 列表从 10000 项只渲染 14 行。React SSR 实际使用 PATH Node 22.17.1，与 locked Node 24.21.0 和图形帧性能分开报告。另实际 Windows host run_process 输出 100 MiB，完整归档摘要相同、显示仅 4096 bytes；这不是原生隔离吞吐证明。

加固使用实际 Electron fuse plugin，关闭 RunAsNode、Node options、Node CLI inspect 和 file protocol extra privileges；开启 only-ASAR，Windows 开启 embedded ASAR integrity。Linux 的 embedded integrity 不声称可用。独立 smoke 读取实际二进制 fuse wire，先证明 NODE_OPTIONS 探针能在私有 Node 中运行，再检查安装应用拒绝注入及调试入口，且实际 credential helper 仍能运行。当前产物 unsigned developer-preview，独立 smoke 通过不使其变为 production。

原生管理员 setup、干净安装／卸载／升级、Windows Job/ACL/canary、Linux namespace/network/keystore、真实 suspend/resume、签名和用户选择的项目 LICENSE 仍需资源或授权。SRT 引入的 node-forge 高危依赖安全门禁继续 blocked；Docker daemon 未可用，官方 Harbor 成绩为空。真实模型已授权：连接1请求335/10 tokens；F31两批10attempt/83请求，完整usage864743 input/15909 output、cache0。第一批source_changed全部保留且不可比；第二批冻结6attempt/50请求，独立Windows grader A3/3、B2/3，三task-cluster B−A−1/3，exploratory95%[-1,0]，不能声明收益。成本unknown，正式Harbor/holdout/native证据仍为零；详见F31-real-model-observations与final-report。F32新检出副本的12条实际文档步骤/24份日志hash、demo原始artifact与3锁摘要已复算；Node24资源实际新下载，三pinnedGit任务hash/checksum一致，0模型请求。这是当前Windows10源码复验，不是干净目标平台安装。金额无上限仍保留有限请求、工具和时间预算。没有运行管理员安装。
