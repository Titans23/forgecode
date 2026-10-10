# ForgeCode 开发版本使用与构建

日常开发主线为 `main`。本次整合由 [PR #1](https://github.com/Titans23/forgecode/pull/1) 进入主线；若该 PR 尚未合并，测试本次变更需先切换 `codex/forgecode-consolidation`。现有 `forge` CLI 和 Python Harness 继续使用；V4 增加 Electron 客户端、私有 stdio Engine、Bridge/FileWorker、Journal/SQLite 可观测性和 bundle 评测协议。当前提交为可测试开发版本，生产发布受阻，详见 [支持矩阵](../platforms/support-matrix.md) 与 [最终验收报告](../implementation/final-report.md)。

## 从源码验证

只想打开已经准备好依赖的客户端：Windows 双击根目录 `Start-ForgeCode.cmd`，或运行 `npm run dev:desktop`。启动、模型连接和项目操作见[桌面客户端使用指南](desktop-quickstart.md)；普通启动无需执行后面的安装包构建流程。

需要锁定的 Python 3.12.13、Node 24.21.0，以及兼容的 npm 和 uv。实际版本与资产摘要见 `release-lock.json`；开发测量还须记录当前 PATH 的实际工具版本。在仓库根目录执行：

```text
git clone --branch main https://github.com/Titans23/forgecode.git
cd forgecode
uv python install 3.12.13
uv sync --locked --all-groups --all-extras
npm ci --ignore-scripts
uv run --no-sync python -m forge --help
uv run --no-sync python -m forge --version
uv run --no-sync python scripts/impl.py doctor --scope development
uv run --no-sync python -m forge.testing.demo --output-dir .local/user-demo
```

demo 输出目录必须是新目录。它复制受控 fixture，启动真实 Engine，执行现有 Harness 的读取、修改与测试，写入 Journal/SQLite 并保留 evidence。模型使用 offline scripted profile，没有模型 API 请求。运行自己的仓库 CLI 会使用配置的模型连接。当前升级已得到用户明确真实模型授权，金额预算无上限；自动测试与 demo 仍保持离线，实际请求需显式选择对应真实实验入口并保留有限请求、工具和时间预算。

Engine 入口是 `python -m forge.engine --data-dir .local/user-engine --profile desktop`，通过 stdin/stdout 通信；它没有 `serve` 或 `--transport` 参数。通常由 Electron Main Supervisor 显式传入已确认模式。未指定模式的兼容入口保持 strict 且拒绝执行，不自动降级。

## 当前开发目录与执行模式

Windows 使用 `D:\projects\forgecode`，WSL 使用 `/home/titans/learn_project/forgecode`，各自维护 `.venv`、`node_modules`、运行目录与原生证据。归档目录只用于恢复，不作为启动入口。

新桌面配置经首次原生确认后保存 `workspace-write`，取消仅浏览；已有偏好不改写。在“环境诊断 → 切换执行模式…”中仍可选择高级 `local-trusted`。切换要求停止任务、关闭、确认清理并重启；旧会话保持原策略。轻量边界、Windows Node 管道子进程及硬链接限制见 [ADR037](../implementation/adr/037-lightweight-sandbox-without-srt.md)。F31 暂缓，正式实验保持关闭。

一次离线 CI 批次使用 `uv run --no-sync python scripts/ci_run.py --layer portable`：先准备一次构建，后续 contracts、quality、unit、portable 复用产物并只读核验清单。单独运行 `impl.py verify --suite ...` 仍自行准备。不要在验证批次运行期间修改源码或清单；源码摘要变化会使结果失效。

## 构建开发客户端

`npm ci --ignore-scripts` 后需要显式下载锁定 Electron 发行文件，并恢复私有 Node/核心工具资产。下列入口不运行管理员设置，也不改写 `release-lock.json`；已有资产被修改时拒绝继续，先审查实际完整性错误。Linux 轻量执行另需系统 bubblewrap。

```text
node node_modules/electron/install.js
uv run --no-sync python scripts/materialize_release.py --output .local/runtime-assets.json
```

Engine、Bridge 和客户端源码及资源需要依次构建，避免组装时并行测试旧资源：

```text
uv run --no-sync python scripts/package_engine.py --build-id v4-local-win-x64 --target win32-x64
uv run --no-sync python scripts/build_bridge.py
uv run --no-sync python scripts/build_desktop.py
uv run --no-sync python scripts/assemble_release.py --build-id v4-local-win-x64 --target win32-x64
uv run --no-sync python scripts/impl.py verify --suite engine-packaged
uv run --no-sync python scripts/impl.py verify --suite desktop-packaged
uv run --no-sync python scripts/impl.py verify --suite hardened
```

安装包由 `scripts/make_installer.py` 创建，`--target` 仅接受 `win32-x64` 或 `linux-x64`。先运行 `--help` 查看必需的输出参数。Linux deb 必须在 Ubuntu 22.04 x64 构建，再在 Ubuntu 22.04/24.04 的 X11 与 Wayland 分别验收。用户于 2026-10-08 将必需 Windows 验收改为本机 Windows 10 x64 22H2（build 19045）；无需 Windows 11 设备。Ubuntu 原生图形及安装验收仍独立推进。Windows Server 不建立 workstation 验收，当前 Windows 10 实测也不证明 strict SRT 隔离。

不得在缺少签名、安全、native 验收或项目 LICENSE 时把 unsigned developer-preview 转为 production。2026-10-08 用户已授权本机管理员设置，实际安装和原生状态见 handoff；授权记录不代表安装或验收通过。

## 凭证、恢复与评测

F31 按用户决定暂缓，正式实验关闭，轻量模式不能解除其门禁。WSL 保留独立目录和依赖；`resume_f31.py` 的日常目标为 main，但本轮不运行该实验接续入口。历史资源缺口见 [F31 跨机交接](../experiments/F31-wsl-handoff.md)。

客户端连接管理由 Main credential broker 管理，Renderer 不提供密钥读回。系统存储不可用或 Linux 返回 basic_text/unknown 时使用内存模式；受控开发 helper roundtrip 不替代目标平台 keystore 验收。不要将 `.env`、连接密钥、个人 session/data 目录提交 GitHub。

工作区授权、审批 hash/nonce、取消与 crash recovery 由 Engine/Main 执行。写操作结果未知时保持 indeterminate，禁止自动重复写入。详见实现规范与对应测试 evidence。

无费用预注册验证与已有 bundle 复算：

```text
uv run --no-sync python scripts/delivery_experiment.py verify --output .local/experiment-readiness.json
uv run --no-sync python -m benchmark.core.results report --from-bundle PATH_TO_RESULTS.zip
uv run --no-sync python scripts/delivery_experiment.py report --from-bundle PATH_TO_RESULTS.zip --plan-a PLAN_A.json --plan-b PLAN_B.json --run-a RUN_A --run-b RUN_B --output .local/paired-report.json
```

评测 RunSpec 和已解析配置 plan 必须匹配 bundle 的冻结 hash。复算保持全部 attempt/usage、计划分母、未知费用和缺失 grader 证据；导入内容保持 imported_unverified。新的源码副本先物化固定任务（仅下载和hash校验，0模型请求）：

```text
uv run --no-sync python scripts/materialize_experiment.py --output-dir .local/f20/aider-smallset
```

该目录必须不存在，脚本校验固定Git提交、实际文件集合和Harbor checksum，拒绝覆盖用户目录。随后已得到明确授权并配置连接的用户可显式运行：

```text
uv run --no-sync python scripts/model_regression.py --authorize-real-model --task-root .local/f20/aider-smallset --output-dir .local/real-model-NEW_DIRECTORY
```

当前升级授权记录见 `docs/implementation/evidence/human-authorization-20261007.json`；它授权本次实际升级，其他使用者需要自己的明确授权。该入口在新私有目录中调用现有Harness并保留全部attempt/usage/未知费用。三道公开任务先前已检查，只建立当前Windows10回归，不是独立holdout、原生隔离或正式Harbor分数。正式Linux Harbor还缺Docker daemon、执行策略和冻结RunSpec／可信请求账本绑定，readiness保持blocked。
