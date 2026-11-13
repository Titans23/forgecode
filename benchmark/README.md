# ForgeCode 1.0.0 Benchmark

公共基准通过 Harbor 0.18.0 的任务环境和独立 verifier 执行。日常 CLI/SDK 不需要 Docker；以下公共基准在 Linux/WSL + Docker 中运行。源码、任务、模型、插件和条件均应冻结，分数代表这些条件下的联合表现。

| 基准 | 状态 | 数据集 / 入口 |
|---|---|---|
| Terminal-Bench 2 | ready | `terminal-bench/terminal-bench-2` / `run_terminal` |
| SWE-bench Verified | ready | `swe-bench/swe-bench-verified` / `run_swebench` |
| Aider Polyglot | ready | `aider-polyglot` / `run_aider` |
| BFCL V4 | planned | 尚无外部状态适配器 |
| OSWorld | not applicable | 尚无 computer-use backend |

`forge eval list --json` 查看实际能力。旧 Aider 直接入口包含其两次尝试反馈协议，见 [Harbor 说明](harbor/README.md)；统一计划采用一次尝试、零任务自动重试、无外部反馈，不能混报。

## 环境与直接运行

在 WSL/Linux 源码目录准备独立环境，不复用 Windows `.venv`：

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.local/share/forgecode/evaluation/.venv"
uv sync --locked --extra evaluation --python 3.12.13
docker version
uv run --no-sync harbor --version
```

模型配置复用用户 `~/.forge/.env`，项目 `.env`、进程环境和显式参数按优先级覆盖。支持已接入的模型协议，密钥使用隐藏输入或环境变量引用，不放在命令参数和 Git 中。容器访问宿主服务须使用可达地址；Docker Desktop 中宿主的 localhost 会映射为 `host.docker.internal`。

必须显式选择重复 `--task`、`--n-tasks N` 或 `--all`。`--n-tasks` 取按任务 ID 排序的前 N 题，不是随机抽样。以下是 10 题、并发 10 的示例，实际模型和范围须经调用方授权：

```bash
run_dir="$HOME/.local/share/forgecode/evaluation/terminal10-$(date -u +%Y%m%dT%H%M%SZ)"
eval_args=(--n-tasks 10 --concurrency 10 --max-retries 0 --output-dir "$run_dir")
uv run --no-sync python -m benchmark run terminal-bench-2 "${eval_args[@]}" --dry-run
# 核对配置、镜像和授权后执行同一参数集：
uv run --no-sync python -m benchmark run terminal-bench-2 "${eval_args[@]}"
```

显式任务示例：`uv run --no-sync python -m benchmark run swe-bench-verified --task '<官方精确任务ID>' --dry-run`。
复用同一批任务可加 `--taskset-dir <旧运行目录/tasks>`，重新校验数据集版本和任务摘要；输出目录必须是新目录。`--dry-run` 不调用模型、不下载任务，不能证明模型可连通或任务安装成功。

Harness 没有累计请求、工具、token 或回合总时长预算。官方任务期限、单次操作超时和取消独立保留。`--max-retries` 是任务级重试，模型传输重试另有策略。新运行默认 `--completion-policy normal`，官方 grader 决定成绩；显式 strict 才启用 Forge 完成门禁。旧冻结配置缺少该字段时保留原 strict 语义。

评测与 CLI/SDK 通过同一公共运行入口装配权限、工具和插件。官方剩余时间进入请求上下文，保留清理与日志导出时间；容器 CPU/内存配额用于提示实际可用资源。有限后台命令需完成或停止，显式会话服务可以移交 verifier，最终由任务容器销毁回收。

## Terminal-Bench 2.0：直接使用官方镜像

默认直接使用每道任务 `task.toml` 声明的官方预构建镜像。Harbor 在容器可写层安装 ForgeCode，不提交派生镜像。普通 uv 缓存不等于预热镜像。

不使用 `--prepare-only`、`--prepared-images`、`--force-build`，不运行额外 docker build/commit，除非调用方明确选择该模式。缺少预构建镜像时保持未验证；安装/联网失败应诊断原原因，不能自动换到预热路径。

默认安装只对 APT 镜像同步/临时网络失败增加最多两次重试；权限、签名或配置错误直接失败。curl/uv 下载重试同样有上限，均受 Harbor 安装阶段期限约束；不改官方镜像或 verifier。

```bash
docker context show
docker info --format '{{.Name}} | {{.ServerVersion}}'
docker image inspect '<task.toml 中的完整镜像名:标签>'
```

Docker Desktop 下应确认 Windows 和 WSL 使用同一引擎，并核对实际任务镜像 ID。系统配置变更需要授权。

若已准备 Harbor 的冻结 catalog 和本地任务包，可单独预取其中声明的官方镜像：

```sh
uv run --no-sync python scripts/prefetch_dataset_images.py --catalog ./catalog.json --output .local/implementation/image-prefetch --concurrency 3
```

catalog 须包含 `name`、`version` 和 `task_ids`；任务包应已存在于 `~/.cache/harbor/tasks/packages/<org>/<name>/<ref摘要>/task.toml`。该工具核对已有镜像，仅对缺失镜像执行 `docker pull`，不构建派生镜像、不启动模型或评测。它是显式运维入口，不属于 Forge 日常安装。

## 冻结 Agent、插件与模型

`forge eval plan config.json --output <新目录>` 不调用模型；未提供已冻结 taskset 时会下载官方任务元数据。配置路径相对 config.json：

```json
{
  "benchmark": "terminal-bench-2",
  "tasks": ["<exact-official-task-id>"],
  "dataset_ref": "latest",
  "source_directory": ".",
  "model": {"provider": "openai_chat", "model": "your-model", "api_key_env": "EVAL_MODEL_KEY"}
}
```

可选 `agent` 指向 AgentDefinition JSON，`models` 配置角色别名，`taskset_directory` 复用冻结输入，`pricing` 提供价格快照。完成策略来自 Agent 的 `completion`，默认 normal。计划保存依赖、源码/插件摘要、官方任务内容、凭据引用和协议；不能运行时静默覆盖。

```sh
forge eval plan ./config.json --output ./frozen-plan
forge eval run --plan ./frozen-plan
forge eval run --plan ./frozen-plan --execute --output ./run
```

包含 Python 插件时另加 `--trust-plugins`。当前计划入口固定并发 1、一次尝试、零任务级自动重试；需要自选并发时使用前面的数据集入口，其运行同样会冻结条件。旧 RunSpec/RPC 调度仍保留历史准入约束，不作为当前 CLI 入口。

## 状态、判分与导出

```sh
forge eval status ./run
forge eval cancel ./run
forge eval report ./run
forge eval export ./run --format atif
forge eval export ./run --format openai
forge eval compare ./baseline ./candidate --change model
```

`--change` 可为 model、plugin、harness。比较列出任务、模型、源码、依赖、完成策略和环境差异；缺少镜像/资源证据时不称为受控比较。一次比较不提供统计显著性或因果优势结论。

结果保留所有尝试、未启动题、未判分题和基础设施错误。缺少终态/用量时完整总量为 unknown，已知小计另列；价格表只能产生估算费用。Ctrl+C 与 cancel 通知所属控制器清理进程组及有所有权凭据的容器，不自动重跑付费任务。

内部未完成/失败结果返回非零退出码，官方 reward 仍独立保存。`forgecode-phase-failures.jsonl` 保留 Harbor 后续错误：Agent 超时和 verifier 故障可以同时出现，汇总分别保留执行与判分状态，不用前一个异常覆盖后一个。

Journal 是完整私有事实源；ATIF v1.7 和 OpenAI messages JSONL 是离线脱敏导出，失败、取消和部分响应也会保留。输出目录不可覆盖，原记录保持不变，公共基准输出标记 evaluation-only。产品不提供训练/蒸馏导出。

补丁归档上限 100 MiB；超过时记录 `omitted_size_limit`，继续官方判分，该工作区不能仅凭补丁重建。Git 采集失败单独记录，不用空补丁冒充成功。原始实验记录、独立判分和源码来源应一并保留。

## 确定性 pi 对照

```sh
cd benchmark/comparison
npm ci --ignore-scripts
cd ../..
uv run --no-sync python -m benchmark.comparison.workflow --output .local/comparison/new-run
```

对照固定 pi SDK 1.5.0，使用公开接口与相同报销规则、输入和独立预期，覆盖正常、错误植入、修复、模型配置变化和目录迁移。它验证工作流可运行，不证明模型质量、人工效率或 token 成本优势。SDK 安装检查另验证第二个干净 Python 环境。

## 历史结果

以下数字保持原模型、源码、协议和分母，不是 ForgeCode 1.0.0 的验收成绩。更早 Aider 结果及缺失原始证据的说明见 [Harbor 历史记录](harbor/README.md#历史-aider-评测)。

### Terminal-Bench 2.0 评测结果（2026-11-02）

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
