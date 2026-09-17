# ForgeCode 历史实验汇总与当前问题分析

更新：2026-09-17；范围：本地留存的 Harbor 实验与既有审计报告。此次只整理与分析，没有启动新评测或修改候选运行时代码。

## 1. 当前成绩及统计口径

最新 9 题续跑已于 9 月 17 日北京时间 11:08:44 结束，控制器退出码为 0。最终状态检查无运行中的评测容器。

| 统计对象 | 通过 | reward=0 | 缺失 reward | 通过率 |
|---|---:|---:|---:|---:|
| 最新 9 题续跑 | 3 | 6 | 0 | 33.3% |
| 38 题 server_error 定向重评（29+9） | 16 | 20 | 2 | 42.1% |
| 原全量 89 题（36+3+50） | 27 | 61 | 1 | 30.3% |
| 将上述 38 题全部替换后的 89 题汇总 | 41 | 46 | 2 | 46.1% |

**46.1% 是选择性故障重评后的合并成绩，不是新的一次全量独立运行成绩，也不应对外称作单次 pass@1。** 重评选择依据是原运行出现 server_error，而不是随机抽样；原先这 38 题中有 2 题已通过官方验证。所有 38 题均使用重评结果替换，不取历史最好值，不更改原始 reward 文件。另 51 题保留原结果（25 通过、26 未通过）。净增加 14 个通过题，没有丢失原先 38 题中的 2 个通过题。

最新 9 题通过：financial-document-processor、large-scale-text-editing、llm-inference-batching-scheduler。未通过：code-from-image、configure-git-webserver、mcmc-sampling-stan、mteb-retrieve、path-tracing-reverse、qemu-startup。

38 题重评未再记录终态 server_error；sparql-university 记录 stream_interrupted，但官方 reward=1。query-optimize 和 torch-pipeline-parallelism 因 VerifierTimeoutError 缺失 reward，不能宣称通过，也不能简单归为算法错误。

## 2. 历史趋势与可比性

盘点发现 71 个本地 Harbor job 配置，包括 SWE-bench、单题冒烟、失败启动、中断批次及续跑分段。它们不是 71 次完整独立实验，不能把所有通过题相加后计算一个总体通过率。完整清单见附录。

| 时间 / 阶段 | 并发 | 结果 | 解读 |
|---|---:|---|---|
| 8/27—8/31 SWE-bench 与基础冒烟 | 1 | 多次启动/环境失败，少量单题成功 | 不同 benchmark；排除于 TB2 能力趋势 |
| 9/1 phase2 | 2 | 2/10 | 早期完整十题批次 |
| 9/2 stage3 | 4 | 1/10 | 中间续跑不完整，另列附录 |
| 9/3 process/runtime/edit/task-contract | 6 | 各 1/10 | 多次修订未带来稳定提升 |
| 9/3 positive-evidence | 6 | 0/10 | 增加证据要求本身未改善结果 |
| 9/4 b9a3d7b / 390af0b | 6 | 2/10、0/10 | 代码及运行条件变化，不能只归因于设计 |
| 9/5 network-restored | 6 | 2/10 | 网络恢复后仍有解题问题 |
| 9/6 reliability | 10 | 3 个通过，8/10 生成结果 | 中断批次；不可作为完整 3/10 实验 |
| 9/7 1f4e481 | 10 | 10 个 RuntimeError、全部无 reward | 运行故障，不能据此评价模型能力 |
| 9/11 DeepSeek 两批 | 10 | 首批 10 个 RuntimeError；续批仅 4/10 有结果，2 通过 | 无有效完整模型对照 |
| 9/14 Luna | 2 | 2/10 | 完整十题；仍有环境/超时影响 |
| 9/14 network-restart | 2 | 0/10 | 服务/协议异常污染，不能等同纯解题能力归零 |
| 9/15 core-redesign | 2 | 4/10 | 固定十题的较好结果 |
| 9/15 c6-repair | 6 | 3/10 | 服务异常及题目回退并存 |
| 9/15 evidence-repair | 5 | 4/10 | 回到此前水平，未证明全面超越 |
| 9/15 unseen20-prepulled | 5 | 9/20 | 与固定十题不重叠；更有泛化参考价值 |
| 9/16 全量三段 | 5 | 27/89 | 38 题终态 server_error，大幅污染成绩 |
| 9/16—9/17 故障重评两段 | 4 | 16/38 | 同一冻结候选，服务异常显著减少 |
| 当前全量替换汇总 | 5/4 混合 | 41/89 | 选择性重评合并，不能与一次运行等同 |

前期固定十题结果多数在 0—2 题之间，9/15 达到 4 题，说明一些机制修复确实改善了可用性。但不能把这一过程解释为单调上升，也不能把新选 20 题的 45% 与十题的 40% 当成提升 5 个百分点：题集不同。

