# ForgeCode V4 当前交接

最新入口在本页顶部；阶段历史保留于下文及 `archive/handoff-72d7fb8-20261008.md`。progress、backlog、任务卡与实际 evidence 保留全部引用，历史通过不替代当前源码验证。

## 新机已恢复并接续（2026-10-09）

当前 Windows 工作目录为 `D:\projects\forgecode-v4`，WSL Ubuntu-26.04 为 `/home/titans/learn_project/forgecode-v4`。原 `D:\projects\forgecode` 的 main、`.env`、`.forge` 与用户数据保持原样。两端均从 GitHub `codex/forgecode-v4` 的 `cb7dbbb71bfdb5c7b3bb872d79ba5d226d9ae819` 新建干净 clone，检查后各应用一次交接补丁；六份包内文件摘要、158 个恢复文件以及恢复树 `8ad78b5e75dc40aa12570958c0ea0e73931c29b6` 已核对。**这些目录已恢复，后续不要再次应用补丁。** 当前未提交增量同时包含原交接内容与本轮修复。

本轮修复 SRT 直接初始化、prepare、启动与 close/cancel 的竞态：关闭后不再准入，reset 排在初始化和启动副作用之后；等待超时保留 owner 资源并返回 unknown。九项生命周期替身测试只证明顺序与超时，不证明 OS 隔离。F31 已纠正 Harbor 吞掉 compose down 失败后被误记 clean 的问题，stop 返回仅记录请求/返回事实，未核实 owner 资源时始终 unknown。新机还修复 Git LF 规范化导致的工具 inventory 摘要不符，以及 Windows LongPathsEnabled=0 下检查点和历史备份的内部长路径失败；不改变系统注册表、用户数据格式或外部路径准入。

Windows10 build19045.6466 的锁定 Python3.12.13、Node24.21.0、Electron44.5.1 与私有 PowerShell/Git/rg 已准备，桌面 Vite 入口已构建。WSL2 Ubuntu26.04 在 Linux 文件系统使用独立依赖。Windows Docker Desktop 已启动且 server OS=linux；WSL 当前用户访问 `/var/run/docker.sock` 被拒绝，二者证据不能互换。WSL26.04 也不替代 Ubuntu22.04/24.04 原生桌面验收。

F28/F31 仍 in_progress。新机 SRT 账户/凭证未配置、WFP cannot-read；未执行管理员 setup 或原生工作负载。历史 Windows 11pass/1fail/11blocked、动态新建 `.env` 可读和完整清理 unknown 均保留。锁定 SRT 的实际路径展开验证再次确认只覆盖当时存在的文件，不能用 watcher/rescan 当动态名称边界。strict 默认、能力准入、F31 禁用 local-trusted、请求/工具/时间预算保持不变，本轮公网模型请求为零。

测试快照、失败/中断、平台分开统计及原始路径见 [新机证据](evidence/F28-F31-new-host-observations-20261009.json) 和 [接收端说明](handoff-win10-wsl-20261009.md)。下一步针对后端动态名称与 owner Job/ACL/helper/listener 实际资源核查继续；不要因为 reset/stop 返回就提升 clean，也不要重跑已知失败的大目录初始化。WSL Docker 访问、官方网络强制隔离、宿主逐请求准入、实际 source/model/environment/grader 快照和 holdout 仍是正式实验门禁。

## 后续任务迁移到另一台 Windows10 + WSL/Docker（2026-10-09）

用户将后续全部工作迁至另一台 Windows10 电脑：目标 Windows 原生继续 F28，WSL/Linux Docker 继续 F31，替代“F28 留在原电脑”的旧安排。先读 [跨机快速交接](handoff-win10-wsl-20261009.md)。交接时本机及 GitHub HEAD 均为 `cb7dbbb71bfdb5c7b3bb872d79ba5d226d9ae819`；最新功能尚未提交/推送，交接 ZIP 的 `working-tree.patch` 保留当前相关源码增量，先拉 GitHub 再恢复补丁，不能仅拉远端就认为已接续当前工作。

