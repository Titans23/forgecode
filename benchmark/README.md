# ForgeCode Benchmark Matrix

ForgeCode 的外部评测统一通过 Harbor 的隔离任务环境和独立 verifier
执行。当前可直接运行的基准如下：

| 基准 | 主要能力 | 适配状态 | 入口 |
| --- | --- | --- | --- |
| Aider Polyglot | C++、Go、Java、JavaScript、Python、Rust 代码编辑 | ready | `benchmark.harbor.run_aider` |
| SWE-bench Verified | 真实 GitHub Issue 的仓库级修复 | ready | `benchmark.harbor.run_swebench` |
| Terminal-Bench 2 | 通用终端任务、脚本和环境操作 | ready | `benchmark.harbor.run_terminal` |
| BFCL V4 | 工具调用、多步状态和错误恢复 | planned | 需要 ToolRegistry 外部状态适配器 |
| OSWorld | GUI、浏览器和多模态操作 | not applicable | ForgeCode 当前没有 computer-use backend |

查看能力矩阵：

```powershell
uv run python -m benchmark list
uv run python -m benchmark list --json
```

也可以通过统一入口选择一个已接入的基准；其余参数会原样转发给对应
runner：

```bash
uv run python -m benchmark run swe-bench-verified --n-tasks 1 --dry-run
uv run python -m benchmark run terminal-bench-2 --n-tasks 1 --dry-run
```

## 运行前准备

以下公开基准命令在 WSL/Linux 终端执行，使用独立环境，不复用 Windows `.venv`：

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.local/share/forgecode/evaluation/.venv"
uv sync --locked --extra evaluation --python 3.12.13
docker version
uv run harbor --version
```

Terminal-Bench 2.0 和 SWE-bench Verified 固定 Harbor 0.18.0，在 WSL/Linux + Docker
运行。模型配置复用 `forge config setup` 的用户配置，项目 `.env`、进程环境与显式
参数按优先级覆盖；密钥只通过环境变量传递。默认运行数据保存在用户数据目录，
不进入 Git。必须指定 `--task`、`--n-tasks` 或 `--all`。

实际模型试跑会产生费用，必须在用户授权的任务数与预算内执行；不要在已授权的
10 题之外自动增加单题试跑。单任务评测也使用相同直接运行路径：

```bash
uv run python -m benchmark.harbor.run_swebench --n-tasks 1
uv run python -m benchmark.harbor.run_terminal --n-tasks 1
```

正式运行前可用 `--dry-run` 检查有效配置和环境前置条件。`--task` 可以重复传入，
也可以用 `--n-tasks` 取按任务 ID 排序后的前 N 题做 smoke run（不是随机或分层抽样）：

```bash
uv run python -m benchmark.harbor.run_swebench \
  --task '<exact-harbor-task-id>' \
  --dry-run