### 同题对照：当前存在明确回退

以 9/15 两个完整批次的题名为固定集合，在当前 89 题汇总中逐一取同题结果：

| 固定题集 | 9/15 | 当前同题 | 新通过 | 丢失通过 |
|---|---:|---:|---|---|
| evidence-repair 十题 | 4/10 | 2/10 | 无 | build-pov-ray、compile-compcert |
| unseen20 二十题 | 9/20 | 10/20 | headless-terminal、kv-store-grpc、mailman | sqlite-with-gcov、tune-mjcf |

服务型任务的改善与之前对子进程生命周期、后台服务保留、超时清理的修复方向一致；既有离线 Linux 探针也支持这些机制。但代码、服务状态、日期、并发和随机采样并非完全受控，以上单次对照不能给出严格因果归因。tune-mjcf 本轮还带有 verifier 依赖启动故障，不能将其回退全归因于 agent。

## 3. 本次总分提升说明什么

原 38 个 server_error 任务由 2 通过变为 16 通过，净增加 14 题。重评使用相同冻结候选内容，因此这是一条强证据：**原 30.3% 包含大量服务与执行条件造成的能力损失，不能直接视为架构上限。**

但并发从 5 降至 4，同时服务恢复、运行时间改变，部分网关端口随重启变化；不能把 15.7 个百分点的合并提升全部归因于降低并发，也不能声称并发 4 最优。自动重试还会增加成功机会，因此 46.1% 不是无条件可复现的单次能力保证。

重评仍有 20 个 reward=0 和 2 个缺失 reward。即使 server_error 消失，问题也没有全部解决；继续无限重试只能混合采样收益和真正的系统改进。

## 4. 当前故障结构与具体证据

当前 89 题的终态中记录 time_budget_exhausted 21 题、acceptance_unmet 20 题、completed 26 题、partial 16 题、failed 4 题、stream_interrupted 1 题；另有 1 题在 agent 启动前失败，无内部终态。这些是内部状态，不等于官方 reward 分类。

按分析器的互斥“主要诊断类别”统计：pass 37、agent_timeout 18、agent_failure 26、verifier_environment_failure 6、infrastructure_failure 1、model_protocol_failure 1。主要诊断优先记录异常，因此 pass 类的 37 **不是**官方通过数 41；例如 stream_interrupted 的题仍可通过。官方成绩始终单独保留。

| 题目 / 类别 | 最新证据 | 判断与后续方向 |
|---|---|---|
| portfolio-optimization | agent 日志报 `No module named benchmark.harbor; benchmark is not a package`；任务 Dockerfile 在 `/app` 放入 `benchmark.py`，入口在任务 cwd 执行 `python -m benchmark.harbor.process_supervisor` | 同名模块遮蔽启动入口，属于 harness 缺陷；优先改成不受任务 cwd 影响的安装包入口，并离线验证 |
| build-pov-ray | time_budget_exhausted；三个官方测试失败，缺 `/app/povray-2.2/file_id.diz` 与可执行文件 | 当前是构建交付缺失；不能沿用此前“能渲染但归档版本不对”的旧诊断 |
| compile-compcert | time_budget_exhausted；三个官方测试失败，缺 `/tmp/CompCert/ccomp` | 重依赖/长编译任务的预算和构建路径仍不稳定 |
| sqlite-with-gcov | 内部 completed，官方 gcov 检查找不到 `.gcda` | 编译成功或功能可用不等于覆盖率产物满足要求 |
| mcmc-sampling-stan | 最新官方数值检查：beta 均值 1.6293，预期区间 [16.1,16.7] | 已从历史依赖失败推进到数值正确性失败；需检查模型、参数化和尺度，不能未经验证指定根因 |
| mteb-retrieve | 内部 completed，输出论文标题与要求不符（MTEB 对应位置得到 HumanEval） | 语义/数据来源错误，不是简单输出格式问题 |
| filter-js-from-html | 内部 completed；恶意输入拦截及保留干净 HTML 的检查均失败 | 自测不足以证明安全过滤与无损行为同时成立 |
| code-from-image、qemu-alpine-ssh、qemu-startup、tune-mjcf | verifier 的 uv bootstrap 相关失败 | 部分 reward=0 被验证环境故障污染；不代表产物一定正确 |
| query-optimize、torch-pipeline-parallelism | VerifierTimeoutError，无 reward | 必须保留“未得到判分”，进一步区分评测环境慢与产物导致测试挂起 |

这里的官方测试阈值只用于事后故障分析；不得把隐藏断言、答案或题名规则回灌给 agent 来制造分数提升。

### 内部“完成”与真实验收仍未对齐