上一次“继续修复沙盒”只做了源码和上游只读定位，未新增修复、测试、管理员操作或模型调用。已定位 SRT 初始化时 glob 展开、reset 吞掉 ACL/代理异常以及 Windows Job 清理链路，具体接入点与未验证线索写入新交接。0.1.3 已完成状态和 1446 回归证据不变，原生动态 `.env` 失败与清理 unknown 也未改变。新机 OS/setup/Docker 必须重新实测；不迁移旧机系统账户、凭证或私有数据。

## 可选本机执行与 Windows 安装更新（2026-10-09）

用户明确选择“新增可选本机执行模式，明确提示无 OS 隔离”，按 [ADR035](adr/035-optional-desktop-local-execution.md) 完成。桌面仍默认 strict；在「环境诊断 → 切换执行模式…」中由用户通过原生对话框确认后，可切换为 `local-trusted`。切换要求引擎空闲、正常关闭且清理确认，原子保存偏好并重启；取消、忙碌或清理未知均不切换，不自动回退。界面持续提示「本机执行 · 无 OS 隔离」，命令拥有当前用户权限；保留项目授权和操作审批。历史会话策略不改写，切换后新建会话。F31 正式评测继续只接受 strict。

完整 regression **1446 pass / 0 fail / 0 error / 0 skip**，报告 `20261008T162534Z-2a1c62e2` 对应 source `6fdfa2e88fdcaa344045cbe53561e28143d5fdaf53644ac2710b696793650af0`。之后仅更新两处检查/截图等待、打包测试的可选超时参数及组装清单，冻结 Engine 的 196 个 Python 输入逐项 hash 未变。最终 source `1087541d7f52a2f4cc7d665d434ec8bae51c4de2881944426f399898113f76b3` 的 contracts2、quality4、安装版 strict/local 桌面14、加固13、安装构建1 通过；开发桌面132、实际 Engine13 保留各自源码快照。14 项定向测试通过；实际冻结引擎经 loopback provider 完成审批、失败验证、修改与成功复验，没有公网模型请求。所有报告、历史失败与验证范围见 [本机模式证据](evidence/F28-desktop-local-mode-observations.json)，不将不同快照拼为同一份通过证明。

本机安装已从 0.1.2 升级到 **0.1.3**，build `v4-local-mode-20261009-win-x64`；先用产品备份流程生成一致性备份，再实际执行安装器，核验安装版资源/程序与已测试产物一致、数据库完整且 schema15、执行偏好未被安装器改变。私有记录在 `.local/desktop-local-mode-20261009/`。安装包 `.local/desktop-packages/make/squirrel.windows/x64/ForgeCode-0.1.3 Setup.exe`，SHA-256 `8269e00cb8d629049745e89b4e954eb1162dda711daae770f08284dd5c2fdb3c`；manifest SHA-256 `c2487a9fbc02d46c1ce097be5a0f81d9dc367b7262d2de42893b2c776ee9fa38`。这是未签名开发预览及本工作站升级证明，干净系统和严格沙盒验收另列。[使用步骤](../install/desktop-quickstart.md) 已更新，Windows 核心工具随包供应，无需用户另装 PowerShell7 或 Docker；项目自身 SDK 依实际项目准备。

严格原生沙盒的动态敏感文件边界与完整初始化清理仍未解决，旧原生 **11 pass / 1 fail / 11 blocked** 结果保留，未提升 capability。Ubuntu 原生桌面/安装器及另一台 WSL/Docker 的 F31 正式评测资源仍 blocked，F28/F31 整体仍 `in_progress`。本轮未重复执行管理员 SRT setup、未付费调用模型、未提交或推送。基底 HEAD 仍为 `cb7dbbb71bfdb5c7b3bb872d79ba5d226d9ae819`；接收端自动拉取只取得 GitHub 已提交内容，当前本地修改须按归属整理验证后才能交接，不能整包加入私有数据或其他共享修改。

## DSH／Pi 参考后的 S1 实施历史（2026-10-08）

