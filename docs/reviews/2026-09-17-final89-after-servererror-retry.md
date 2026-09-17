# ForgeCode 全量最终结果：51 题保留结果 + 38 题重评结果

2026-09-17。此报告按用户要求，仅以最终选定的 89 个结果为分析对象。原先 38 个 server_error 尝试已被其重评结果全部替换；其失败原因、调用量、状态不进入本报告的最终结果统计。另 51 题保留原正式结果。替换不是择优取分，不合并重复题。

## 最终成绩

| 来源 | 题数 | 通过 | reward=0 | 无 reward |
|---|---:|---:|---:|---:|
| 原评测未 server_error 的任务 | 51 | 25 | 26 | 0 |
| server_error 任务的重评结果 | 38 | 16 | 20 | 2 |
| 最终全量 | 89 | **41** | **46** | **2** |

最终通过率 **46.1%**，分母保留全部 89 题。重评包括 9/16 的 29 题和 9/17 续跑的 9 题。该成绩描述当前全量最终结果；发布实验时说明它经故障重评合并即可，不能再以原始 27/89 代替当前成绩。

终态 server_error 已为 0；sparql-university 记录 stream_interrupted，但官方通过，仍计入 41。query-optimize 和 torch-pipeline-parallelism 均为 VerifierTimeoutError、没有 reward；不能当作已通过，也不能假定仅重跑验证器就一定成功。

## 未通过 48 题的互斥主要诊断

| 主要诊断 | 数量 | 含义 |
|---|---:|---|
| agent_failure | 26 | 正确性、交付或验收失败，需要进一步按官方断言定位 |
| agent_timeout | 15 | 预算耗尽且未通过；具体根因可能是长操作或迭代不收敛 |
| verifier_environment_failure | 6 | 验证依赖启动/超时等故障，产物正确性尚不能据此确认 |
| infrastructure_failure | 1 | agent 入口启动失败 |
| 合计 | **48** | 对应 46 个零分 + 2 个未判分 |

分类来自现有分析器，是诊断优先级，不代表每题只存在一个问题。尤其验证超时仍需排查是否由提交产物挂起造成。当前 41 题通过与 48 题未通过/未判分构成完整集合，不能把带故障但已通过的题再次扣除。

## 当前最值得处理的失败

### 1. 有实现，但语义或算法错误

- model-extraction-relu-logits：内部 completed，但多个目标矩阵行不匹配。
- torch-tensor-parallelism：列并行和行并行测试失败，日志包含 rank 1 权重梯度不匹配。需要检查 forward/backward、通信与分片语义，不能只验证输出 shape。
- mcmc-sampling-stan：已产生数值结果，但 beta 均值约 1.6293，官方要求区间为 [16.1,16.7]。这是当前重评的数值错误，不再沿用早期依赖未安装的诊断；具体参数化/尺度根因还需代码追踪。
- rstan-to-pystan：要求三个 rho/beta 值，当前各只有一个，模型结构或结果导出未覆盖完整要求。
- raman-fitting：两个峰的拟合位置等参数明显偏离参考，需检查数据列、坐标、拟合范围、初值和目标函数；不能只用优化器成功退出证明拟合正确。
- count-dataset-tokens：输出计数错误。需要核验数据子集、tokenizer 和计数规则。
- mteb-retrieve、mteb-leaderboard：提交内容与要求的数据答案不符；文件形式正确不能代替来源与语义核验。
- filter-js-from-html：内部 completed，但恶意输入拦截及干净 HTML 保留均失败。
- db-wal-recovery：WAL 更新没有正确应用，数据结构/行数检查掩盖了内容错误。

这些已不能用之前的 server_error 解释。通用改进方向是独立参考算例、性质测试、反例和输入保护；不能将官方隐藏答案回灌成题名专用规则。

### 2. 性能或质量目标未满足

- train-fasttext：准确率 0.615575，要求大于 0.62；模型 569,592,589 字节，要求小于 157,286,400。既有准确率问题，也有明显大小问题，不能概括成“只差一点准确率”。
- largest-eigenval：多项加速测试不达标。是否受资源争用影响还需隔离复测，不能直接断言纯算法或纯环境根因。
- path-tracing：图像相似度约 0.7735，要求 0.99。
- path-tracing-reverse：相似度约 0.9541，要求 0.995；方向有所接近，但仍未达到要求。
- winning-avg-corewars：针对测试对手仅 12% 胜率，要求至少 33%。

这些任务需要直接围绕目标指标验证；有输出、可运行或样例成功都不足够。

### 3. 构建、服务和最终交付失败

