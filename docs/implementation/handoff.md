# ForgeCode V4 当前交接

本文件只保留最新状态；完整阶段历史见 `archive/handoff-72d7fb8-20261008.md`。progress、backlog、任务卡与实际 evidence 保留全部引用，历史通过不替代当前源码验证。

## 本机授权与 F31 跨机接手（2026-10-08）

用户已明确授予本项目本机管理员配置权限，并选择 F31 转到另一台 WSL / Docker 电脑；最新授权见 `evidence/human-authorization-local-setup-20261008.json`，此前“本机 setup 未授权”的描述仅为历史。原机继续 Windows10 F28，不在原机安装或启动 Docker 来替代用户指定目标。

F31 使用独立启动器 `scripts/resume_f31.py`：在接收端 WSL 自动克隆或快进拉取 GitHub `codex/forgecode-v4` 最新提交，打印 HEAD，继承当地 Codex 配置并提交接续任务；已有未提交文件、不同仓库/分支或本地独有提交会阻断更新并完整保留。入口与说明会装入小型交接 ZIP，不复制凭证、会话、node_modules 或私有原始报告。继续入口：[F31 WSL 交接](../experiments/F31-wsl-handoff.md)。另一台设备还未实际连接或运行，不能把本机启动器测试写成 WSL / Docker 通过。

F28 已找到并修复 SRT 诊断误报：源码根的旧 release-manifest 被 doctor 错当成当前安装版；现在沿用 verify_runtime 确认的 installed 标记，native verifier 同样选择对应入口。真实只读查询已返回 observed，账户/组/凭证均未配置；WFP cannot-read 不代表过滤器不存在。2 项诊断行为测试及既有定向合计18 pass，启动器的9项真实本地 Git/CLI 参数测试通过，均无模型请求。

当前合并源码 `737d0ac7123650d9323f28f3bbef05c4e55b3395d51380d7aa3b3a6de4a20eb6` 已完成 contracts2、quality4、完整 regression1412（零失败/跳过）和真实 Electron development116 项检查；包含此前 Windows10 目标与全部当前界面修改。native-windows 实测仍 blocked/0checks，live-eval 在原机仍 blocked。6份正式报告已复算，索引 `evidence/F28-setup-F31-handoff-observations.json`；正式测试进程已结束。本轮未重建安装包、未发模型请求。

PowerShell7.6.6 x64 MSI 已从微软官方 GitHub 下载，官方 SHA-256 与本地一致，Authenticode 为有效 Microsoft Corporation 签名。第一次固定安装请求由 Windows 返回“操作已被用户取消”，固定系统路径仍缺失；未运行 SRT install。具体原始记录在 `.local/setup-windows10-20261008/`，最终状态以本轮证据为准。已获得授权无需重复询问，UAC 仍需本机实际处理。

## 侧聊界面更新（2026-10-08）

最新窗口与页面更新：品牌、页名与系统窗口按钮合并为顶部栏，Windows/Linux 使用 Electron 原生 titlebar overlay 和主题色；保留拖动区域，内容避让原生控件。移除页面大圆角外框，诊断页默认展示真实状态摘要，详细数据按需展开。最终源码 `e82921aa64d54ea1a4f0745f49c8af7cd5aff1f53ca20d87718333644c2bd779` 的正式 Electron development 116 项通过（含 40 项布局检查），证据 `20261008T101557Z-7e649923`；相关 UI／实际 Web 入口回归 7 pass，typecheck 通过，浅深色实拍已目视检查。此前按钮间距、表单、有界短列表与向导的 107 项报告 `20261008T095136Z-b771d395` 保留于任务卡和 progress。本轮不声称全仓库回归或 Linux／安装包原生验收通过。资源已构建；用户当前窗口及主聊天未重启／中断，空闲时退出客户端，再用根目录启动入口打开新版。

后续启动修复：用户实测双击后无窗口，确认启动器的 `windowsHide:true` 同时隐藏了 Electron GUI。已仅修正 `scripts/start_desktop.mjs` 的 GUI 启动参数，新增真实启动函数／窗口测试，3 项启动回归通过；正常 CMD 入口已重开出可见、响应正常的 ForgeCode。原隐藏实例无任何任务或模型请求，关闭该实例后保留原数据，主线测试未中断。证据 `evidence/F16-launcher-visibility-observations.json`，历史全量报告不覆盖这次后续启动脚本改动。

