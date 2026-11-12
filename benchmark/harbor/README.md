# Harbor adapter · ForgeCode 1.0.0

当前公共基准的安装、官方镜像、冻结计划、运行、取消、比较和导出统一见 [benchmark 指南](../README.md)。本页说明适配器职责及旧 Aider 协议，避免复制当前操作步骤。

适配器将冻结的包源码装入官方任务容器，建立独立 Git 基线，通过 `--message-file` 提交完整任务，执行同一 Forge 模型/工具循环，归档结果和候选补丁，再由 Harbor 的官方 verifier 判分。测试与参考答案不注入 Agent 上下文。

Terminal-Bench 2 和 SWE-bench Verified 使用 Harbor 0.18.0；默认一次尝试、零任务级自动重试、无累计 Harness 预算、normal 完成策略。官方任务时限保持独立。模型参数与源码在主机冻结，容器不能悄悄改写；请求、工具、失败、取消和未知用量进入 Journal。

默认直接使用官方预构建镜像。`--prepare-only`、`--prepared-images`、`--force-build` 属于调用方明确选择的独立模式，不能作为安装失败后的自动回退。原生进程清理和容器资源清理仅作用于拥有记录的资源。

## Aider 兼容协议

`benchmark.harbor.run_aider` 保留 Aider 的独立旧入口：官方 verifier 失败后允许一次同会话反馈修复，并区分 pass@1 与 pass@2。`--no-feedback` 选择单次尝试变体；任务级基础设施重试与模型传输重试是不同条件。完整 225 题、重试、镜像构建及外部反馈均须显式确认运行范围。

统一 `forge eval plan` 的无外部反馈协议与此入口不同，不能混报。SWE-bench/Terminal-Bench 不加载 Aider feedback plugin。用于历史实验的源码和输入应按原版本恢复，不能将 1.0.0 的默认配置当作原条件。

## 历史 Aider 评测

下文保留历史发布报告的数值和限制。它们不证明当前版本通过，也不授权重新运行实验。

### Full 225-task run (2026-08-11/12)

The following sections preserve historical release-report values, not current
acceptance. Their provenance is the v0.1.1 report at Git commit
[`a9f290f`](https://github.com/Titans23/forgecode/blob/a9f290f3b00270ef787f6ed4a9b2ed87dc1722bb/EVALUATION_SUMMARY_20260813.md).
The referenced job/summary JSON and individual trials were not found in the
current checkout during the documentation audit. The release commit includes
post-run fixes; it does not identify the exact source bytes used by the run.
The old adapter staged source separately for each trial without the immutable
source manifest used by the current runner. Raw results, staged source and
request/attempt records are needed to independently revalidate these numbers.

Run directory: `benchmark/runs/harbor/full-225-luna-fixed/2026-08-11__22-30-48`.
The run used Harbor 0.18.0, Docker 29.7.2, the Aider Polyglot dataset at
commit `f30b14415dd733c83627204bad0af69a89ceb46f`, and model
`gpt-5.6-luna`. ForgeCode was evaluated with `--concurrency 2`, one retry,
`--force-build`, and the Aliyun Ubuntu mirror.

The job finished all 225 task records, but the configured provider became
unavailable during the long run. Harbor recorded 189 infrastructure errors:
100 `NetworkConnectionError`, 88 `ApiRateLimitError`, and one
`NonZeroAgentExitCodeError`. Therefore Harbor's raw aggregate mean (`0.1689`,
38 reward=1 and 77 reward=0 among 115 evaluator trials) is a service-constrained
number, not a reliable ForgeCode code-accuracy estimate. Among the 36 trials that completed without an exception, final reward
was 35/36 (pass@2 = 97.22%). The same subset had first-attempt pass@1 of
11/36 (30.56%) and 24 successful feedback repairs. By language, final reward
was C++ 4/5, Go 7/7, Java 10/10, JavaScript 8/8, Python 5/5, and Rust 1/1.
These are the most useful code-quality signals from this run, but they come
from a small, non-random survivor subset and must not be presented as the
full-suite accuracy.

The recorded rerun below used the adjusted setup/retry options. It is a
historical result, not a pending experiment; no additional paid run is scheduled.
Keep the all-task denominator as well as the separately labelled scored subset.

### Full 225-task run (2026-08-12/13, GPT-5.6 Luna)

Run directory: `benchmark/runs/harbor/full-225-luna-rerun/2026-08-12__11-53-03`.
The run used Harbor 0.18.0, Docker 29.7.2, dataset commit
`f30b14415dd733c83627204bad0af69a89ceb46f`, model `gpt-5.6-luna`,
`--concurrency 1`, `--max-retries 3`, `--force-build`, setup timeout multiplier
12, and eight installation retries. All 225 task records finished; one C++
trial (`polyglot_cpp_circular-buffer`) raised `RewardFileNotFoundError`, so 224
trials were code-scored.

Final verifier reward was 211/225 (raw Harbor mean `0.93778`), or 211/224 =
94.20% after excluding that infrastructure error. Strict first-attempt pass@1
was 97/224 = 43.30%; 114 tasks passed only after the permitted Aider feedback
repair. The low pass@1 is therefore not the final repaired-task accuracy.

By language, final reward / scored trials was C++ 24/25 (96.00%), Go 37/39
(94.87%), JavaScript 49/49 (100%), Java 39/47 (82.98%), Python 32/34
(94.12%), and Rust 30/30 (100%). Strict first-attempt rates were 32.00%,
35.90%, 69.39%, 19.15%, 23.53%, and 80.00%, respectively.

The 13 code-level final failures were Python forth and go-counting; Go
hexadecimal and robot-simulator; C++ crypto-square; and Java rational-numbers,
rest-api, sgf-parsing, forth, wordy, zebra-puzzle, ocr-numbers, and ledger.
The most actionable ForgeCode defects were incorrect task-scope anchoring for
`src/main/...` files, weak recovery after unavailable verification commands,
and incomplete handling of exact hidden-test APIs and multi-step concurrency
protocols.

The historical report records 42,090,702 input tokens and 885,602 output
tokens across 3,488 model calls and 3,840 tool calls. Provider cost was not
reported by the local proxy. After the run, scope canonicalization and
`pytest`/`python` verification fallback were added. The release report states
`448 passed` and a successful `uv lock --check` for those post-run fixes;
the exact tested worktree, command output and JUnit report are not available
here. That count is not current-source acceptance or model evaluation evidence.
The 211/225 result includes the allowed feedback repair (97/224 first-attempt
passes and 114 repairs) and must be labelled as a historical reported result,
not current accuracy or a measured improvement. Current CI and separately
sealed native task conditions are tracked in `docs/implementation/progress.json`.