用户进一步明确“只需要安装 forgecode 后即可开箱即用”，已写入主规范第 1.1、12、20 章和 ADR034。S1 扩为 Windows PowerShell/Git/ripgrep 随包供应、Ubuntu deb 自动依赖与应用专属系统配置，以及首次使用自动 setup/自检；S4 必测干净目标系统只走产品安装和正常系统授权即可执行首个受限任务，手工补依赖/配置不算通过。Pi 官方安装器还可管理 Node 和 Portable Git；默认本机执行不等于隔离。DSH 已核对入口要求 Node，沙盒自动选择平台后端而不承诺任意系统完整隔离。最初的来源与需求记录保留在 `evidence/F28-sandbox-oobe-observations.json`；用户随后要求执行，当前实施与实际 setup 以本节及 S1 索引为准。

用户要求规划新沙盒实现并更新文档，已形成 [沙盒改进方案](sandbox-redesign.md) 与 [ADR034](adr/034-sandbox-execution-and-runtime.md)，同步主规范、F28/F31 任务卡、backlog/progress、支持矩阵及跨机指南。DSH 固定参考5badb150、Pi固定参考ce950d78；核读官方源码，没有安装或运行第三方项目。

方向为：保留现有 Harness/Backend 与桌面 strict，Windows 目标安装包内供应锁定 PowerShell 7，补齐可信 canary 到能力准入、Shell/File 动态敏感路径和完整 owner 清理。F31 保持 Harbor 官方容器与宿主逐请求授权。DSH 写入限制后端、Pi 本地自动回退、额外 Shell 后备和微虚拟机不加入 P0。

S1 代码已落地并完成本机回归：私有 PowerShell 7.6.6、MinGit 2.56.0.2、ripgrep 15.2.0 完整供应/哈希/许可、统一路径与受控 PATH、Windows 首次使用固定 setup、Ubuntu deb 依赖及 ForgeCode 专属 AppArmor 配置已写入代码。完整 S2 准入、S3 边界/清理和 S4 干净安装尚未完成；F28/F31 仍 in_progress。最初设计和开箱即用要求的检查保留，当前实际代码、测试和资源状态见 `evidence/F28-sandbox-s1-observations.json`。

开始规划时实际 HEAD 为 `cb7dbbb71bfdb5c7b3bb872d79ba5d226d9ae819`；既有侧聊 UI 修改及 F16/安装指南记录保留。当前源码已取消系统固定 PowerShell 路径依赖，私有工具实际启动与最小 PATH 测试通过；不能据此假定沙盒身份或干净系统安装已可用。全工作区回归的源码与结果另记 S1 索引。

本机已按现有授权实际执行一次 SRT install，账户/凭证/组已配置，不能重复安装或轮换凭证。首次 native 失败发现受控环境缺 LOCALAPPDATA，现通过 Windows Known Folder API 供应；修复后重试在开发目录权限初始化阶段 120 秒超时。经 owner/DB/PID/ACL 核对，非 force 恢复上游记录、仅撤销本轮新增根目录 ACE，验证无持有者/相关 SID 权限/helper 后才删除本轮 marker。两个合成 fixture 空占位目录/记录保留；此次限定恢复不是通用 cleanup 通过。私有原始记录在 `.local/sandbox-s1-20261008/`，勿盲删、勿反复重跑相同庞大目录初始化。

实际 Forge 包布局已完成 SRT 初始化；进一步定位锁定 SRT 不转发任务 stdin，导致 dispatcher 收到空输入。现按规范采用 Windows 一次性只读 payload（工作区/控制目录外、owner 路径、内容摘要、1 MiB 上限、单文件清理），Linux 保留 stdin。完整原生能力仍未提升，最终回归与原生检查结果见 S1 索引。Ubuntu22.04/24.04 桌面安装/策略加载缺设备，记 blocked；另一台电脑继续 F31 S5 与 Linux Docker 实测，不把原机 Windows 结果搬作其环境证据。本轮新增模型请求为零。

