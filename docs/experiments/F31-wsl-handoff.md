# F31：在另一台 WSL / Docker 电脑继续

当前接续使用 `/home/titans/learn_project/forgecode` 的 `codex/forgecode-consolidation`；旧 V4 目录已完整归档。两端源码已对齐到整合提交，Linux 保留自己的构建清单与依赖。PR #1 验收合入 main 前，继续现有整合目录，不运行下文以 main 为目标的自动更新流程。正式 Docker/模型门禁仍关闭，最新结果见 [当前 handoff](../implementation/handoff.md)。

目录合并前的恢复记录（2026-10-09）：`Ubuntu-26.04` 的 `/home/titans/learn_project/forgecode-v4` 已恢复 GitHub 基底及交接补丁，当前为有意保留的脏工作区；不要再次运行自动更新或重套补丁。独立 Python/Node 依赖已准备，96 项定向测试及 contracts/quality 通过；WSL 当前用户访问 `/var/run/docker.sock` 被拒绝，正式 live-eval 仍 blocked。Windows Docker Desktop 的 Linux server 可用不替代这个执行身份的证据。当前结果、模型调用为零及剩余门禁见 [新机证据](../implementation/evidence/F28-F31-new-host-observations-20261009.json)。下文为原迁移流程与协议要求。

用户于 2026-10-08 选择将 F31 正式评测移至另一台具有 WSL 和 Docker 的电脑，并要求交接入口自动拉取 GitHub 最新代码后继续。2026-10-09 又将后续全部工作迁到这台 Windows10 电脑：F28 在目标 Windows 原生环境继续，F31 在当地 WSL/Linux Docker 继续，替代留在原电脑的安排。先读 [新机总交接](../implementation/handoff-win10-wsl-20261009.md)，恢复随包未提交增量后再继续。当前真实进度以 `docs/implementation/handoff.md`、`progress.json` 和 `tasks/F31.md` 为准；本文件不是评测通过报告。

F28 S1 已开始接入 Windows 私有工具与 Ubuntu 安装配置，实际代码/打包/原生状态见 [S1 证据](../implementation/evidence/F28-sandbox-s1-observations.json)。它不解除 F31 的官方网络、宿主逐请求授权和 F28 依赖门禁；新机仍先运行无模型诊断。GitHub 启动器只取得已推送的提交，原机未提交工作区不会自动传输，必须核对实际 HEAD 与 handoff。

原机 F 构建已取得 11 项原生通过，同时实测动态新建 `.env` 可读，原生整体为 fail，另 11 项 blocked。不要在接收端把“已装 Docker”记为这一 F28 边界问题的解决；S5 无模型实现可以独立继续，正式评测依赖仍保持。

2026-10-09 桌面新增用户明确选择的本机执行模式（[ADR035](../implementation/adr/035-optional-desktop-local-execution.md)），用于当前用户权限下的日常任务，无 OS 隔离。F31 evaluation 继续拒绝此模式；接收端仍按下面的官方 Docker 和授权要求推进。

## 启动

旧版 F31 启动器用于干净 GitHub 检出。在 2026-10-09 总交接包中，先用 `--update-only` 拉取，按总交接恢复 `working-tree.patch`，再直接在源码目录打开 Codex；应用补丁后再次自动更新会如实拒绝脏工作区。没有待恢复补丁的日常干净检出可在 WSL 终端执行：

```bash
python3 resume_f31.py
```

需要 WSL 内可用的 Git、Python 3 和已安装并登录的 Codex CLI。脚本自动克隆或快进更新 `https://github.com/Titans23/forgecode.git` 的 `main` 分支到 `~/learn_project/forgecode`，打印实际 HEAD，随后启动 Codex 并提交接续任务。已有目录必须属于该仓库和分支且没有未提交内容、本地独有提交；否则保留所有文件并说明原因，不 reset、stash 或强推。