```

## Terminal-Bench 2.0：直接使用官方镜像

**后续评测默认走此流程，不生成或使用 ForgeCode 预热镜像。** 每个任务有自己的
官方环境镜像。Harbor 从官方镜像创建容器，在容器内安装 ForgeCode，再执行 Agent
和官方 verifier；安装写入容器可写层，不提交为新镜像。普通 uv 文件缓存不等于预热镜像。

不传 `--prepare-only`、`--prepared-images` 或 `--force-build`，不另外执行
`docker build` / `docker commit`。预热仅在用户明确要求该模式时使用；安装或
联网失败应诊断具体原因，不能自动切换到预热路径。

### 启动前检查

在 WSL/Linux 当前仓库执行上面的依赖准备命令。使用 Docker Desktop 时，对比
Windows 和 WSL 两端任务镜像 ID，确认连接同一引擎，避免误用独立的 Ubuntu daemon。
系统设置变更需要用户授权，操作走命令行。

```bash
docker context show
docker info --format '{{.Name}} | {{.ServerVersion}}'
docker image ls --filter 'reference=alexgshaw/*'
```

所选任务应在官方 `task.toml` 中声明预构建镜像，并在当前引擎中存在；逐一使用
`docker image inspect <完整镜像名:标签>` 确认。缺失时先核对引擎和镜像名，需要时
拉取官方镜像，不构建派生镜像替代。没有预构建镜像的任务不适用本流程，应先说明
缺口，不能默默构建。启动后还需核对实际容器镜像。

以下示例使用 Anthropic 兼容服务上的 `gpt-6-luna`。地址需改为本次可用服务；
Docker Desktop 容器访问 Windows 服务使用 `host.docker.internal`，不能用容器自身
的 `localhost`。密钥使用已有配置，或隐藏输入当前 shell 环境，不写入命令历史：

```bash
read -rsp 'API key: ' FORGE_API_KEY
printf '\n'
export FORGE_API_KEY
export FORGE_BASE_URL='http://host.docker.internal:60578'
```

### 10 题、并发 4 的标准命令

以下使用 Bash、新输出目录、每题一次、任务级重试 0，显式固定预算和上下文。
官方任务时限仍生效。`--max-retries 0` 不关闭同一预算内的交付修复（当前最多 2 次），
也不等于关闭模型传输层重试。首次无外部阻塞的提前放弃可获得一次诊断反馈，
也占用上述修复次数与原预算，不是另一次任务尝试。最终由官方 verifier 判分；
内部要求执行行为检查及说明覆盖范围，不强制额外填写需求 ID 或 JSON 断言协议。

```bash
run_dir="$HOME/.local/share/forgecode/evaluation/terminal10-gpt6luna-$(date -u +%Y%m%dT%H%M%SZ)-c4"
eval_args=(
  --n-tasks 10 --concurrency 4 --max-retries 0
  --model gpt-6-luna --provider anthropic --base-url "$FORGE_BASE_URL"
  --max-model-calls 120 --max-tool-calls 240 --max-turn-seconds 1800
  --max-output-tokens 65536 --context-window 262144
  --output-dir "$run_dir"
)
uv run --no-sync python -m benchmark run terminal-bench-2 "${eval_args[@]}" --dry-run
```

复用以前冻结的同一批题时，在 dry-run **之前**执行
`eval_args+=(--taskset-dir '/绝对路径/上一次运行目录/tasks')`。这里只复用经摘要校验的
任务输入，与预热无关；不要传旧 `prepared-images.json`。否则 runner 会解析数据集
版本并冻结本次选择，跨次比较需核对摘要而非仅看题数。

`--dry-run` 只检查配置及基本前置条件，不调用模型、不下载任务，也不证明模型服务
可连通、任务镜像齐备或安装成功。核对模型、并发、预算及前置条件正确，且本次模型
调用已获授权后，执行同一参数集：

```bash
uv run --no-sync python -m benchmark run terminal-bench-2 "${eval_args[@]}"
```

### 状态、取消和结果

另一个 WSL/Linux 终端使用实际目录（新 shell 不继承上面的 `run_dir`）：

```bash
run_dir='/绝对路径/本次运行目录'
uv run --no-sync python -m benchmark status "$run_dir"
docker ps --format '{{.Names}} | {{.Image}} | {{.Status}}'
uv run --no-sync python -m benchmark report "$run_dir"
```

容器镜像应与任务声明一致，例如 `alexgshaw/<任务名>:<标签>`。本流程不会新建
`forgecode-evaluation-prepared`、`forgecode-evaluation-runtime`、`forgecode-verifier-cache`
或 `forgecode-evaluation-base` 标签；若出现新增标签，检查是否另有预热命令在运行。

需要停止时执行 `uv run --no-sync python -m benchmark cancel "$run_dir"`，随后核对
状态与所属容器清理结果，不强制终止整个 Docker 引擎或使用 `docker system prune`。
失败保留原记录，不把重新运行当作原任务续跑或自动消耗新的模型预算。

保留 `experiment.json`、`harbor.json`、任务输入、源码快照、Journal 和 verifier 结果；
所有 10 题计入分母，基础设施失败单列。running 不代表通过，以独立 verifier 和完整
报告为准。产物留在运行数据目录，不提交 Git。

## 各基准命令

SWE-bench Verified 不使用 Aider 专用的 feedback repair：

```bash
uv run python -m benchmark.harbor.run_swebench --all
```

Terminal-Bench 默认单并发、每题一次、任务级重试 0；当前公开基准协议使用本机 Docker：

```bash
uv run python -m benchmark.harbor.run_terminal --all
```

Aider Polyglot 保留原有入口和一次 verifier-feedback repair 协议：

```powershell
uv run python -m benchmark.harbor.run_aider `
  --concurrency 1 `
  --max-retries 3 `
  --output-dir benchmark/runs/harbor/aider-polyglot
```

## 汇总和报告规范

所有 Harbor job 都可以使用同一个汇总器：

```powershell
uv run python -m benchmark.harbor.summarize `
  benchmark/runs/harbor/swe-bench-verified/<job-timestamp> `
  --output benchmark/runs/harbor/swe-bench-verified/summary.json
```

汇总会分开记录：

- `scored_trials`：有独立 verifier reward 且没有基础设施异常的任务数；
- `final_pass_rate`：最终 verifier 通过率；
- `pass_at_1_rate`：第一次尝试通过率；无 repair 协议的基准中它等于最终通过率；
- `pass_at_2_rate`：历史 Aider 字段，表示允许一次 repair 后的最终通过率；
- `infrastructure_failures`：Provider、Docker、安装和 verifier 基础设施异常；
- ForgeCode 内部 `status`、model/tool calls、输入/输出 tokens。

发布结果时至少保存：ForgeCode commit、模型和 endpoint、Harbor 版本、
dataset 标识/版本、并发、重试和 repair 协议、任务数、基础设施异常数、
首轮通过率、最终通过率以及 token 消耗。不同 benchmark 的 verifier 和
任务分布不同，不把它们合成一个未经定义的总分。

## 边界

BFCL V4 需要把 ForgeCode 的内部工具注册表转换成 benchmark 提供的外部
工具 schema、状态转移和结果判定，目前只列入规划，不报告 BFCL 分数。
OSWorld 需要桌面应用、视觉输入和 GUI action backend，也不应使用终端
benchmark 的结果替代它。ForgeCode 加入这些能力后，再新增独立 adapter 和
独立结果字段。