payload 适配后的实际任务创建了合成文件，但宿主读取受 Windows `OWNER RIGHTS` 继承影响；新的 verifier 为其新建空 fixture 追加明确的宿主 SID 可继承权限，仅用于测试自身目录。旧 fixture 的单个合成文件经核对后删除，DB/ACL/helper 复核通过才释放该次 owner marker。外层 PowerShell 已显式传回 `$LASTEXITCODE`，实际回归复现修复前 23→1、修复后 23→23。此轮不重复安装共享 SRT，不把任何一次手工限定恢复当作通用 cleanup。

当前 F 构建真实原生结果是 **fail（11 pass / 1 fail / 11 blocked）**，不能笼统记成设备缺失：新建 `dynamic/.env` 可读，已触发方案 S3 的可行性停止条件。其他已过检查包括实际工作区读写、payload 不可写/删、中文/长路径/CRLF、原始退出码及部分文件/网络边界。完整清理和其余原生场景仍未通过；S2 准入保持关闭。原始结果 `.local/sandbox-s1-20261008/native-windows-final.json`，限定收尾 `final-reconciliation.json`；相关合成 task-owned 文件/目录已归档移除、3465 路径复核无临时 SID ACE，零 helper/holder，十个空 placeholder 保留，匹配 marker 已释放。

最终当前源码 `9aeab88cb1bdf078482e54243da5e6ac19af8f72fb4d16b99af7160053277628`：contracts2、quality4、完整 regression **1437 pass / 0 fail / 0 error / 0 skip**，对应 `20261008T152141Z-7c4157db`、`20261008T152141Z-c6ff5444`、`20261008T152141Z-4e8ea0ed`。此前完整回归的一项 Windows 杀进程后锁释放时序失败保留；测试现在先验证活 owner 仍互斥，再有界等待实际 OS 锁释放后执行一次 CLI，产品锁与模型重试策略未改。

安装构建1、实际 Engine13、安装版桌面预览5、加固13、开发桌面132 均通过，保留源码 `d755ef80` 的原报告。最终 `v4-sandbox-s1-20261008f-win-x64` 含 2956 个文件，manifest SHA-256 `8d5d8f23445c62e0291f4bf40c730fa8046248e815557b6c64459880a1501d56`；其后仅修正上述测试时序，196 个冻结 Python 源文件和 1438 个 Bridge/依赖文件与 F 构建逐项 hash 相同。不能把这些旧快照报告改称当前源码的新 CI 或原生通过；本轮没有新 hosted run。依赖审计仍 2 high，Linux 安装器仍因目标系统不可用而 blocked。

本轮 S1 与共享工作区的界面修改仍**未提交、未推送**，基底 HEAD 为 `cb7dbbb71bfdb5c7b3bb872d79ba5d226d9ae819`。另一台电脑的启动器只拉取 GitHub 已提交内容，当前本地 S1 不会自动出现在接收端。不得整包提交 `.forge/`、私有原始证据或共享无关修改；交接时先按变更归属准备可复核提交。

## 本机授权与 F31 跨机接手历史（2026-10-08，setup 最新状态见上节）

用户已明确授予本项目本机管理员配置权限，并选择 F31 转到另一台 WSL / Docker 电脑；最新授权见 `evidence/human-authorization-local-setup-20261008.json`，此前“本机 setup 未授权”的描述仅为历史。原机继续 Windows10 F28，不在原机安装或启动 Docker 来替代用户指定目标。

F31 使用独立启动器 `scripts/resume_f31.py`：在接收端 WSL 自动克隆或快进拉取 GitHub `codex/forgecode-v4` 最新提交，打印 HEAD，继承当地 Codex 配置并提交接续任务；已有未提交文件、不同仓库/分支或本地独有提交会阻断更新并完整保留。入口与说明会装入小型交接 ZIP，不复制凭证、会话、node_modules 或私有原始报告。继续入口：[F31 WSL 交接](../experiments/F31-wsl-handoff.md)。另一台设备还未实际连接或运行，不能把本机启动器测试写成 WSL / Docker 通过。

