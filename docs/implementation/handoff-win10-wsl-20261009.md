# ForgeCode：迁移到 Windows 10 + WSL / Docker 电脑

## 当前接续入口（目录合并后）

Windows 活动目录已统一为 `D:\projects\forgecode`，WSL 为 `/home/titans/learn_project/forgecode`；整合分支 `codex/forgecode-consolidation`，目标主线 `main`。已恢复的 working-tree.patch 不再应用；旧 V4 恢复步骤仅作历史。最新源码、备份、轻量模式及验证状态见 [当前交接](handoff.md) 顶部、[合并记录](consolidation-plan.md) 和 [ADR036](adr/036-consolidation-and-workspace-write.md)。

更新于 2026-10-09。本文件是新会话的快速入口。**接收端现已恢复并实测**，Windows 原生继续 F28，WSL/Linux Docker 继续 F31；下方迁移步骤及原机状态作为历史保留。

## 目录合并前的恢复记录：不要重复应用补丁

- Windows 当前工作目录：`D:\projects\forgecode-v4`；原 `D:\projects\forgecode` 保留未动。WSL：`Ubuntu-26.04` 中 `/home/titans/learn_project/forgecode-v4`，独立 `.venv`、`node_modules` 与 `.local`。
- GitHub 分支仍为 `codex/forgecode-v4`，实际远端与 clone 基底一致为 `cb7dbbb71bfdb5c7b3bb872d79ba5d226d9ae819`。补丁 SHA256 为 `775fc3b3fb1fe744ec176807ae518e5d12b2dd64fec768d77a079d30dd26fac0`，六份包内文件和 158 个恢复文件核对通过，恢复树为 `8ad78b5e75dc40aa12570958c0ea0e73931c29b6`。当前代码已在恢复树上继续修改，不能 reset 或重套原补丁。
- Windows10 Pro x64 19045.6466；WSL3.0.1.0、内核6.18.40.1、Ubuntu26.04。Windows Docker Desktop4.88.1 / Engine29.7.2 的 server OS 为 linux；WSL 原生 Docker29.1.3 的 socket 对当前用户拒绝访问，正式执行 blocked。没有为绕过访问限制更改 socket 权限、用户组或切 root。
- 两端锁定 Python3.12.13、Node24.21.0 已安装，Windows Electron44.5.1、私有 PowerShell7.6.6/Git2.56.0.2/rg15.2.0 已核验；工具 inventory 按仓库 LF 字节重算，不放宽资产校验。Windows Node 目录是 `.local/release-runtime/node-24.21.0-win-x64`；WSL Node 为 `.local/dev-node/bin`，运行构建/验证时明确置于 PATH 首位。
- 续修已处理初始化/启动与关闭竞态、Harbor 清理状态误报及两处 Windows 私有长路径存储故障。实际动态 `.env` 边界与完整原生 owner 清理仍未解决；SRT 新机账户/凭证缺失，WFP cannot-read 不代表无过滤器。未管理员 setup、未模型调用、未在新机升级安装版。
- 最新代码、验证和下一步以 [当前 handoff](handoff.md) 与 [新机证据](evidence/F28-F31-new-host-observations-20261009.json) 为准。原1446回归、旧安装升级、旧原生11pass/1fail/11blocked不能改写成接收端通过。

## 先恢复代码，再恢复上下文

GitHub：`https://github.com/Titans23/forgecode.git`；分支：`codex/forgecode-v4`。交接时实际本机 HEAD 与 `git ls-remote` 的远端 HEAD 都是：

```text
cb7dbbb71bfdb5c7b3bb872d79ba5d226d9ae819
```

**只 clone/pull GitHub 会丢掉当前工作进度。** 最新 S1 私有工具供应、桌面可选本机执行和共享界面修改仍未提交、未推送。本次交接附带 `working-tree.patch`，包含这些已有代码、测试和文档的增量，不修改原仓库提交或真实暂存区。完整文件清单与摘要见包内 `transfer-manifest.json`。

交接 ZIP 位于原机 `.local/handoff-win10-wsl-20261009/ForgeCode-handoff-20261009.zip`。解压后先读 `START-HERE.md`，把 `CONTINUE-PROMPT.txt` 发给新机器的 Codex。ZIP 中的本文件与仓库版本相同。

接收顺序：