26 个内部 completed 中，19 个官方通过、7 个失败，即 26.9% 的“完成”没有通过官方判定。41 个官方通过中，22 个内部并非 completed（18 partial、4 failed）。

这说明目前状态机同时存在误报完成和较保守的未完成判定。后者不一定全部错误，因为官方测试也可能只覆盖需求子集；但更多 finish 重试、更多文字证据或更复杂的状态分类，不能替代真实行为验证。应该优先建立独立的语义检查、产物检查和可运行性检查。

历史审计还发现自测预期取自当前实现、输入被自测破坏、少量样例记忆、对产物类型作错误假设等现象。它们与本次数值、检索、gcov 失败属于同一类风险：实现和“验证”共享了错误假设。

## 5. 架构判断与优化顺序

**现有证据不足以支持推倒重写整个项目。需要有针对性的子系统改造，并用干净实验验证收益。** 单一 TurnRunner、统一执行器、共享预算、持久化轨迹仍可保留；大规模多 agent 化会增加请求压力和归因复杂度，当前没有实验支持它能解决首要失败。

| 优先级 | 改造方向 | 应交付的验证证据 |
|---|---|---|
| P0 | 启动入口命名隔离：避免任务内 benchmark.py 等文件遮蔽 harness；可靠定位安装包 | 含同名文件/目录的离线任务可启动，原任务入口仍正常 |
| P0 | 批次级服务健康熔断：连续 server_error/连接失败时停止领取新题，保留已完成结果与待运行队列 | 注入故障时不会继续消耗后续题；恢复后唯一续跑，不重复计分 |
| P0 | 持久化控制器与恢复：真实进程身份、心跳、可解释暂停状态、短路径、网关重新发现 | 重启、端口变化、进程消失后能恢复；无幽灵“运行中”状态 |
| P0 | verifier 依赖预检/预构建，在官方允许流程内缓存依赖；不放宽测试断言 | 同一官方验证器在稳定环境中可复现运行，仍使用原判分规则 |
| P1 | 独立验收：数值不变量、数据来源、产物格式、覆盖率文件、服务就绪等行为检查 | 加入看似成功但语义错误的负样例，自测能够检出 |
| P1 | 预算按风险分配：早期检查依赖、估算长编译、限制扫描和无收益重试、预留最终验证时间 | 降低超时和零产物率；不能靠放宽总预算掩盖退化 |
| P1 | 将产品内部完成状态与官方结果分开记录，再校准完成判定 | completed 错误率下降，同时不增加无收益 finish 循环 |
| P2 | 优化上下文与模型调用成本 | 相同题集、相同预算下减少 token 和重复工具调用，保持通过数 |

现有代码已有单任务健康处理、超时预算和清理机制；这里强调完善批次级故障处置及其恢复证据，不是假定所有相关模块都不存在。本次没有实施这些新改动。

## 6. 成本与后续实验建议

原全量已完成记录累计 1,886 次模型调用、2,429 次工具调用；重评已完成记录累计 920 次模型调用、1,390 次工具调用。实际已完成尝试合计至少 2,806 次模型调用、3,819 次工具调用，另有中断尝试的消费未计入。当前替换结果集的 2,603/3,675 是选中结果的调用量，不能当作整个实验实际成本。

重评 38 题记录 input_tokens=24,514,392、cache_read=4,234,752、output_tokens=502,419。不同版本 token 统计口径与缓存计费未完全统一，不据此推算费用，也不把 input/cache 数字直接与所有旧报告相加。

建议下一轮先完成 P0 的离线故障注入和针对性回归，再冻结候选、数据集、模型实际标识、provider、reasoning 设置、预算、依赖和控制器配置，独立跑一次完整 89 题。并发 4 可以作为近期运行基线，但尚未证明最优。批次内不修改代码；故障重评另表公布，原始独立运行成绩始终保留。

固定十题适合查回归，不宜继续作为主要优化目标。原先未见的二十题现已用于分析，也不再是全新保留集。比较并发或架构时一次只改一个主要变量，使用相同题集并报告逐题增减及服务故障；条件允许再用重复运行区分随机波动。此次不自动开启付费评测。

## 7. 可复核资料

- [本次完整数据](./2026-09-17-experiment-history-data.json)：71 个 job、原始 89 题、38 题重评、替换后 89 题、状态与逐题变化。
- [只读整理脚本](../../benchmark/analysis/summarize_experiment_history.py)：读取原始结果，校验题集唯一性和 38 题选择一致性，生成汇总。
- [原始全量总结](./2026-09-16-full89-final-summary.json)。
- [历史证据质量](./2026-09-15-history-evidence.md)、[c2/c6 对照](./2026-09-15-c2-c6-comparison.md)、[深度实验分析](./2026-09-15-deep-run-analysis.md)。
- [新选二十题分析](./2026-09-15-unseen20-analysis-and-design.md)、[此前修复记录](./2026-09-16-known-issue-repair.md)。