上述源码已提交并上传 `942ea7187d91ab248d7795c55239c475cb078150`，远端分支核对一致。启动器已实际从 GitHub 克隆旧 HEAD，再快进到这个新提交；ZIP 内原样入口的再次 fetch 也通过。交接包 `.local/f31-handoff-20261008/F31-WSL-handoff-20261008.zip`（5759 bytes，SHA-256 `8239a5574eaac5b6dedfec58591755fc3fb0b43c2ec718d5b20366548ef7d2e1`）只含启动器、README 和清单。当前 GitHub run37764658064 曾观测为进行中，未计 hosted pass；后续文档 checkpoint 的实际 HEAD 以 git 为准。

F28 已找到并修复 SRT 诊断误报：源码根的旧 release-manifest 被 doctor 错当成当前安装版；现在沿用 verify_runtime 确认的 installed 标记，native verifier 同样选择对应入口。真实只读查询已返回 observed，账户/组/凭证均未配置；WFP cannot-read 不代表过滤器不存在。2 项诊断行为测试及既有定向合计18 pass，启动器的9项真实本地 Git/CLI 参数测试通过，均无模型请求。

上一轮合并源码 `737d0ac7123650d9323f28f3bbef05c4e55b3395d51380d7aa3b3a6de4a20eb6` 已完成 contracts2、quality4、完整 regression1412（零失败/跳过）和真实 Electron development116 项检查；包含截至该次验证的 Windows10 目标与界面修改。native-windows 实测仍 blocked/0checks，live-eval 在原机仍 blocked。6份正式报告已复算，索引 `evidence/F28-setup-F31-handoff-observations.json`；正式测试进程已结束。本轮未重建安装包、未发模型请求。

PowerShell7.6.6 x64 MSI 已从微软官方 GitHub 下载，官方 SHA-256 与本地一致，Authenticode 为有效 Microsoft Corporation 签名。第一次固定安装请求由 Windows 返回“操作已被用户取消”，固定系统路径仍缺失；未运行 SRT install。具体原始记录在 `.local/setup-windows10-20261008/`，最终状态以本轮证据为准。已获得授权无需重复询问，UAC 仍需本机实际处理。

## 侧聊界面更新（2026-10-08）

侧聊保留圆润布局，最新精简首页口号、设置说明、指南和输入提示；密钥清除警告、授权与执行限制保留。独立 renderer 预览的 46 项实际共享界面检查（40 项布局）、6 项 UI 回归与 UI typecheck 通过；实拍及证据见 `evidence/F16-soft-surfaces-observations.json`。未覆盖主线程构建资源或重启用户客户端；主线最终构建及下次 `Start-ForgeCode.cmd` 启动应包含当前 UI。先前 132 项桌面报告只对应旧源码，原生／安装包验收仍独立。

后续启动修复：用户实测双击后无窗口，确认启动器的 `windowsHide:true` 同时隐藏了 Electron GUI。已仅修正 `scripts/start_desktop.mjs` 的 GUI 启动参数，新增真实启动函数／窗口测试，3 项启动回归通过；正常 CMD 入口已重开出可见、响应正常的 ForgeCode。原隐藏实例无任何任务或模型请求，关闭该实例后保留原数据，主线测试未中断。证据 `evidence/F16-launcher-visibility-observations.json`，历史全量报告不覆盖这次后续启动脚本改动。

用户在侧聊明确要求简洁界面、系统浅深色和启动指南。本轮修改 UI、固定源码启动入口及其测试／文档；主线 F28/F31 的执行策略和门禁未改。根目录 `Start-ForgeCode.cmd` 双击启动当前源码，说明见 `docs/install/desktop-quickstart.md`，旧安装包未重建。

