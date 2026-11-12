# ForgeCode Benchmark Matrix

## 冻结 Agent / 插件 / 模型的统一入口

`forge eval plan config.json --output <新目录>` 冻结源码、依赖、精确任务、Agent 组合、插件产物与模型。Harness 累计预算已移除；官方任务截止时间和外部评测准入规则保持独立。
新计划使用 Agent 的 `completion`，默认 `normal`，官方 grader 独立判断结果；需要 Forge 严格验收时选择 `completion: "strict"`。直接运行数据集可传 `--completion-policy strict`。选择会写入冻结配置和比较条件，旧冻结配置缺少字段时保持其原来的严格策略。
规划不调用模型；未给 `taskset_directory` 时会下载官方任务元数据和输入，需要 `[evaluation]`。
配置示例（路径相对 config.json，任务 ID 使用官方精确值）：

```json
{
  "benchmark": "terminal-bench-2",
  "tasks": ["<exact-official-task-id>"],
  "dataset_ref": "latest",
  "source_directory": ".",
  "model": {"provider": "openai_chat", "model": "your-model", "api_key_env": "EVAL_MODEL_KEY"},
  "harness": {"max_delivery_repairs": 2}
}
```

可选 `agent` 指向 AgentDefinition JSON，`models` 提供角色模型别名；插件使用标准交付产物和固定依赖。
`pricing` 可提供现有 PriceBook 的 `revision/currency/source/rates` 快照，不提供时费用保持 unknown。
冻结后的运行不接受随意覆盖任务、源码或采样参数。旧 Harness 预算参数会被拒绝，历史有预算的报告和冻结输入保留，不能作为当前无预算运行的同条件结果。

```sh
forge eval run --plan <计划目录>
# 明确执行才会使用模型额度；包含 Python 插件时另加 --trust-plugins。
forge eval run --plan <计划目录> --execute --output <新结果目录>
forge eval compare <基线结果目录> <候选结果目录> --change model
forge eval compare <基线结果目录> <候选结果目录> --change plugin
forge eval compare <基线结果目录> <候选结果目录> --change harness
```

新计划支持三项已注册基准，使用同一官方任务 runner、独立 grader、单次尝试、零任务级自动重试、无外部反馈协议。
旧 Aider 入口及其 verifier-feedback repair 行为保持兼容，不能把两种协议混报。
新计划只直接使用任务声明的官方预构建镜像；缺少镜像时明确拒绝执行，不自动构建或启用预热模式。
现有 V4 scheduler 的授权/能力准入仍保持原限制；CLI 冻结计划没有解除它。

比较保留所有尝试与基础设施失败，并列出非预期配置差异。缺少终态或用量时，全量 tokens/费用为 unknown，
已知小计单独保留；调用方价格表只能形成 estimated 费用。分数描述冻结条件下模型与 Harness 的联合表现。
协议测试不等于云端模型/网关实测；本轮实现没有运行新的付费公开基准。

## 相对 pi 的确定性工作流对照

```sh
cd benchmark/comparison
npm ci --ignore-scripts
cd ../..
uv run --no-sync python -m benchmark.comparison.workflow --output .local/comparison/<新目录>
```

锁定 npm pi SDK 1.5.0（完整锁文件），用公开 SDK/扩展接口并保留其内置工具。
两边使用相同报销规则、输入与控制器持有的预期结果，检查正常、错误植入、修复、模型配置变化、目录迁移五个阶段。
轨迹记录失败、源码身份、请求次数、时间和清理状态。人工操作成本、真实模型成功率和 token 费用均标为未测。
迁移阶段使用同机已安装的 SDK 与新工作目录；另由 SDK 安装测试验证 ForgeCode 的第二个干净 Python 环境。
双方均能通过该流程，因此它只建立可运行对照与目标差异化，不构成 ForgeCode 优于 pi 的结论。

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

## Terminal-Bench 2.0 评测结果（2026-11-02）