可用 `--workspace ~/其他目录/forgecode` 指定目录，或 `--update-only` 只拉代码。未安装 Codex 时，代码仍会准备好；在当地 Codex 中打开该目录，发送“按 docs/experiments/F31-wsl-handoff.md 继续 F31”即可。CLI 使用当地账户、模型和权限设置，不复制原机登录数据。[Codex CLI 参数](https://learn.chatgpt.com/docs/developer-commands?surface=cli)。

## 接手任务

1. 读取 AGENTS.md、实施 PLANS、规范 0–3 与 17–18 章、progress、handoff、F31 任务卡及本文件；再读 [沙盒改进方案](../implementation/sandbox-redesign.md) 第 7—9 节与 [ADR034](../implementation/adr/034-sandbox-execution-and-runtime.md)。核对 HEAD、git status 和依赖锁；保留既有 Python Harness / CLI。F31 实施 S5，文件/命令/验证统一进入官方 attempt 环境；本机 F28 的随包 Shell 与原生迁移不由 Docker 替代。
2. 确认当前 Python 和 Docker 都在 WSL/Linux 路径中运行，`docker version --format '{{.Server.Os}}'` 必须成功输出 `linux`。记录 WSL 发行版、内核、Docker client/server 版本和 context。WSL / Docker 可用于官方容器评测，不建立 Windows 或 Ubuntu 原生桌面验收。
3. 按 `docs/install/development.md` 与 `release-lock.json` 安装固定版本。基础顺序为 `uv python install 3.12.13`、`uv sync --locked --all-groups --all-extras`、锁定 Node/npm 下的 `npm ci --ignore-scripts`、`node node_modules/electron/install.js`、`uv run --no-sync python scripts/materialize_release.py --output .local/runtime-assets.json`、Bridge/Desktop 构建。不要从 Windows 复制 .venv、node_modules 或原生运行时。
4. 先运行下面的无模型检查，保存真实输出；缺失环境先标 blocked 并继续无依赖实现。

```bash
uv run --no-sync python scripts/impl.py doctor --scope development
uv run --no-sync python scripts/delivery_experiment.py verify --output .local/f31-readiness.json
uv run --no-sync python scripts/impl.py verify --suite live-eval
```

5. F31 剩余代码包括官方 Docker 环境与网络强制隔离、可信宿主逐请求准入与授权、实际 source/model/environment/grader 快照及绑定。已有计划导入和账本归属校验不构成执行准入；当前 `delivery_experiment.py run` 会如实返回未完成条件，不能仅改状态让它启动。
6. 在新机器本地配置评测模型连接。既有人类授权见 `docs/implementation/evidence/human-authorization-20261007.json` 与 `human-authorization-local-setup-20261008.json`；真实模型金额无上限，但请求数、工具数、attempt/trial 时间仍有限。授权记录不是凭证，不能从 ZIP 或日志取得 key。完成宿主授权绑定与实际环境检查后再发模型请求。
7. 只比较修复上限 0/2，其余预算与条件一致；单 worker 交错运行，保留全部 attempt、usage、未知费用与独立 grader 产物，采用最后获准 attempt。三道已检查任务只作预实验／回归；正式独立 holdout 与 48/120 trial 规模尚待确定，不能把历史 A3/3、B2/3当作正式 Harbor 成绩。
8. 先补行为测试，再改代码并跑回归，更新任务、progress、证据与 handoff。真实结果按 `docs/experiments/delivery-repair.md` 导出与离线复算。原电脑的私有 `.local` 原始日志未迁移；如需复算历史批次，明确记录原始产物仍在原机，不能假称当前可复核。

2026-10-08 的旧包只带启动器与说明；2026-10-09 的总交接包另带经过检查的源码补丁。两者均不带 API key、.env、.forge、会话、登录缓存或私有评测产物。实际拉取的提交、恢复的补丁和接收端环境共同决定下一轮起点。