- build-pov-ray、compile-compcert：预算耗尽后仍缺少必要构建产物。
- caffe-cifar-10：训练及模型产物未完成。
- adaptive-rejection-sampler：当前重评结束仍缺 Rscript，运行环境没有准备好。
- make-mips-interpreter、make-doom-for-mips：超时未通过，需分阶段证明构建、执行和目标产物可用。
- sam-cell-seg：脚本执行失败且没有生成要求的 CSV，后续检查连锁失败。
- configure-git-webserver：官方实际访问返回 HTTP 404；服务端口存在不能替代发布链路测试。
- pypi-server：内部 completed，但从本地索引 pip install 指定包失败；应从消费者视角验证安装流程。
- sqlite-with-gcov：内部 completed，但未生成 .gcda 文件。
- polyglot-c-py：官方目录检查发现额外 cmain 文件，要求目录只包含 main.py.c。这个具体阻塞看似简单，但仅修目录并不保证后续所有断言通过，仍需完整验证。

优先建立早期依赖预检和可执行里程碑，并在最终交付时检查文件、目录、副作用与外部消费者流程。不要将每道题的具体文件名固化到通用 harness。

### 4. 尚有 7 题受到启动/验证故障影响

- portfolio-optimization：任务目录 benchmark.py 遮蔽 benchmark.harbor 入口包，模型没有开始执行。需要修复启动隔离。
- code-from-image、qemu-alpine-ssh、qemu-startup、tune-mjcf：验证器 uv bootstrap 相关故障。
- query-optimize、torch-pipeline-parallelism：验证超时，无 reward。

这 7 题不能保证修环境后都会通过。即便全部通过，相对当前结果也最多增加 7 个通过；剩余 41 个主要归于 agent 失败/超时的未通过任务依然需要处理。

## 内部完成判定与官方结果

| 内部停止原因 | 题数 | 官方通过 | 模型调用 | 工具调用 |
|---|---:|---:|---:|---:|
| completed | 26 | 19 | 624 | 906 |
| time_budget_exhausted | 21 | 3 | 802 | 1163 |
| partial | 16 | 6 | 452 | 628 |
| acceptance_unmet | 20 | 12 | 522 | 698 |
| failed | 4 | 0 | 180 | 254 |
| 启动失败 | 1 | 0 | 0 | 0 |
| stream_interrupted | 1 | 1 | 23 | 26 |

26 个 completed 中，19 个通过、7 个失败：mteb-leaderboard、gcode-to-text、sqlite-with-gcov、filter-js-from-html、model-extraction-relu-logits、mteb-retrieve、pypi-server。完成误报比例为 26.9%。

另一方面，41 个官方通过中有 22 个内部 status 不是 completed（18 partial、4 failed）。这说明内部完成判定尚未与行为证据稳定对齐，但官方通过不意味着内部所有未满足要求都应删除。应区分产物错误、检查器协议错误、证据过期、环境不可用，避免无效修复循环。

21 个 time_budget_exhausted 中只有 3 个通过（build-cython-ext、merge-diff-arc-agi-task、hf-model-inference），累计 802 次模型、1163 次工具调用，占当前选中结果调用量约 30.8% 和 31.6%。部分超时题主要诊断优先归到验证故障，因此不能将 21 与前述 15、6 简单相加。

## 当前判断和优化顺序

服务错误重评已完成，下一步不应继续将 server_error 当作当前低通过率的主因。当前重点是独立语义验证、长任务收敛、准确交付，以及验收层的可靠性。

1. 修复入口遮蔽和验证环境故障，恢复受污染题目的可判分性；用离线 fixture 和官方允许的依赖预检验证。
2. 先做可定位的交付回归：目录清洁度、覆盖率数据、本地包索引安装、Git 发布后的 HTTP 内容。它们有直接证据，较容易验证修改效果。
3. 对 completed 却官方失败的七题建立“自测通过但产物错误”的负例回归，增强独立行为测试，不只增加 finish 约束。
4. 对数值、并行和性能任务使用小型可信基线、边界样例与指标分解；给长构建/训练设置阶段预算并保留最终验证时间。
5. 保留当前 89 题最终成绩作为修复前基线。修改后冻结源码与条件，再按固定题集逐题比较增减；当前证据不要求推倒重写整个项目。

当前选中结果累计 2603 次模型调用、3675 次工具调用。这是最终 89 个结果的工作量，不含被替换的旧尝试和暂停遗留尝试，因此不能等同总费用。

## 附录：最终 89 题及来源