## 附录 A：全部本地 job 清单

“结果数”是保留的 result.json 数，含 reward 缺失；“计划”仅取配置显式指定题数，? 表示该字段不足以确定分母；“结束标记”仅指 job finished_at，并不保证所有题有效判分。一个实验可能有多个 job，续跑分段不能重复算作独立实验。SWE 与 TB2 不合并；无模型的 lifecycle smoke 不用于模型能力评价。

| # | Job / 运行目录 | 数据集 | 模型 | 并发 | 计划 | 已建 trial | 结果数 | 通过 | reward 缺失 | 结束标记 |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 1 | [2026-08-27__14-37-21](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-smoke/2026-08-27__14-37-21)<br>swe-bench-smoke | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | ? | 1 | 1 | 0 | 1 | 有 |
| 2 | [2026-08-27__14-41-09](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-smoke/2026-08-27__14-41-09)<br>swe-bench-smoke | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | ? | 1 | 1 | 0 | 0 | 有 |
| 3 | [2026-08-27__15-33-11](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-smoke-desktop/2026-08-27__15-33-11)<br>swe-bench-smoke-desktop | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | ? | 1 | 1 | 0 | 1 | 有 |
| 4 | [2026-08-27__15-38-08](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-smoke-desktop-2/2026-08-27__15-38-08)<br>swe-bench-smoke-desktop-2 | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | ? | 1 | 1 | 0 | 1 | 有 |
| 5 | [2026-08-27__15-44-21](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-smoke-desktop-3/2026-08-27__15-44-21)<br>swe-bench-smoke-desktop-3 | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | ? | 1 | 1 | 0 | 1 | 有 |
| 6 | [2026-08-27__15-49-55](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-smoke-desktop-4/2026-08-27__15-49-55)<br>swe-bench-smoke-desktop-4 | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | ? | 1 | 1 | 0 | 1 | 有 |
| 7 | [2026-08-27__16-04-15](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-smoke-desktop-5/2026-08-27__16-04-15)<br>swe-bench-smoke-desktop-5 | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | ? | 1 | 1 | 1 | 0 | 有 |
| 8 | [2026-08-27__16-15-42](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-verified-full/2026-08-27__16-15-42)<br>swe-bench-verified-full | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | ? | 10 | 10 | 0 | 9 | 无 |
| 9 | [2026-08-27__16-33-27](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full/2026-08-27__16-33-27)<br>terminal-bench-2-full | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | ? | 1 | 0 | 0 | 0 | 无 |
| 10 | [2026-08-27__16-39-48](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-verified-retry/2026-08-27__16-39-48)<br>swe-bench-verified-retry | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | 9 | 3 | 2 | 0 | 2 | 无 |
| 11 | [2026-08-27__16-47-35](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-prepull-test/2026-08-27__16-47-35)<br>swe-bench-prepull-test | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 12 | [2026-08-27__17-27-35](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-verified-retry-prepulled/2026-08-27__17-27-35)<br>swe-bench-verified-retry-prepulled | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | 9 | 9 | 9 | 0 | 5 | 有 |
| 13 | [2026-08-28__12-51-41](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-verified-retry-2/2026-08-28__12-51-41)<br>swe-bench-verified-retry-2 | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | 8 | 5 | 5 | 1 | 0 | 无 |
| 14 | [2026-08-31__10-33-47](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-verified-continuation/2026-08-31__10-33-47)<br>swe-bench-verified-continuation | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | 1 | 1 | 0 | 0 | 0 | 无 |
| 15 | [2026-08-31__10-51-16](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-verified-continuation/2026-08-31__10-51-16)<br>swe-bench-verified-continuation | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 16 | [2026-08-31__10-58-49](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-verified-continuation/2026-08-31__10-58-49)<br>swe-bench-verified-continuation | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | 1 | 0 | 0 | 0 | 0 | 无 |
| 17 | [2026-08-31__11-00-25](D:/projects/forgecode/benchmark/runs/harbor/swe-bench-verified-continuation/2026-08-31__11-00-25)<br>swe-bench-verified-continuation | swe-bench/swe-bench-verified | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 18 | [2026-08-31__11-30-19](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__11-30-19)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 1 | 有 |
| 19 | [2026-08-31__11-35-21](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__11-35-21)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 1 | 有 |
| 20 | [2026-08-31__11-42-09](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__11-42-09)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 21 | [2026-08-31__11-56-42](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__11-56-42)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 22 | [2026-08-31__12-09-18](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-smoke-gitless/2026-08-31__12-09-18)<br>terminal-bench-2-smoke-gitless | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 0 | 0 | 0 | 无 |
| 23 | [2026-08-31__14-41-18](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-smoke-retry/2026-08-31__14-41-18)<br>terminal-bench-2-smoke-retry | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 1 | 0 | 有 |
| 24 | [2026-08-31__14-50-53](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__14-50-53)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 25 | [2026-08-31__15-08-30](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__15-08-30)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 26 | [2026-08-31__15-12-19](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__15-12-19)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 27 | [2026-08-31__15-19-53](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__15-19-53)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 1 | 0 | 有 |
| 28 | [2026-08-31__15-26-59](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__15-26-59)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 29 | [2026-08-31__15-55-09](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__15-55-09)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 30 | [2026-08-31__16-07-23](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__16-07-23)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 31 | [2026-08-31__16-10-44](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__16-10-44)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 32 | [2026-08-31__16-19-44](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__16-19-44)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 0 | 有 |
| 33 | [2026-08-31__16-27-27](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-continuation/2026-08-31__16-27-27)<br>terminal-bench-2-continuation | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 1 | 1 | 1 | 0 | 1 | 无 |
| 34 | [2026-08-31__17-32-09](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-phase1-rerun/2026-08-31__17-32-09)<br>terminal-bench-2-phase1-rerun | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 10 | 1 | 0 | 0 | 0 | 无 |
| 35 | [2026-08-31__17-37-38](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-phase1-rerun/2026-08-31__17-37-38)<br>terminal-bench-2-phase1-rerun | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 10 | 2 | 1 | 0 | 0 | 无 |
| 36 | [2026-09-01__11-09-27](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-phase1-rerun/2026-09-01__11-09-27)<br>terminal-bench-2-phase1-rerun | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 1 | 9 | 9 | 9 | 1 | 0 | 有 |
| 37 | [2026-09-01__13-01-00](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-phase2-concurrency2/2026-09-01__13-01-00)<br>terminal-bench-2-phase2-concurrency2 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 2 | 10 | 10 | 10 | 2 | 0 | 有 |
| 38 | [2026-09-01__16-26-26](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-stage3/2026-09-01__16-26-26)<br>terminal-bench-2-stage3 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 2 | ? | 10 | 9 | 1 | 0 | 无 |
| 39 | [2026-09-02__16-58-35](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-stage3-concurrency4/2026-09-02__16-58-35)<br>terminal-bench-2-stage3-concurrency4 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 4 | 10 | 10 | 10 | 1 | 0 | 有 |
| 40 | [2026-09-02__18-21-18](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-stage3-rerun-c4/2026-09-02__18-21-18)<br>terminal-bench-2-stage3-rerun-c4 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 4 | 10 | 10 | 9 | 1 | 0 | 无 |
| 41 | [2026-09-03__10-28-42](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-process-recovery-c6/2026-09-03__10-28-42)<br>terminal-bench-2-process-recovery-c6 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 10 | 1 | 0 | 有 |
| 42 | [2026-09-03__11-17-15](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-runtime-recovery-c6/2026-09-03__11-17-15)<br>terminal-bench-2-runtime-recovery-c6 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 10 | 1 | 0 | 有 |
| 43 | [2026-09-03__12-31-57](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-edit-recovery-c6/2026-09-03__12-31-57)<br>terminal-bench-2-edit-recovery-c6 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 10 | 1 | 0 | 有 |
| 44 | [2026-09-03__13-28-50](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-positive-evidence-c6/2026-09-03__13-28-50)<br>terminal-bench-2-positive-evidence-c6 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 10 | 0 | 0 | 有 |
| 45 | [2026-09-03__14-41-48](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-task-contract-c6/2026-09-03__14-41-48)<br>terminal-bench-2-task-contract-c6 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 10 | 1 | 0 | 有 |
| 46 | [2026-09-03__18-25-30](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-runtime-v2-c6/2026-09-03__18-25-30)<br>terminal-bench-2-runtime-v2-c6 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 9 | 1 | 0 | 无 |
| 47 | [2026-09-04__10-11-38](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-baseline-eff5909-c6-20260904/2026-09-04__10-11-38)<br>terminal-bench-2-baseline-eff5909-c6-20260904 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 8 | 2 | 1 | 0 | 无 |
| 48 | [2026-09-04__10-33-13](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-baseline-eff5909-c6-restored-20260904/2026-09-04__10-33-13)<br>terminal-bench-2-baseline-eff5909-c6-restored-20260904 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 7 | 0 | 0 | 无 |
| 49 | [2026-09-04__11-51-02](D:/projects/forgecode/benchmark/runs/harbor/driver-lifecycle-smoke-20260904/2026-09-04__11-51-02)<br>driver-lifecycle-smoke-20260904 | smoke/未标注 | 无 | 1 | ? | 1 | 1 | 1 | 0 | 有 |
| 50 | [2026-09-04__11-52-34](D:/projects/forgecode/benchmark/runs/harbor/driver-lifecycle-smoke-utf8-20260904/2026-09-04__11-52-34)<br>driver-lifecycle-smoke-utf8-20260904 | smoke/未标注 | 无 | 1 | ? | 1 | 1 | 1 | 0 | 有 |
| 51 | [2026-09-04__11-55-36](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-runtime-v2-b9a3d7b-c6/2026-09-04__11-55-36)<br>terminal-bench-2-runtime-v2-b9a3d7b-c6 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 10 | 2 | 0 | 有 |
| 52 | [2026-09-04__15-35-21](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-runtime-v2-390af0b-c6/2026-09-04__15-35-21)<br>terminal-bench-2-runtime-v2-390af0b-c6 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 10 | 0 | 0 | 有 |
| 53 | [2026-09-04__15-58-46](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-runtime-v2-390af0b-c6-rerun1/2026-09-04__15-58-46)<br>terminal-bench-2-runtime-v2-390af0b-c6-rerun1 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 6 | 0 | 0 | 无 |
| 54 | [2026-09-05__14-24-02](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-runtime-v2-390af0b-c6-network-restored-20260905/2026-09-05__14-24-02)<br>terminal-bench-2-runtime-v2-390af0b-c6-network-restored-20260905 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 10 | 2 | 0 | 有 |
| 55 | [2026-09-06__12-54-44](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-runtime-v2-reliability-c10-20260906-125422/2026-09-06__12-54-44)<br>terminal-bench-2-runtime-v2-reliability-c10-20260906-125422 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 10 | 10 | 10 | 8 | 3 | 0 | 无 |
| 56 | [2026-09-07__15-06-43](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-runtime-v2-1f4e481-c10-20260907-150538/2026-09-07__15-06-43)<br>terminal-bench-2-runtime-v2-1f4e481-c10-20260907-150538 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 10 | 10 | 10 | 10 | 0 | 10 | 有 |
| 57 | [2026-09-11__16-11-29](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-1f4e481-deepseek-flash-c10-20260911-161055/2026-09-11__16-11-29)<br>terminal-bench-2-1f4e481-deepseek-flash-c10-20260911-161055 | terminal-bench/terminal-bench-2 | deepseek-flash | 10 | 10 | 10 | 10 | 0 | 10 | 有 |
| 58 | [2026-09-11__16-13-45](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-1f4e481-deepseek-flash-c10-network-restored-20260911/2026-09-11__16-13-45)<br>terminal-bench-2-1f4e481-deepseek-flash-c10-network-restored-20260911 | terminal-bench/terminal-bench-2 | deepseek-flash | 10 | 10 | 10 | 4 | 2 | 2 | 无 |
| 59 | [2026-09-14__11-39-31](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-1f4e481-luna-c2-20260914-113857/2026-09-14__11-39-31)<br>terminal-bench-2-1f4e481-luna-c2-20260914-113857 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 2 | 10 | 10 | 10 | 2 | 0 | 有 |
| 60 | [2026-09-14__15-39-11](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-contract-repair-20260914-153906/2026-09-14__15-39-11)<br>terminal-bench-2-luna-c2-contract-repair-20260914-153906 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 2 | 10 | 4 | 4 | 0 | 1 | 无 |
| 61 | [2026-09-14__16-42-43](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-network-restart-20260914-164237/2026-09-14__16-42-43)<br>terminal-bench-2-luna-c2-network-restart-20260914-164237 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 2 | 10 | 10 | 10 | 0 | 0 | 有 |
| 62 | [2026-09-15__13-33-35](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35)<br>terminal-bench-2-luna-c2-core-redesign-20260915-133329 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 2 | 10 | 10 | 10 | 4 | 0 | 有 |
| 63 | [2026-09-15__16-30-01](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01)<br>terminal-bench-2-luna-c6-repair-20260915-162956 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 6 | 10 | 10 | 10 | 3 | 0 | 有 |
| 64 | [2026-09-15__17-34-30](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c5-evidence-repair-20260915-173425/2026-09-15__17-34-30)<br>terminal-bench-2-luna-c5-evidence-repair-20260915-173425 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 5 | 10 | 10 | 10 | 4 | 0 | 有 |
| 65 | [2026-09-15__19-00-13](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-unseen20-luna-c5-20260915-185937/2026-09-15__19-00-13)<br>terminal-bench-2-unseen20-luna-c5-20260915-185937 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 5 | 20 | 14 | 9 | 2 | 1 | 无 |
| 66 | [2026-09-15__19-39-04](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-unseen20-luna-c5-prepulled-20260915-193858/2026-09-15__19-39-04)<br>terminal-bench-2-unseen20-luna-c5-prepulled-20260915-193858 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 5 | 20 | 20 | 20 | 9 | 0 | 有 |
| 67 | [2026-09-16__11-06-53](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53)<br>terminal-bench-2-full89-luna-c5-20260916-110647 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 5 | ? | 36 | 36 | 16 | 0 | 无 |
| 68 | [2026-09-16__15-02-26](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/resume-20260916-150000/2026-09-16__15-02-26)<br>resume-20260916-150000 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 5 | 53 | 8 | 3 | 2 | 0 | 无 |
| 69 | [2026-09-16__15-52-29](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29)<br>tb2-r2-0916-155118 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 5 | 50 | 50 | 50 | 9 | 1 | 有 |
| 70 | [2026-09-16__17-37-48](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48)<br>tb2-se38-c4-0916-173742 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 4 | 38 | 33 | 29 | 13 | 2 | 无 |
| 71 | [2026-09-17__09-50-41](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41)<br>tb2-se9-c4-0917-095024 | terminal-bench/terminal-bench-2 | gpt-5.6-luna | 4 | 9 | 9 | 9 | 3 | 0 | 有 |