本次 UI 测试快照 `0fc96137574ef3ec7b5556520104ce965c8b24432a463af56c1fcbc7a6cfe344` 的 Electron development 检查 72 项通过（`20261008T081933Z-bdef7b5b`），同一源码的完整回归 1390 pass／0 skip/failure/error（`20261008T082017Z-8be59488`）。全工作区 typecheck、启动依赖检查通过；浅深色实拍在该 desktop evidence 的 `desktop-run` 中，已目视核查。本侧聊测试进程已结束，细节见 progress 的 F16.ui_refresh 与任务卡。前次回归因 Web 测试仍定位旧项目按钮而停止，记录 `20261008T080746Z-1f2ea14a` 不计通过；选择器已更新，单项及完整回归通过。本侧聊没有模型请求、管理员设置、提交或推送。

上述 UI 验证当时的基底 HEAD 为 `f34bad970161ae5ef4dbbffcaa3ede4a3eaa2234`，其修改现已随共享提交942ea71上传。下述 F28/F31 历史证据仍对应其记录的源码，不能自动作为本次界面的安装包或原生验收。

在上述 UI 验证结束后，主聊天已合入 Windows 10 验收相关修改并继续验证，当前合并源码哈希已变化。本侧聊保留主线修改和运行进程，不把 UI 快照报告作为后续合并工作区的证明；主线最新状态以其验证记录为准。

## Windows 10 本机验收调整（2026-10-08）

用户明确将 Windows 11 必需验收改为本机 Windows 10 x64 build19045。已同步 Python/Engine/Bridge 的工作站判断、doctor/setup/native 入口、安装基准、证据门禁、声明式 runner 标签、规范与平台矩阵；不再要求 Windows 11 设备。Windows 11 的既有运行兼容保留，必需验收证据采用 Windows 10。授权与决策见 ADR033。

本机隔离检出 source `4d92b9fb73db0c5a9b3b73f52aca1ad1aece2c6e51a3de11c62bbc43c452e9e8`：contracts2/quality4/regression1398 全通过，零失败／跳过，其中 unit236、portable/integration418。合回当前工作区并保留侧聊界面改动后，定向21项和真实 Electron desktop72项通过，源码为 `a00f3fc8db61bb90b96b42ed461b616d423bcf8da91d5dc9058a5f3447e707df`。这是两份明确区分的源码范围，不能把隔离回归写成整个合并工作区的全量回归。

原生诊断确认 Windows10/AMD64、锁定运行时与 NTFS，通过 OS 准入；固定 PowerShell7 缺失、SRT 只读状态未取得，native 仍 blocked／0 checks，未提升任何 capability。主工作区验收重算 110 映射有效、156 项必需平台证明 blocked；隔离目录因没有旧私有报告而得到178，原报告保留。首轮缺 Electron 导致跳过后中止，补齐锁定 Electron、实际 storage 复测通过，再完整重跑1398；不将首次运行计为通过。七份正式报告已复算，索引 `evidence/F28-windows10-acceptance-observations.json`。

该目标调整阶段无模型调用、本机管理员操作或安装包重建，测试进程均已结束，当时尚未提交；现已合入共享提交942ea71，证据各自保留。后续新增的系统授权、UAC 尝试、诊断修复与 F31 跨机安排见本页顶部；Ubuntu 原生图形验收仍独立推进。

## 此前 F28/F31 源码阶段

用户要求继续 F28/F31，先完成当前环境的代码和回归；外部资源缺失记 blocked，继续独立工作。当前分支 `codex/forgecode-v4`，F31测试父HEAD为 `e6bda835253425046068cd905db7d819a538b7c5`；提交后的真实HEAD以git为准。

F28本轮修复FileWorker关闭边界和Bridge并发取消/关闭重复清理。4项新增行为先失败后通过，实际helper组5/5、Bridge组12/12；Node owner替身只证明串行化。source9c0b2502下contracts2/quality4/unit225/portable403/regression1372全pass，五份报告与fresh本机CIgate复算通过。证据 `evidence/F28-admission-observations.json`。

