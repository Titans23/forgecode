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

```powershell
uv run python -m benchmark run swe-bench-verified --n-tasks 1 --concurrency 1
uv run python -m benchmark run terminal-bench-2 --n-tasks 1 --concurrency 1
```

## 运行前准备

```powershell
uv sync
docker version
uv run harbor --version
```

`.env` 至少需要包含 `MODEL_ID` 和 `ANTHROPIC_BASE_URL`。API key 仍然从
`ANTHROPIC_API_KEY` 读取；runner 只通过 Harbor 的环境变量映射传递，不把
密钥写入命令行参数或结果文件。

先用单任务验证安装和模型连通性，再扩大规模：

```powershell
uv run python -m benchmark.harbor.run_swebench `
  --n-tasks 1 `
  --concurrency 1 `
  --output-dir benchmark/runs/harbor/swe-bench-smoke

uv run python -m benchmark.harbor.run_terminal `
  --n-tasks 1 `
  --concurrency 1 `
  --output-dir benchmark/runs/harbor/terminal-bench-smoke
```

正式运行前可用 `--dry-run` 检查 Harbor 命令。`--task` 可以重复传入，
也可以用 `--n-tasks` 做小规模 smoke/stratified run：

```powershell
uv run python -m benchmark.harbor.run_swebench `
  --task <harbor-task-name> `
  --dry-run
```

## 各基准命令

SWE-bench Verified 不使用 Aider 专用的 feedback repair：

```powershell
uv run python -m benchmark.harbor.run_swebench `
  --concurrency 1 `
  --max-retries 3 `
  --output-dir benchmark/runs/harbor/swe-bench-verified
```

Terminal-Bench 任务更广、资源差异更大，建议先单并发；如果使用云沙箱，
可传 Harbor 支持的 `--environment`，例如 `--environment daytona`：

```powershell
uv run python -m benchmark.harbor.run_terminal `
  --concurrency 4 `
  --environment daytona `
  --output-dir benchmark/runs/harbor/terminal-bench
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