## 附录 B：本次替换新增的 14 个通过题

bn-fit-modify, cobol-modernization, feal-differential-cryptanalysis, fix-ocaml-gc, hf-model-inference, modernize-scientific-stack, password-recovery, pytorch-model-cli, reshard-c4-data, schemelike-metacircular-eval, sparql-university, financial-document-processor, large-scale-text-editing, llm-inference-batching-scheduler。

## 附录 C：当前 89 题逐题结果与原始证据

这里严格使用替换规则确定的一个结果，不取历史最大值。reward 缺失以“缺失”表示。

| 题目 | reward | 内部终态 | Harbor 异常 | 原始 trial |
|---|---:|---|---|---|
| adaptive-rejection-sampler | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/adaptive-rejection-sampler__TgEe9Cz) |
| bn-fit-modify | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/bn-fit-modify__e5gYxZm) |
| break-filter-js-from-html | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/break-filter-js-from-html__rQYK7bs) |
| build-cython-ext | 1.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/build-cython-ext__U7Qat85) |
| build-pmars | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/build-pmars__ozaSwu3) |
| build-pov-ray | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/build-pov-ray__C6nDdHj) |
| caffe-cifar-10 | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/caffe-cifar-10__crwZeR7) |
| cancel-async-tasks | 1.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/cancel-async-tasks__nq8DYFk) |
| chess-best-move | 0.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/chess-best-move__LW4yNXQ) |
| circuit-fibsqrt | 0.0 | failed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/circuit-fibsqrt__ACPPhc6) |
| cobol-modernization | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/cobol-modernization__m7jSoT9) |
| code-from-image | 0.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/code-from-image__5d5w6or) |
| compile-compcert | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/compile-compcert__aE3E7yd) |
| configure-git-webserver | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/configure-git-webserver__j5WHq7C) |
| constraints-scheduling | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/constraints-scheduling__AGG6i9G) |
| count-dataset-tokens | 0.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/count-dataset-tokens__bWjkHYX) |
| crack-7z-hash | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/crack-7z-hash__aLecZLh) |
| custom-memory-heap-crash | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/custom-memory-heap-crash__6PJiy4d) |
| db-wal-recovery | 0.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/db-wal-recovery__oHDUxnB) |
| distribution-search | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/distribution-search__RSwZkVk) |
| dna-assembly | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/dna-assembly__QcN2v5c) |
| dna-insert | 0.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/dna-insert__o5q9XPV) |
| extract-elf | 0.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/extract-elf__sUzx7Y6) |
| extract-moves-from-video | 0.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/extract-moves-from-video__mEEDuWM) |
| feal-differential-cryptanalysis | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/feal-differential-cryptanalysis__GupgrcU) |
| feal-linear-cryptanalysis | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/feal-linear-cryptanalysis__rdtqmPP) |
| filter-js-from-html | 0.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/filter-js-from-html__EUjMX5m) |
| financial-document-processor | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/financial-document-processor__hqqjk6S) |
| fix-code-vulnerability | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/fix-code-vulnerability__H3ieECH) |
| fix-git | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/fix-git__FXM9gVy) |
| fix-ocaml-gc | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/fix-ocaml-gc__p32sVKu) |
| gcode-to-text | 0.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/resume-20260916-150000/2026-09-16__15-02-26/gcode-to-text__ScjeZ6B) |
| git-leak-recovery | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/git-leak-recovery__6KaSMmQ) |
| git-multibranch | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/git-multibranch__9iRacYp) |
| gpt2-codegolf | 0.0 | failed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/gpt2-codegolf__DMY7fn7) |
| headless-terminal | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/headless-terminal__6HzpsqR) |
| hf-model-inference | 1.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/hf-model-inference__tUqwsTo) |
| install-windows-3-11 | 0.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/install-windows-3-11__FTSmxcn) |
| kv-store-grpc | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/kv-store-grpc__2A7tutW) |
| large-scale-text-editing | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/large-scale-text-editing__j64TKuo) |
| largest-eigenval | 0.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/largest-eigenval__sXPZZLY) |
| llm-inference-batching-scheduler | 1.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/llm-inference-batching-scheduler__W8YaSdv) |
| log-summary-date-ranges | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/log-summary-date-ranges__6j4A9ef) |
| mailman | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/mailman__ShD4w3k) |
| make-doom-for-mips | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/make-doom-for-mips__6FC9QZJ) |
| make-mips-interpreter | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/make-mips-interpreter__9LVQy9A) |
| mcmc-sampling-stan | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/mcmc-sampling-stan__hhphCUG) |
| merge-diff-arc-agi-task | 1.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/resume-20260916-150000/2026-09-16__15-02-26/merge-diff-arc-agi-task__4s9Z4G7) |
| model-extraction-relu-logits | 0.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/model-extraction-relu-logits__AUggVNM) |
| modernize-scientific-stack | 1.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/modernize-scientific-stack__tSwnH35) |
| mteb-leaderboard | 0.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/mteb-leaderboard__UJS8rpA) |
| mteb-retrieve | 0.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/mteb-retrieve__jfXXQW7) |
| multi-source-data-merger | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/resume-20260916-150000/2026-09-16__15-02-26/multi-source-data-merger__hCvqe3j) |
| nginx-request-logging | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/nginx-request-logging__K8kg4o9) |
| openssl-selfsigned-cert | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/openssl-selfsigned-cert__7NqDKCh) |
| overfull-hbox | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/overfull-hbox__6CTDVAk) |
| password-recovery | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/password-recovery__QGTHN7S) |
| path-tracing | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/path-tracing__U5Pv2fv) |
| path-tracing-reverse | 0.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/path-tracing-reverse__xdyaGx6) |
| polyglot-c-py | 0.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/polyglot-c-py__Vu42yvq) |
| polyglot-rust-c | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/polyglot-rust-c__DF2Qjx5) |
| portfolio-optimization | 0.0 | 无 | NonZeroAgentExitCodeError | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/portfolio-optimization__VW4NoNY) |
| protein-assembly | 0.0 | failed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/protein-assembly__iSBd7ff) |
| prove-plus-comm | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/prove-plus-comm__AJWwAJi) |
| pypi-server | 0.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/pypi-server__DUZ2hnt) |
| pytorch-model-cli | 1.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/pytorch-model-cli__UXZx5Wq) |
| pytorch-model-recovery | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/pytorch-model-recovery__g42MeaJ) |
| qemu-alpine-ssh | 0.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/qemu-alpine-ssh__zPTXnak) |
| qemu-startup | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/qemu-startup__nY8NhX9) |
| query-optimize | 缺失 | time_budget_exhausted | VerifierTimeoutError | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/query-optimize__nMhCPWj) |
| raman-fitting | 0.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/raman-fitting__TefMQLU) |
| regex-chess | 0.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/regex-chess__BfK6vgh) |
| regex-log | 1.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/regex-log__zdoUtAC) |
| reshard-c4-data | 1.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/reshard-c4-data__pFUELNg) |
| rstan-to-pystan | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/rstan-to-pystan__AH9xddd) |
| sam-cell-seg | 0.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/sam-cell-seg__GeQ7sYL) |
| sanitize-git-repo | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/sanitize-git-repo__dvS36d6) |
| schemelike-metacircular-eval | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/schemelike-metacircular-eval__FihtQkb) |
| sparql-university | 1.0 | stream_interrupted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/sparql-university__fteqAb6) |
| sqlite-db-truncate | 1.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/sqlite-db-truncate__LJTXHAZ) |
| sqlite-with-gcov | 0.0 | completed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/sqlite-with-gcov__N8KW4Cg) |
| torch-pipeline-parallelism | 缺失 | partial | VerifierTimeoutError | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/torch-pipeline-parallelism__WQ5WcUD) |
| torch-tensor-parallelism | 0.0 | partial | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/torch-tensor-parallelism__cw47QrR) |
| train-fasttext | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/train-fasttext__oLJSqHx) |
| tune-mjcf | 0.0 | time_budget_exhausted | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/tune-mjcf__APzKu9p) |
| video-processing | 0.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/video-processing__PbCPM6U) |
| vulnerable-secret | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/vulnerable-secret__b6mykzy) |
| winning-avg-corewars | 0.0 | failed | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/winning-avg-corewars__QtQbWug) |
| write-compressor | 1.0 | acceptance_unmet | 无 | [证据](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/write-compressor__CrveGxD) |