| 题目 | 来源 | reward | 停止原因 | 主要诊断 | 原始证据 |
|---|---|---:|---|---|---|
| adaptive-rejection-sampler | 重评 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/adaptive-rejection-sampler__TgEe9Cz) |
| bn-fit-modify | 重评 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/bn-fit-modify__e5gYxZm) |
| break-filter-js-from-html | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/break-filter-js-from-html__rQYK7bs) |
| build-cython-ext | 原结果保留 | 1.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/build-cython-ext__U7Qat85) |
| build-pmars | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/build-pmars__ozaSwu3) |
| build-pov-ray | 原结果保留 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/build-pov-ray__C6nDdHj) |
| caffe-cifar-10 | 原结果保留 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/caffe-cifar-10__crwZeR7) |
| cancel-async-tasks | 原结果保留 | 1.0 | partial | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/cancel-async-tasks__nq8DYFk) |
| chess-best-move | 原结果保留 | 0.0 | acceptance_unmet | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/chess-best-move__LW4yNXQ) |
| circuit-fibsqrt | 原结果保留 | 0.0 | failed | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/circuit-fibsqrt__ACPPhc6) |
| cobol-modernization | 重评 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/cobol-modernization__m7jSoT9) |
| code-from-image | 重评 | 0.0 | acceptance_unmet | verifier_environment_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/code-from-image__5d5w6or) |
| compile-compcert | 原结果保留 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/compile-compcert__aE3E7yd) |
| configure-git-webserver | 重评 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/configure-git-webserver__j5WHq7C) |
| constraints-scheduling | 原结果保留 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/constraints-scheduling__AGG6i9G) |
| count-dataset-tokens | 重评 | 0.0 | acceptance_unmet | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/count-dataset-tokens__bWjkHYX) |
| crack-7z-hash | 原结果保留 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/crack-7z-hash__aLecZLh) |
| custom-memory-heap-crash | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/custom-memory-heap-crash__6PJiy4d) |
| db-wal-recovery | 原结果保留 | 0.0 | partial | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/db-wal-recovery__oHDUxnB) |
| distribution-search | 原结果保留 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/distribution-search__RSwZkVk) |
| dna-assembly | 原结果保留 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/dna-assembly__QcN2v5c) |
| dna-insert | 原结果保留 | 0.0 | partial | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/dna-insert__o5q9XPV) |
| extract-elf | 原结果保留 | 0.0 | acceptance_unmet | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/extract-elf__sUzx7Y6) |
| extract-moves-from-video | 原结果保留 | 0.0 | acceptance_unmet | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/extract-moves-from-video__mEEDuWM) |
| feal-differential-cryptanalysis | 重评 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/feal-differential-cryptanalysis__GupgrcU) |
| feal-linear-cryptanalysis | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/feal-linear-cryptanalysis__rdtqmPP) |
| filter-js-from-html | 重评 | 0.0 | completed | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/filter-js-from-html__EUjMX5m) |
| financial-document-processor | 重评 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/financial-document-processor__hqqjk6S) |
| fix-code-vulnerability | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/fix-code-vulnerability__H3ieECH) |
| fix-git | 原结果保留 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/fix-git__FXM9gVy) |
| fix-ocaml-gc | 重评 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/fix-ocaml-gc__p32sVKu) |
| gcode-to-text | 原结果保留 | 0.0 | completed | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/resume-20260916-150000/2026-09-16__15-02-26/gcode-to-text__ScjeZ6B) |
| git-leak-recovery | 原结果保留 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/git-leak-recovery__6KaSMmQ) |
| git-multibranch | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/git-multibranch__9iRacYp) |
| gpt2-codegolf | 重评 | 0.0 | failed | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/gpt2-codegolf__DMY7fn7) |
| headless-terminal | 原结果保留 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/headless-terminal__6HzpsqR) |
| hf-model-inference | 重评 | 1.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/hf-model-inference__tUqwsTo) |
| install-windows-3-11 | 原结果保留 | 0.0 | acceptance_unmet | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/install-windows-3-11__FTSmxcn) |
| kv-store-grpc | 原结果保留 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/kv-store-grpc__2A7tutW) |
| large-scale-text-editing | 重评 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/large-scale-text-editing__j64TKuo) |
| largest-eigenval | 重评 | 0.0 | partial | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/largest-eigenval__sXPZZLY) |
| llm-inference-batching-scheduler | 重评 | 1.0 | partial | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/llm-inference-batching-scheduler__W8YaSdv) |
| log-summary-date-ranges | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/log-summary-date-ranges__6j4A9ef) |
| mailman | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/mailman__ShD4w3k) |
| make-doom-for-mips | 重评 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/make-doom-for-mips__6FC9QZJ) |
| make-mips-interpreter | 原结果保留 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/make-mips-interpreter__9LVQy9A) |
| mcmc-sampling-stan | 重评 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/mcmc-sampling-stan__hhphCUG) |
| merge-diff-arc-agi-task | 原结果保留 | 1.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/resume-20260916-150000/2026-09-16__15-02-26/merge-diff-arc-agi-task__4s9Z4G7) |
| model-extraction-relu-logits | 重评 | 0.0 | completed | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/model-extraction-relu-logits__AUggVNM) |
| modernize-scientific-stack | 重评 | 1.0 | partial | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/modernize-scientific-stack__tSwnH35) |
| mteb-leaderboard | 原结果保留 | 0.0 | completed | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/mteb-leaderboard__UJS8rpA) |
| mteb-retrieve | 重评 | 0.0 | completed | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/mteb-retrieve__jfXXQW7) |
| multi-source-data-merger | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/resume-20260916-150000/2026-09-16__15-02-26/multi-source-data-merger__hCvqe3j) |
| nginx-request-logging | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/nginx-request-logging__K8kg4o9) |
| openssl-selfsigned-cert | 原结果保留 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/openssl-selfsigned-cert__7NqDKCh) |
| overfull-hbox | 原结果保留 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/overfull-hbox__6CTDVAk) |
| password-recovery | 重评 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/password-recovery__QGTHN7S) |
| path-tracing | 原结果保留 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/path-tracing__U5Pv2fv) |
| path-tracing-reverse | 重评 | 0.0 | partial | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/path-tracing-reverse__xdyaGx6) |
| polyglot-c-py | 重评 | 0.0 | partial | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/polyglot-c-py__Vu42yvq) |
| polyglot-rust-c | 原结果保留 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/polyglot-rust-c__DF2Qjx5) |
| portfolio-optimization | 原结果保留 | 0.0 | 无内部终态 | infrastructure_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/portfolio-optimization__VW4NoNY) |
| protein-assembly | 原结果保留 | 0.0 | failed | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/protein-assembly__iSBd7ff) |
| prove-plus-comm | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/prove-plus-comm__AJWwAJi) |
| pypi-server | 重评 | 0.0 | completed | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/pypi-server__DUZ2hnt) |
| pytorch-model-cli | 重评 | 1.0 | partial | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/pytorch-model-cli__UXZx5Wq) |
| pytorch-model-recovery | 原结果保留 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/pytorch-model-recovery__g42MeaJ) |
| qemu-alpine-ssh | 原结果保留 | 0.0 | partial | verifier_environment_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/qemu-alpine-ssh__zPTXnak) |
| qemu-startup | 重评 | 0.0 | time_budget_exhausted | verifier_environment_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/qemu-startup__nY8NhX9) |
| query-optimize | 重评 | 缺失 | time_budget_exhausted | verifier_environment_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/query-optimize__nMhCPWj) |
| raman-fitting | 重评 | 0.0 | acceptance_unmet | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/raman-fitting__TefMQLU) |
| regex-chess | 原结果保留 | 0.0 | partial | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/regex-chess__BfK6vgh) |
| regex-log | 原结果保留 | 1.0 | partial | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/regex-log__zdoUtAC) |
| reshard-c4-data | 重评 | 1.0 | partial | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/reshard-c4-data__pFUELNg) |
| rstan-to-pystan | 重评 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/rstan-to-pystan__AH9xddd) |
| sam-cell-seg | 重评 | 0.0 | partial | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/sam-cell-seg__GeQ7sYL) |
| sanitize-git-repo | 重评 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/sanitize-git-repo__dvS36d6) |
| schemelike-metacircular-eval | 重评 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/schemelike-metacircular-eval__FihtQkb) |
| sparql-university | 重评 | 1.0 | stream_interrupted | model_protocol_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/sparql-university__fteqAb6) |
| sqlite-db-truncate | 重评 | 1.0 | completed | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/sqlite-db-truncate__LJTXHAZ) |
| sqlite-with-gcov | 原结果保留 | 0.0 | completed | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/sqlite-with-gcov__N8KW4Cg) |
| torch-pipeline-parallelism | 重评 | 缺失 | partial | verifier_environment_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/torch-pipeline-parallelism__WQ5WcUD) |
| torch-tensor-parallelism | 重评 | 0.0 | partial | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/torch-tensor-parallelism__cw47QrR) |
| train-fasttext | 重评 | 0.0 | time_budget_exhausted | agent_timeout | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/train-fasttext__oLJSqHx) |
| tune-mjcf | 原结果保留 | 0.0 | time_budget_exhausted | verifier_environment_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/tune-mjcf__APzKu9p) |
| video-processing | 原结果保留 | 0.0 | acceptance_unmet | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/video-processing__PbCPM6U) |
| vulnerable-secret | 原结果保留 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/vulnerable-secret__b6mykzy) |
| winning-avg-corewars | 原结果保留 | 0.0 | failed | agent_failure | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/winning-avg-corewars__QtQbWug) |
| write-compressor | 原结果保留 | 1.0 | acceptance_unmet | pass | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/write-compressor__CrveGxD) |

数据来源：[最终合并数据](./2026-09-17-experiment-history-data.json)。所有失败样例均取最终选定 trial 的 verifier 日志。未启动评测，未修改运行时代码和 reward。
