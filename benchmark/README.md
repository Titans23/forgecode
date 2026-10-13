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

先用单任务验证安装和模型连通性，再扩大规模：

```bash
uv run python -m benchmark.harbor.run_swebench --n-tasks 1
uv run python -m benchmark.harbor.run_terminal --n-tasks 1
```

正式运行前可用 `--dry-run` 检查有效配置和环境前置条件。`--task` 可以重复传入，
也可以用 `--n-tasks` 做小规模 smoke/stratified run：

```bash
uv run python -m benchmark.harbor.run_swebench \
  --task '<exact-harbor-task-id>' \
  --dry-run
```

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