1. 将 ZIP 放到新机器并解压，核对随包摘要。Windows 和 WSL 各用自己的源码目录、虚拟环境与 `node_modules`，不要共用一套跨平台依赖。建议 Windows 用本地 NTFS，WSL 用 Linux 文件系统内的目录。
2. 在新目录 clone GitHub 分支（可用 `git -c core.autocrlf=input clone --single-branch --branch codex/forgecode-v4 https://github.com/Titans23/forgecode.git`），或对已有**干净且没有独有提交**的该分支执行 fetch + fast-forward。已有未提交工作必须保留，不 reset、clean、自动 stash 或覆盖。
3. 核对 HEAD。若仍是上述基底，先 `git apply --check /完整路径/working-tree.patch`，再 `git apply /完整路径/working-tree.patch`。若 GitHub 已前进，让 Codex检查是否已包含这些改动，并基于补丁的旧对象做三方合并；不要退回旧提交或强套补丁。补丁只能应用一次。
4. 检查 `git status`，确认 `apps/desktop/src/main/execution_mode.ts`、ADR035、本文件和 `tests/implementation/integration/test_desktop_execution_mode.py` 存在。恢复后工作区有未提交修改是预期结果。
5. WSL 的现有启动器 `python3 resume_f31.py --workspace ~/learn_project/forgecode --update-only` 可以完成第 2 步；应用补丁后不要再次用它自动更新脏工作区。此后直接在相应源码目录打开 Codex，使用下面的续作提示词。该启动器仍仅负责 F31，不把 Windows 原生任务放进 WSL。

包内不含 `.env`、`.forge`、个人会话、API key、Codex 登录数据、Windows 凭证库、SRT 账户/WFP/状态库、`.venv`、`node_modules`、安装包或私有原始测试产物；也不包含 `build/`、`svg_probe.txt`、`update_implementation_pack/` 等无关文件。共享 UI 修改作为当前已验证源码的一部分保留在补丁中，未替其他工作提交或推送。

## 当前已完成到哪里

| 范围 | 真实状态 |
|---|---|
| F00 | 已完成仓库审计；接手核对现状即可，不从零重写 Harness/CLI |
| F28 S1 | Windows 私有 PowerShell 7.6.6、MinGit 2.56.0.2、ripgrep 15.2.0、资产校验、受控 PATH、首次使用 setup、Ubuntu deb 依赖及专属配置已实现 |
| Windows 请求传递 | 锁定 SRT 不转发任务 stdin，已改为 owner 专属只读 payload；外层 PowerShell 保留原始退出码 |
| 可选本机执行 | ADR035 已实现：默认 strict，用户在原生对话框明确选择 `local-trusted`，持续提示无 OS 隔离；空闲、正常关闭、清理确认后保存并重启；历史会话策略不变 |
| 原机安装 | 已备份并将原机实际升级至 0.1.3；这不代表新机器已安装，也不代表严格沙盒通过 |
| F28 S2/S3/S4 | 原生能力准入、动态敏感路径、完整清理与干净安装仍未完成，严格模式继续拒绝未证明的能力 |
| F31 | 预算配置、计划冻结和请求/费用归属校验已实现；官方容器执行、网络强制策略、可信宿主逐请求准入及正式实验未完成 |

普通安装版的本机模式无需用户另装 PowerShell 7 或 Docker；源码开发仍需要锁定开发工具。项目自己的 SDK/编译器依项目准备。本机模式不具备 OS 隔离，不能用于冒充 F31 strict 评测。

## 最近的测试结论与适用范围

完整回归 **1446 pass / 0 fail / 0 error / 0 skip**，正式 ID `20261008T162534Z-2a1c62e2`，source `6fdfa2e88fdcaa344045cbe53561e28143d5fdaf53644ac2710b696793650af0`。之后仅改动检查断言/截图等待、打包测试可选超时参数和组装清单；冻结 Engine 的 196 个 Python 输入逐项 hash 未变。

后续源码快照 `1087541d7f52a2f4cc7d665d434ec8bae51c4de2881944426f399898113f76b3` 的 contracts2、quality4、安装版 strict/local 桌面14、加固13、安装构建1 通过。开发桌面132、实际 Engine13 各自保留报告对应的源码快照。14 项定向测试、冻结引擎经 loopback provider 的审批/失败验证/修改/成功复验通过；固定模型响应仅用于离线测试，没有公网模型调用。