F31已完成本轮代码和回归：显式human_unbounded/null金额配置、实际wizard选择、冻结网络策略一致性、持久请求/用量归属校验及合法迟到usage。16项新增行为先失败，修复后77项定向回归通过。实际contracts/Bridge/Desktop重建后冻结source `457c5613302dc9db0828fabee9be6fa68d18db9ee36fa4ee39728337d669e07d`：contracts2/quality4/unit226/portable418/regression1388全pass，0skip/failure/error。live-eval检查blocked，acceptance110映射有效/156平台证明blocked；七份正式报告及fresh本机CIgate复算通过。证据 `evidence/F31-budget-observations.json`，完整ID见progress。工具session1718已退出0，无待等待本机测试进程；私有finalizer已成功。

F31源码已提交上传 `64032fe475e3eabea06a65bd5653e85c6b173409`，ls-remote一致；实际新run37723899477已完成并复算。F28已上传e6bda835。源码哈希与本机完整回归一致；当前仅补文档证据的checkpoint，原有无关未跟踪文件保留。无运行中的本机测试或CI等待，本阶段代码/回归/上传已完成，F28/F31整体仍in_progress。

## 最近的 hosted 证据

F31 64032fe的实际run37723899477：Ubuntu22.04/24.04、Windows2025各contracts2/quality4/unit226/portable418通过，0skip/failure/error；三个freshCIgate通过，每平台16项新增行为均在实际JUnit中确认通过。四artifact/四日志/六JUnit及安全audit报告复算通过；security实际2high/exit2，整体workflow failure。索引 `evidence/F31-budget-hosted-observations.json`。这是portable证明，不建立原生产品验收。

此前F28 e6bda835的run37721608025三平台各unit225/portable403及契约/质量/CIgate通过，security同样失败；证据 `evidence/F28-admission-hosted-observations.json`。两次CI均保留实际commit，不用历史产物替代当前证明。

## 授权与保护

- 已授权测试过的开发版本上传、现有配置真实模型及金额无上限；费用未知保留unknown，请求/工具/时间仍有限。
- CI 管理员设置范围仍为 Titans23/forgecode、codex/forgecode-v4分支push、GitHub临时Ubuntu24.04 runner、锁定Electron helper的root:root/4755，见 `evidence/human-authorization-ci-20261008.json`；新增本机 PowerShell/SRT 管理员授权见本页顶部及独立授权记录。
- 本轮F28/F31均0新增模型请求、0本机管理员操作、未重建安装包。历史模型两批10attempt/83请求及失败保留，完整批次A3/3、B2/3不是正式Harbor/native证明。
- 保留用户 `.forge`、无关未跟踪文件、旧四份10:33 evidence。先前侧聊清理仅涉及已完成且无引用的旧pytest tmp；后续经用户明确要求的界面修改见上文。

## 剩余门禁与资源

F28/F31均in_progress。F28仍缺原生verified promotion、完整初始化SRT/helper/listener清理及动态敏感路径一致性。锁定上游reset会记录并吞掉部分清理错误，不能凭resolved Promise宣布clean。

F31正式Docker环境/网络隔离与可信宿主逐请求准入/授权仍未完成；账本归属校验不建立该执行权限。预注册仍需实际官方source/model/environment/grader快照，正式独立holdout任务和规模待选择，已有模型授权不重复申请。

具体环境资源：Windows 必需验收为本机 Windows10 x64 build19045，无需 Windows11 设备；私有工具已供应，固定 SRT setup 已实际成功。当前 Windows 阻塞包括原生动态 `.env` 保护失败和完整清理/干净安装未验收，不能归因于未装 PowerShell。F31 使用用户另一台 WSL/Docker 电脑，接收端必须实际确认 server OS 为 linux；Ubuntu22.04/24.04 x64 X11/Wayland 原生图形验收仍独立。签名/LICENSE、2项high依赖和metadata额外开销5.2103%>5%仍受阻。

## 恢复读取

先核对实际HEAD/status、本文、progress/current task和对应任务卡。只在调查历史时读取archive与旧日志；不要轮询本文明确已结束的session，也不要把私有准备文件当作已验证公开实现。