**64/89 题通过（71.9%），25 题未通过，0 题未判分。** 按每道唯一题目最近一次正式判分汇总，包含原答案补判和重新作答。以下时间均为北京时间（UTC+8）。

| 评测时间（2026 年） | 范围 | ForgeCode 版本 | 官方判分结果 |
| --- | --- | --- | --- |
| 10-29 15:16 至 10-30 12:22 | 三批覆盖 89 题，并发依次为 4、6、4，含中断后的重跑及安装失败题补跑 | [`7fcf3c6`](https://github.com/Titans23/forgecode/commit/7fcf3c62e2fcae3ced4a096cf47fd26a96c865c4) | 去重后 59 通过、23 未通过、7 未判分 |
| 11-02 14:40 至 14:54 | 恢复 `torch-pipeline-parallelism` 原答案，单独运行 verifier，模型调用 0 次 | 原答案由 `7fcf3c6` 生成 | 1 通过 |
| 11-02 14:54 至 15:28 | 其余 6 道未判分题重新作答，并发 4 | [`4fd07b7`](https://github.com/Titans23/forgecode/commit/4fd07b77f54a3f681fab04230ecef465068e02d6)（含大补丁归档与 Debian 安装修复） | 4 通过、2 未通过 |

重新作答后通过的 4 题为 `hf-model-inference`、`qemu-startup`、`rstan-to-pystan` 和 `train-fasttext`。`pytorch-model-recovery` 和 `qemu-alpine-ssh` 均出现模型请求异常，官方 verifier 对两份产物均判为 0 分。

从 10-29 这轮重新开始评测起，按每题**首次启动**计（含中断和未判分），通过 51/89（57.3%）。全程任务日志记录了 1,699 次已结束的模型请求，其中 17 次 `http_200` 异常、2 次取消。已记录输入 23,539,351 tokens、输出 429,032 tokens，缓存读取字段为 5,469,312 tokens。19 次请求缺少最终用量，统计不含启动前的服务探测。

评测使用 `gpt-6.1-sol`（本地 Anthropic 兼容端点）、256K 上下文、64K 输出上限、Harbor `0.18.0` 和各题官方镜像。冻结数据集为 `terminal-bench/terminal-bench-2`，版本为 `sha256:c6fc2e2382c1dbae99b2d5ecd2f4f4a60c3c01e0d84642d69b4afd92e99d078b`。每次尝试最多 120 次模型调用、240 次工具调用、1800 秒，受官方时限约束。任务级自动重试为 0，同一预算内最多 2 次交付修复。

评测清单中的源码快照 SHA-256 分别为 `65c73de9cad98316f95978ed64b7485033b68174f9d05ffb15c7fc41df0709d6`（10 月三批）和 `513effda64f1189f1cde76a36569a35ced4363dd58faf12e67ccb3d79681749b`（11 月重新作答）。

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

以下使用 Bash、新输出目录、每题一次、任务级重试 0，显式固定模型和上下文。
官方任务时限仍生效。`--max-retries 0` 不关闭同一回合内的交付修复（当前最多 2 次），
也不等于关闭模型传输层重试。首次无外部阻塞的提前放弃可获得一次诊断反馈，
也占用上述修复次数，不是另一次任务尝试。最终由官方 verifier 判分；
内部要求执行行为检查及说明覆盖范围，不强制额外填写需求 ID 或 JSON 断言协议。

判分前的 Git 补丁归档上限为 100 MiB。超过上限时，适配器在
`forgecode-patch-capture.json` 记录字节数和 `omitted_size_limit`，并继续在原容器中运行官方
verifier。该次运行的工作区无法依靠补丁重建。Git 采集命令失败仍单独报错。

```bash
run_dir="$HOME/.local/share/forgecode/evaluation/terminal10-gpt6luna-$(date -u +%Y%m%dT%H%M%SZ)-c4"
eval_args=(
  --n-tasks 10 --concurrency 4 --max-retries 0
  --model gpt-6-luna --provider anthropic --base-url "$FORGE_BASE_URL"
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