详见 `evidence/F28-desktop-local-mode-observations.json` 和 `evidence/F28-sandbox-s1-observations.json`。新机只有公开索引和报告摘要，原始 `.local/implementation/`、原机安装备份及 `.local/sandbox-s1-20261008/` 尚在原电脑。不能声称已在新机复算历史报告；需要复核时重新运行或另行取得必要原始产物。此交接追加的文档/启动器提示词也不是上述旧回归的测试对象。

原机安装产物：0.1.3，build `v4-local-mode-20261009-win-x64`，未签名开发预览。安装器 SHA-256 `8269e00cb8d629049745e89b4e954eb1162dda711daae770f08284dd5c2fdb3c`。ZIP 不携带约 359 MiB 的安装包，必要时在新机重建。

## 沙盒问题：从这里继续，不重复安装碰运气

原机 Windows 10 build19045 的实际原生结果是 **11 pass / 1 fail / 11 blocked**：工作区读写、只读 payload、中文/长路径/CRLF、退出码、部分目录边界、回环直连/去代理绕过拒绝和命名管道拒绝已通过。失败项是会话中动态创建的 `dynamic/.env` 仍能读取；取消及初始化后清理为 unknown。

用户要求继续修复后、迁移前，**只完成了源码定位，没有新增沙盒修复、原生重跑或管理员操作**。以下是已读代码与待验证线索，不能当作已修复成果：