用户在侧聊明确要求简洁界面、系统浅深色和启动指南。本轮修改 UI、固定源码启动入口及其测试／文档；主线 F28/F31 的执行策略和门禁未改。根目录 `Start-ForgeCode.cmd` 双击启动当前源码，说明见 `docs/install/desktop-quickstart.md`，旧安装包未重建。

本次 UI 测试快照 `0fc96137574ef3ec7b5556520104ce965c8b24432a463af56c1fcbc7a6cfe344` 的 Electron development 检查 72 项通过（`20261008T081933Z-bdef7b5b`），同一源码的完整回归 1390 pass／0 skip/failure/error（`20261008T082017Z-8be59488`）。全工作区 typecheck、启动依赖检查通过；浅深色实拍在该 desktop evidence 的 `desktop-run` 中，已目视核查。本侧聊测试进程已结束，细节见 progress 的 F16.ui_refresh 与任务卡。前次回归因 Web 测试仍定位旧项目按钮而停止，记录 `20261008T080746Z-1f2ea14a` 不计通过；选择器已更新，单项及完整回归通过。本侧聊没有模型请求、管理员设置、提交或推送。

当前这些源码改动尚未提交；实际基底 HEAD 为 `f34bad970161ae5ef4dbbffcaa3ede4a3eaa2234`。下述 F28/F31 历史证据仍对应其记录的源码，不能自动作为本次界面的安装包或原生验收。

在上述 UI 验证结束后，主聊天已合入 Windows 10 验收相关修改并继续验证，当前合并源码哈希已变化。本侧聊保留主线修改和运行进程，不把 UI 快照报告作为后续合并工作区的证明；主线最新状态以其验证记录为准。

## Windows 10 本机验收调整（2026-10-08）

用户明确将 Windows 11 必需验收改为本机 Windows 10 x64 build19045。已同步 Python/Engine/Bridge 的工作站判断、doctor/setup/native 入口、安装基准、证据门禁、声明式 runner 标签、规范与平台矩阵；不再要求 Windows 11 设备。Windows 11 的既有运行兼容保留，必需验收证据采用 Windows 10。授权与决策见 ADR033。

本机隔离检出 source `4d92b9fb73db0c5a9b3b73f52aca1ad1aece2c6e51a3de11c62bbc43c452e9e8`：contracts2/quality4/regression1398 全通过，零失败／跳过，其中 unit236、portable/integration418。合回当前工作区并保留侧聊界面改动后，定向21项和真实 Electron desktop72项通过，源码为 `a00f3fc8db61bb90b96b42ed461b616d423bcf8da91d5dc9058a5f3447e707df`。这是两份明确区分的源码范围，不能把隔离回归写成整个合并工作区的全量回归。

原生诊断确认 Windows10/AMD64、锁定运行时与 NTFS，通过 OS 准入；固定 PowerShell7 缺失、SRT 只读状态未取得，native 仍 blocked／0 checks，未提升任何 capability。主工作区验收重算 110 映射有效、156 项必需平台证明 blocked；隔离目录因没有旧私有报告而得到178，原报告保留。首轮缺 Electron 导致跳过后中止，补齐锁定 Electron、实际 storage 复测通过，再完整重跑1398；不将首次运行计为通过。七份正式报告已复算，索引 `evidence/F28-windows10-acceptance-observations.json`。

本轮无模型调用、无本机管理员操作、未重建安装包；测试进程均已结束。修改已在当前工作区，尚未提交／推送；既有 UI 和本轮 Windows 目标修改各自证据保留。后续可继续 F28/F31 独立代码工作；Windows 管理员步骤需先补齐固定 PowerShell7 安装并核对共享 SRT 状态，再确认最小 setup 范围。官方评测仍需 Linux Docker，Ubuntu 原生图形验收独立推进。

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

具体环境资源：Windows 必需验收为本机 Windows10 x64 build19045，无需 Windows11 设备；当前缺固定路径 PowerShell7，SRT 已可只读查询但未配置。管理员操作已获授权，首次 UAC 返回取消。F31 使用用户另一台 WSL/Docker 电脑，接收端必须实际确认 server OS 为 linux；Ubuntu22.04/24.04 x64 X11/Wayland 原生图形验收仍独立。签名/LICENSE、2项high依赖和metadata额外开销5.2103%>5%仍受阻。

## 恢复读取

先核对实际HEAD/status、本文、progress/current task和对应任务卡。只在调查历史时读取archive与旧日志；不要轮询本文明确已结束的session，也不要把私有准备文件当作已验证公开实现。