- `sandbox_bridge/src/srt-adapter.ts`：`nativeConfig()` 把敏感名称编译为 glob；`probe()` 仅报告前提；`prepare()` 仍要求 verified；`initializeNative()` 是固定原生验证器的底层入口；`cancelExecution()` 和 `close()` 尚不能证明 Windows 后代及完整资源清理。
- SRT 0.0.78 的 `dist/sandbox/windows-sandbox-utils.js`，`expandWindowsFsPaths()` 约 941–997 行明确只在初始化展开 glob。现有 ACL 只保护具体对象/继承目录，不能据此宣称以后任意位置的同名文件也被保护。不能使用 watcher、启动时扫描或修改测试断言掩盖此问题。
- 同文件 `restoreWindowsAcl()` / `revokeWindowsAcl()` 约 1078–1167 行返回逐路径结果，但失败可能只记录日志或返回 undefined。
- SRT `dist/sandbox/sandbox-manager.js` 的 `reset()` 约 1716–1832 行吞掉部分 ACL/代理关闭异常并清空状态。ForgeCode 当前保持 unknown，不能直接把 resolved reset 改成 clean。下一步可研究保留实际清理结果和资源归属的最小适配，并验证并发关闭/启动中取消的顺序；这些改动尚未实施。
- 上游固定源码 [logon.rs](https://github.com/anthropic-experimental/sandbox-runtime/blob/v0.0.78/vendor/srt-win-src/src/logon.rs) 显示 broker→runner→child 的两级 Job、child breakaway 和某种 Job assign 失败的后备分支；仅增加外层普通 Job 或只观察 broker PID 消失不能直接当作全体后代清理证明。
- `forge/sandbox/windows_worker.py` 的 worker lease 会保留未确认清理的 marker，这是保护措施。不得全局按进程名杀进程、盲删 marker、清空共享 SRT 数据库或用 `--force` 恢复。

原机 SRT install 已成功执行一次，并完成特定失败 fixture 的人工限定收尾；**这不等于通用 cleanup 通过，也不意味着新机已配置**。新机先读状态，只有符合实际首次配置条件时才走产品固定 setup 和正常 UAC，不复制旧机 SID、账户凭据、ACL、WFP 或 owner marker。

S3 是现方案的可行性门禁。若锁定 SRT 的最小适配仍无法覆盖动态名称规则，按 `sandbox-redesign.md` 第 6.1 节保留失败，提出具体后端/范围调整依据；不要偷偷改成写入限制或普通进程并称 strict 已完成。

## 新机器的第一轮动作

1. 快速读取本文件、`AGENTS.md`、`PLANS.md`、`handoff.md`、`progress.json`；再读主规范 0–3/11–12 章、`tasks/F28.md`、`tasks/F31.md`、`sandbox-redesign.md`、ADR034/035。F31 再读规范 17–18 章和 `docs/experiments/F31-wsl-handoff.md`。不要为上下文恢复一开始就遍历全部历史报告。
2. 记录 Windows 版本/build/架构、WSL 发行版与版本、Docker context/client/server；在 WSL 中执行 `docker version --format '{{.Server.Os}}'`，必须实际成功输出 `linux`。有 WSL/Docker 只是用户报告的资源，诊断成功前仍为待核实。
3. Windows 和 WSL 分别按 `docs/install/development.md` 及 `release-lock.json` 准备 Python 3.12.13、Node 24.21.0 和当地依赖。先 `uv sync --locked --all-groups --all-extras`、`npm ci --ignore-scripts`，再物化锁定资源与构建；不要复制原机 .venv/node_modules。`commands.local.json` 的 `D:/learn_project/forgecode` 是原机审计记录，不是目标机器固定路径。
4. Windows 原生继续 F28 的动态文件边界、清理与能力准入；原生 canary 只使用合成秘密及小型 fixture。不要再次对庞大开发树盲跑同一 120 秒初始化；优先已验证的安装布局。每次先确认 owner/残留，避免叠加不确定状态。
5. WSL 继续独立的 F31 S5 无模型实现和诊断。官方 attempt 的文件、命令、verify 必须在同一容器，不能暴露宿主 Docker socket、隐藏 grader 或控制面凭证；宿主逐请求授权与有限请求/工具/时间预算保持。
6. 先运行相关行为测试，再按实际改动执行 contracts、quality、regression，并分别留 Windows native、Linux/Docker、安装版证据。WSL/WSLg 的通过不自动等于 Ubuntu22.04/24.04 X11/Wayland 原生桌面验收。
7. 新机本地配置模型连接。已有授权与预算决定见人类授权记录，但 ZIP 不提供 key；首先完成无费用检查，F28/F31 正式门禁未满足前不发正式模型实验。正式 holdout 和规模仍待确定；历史 A3/3、B2/3 不是官方 Harbor 成绩。
8. 更新 progress、任务卡、证据和 handoff。外部资源缺失标 blocked 并继续独立代码；GitHub 上传仅针对可复核的开发变更，不夹带凭证或私有产物。

现有无模型入口（先完成当地依赖/资源准备）：

```text
uv run --no-sync python scripts/impl.py doctor --scope development
uv run --no-sync python scripts/delivery_experiment.py verify --output .local/f31-readiness.json
uv run --no-sync python scripts/impl.py verify --suite live-eval
```

## 给新会话的提示词

```text
你正在接手 ForgeCode 的同一项工作，不是从零启动项目。用户已将后续任务迁至这台 Windows10 + WSL/Docker 电脑。先读交接包 START-HERE.md 和 handoff-win10-wsl-20261009.md，核对 transfer-manifest.json。GitHub 是 https://github.com/Titans23/forgecode.git 的 codex/forgecode-v4；交接基底 cb7dbbb71bfdb5c7b3bb872d79ba5d226d9ae819。最新功能还有未提交增量：先在干净目录拉取 GitHub，再检查并应用 working-tree.patch；远端已前进则做内容核对/三方合并，不覆盖本地修改，不只拉旧代码就开始工作。
恢复后读取 AGENTS.md、docs/implementation/PLANS.md、handoff-win10-wsl-20261009.md、handoff.md、progress.json、F28/F31 任务卡、sandbox-redesign.md、ADR034/035。Windows 原生继续修复 F28 沙盒，WSL/Linux Docker 继续 F31；分别准备依赖和记录证据。当前本机执行模式已实现且明确无 OS 隔离，严格模式仍默认，F31 不接受 local-trusted。最近全量回归1446通过；Windows原生11通过/1失败/11阻塞，动态新建 .env 可读、完整清理未知仍未解决。迁移前最后一段只做了源码定位，尚未实施新修复。
先验证新机真实环境和源码，按交接中的清理链路/动态文件边界接续。保留现有 Harness/CLI、用户数据、原生准入和有限请求/工具/时间预算；不重复询问已确定的产品方向，不把 Windows/WSL/Docker 证据混用，不把 watcher 或 reset 返回当作边界/清理证明。先完成能独立推进的代码和回归，资源缺失记 blocked。模型凭据在新机本地配置，正式实验门禁满足后再执行。更新任务、progress、证据及 handoff。
```
