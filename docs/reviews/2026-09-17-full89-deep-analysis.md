# 最近一次全量 89 题评测：逐层诊断

分析日期：2026-09-17。主体是 2026-09-16 并发 5、gpt-5.6-luna 的原始全量 89 题；9/16—9/17 并发 4 的 38 题重评只作对照。不得混用两轮的失败原因、内部状态和通过数。

## 一、成绩与实验条件

原始全量：**27/89（30.3%），61 个 reward=0，1 个缺失 reward**。模型通过 anthropic 兼容接口访问本地网关，配置模型名为 gpt-5.6-luna；未显式固定 reasoning，无法据此保证与外部榜单使用完全相同的后端条件。

全量经三段完成：36 题通过 16；3 题通过 2；50 题通过 9。保留每个题的正式结果，排除暂停遗留的 5 个未完成尝试。首个保留 trial 开始于北京时间 11:07:01，最后一个结束于 17:23:32；约 6 小时 16 分钟的跨度包含暂停和恢复，不能视为连续满载吞吐。

每题配置上限为 120 次模型调用、240 次工具调用、1800 秒 kernel 时间，但实际执行期限还受 Harbor 任务期限限制。47 题记录精确 870 秒，另有两题约 870 秒；其他题约 570、720、1170、1770 或 1800 秒。运行时在外层期限中预留约 30 秒给启动、清理和日志导出。**因此“每题实际都有 30 分钟”是不正确的。** 应在后续报告同时记录配置上限、有效期限、实际耗时，不能简单将短任务超时诊断为预算被错误截断。

后续 38 题重评为 16/38；替换后汇总 41/89（46.1%），净增加 14 题。这是选择性故障重评汇总，不是另一次全量独立 pass@1。

## 二、服务异常：本次成绩最大的污染源

38/89（42.7%）的原始任务以 server_error 结束，其中 2 题已完成足够产物并通过官方测试。保留的会话日志中共有 116 条 server_error 请求完成事件；最早在北京时间 16:38:14，最晚在 17:18:38。该时间窗只指采集到的错误事件，不证明服务在整个窗口内持续不可用，也无法仅凭这些日志确定是上游故障、本地代理还是限流。

38 题中有 25 题只记录 3 次模型请求、0 次工具调用；server_error 组的 agent_execution 中位数仅 22.1 秒。这些题尚未进入有效解题阶段。adaptive-rejection-sampler 的三个错误响应分别约 0.61、0.49、0.48 秒，最终三次均无文本和工具输出；说明该样本不是复杂推理用尽预算，而是短时间连续请求失败。

原始 38 题只有 2 个通过，同一冻结候选在重评中达到 16 个通过，证明服务故障造成了真实的成功机会损失。但服务恢复、并发、运行时间及采样同时变化，不能宣称“降低一个并发就提升 15.7 个百分点”。更不能把剩余未通过题全部归因于服务。

剩余不带终态 server_error 的 51 题为 25/51（49.0%）。这是一个受选择条件影响的诊断子集，仍包含环境和启动故障，不是剔除噪声后可对外报告的模型能力分数。

改进重点：已有单题重试并未阻止批次继续领取新题。需要按 provider/模型共享健康状态，在跨任务错误持续出现时暂停领取、保存队列、用少量探针确认恢复，然后继续。不能只增加每题重试次数。请求错误还应保留脱敏后的 HTTP 类别、上游请求 ID 和重试决策，使本地代理与上游故障可区分。

## 三、停止原因与官方验收交叉表

| 内部停止原因 | 题数 | 官方通过 | 模型调用 | 工具调用 | agent 执行累计分钟 |
|---|---:|---:|---:|---:|---:|
| completed | 15 | 12 | 392 | 587 | 129.5 |
| time_budget_exhausted | 12 | 2 | 609 | 855 | 254.8 |
| partial | 6 | 2 | 158 | 196 | 66.2 |
| acceptance_unmet | 14 | 9 | 360 | 420 | 137.7 |
| failed | 3 | 0 | 164 | 227 | 55.3 |
| server_error | 38 | 2 | 203 | 144 | 47.2 |
| no_payload | 1 | 0 | 0 | 0 | 0.0 |

停止原因不能替代 reward：12 个 time_budget_exhausted 中有 2 个通过；14 个 acceptance_unmet 中有 9 个通过；38 个 server_error 中也有 2 个通过。

原始全量的内部 status=completed 共 15 题，12 通过、3 失败（20% 未通过）。三题为 mteb-leaderboard、gcode-to-text、sqlite-with-gcov。另一方面，27 个官方通过中有 15 个内部不是 completed（11 partial、4 failed）。这与补评汇总的 26/7、41/22 是不同统计对象。

### 验收层的两种独立问题

1. **产物语义检查不足。** 标记完成但实际输出、数据来源、覆盖率产物错误，说明自测可能只覆盖形式或沿用了实现的错误假设。
2. **检查器协议及证据状态造成阻塞。** 官方通过的 openssl-selfsigned-cert、write-compressor、kv-store-grpc 等，内部理由包含“最后一行不是 JSON 对象”“缺少输出键”；其他任务包含检查过期、需求绑定未验证、cwd 契约不一致。这些不等于产物正确性一定失败。

官方通过也不证明所有内部未满足要求都应删除：隐藏测试可能覆盖不全。正确方案是分别记录产物失败、检查器错误、环境不可用、证据过期，提供明确而有限的修复路径。不得靠放宽断言提高完成率，也不要让模型无限修补验收元数据。

## 四、真实解题失败：哪些任务值得优先改

以下均取自原始全量的 verifier 日志，不能与重评中的同类题混淆。

| 任务 | 直接证据 | 能支持的诊断 |
|---|---|---|
| build-pov-ray | 三项官方测试失败，缺版本文件和可执行程序；预算耗尽 | 尚未交付可用构建，早期源码/依赖确认与阶段退出策略不足 |
| compile-compcert | 缺 `/tmp/CompCert/ccomp`，三项测试失败 | 长构建路径没有在期限内落地产物 |
| caffe-cifar-10 | 六项测试失败，模型文件、配置及 500 步训练证据缺失；仅 7 次模型、13 次工具调用即耗尽时间 | 时间可消耗在少数长操作，单纯减少模型调用不能解决 |
| make-mips-interpreter | VM 执行、帧文件和图像检查均失败；103 次模型调用 | 大量迭代未完成端到端交付，需要可执行里程碑与收敛判断 |
| path-tracing | 相似度 0.7735，官方阈值 0.99；91 次模型、146 次工具 | 有输出不等于语义正确；需要定位渲染误差而非仅确认文件存在 |
| db-wal-recovery | 记录数/结构能通过部分检查，但 WAL 更新值未应用：100 而非 150 | 数据恢复语义不完整；仅凭行数或文件可读不足以验收 |
| dna-assembly | 序列缺少要求的 BsaI 识别位点 | 领域约束没进入有效行为检查 |
| winning-avg-corewars | 对指定对手胜率 12%，要求至少 33% | 性能目标未达成；有效测试应直接覆盖目标分布 |
| mteb-leaderboard | 标记 completed，但提交的模型名与任务要求答案不同 | 数据来源、时间条件和检索语义核验不足 |
| gcode-to-text | 标记 completed，但输出是对文字的错误解释，并非正确解码结果 | 感知/解析结果缺少独立验证 |
| sqlite-with-gcov | 标记 completed，但无 `.gcda` 文件 | 构建成功与生成所需覆盖率数据被混淆 |
| regex-chess | 多局官方对局检查失败 | 样例成功不能替代泛化测试 |

这些证据证明失败现象，尚不都构成完整根因。比如不能仅由相似度推断是哪一项渲染方程错误，不能仅由错误数值推断具体参数化问题。官方答案与阈值仅用于事后分析，不加入 agent 提示或题名专用逻辑。

## 五、独立于解题能力的 harness/verifier 故障

portfolio-optimization：agent 约 1.4 秒即启动失败，模型/工具调用均为 0。任务 Dockerfile 将 benchmark.py 放在 /app，ForgeCode 从任务 cwd 执行 `python -m benchmark.harbor.process_supervisor`，报 `'benchmark' is not a package`。这明确支持同名模块遮蔽诊断。应使用不受任务 cwd 影响的安装入口，并用同名文件、同名目录的离线 fixture 验证。

原全量的 verifier 故障有五题：qemu-alpine-ssh、qemu-startup、tune-mjcf 为 uv bootstrap；torch-tensor-parallelism 为依赖下载超时；query-optimize 为 VerifierTimeoutError，且缺少 reward。其中三题还同时有 server_error，不能把这些数字与 38 简单相加。启动前依赖预检、允许范围内的缓存与固定依赖有意义，但不得修改官方测试断言。query-optimize 超时需进一步分辨环境耗时与产物挂起，不能直接认定全部是外部网络故障。

## 六、题型与难度：原始成绩受故障分布影响

以下分类直接取 Harbor 缓存 task.toml 的 metadata，并按结果中的 task ref 匹配。补评后列仅帮助判断故障污染程度，不是独立同条件成绩。

| 官方类别 | 题数 | 原始通过 | server_error 题数 | 补评合并后通过 |
|---|---:|---:|---:|---:|
| security | 8 | 5 | 3 | 6 |
| debugging | 5 | 4 | 1 | 4 |
| software-engineering | 26 | 8 | 11 | 11 |
| machine-learning | 3 | 1 | 1 | 2 |
| games | 1 | 0 | 0 | 0 |
| system-administration | 9 | 3 | 2 | 3 |
| personal-assistant | 1 | 1 | 0 | 1 |
| file-operations | 5 | 0 | 1 | 1 |
| scientific-computing | 8 | 0 | 4 | 2 |
| mathematics | 4 | 1 | 3 | 2 |
| data-processing | 4 | 3 | 1 | 4 |
| data-science | 8 | 0 | 7 | 2 |
| model-training | 4 | 1 | 3 | 2 |
| video-processing | 1 | 0 | 0 | 0 |
| optimization | 1 | 0 | 0 | 0 |
| data-querying | 1 | 0 | 1 | 1 |

数据科学原始 0/8，但其中 7 题遭遇 server_error，不能据此认定模型完全不会数据科学。科学计算原始 0/8，其中 4 题遭遇服务错误；补评汇总仍仅 2/8，说明服务恢复后仍有能力/执行问题。debugging 原始 4/5 与 security 5/8 表现相对较好，但样本小，不能外推成稳定专长。

按难度：easy 2/4、medium 21/55、hard 4/30。hard 中 16/30 遭遇 server_error，补评后变为 10/30；medium 变为 28/55。原始 hard 的 13.3% 同时包含高难度与更重的服务故障，不能仅解释为推理能力不足。每类样本尤其 1—5 题的小类不宜排名。

## 七、成本、时间与高消耗失败

原始保留结果共 1,886 次模型调用、2,429 次工具请求；input_tokens=55,272,288，cache_read_input_tokens=8,445,440，output_tokens=927,438。另有 119 次请求 usage 不完整/未知，且暂停丢弃的尝试未计入，因此这些数值不是完整账单。input 与 cache 的 provider 口径需单独核对，不直接相加推算费用。

轨迹计时累计 model_seconds≈6.80 小时，tools_seconds≈4.00 小时，compaction_seconds≈6.08 分钟（12 次压缩请求）。计时字段可能存在嵌套，不能简单相加成 wall time；目前也没有足够依据将本轮低分主要归因于压缩。

| 阶段 | 89 题累计小时 | 单题中位秒 |
|---|---:|---:|
| environment_setup | 0.17 | 4.6 |
| agent_setup | 2.00 | 65.1 |
| agent_execution | 11.51 | 346.8 |
| verifier | 3.15 | 81.0 |

以上是各 trial 时间相加，多个任务并行，不能当作实际等待时间。镜像启动累计仅约 0.17 小时；agent 安装/setup 累计约 2 小时、验证约 3.15 小时，说明镜像已准备好后，安装与判分依赖仍有开销。

12 个预算耗尽任务使用 609 次模型调用（32.3%）、855 次工具调用（35.2%），只有 2 个通过。全部通过题共用 702 次模型调用；未通过/无 reward 题共用 1,184 次（62.8%）。高消耗失败应优先看是否逐步收敛，不能默认再加一倍预算就会解决。

| 任务 | reward | 模型调用 | 工具调用 | agent 执行秒 |
|---|---:|---:|---:|---:|
| make-mips-interpreter | 0.0 | 103 | 141 | 1782 |
| path-tracing | 0.0 | 91 | 146 | 1782 |
| circuit-fibsqrt | 0.0 | 81 | 85 | 1506 |
| dna-assembly | 0.0 | 79 | 86 | 1782 |
| mailman | 1.0 | 70 | 121 | 1528 |
| build-cython-ext | 1.0 | 62 | 92 | 882 |
| overfull-hbox | 0.0 | 59 | 92 | 735 |
| install-windows-3-11 | 0.0 | 54 | 59 | 1265 |
| git-multibranch | 1.0 | 54 | 73 | 644 |
| winning-avg-corewars | 0.0 | 53 | 101 | 795 |

## 八、实施顺序与后续验证

1. **先修启动与批次故障控制。** 修复模块遮蔽；模拟代理不可用、服务恢复、重启和端口变化，确认不会持续领取失败任务，也不会重复计分。它们有明确故障证据，离线即可验证。
2. **拆分检查器失败与产物失败。** 将 checker_error、artifact_failed、environment_unavailable、stale_evidence 明确分开；模板化常用验证输出，允许保留原断言的有限检查器修复。对官方通过却内部阻塞的样例做回归，不能一律将其标为通过。
3. **把预算用于可观测进展。** 早期确认源码、工具链、依赖和目标产物；编译/训练/搜索设置阶段期限；在最终期限前预留端到端验证。判定重复失败时换方法或明确交付部分成果，避免反复微调同一错误假设。
4. **增强独立语义验证。** 用性质测试、参考实现、小型可信算例、输入不变性检查、负样例与边界条件，覆盖“格式正确但语义错误”。不将官方隐藏答案写入通用策略。
5. **最后做独立全量验证。** 固定源码摘要、模型/provider/reasoning、预算与依赖，使用完整 89 题；批次中不改代码。原始分数与任何故障补评分别公布。并发 4 可作为近期工作配置，但不是已证明的最优值。

本次证据支持保留现有统一执行器和预算框架，对入口隔离、批次健康与验收状态机做重点改造；不足以支持立即推倒重写或用多 agent 扩大请求量。成功标准应是干净全量的原始通过率提高、服务故障不再批量吞题、completed 误报减少，并且代价没有不受控上升。

## 附录：原始全量逐题追踪

每行链接指向本次原始 trial。补评结果在另一份历史汇总，不替换这里的证据。

| 题目 | reward | 停止原因 | 模型/工具 | agent 秒 | 官方失败检查 | 证据 |
|---|---:|---|---:|---:|---|---|
| adaptive-rejection-sampler | 0.0 | server_error | 3/0 | 16.8 | test_ars_function_exists; test_can_generate_standard_distribution_samples; test_has_test_function; test_formal_testing_with_known_truth; test_sample_files_generated; test_implementation_is_modular; test_implementation_handles_errors; test_input_validation_functionality; test_log_concavity_functionality | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/adaptive-rejection-sampler__iqtZpmn) |
| bn-fit-modify | 0.0 | server_error | 3/0 | 21.7 | test_bn_sample_exists; test_learned_dag_structure_exists; test_learned_dag_structure_csv_col_names; test_learned_dag_structure; test_intervened_dag_structure_exists; test_intervened_dag_structure_csv_col_names; test_intervened__data_structure; test_sampled_csv_col_names; test_sampled_data | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/bn-fit-modify__XKFZ3Ex) |
| break-filter-js-from-html | 1.0 | completed | 20/23 | 453.9 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/break-filter-js-from-html__rQYK7bs) |
| build-cython-ext | 1.0 | time_budget_exhausted | 62/92 | 882.1 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/build-cython-ext__U7Qat85) |
| build-pmars | 1.0 | completed | 29/41 | 645.2 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/build-pmars__ozaSwu3) |
| build-pov-ray | 0.0 | time_budget_exhausted | 33/78 | 1811.3 | test_illum1_render_and_verify; test_povray_version; test_povray_built_from_correct_source | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/build-pov-ray__C6nDdHj) |
| caffe-cifar-10 | 0.0 | time_budget_exhausted | 7/13 | 1180.9 | test_caffe_version_and_source; test_cifar10_model_exists; test_prototxt_files_exist; test_cpu_only_training_configured; test_training_completed_500_iterations; test_model_accuracy_verification | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/caffe-cifar-10__crwZeR7) |
| cancel-async-tasks | 1.0 | partial | 24/24 | 358.6 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/cancel-async-tasks__nq8DYFk) |
| chess-best-move | 0.0 | acceptance_unmet | 21/23 | 365.2 | test_move_correct | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/chess-best-move__LW4yNXQ) |
| circuit-fibsqrt | 0.0 | failed | 81/85 | 1505.6 | test_sqrt_fib | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/circuit-fibsqrt__ACPPhc6) |
| cobol-modernization | 0.0 | server_error | 3/0 | 21.6 | test_required_files_exist; test_program_output | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/cobol-modernization__vTT5PyD) |
| code-from-image | 0.0 | server_error | 3/0 | 23.1 | test_output_file_exists; test_output_is_correct | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/code-from-image__Aq8MyVH) |
| compile-compcert | 0.0 | time_budget_exhausted | 30/54 | 1810.8 | test_compcert_exists_and_executable; test_compcert_valid_and_functional; test_compcert_rejects_unsupported_feature | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/compile-compcert__aE3E7yd) |
| configure-git-webserver | 0.0 | server_error | 3/0 | 22.1 | test_hello_html_exists | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/configure-git-webserver__FKwEW6Y) |
| constraints-scheduling | 1.0 | acceptance_unmet | 19/23 | 355.0 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/constraints-scheduling__AGG6i9G) |
| count-dataset-tokens | 0.0 | server_error | 3/0 | 21.2 | test_command_output_content_example | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/count-dataset-tokens__ED8CFWM) |
| crack-7z-hash | 0.0 | time_budget_exhausted | 29/28 | 881.9 | test_solution_file; test_solution_content | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/crack-7z-hash__aLecZLh) |
| custom-memory-heap-crash | 1.0 | completed | 27/39 | 428.3 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/custom-memory-heap-crash__6PJiy4d) |
| db-wal-recovery | 0.0 | partial | 19/36 | 270.0 | test_recovered_data_completeness; test_wal_was_decrypted | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/db-wal-recovery__oHDUxnB) |
| distribution-search | 1.0 | acceptance_unmet | 20/21 | 393.1 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/distribution-search__RSwZkVk) |
| dna-assembly | 0.0 | time_budget_exhausted | 79/86 | 1781.6 | test_primers | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/dna-assembly__QcN2v5c) |
| dna-insert | 0.0 | partial | 35/47 | 831.9 | test_primers | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/dna-insert__o5q9XPV) |
| extract-elf | 0.0 | acceptance_unmet | 19/24 | 346.8 | test_output_matches_reference | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/extract-elf__sUzx7Y6) |
| extract-moves-from-video | 0.0 | acceptance_unmet | 20/39 | 1769.1 | test_solution_file_exists; test_solution_content_similarity | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/extract-moves-from-video__mEEDuWM) |
| feal-differential-cryptanalysis | 0.0 | server_error | 3/0 | 21.7 | test_feal_differential_cryptanalysis_attack | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/feal-differential-cryptanalysis__gW9BMkE) |
| feal-linear-cryptanalysis | 1.0 | completed | 18/27 | 442.8 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/feal-linear-cryptanalysis__rdtqmPP) |
| filter-js-from-html | 0.0 | server_error | 3/0 | 22.2 | test_filter_blocks_xss; test_clean_html_unchanged | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/filter-js-from-html__RfzncbS) |
| financial-document-processor | 0.0 | server_error | 3/0 | 27.3 | test_directories_created; test_invoices_moved_correctly; test_other_documents_moved_correctly; test_summary_csv_exists; test_summary_csv_structure; test_summary_csv_content; test_original_documents_dir_empty | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/financial-document-processor__ggYm9qA) |
| fix-code-vulnerability | 1.0 | completed | 17/29 | 241.7 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/fix-code-vulnerability__H3ieECH) |
| fix-git | 1.0 | acceptance_unmet | 30/36 | 429.8 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/fix-git__FXM9gVy) |
| fix-ocaml-gc | 0.0 | server_error | 4/4 | 52.0 | test_tests_output | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/fix-ocaml-gc__QmpV2Ux) |
| gcode-to-text | 0.0 | completed | 29/39 | 530.7 | test_hello_file_content | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/resume-20260916-150000/2026-09-16__15-02-26/gcode-to-text__ScjeZ6B) |
| git-leak-recovery | 1.0 | acceptance_unmet | 28/37 | 528.8 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/git-leak-recovery__6KaSMmQ) |
| git-multibranch | 1.0 | completed | 54/73 | 644.2 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/git-multibranch__9iRacYp) |
| gpt2-codegolf | 0.0 | server_error | 5/5 | 55.9 | test_gpt2_implementation | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/gpt2-codegolf__A7rcnx4) |
| headless-terminal | 1.0 | acceptance_unmet | 21/21 | 328.6 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/headless-terminal__6HzpsqR) |
| hf-model-inference | 0.0 | server_error | 3/0 | 15.8 | test_model_downloaded; test_flask_api_running; test_sentiment_endpoint; test_api_error_handling | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/hf-model-inference__QTnGcD9) |
| install-windows-3-11 | 0.0 | acceptance_unmet | 54/59 | 1265.5 | test_qemu_running_with_correct_params; test_windows_keys_with_visual_feedback | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/install-windows-3-11__FTSmxcn) |
| kv-store-grpc | 1.0 | acceptance_unmet | 18/20 | 414.3 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/kv-store-grpc__2A7tutW) |
| large-scale-text-editing | 0.0 | server_error | 3/0 | 14.8 | test_apply_macros_exists; test_apply_macros_well_formed; test_apply_macros_runs; test_input_equiv_expected; test_macros_nonempty_and_efficient | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/large-scale-text-editing__RXKeaMp) |
| largest-eigenval | 0.0 | server_error | 4/1 | 37.5 | test_speedup | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/largest-eigenval__oez24sn) |
| llm-inference-batching-scheduler | 0.0 | server_error | 13/18 | 239.8 | test_performance_thresholds | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/llm-inference-batching-scheduler__UAwnZhT) |
| log-summary-date-ranges | 1.0 | completed | 9/11 | 114.0 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/log-summary-date-ranges__6j4A9ef) |
| mailman | 1.0 | completed | 70/121 | 1528.3 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/mailman__ShD4w3k) |
| make-doom-for-mips | 0.0 | server_error | 4/1 | 35.1 | test_vm_execution; test_frame_bmp_exists; test_frame_bmp_similar_to_reference | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/make-doom-for-mips__bh2kWhW) |
| make-mips-interpreter | 0.0 | time_budget_exhausted | 103/141 | 1781.6 | test_vm_execution; test_frame_bmp_exists; test_frame_bmp_similar_to_reference | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/make-mips-interpreter__9LVQy9A) |
| mcmc-sampling-stan | 0.0 | server_error | 4/1 | 40.3 | test_rstan_package_installed; test_hierarchical_model_implemented; test_r_script_created_and_used_rstan; test_stan_model_sampling | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/mcmc-sampling-stan__cmowQ5o) |
| merge-diff-arc-agi-task | 1.0 | time_budget_exhausted | 38/47 | 881.9 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/resume-20260916-150000/2026-09-16__15-02-26/merge-diff-arc-agi-task__4s9Z4G7) |
| model-extraction-relu-logits | 0.0 | server_error | 4/1 | 33.8 | test_stolen_matrix_matches | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/model-extraction-relu-logits__pRPAbS6) |
| modernize-scientific-stack | 0.0 | server_error | 3/0 | 26.0 | test_modernized_code_runs; test_dependency_file_exists | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/modernize-scientific-stack__zSqeQcc) |
| mteb-leaderboard | 0.0 | completed | 29/63 | 808.4 | test_data_matches | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/mteb-leaderboard__UJS8rpA) |
| mteb-retrieve | 0.0 | server_error | 3/0 | 16.1 | test_result_exists; test_data_matches | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/mteb-retrieve__fPtbUzy) |
| multi-source-data-merger | 1.0 | completed | 9/15 | 188.6 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/resume-20260916-150000/2026-09-16__15-02-26/multi-source-data-merger__hCvqe3j) |
| nginx-request-logging | 1.0 | completed | 26/35 | 473.1 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/nginx-request-logging__K8kg4o9) |
| openssl-selfsigned-cert | 1.0 | acceptance_unmet | 17/18 | 398.3 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/openssl-selfsigned-cert__7NqDKCh) |
| overfull-hbox | 0.0 | time_budget_exhausted | 59/92 | 734.6 | test_input_file_matches | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/overfull-hbox__6CTDVAk) |
| password-recovery | 0.0 | server_error | 3/0 | 15.2 | test_recovery_file_exists; test_password_match | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/password-recovery__EZfJ7AC) |
| path-tracing | 0.0 | time_budget_exhausted | 91/146 | 1781.7 | test_image_similarity | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/path-tracing__U5Pv2fv) |
| path-tracing-reverse | 0.0 | server_error | 11/24 | 139.4 | test_image_c_exists; test_image_compiles; test_image_similarity | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/path-tracing-reverse__wujoZ2a) |
| polyglot-c-py | 0.0 | server_error | 3/0 | 14.0 | test_fibonacci_polyglot | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/polyglot-c-py__aWjJjX6) |
| polyglot-rust-c | 0.0 | time_budget_exhausted | 34/33 | 881.3 | test_fibonacci_polyglot | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/polyglot-rust-c__DF2Qjx5) |
| portfolio-optimization | 0.0 | 启动失败 | 0/0 | 1.4 | test_c_extension_exists; test_correctness_small; test_performance_and_scalability | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/portfolio-optimization__VW4NoNY) |
| protein-assembly | 0.0 | failed | 30/41 | 1017.5 | test_gblock | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/protein-assembly__iSBd7ff) |
| prove-plus-comm | 1.0 | completed | 16/18 | 183.5 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/prove-plus-comm__AJWwAJi) |
| pypi-server | 0.0 | server_error | 3/0 | 16.5 | test_api | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/pypi-server__j5R7Yzc) |
| pytorch-model-cli | 0.0 | server_error | 3/0 | 21.7 | test_weights_file_exists; test_cli_tool_exists; test_prediction_file_exists; test_prediction_file_content; test_cli_tool_executable; test_cli_tool_output | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/pytorch-model-cli__XE2ZBqT) |
| pytorch-model-recovery | 1.0 | completed | 15/19 | 484.0 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/pytorch-model-recovery__g42MeaJ) |
| qemu-alpine-ssh | 0.0 | partial | 20/31 | 874.4 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/qemu-alpine-ssh__zPTXnak) |
| qemu-startup | 0.0 | server_error | 4/3 | 40.0 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/qemu-startup__WJm65Zf) |
| query-optimize | 缺失 | server_error | 3/0 | 18.8 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/query-optimize__pvGoMxK) |
| raman-fitting | 0.0 | server_error | 3/0 | 22.1 | test_result_file_exists; test_G_Peak; test_2D_Peak | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/raman-fitting__5FtPDkw) |
| regex-chess | 0.0 | partial | 39/37 | 1235.9 | test_immortal_game; test_game_of_century; test_naroditsky_ivanchuk | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/regex-chess__BfK6vgh) |
| regex-log | 1.0 | partial | 21/21 | 400.4 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/regex-log__zdoUtAC) |
| reshard-c4-data | 0.0 | server_error | 3/0 | 173.4 | test_compress_decompress_workflow | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/reshard-c4-data__BcUsV8q) |
| rstan-to-pystan | 0.0 | server_error | 20/22 | 702.4 | test_output_files_exist; test_alpha_estimation_accuracy; test_sigma_estimation_accuracy; test_rho_estimation_accuracy; test_beta_estimation_accuracy | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/rstan-to-pystan__fA5LZR9) |
| sam-cell-seg | 0.0 | server_error | 3/0 | 16.8 | test_python_file_exists; test_run_script; test_csv_output_exists; test_csv_shape_cols; test_masks_are_no_longer_rect; test_mask_alignment; test_no_polyline_overlaps; test_single_contiguous_mask_per_cell; test_coords_are_flat_lists | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/sam-cell-seg__7QuwCK6) |
| sanitize-git-repo | 1.0 | server_error | 36/49 | 584.9 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/sanitize-git-repo__48x398m) |
| schemelike-metacircular-eval | 0.0 | server_error | 3/0 | 15.5 | test_interp | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/schemelike-metacircular-eval__pnHBTsD) |
| sparql-university | 0.0 | server_error | 3/0 | 18.6 | test_sparql_file_exists; test_sparql_runs_without_error; test_sparql_query_results | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/sparql-university__EuoJrUE) |
| sqlite-db-truncate | 1.0 | server_error | 14/13 | 181.8 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/sqlite-db-truncate__WDRARiP) |
| sqlite-with-gcov | 0.0 | completed | 24/34 | 604.0 | test_gcov_enabled | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/sqlite-with-gcov__N8KW4Cg) |
| torch-pipeline-parallelism | 0.0 | server_error | 5/2 | 50.0 | test_pipeline_parallel_exists; test_no_hooks_in_pipeline_parallel; test_pipeline_parallel | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/torch-pipeline-parallelism__3jfqudc) |
| torch-tensor-parallelism | 0.0 | server_error | 3/0 | 16.6 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/torch-tensor-parallelism__rrUFex9) |
| train-fasttext | 0.0 | server_error | 3/0 | 19.9 | test_accuracy; test_model_size | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/train-fasttext__iGxHoy9) |
| tune-mjcf | 0.0 | time_budget_exhausted | 44/45 | 881.2 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/tune-mjcf__APzKu9p) |
| video-processing | 0.0 | acceptance_unmet | 49/50 | 913.7 | test_jump_analyzer_example_video; test_jump_analyzer_test_video | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/video-processing__PbCPM6U) |
| vulnerable-secret | 1.0 | acceptance_unmet | 15/17 | 173.2 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/vulnerable-secret__b6mykzy) |
| winning-avg-corewars | 0.0 | failed | 53/101 | 795.0 | test_warrior_performance | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/winning-avg-corewars__QtQbWug) |
| write-compressor | 1.0 | acceptance_unmet | 29/32 | 580.8 | — | [trial](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/write-compressor__CrveGxD) |

数据来源：[本次深度统计](./2026-09-17-full89-deep-data.json)、[历史与重评数据](./2026-09-17-experiment-history-data.json)、[原始全量统计](./2026-09-16-full89-final-summary.json)。统计读取原始 result、agent 终态与会话事件、verifier 输出及 task metadata；未修改评分文件，未启动新评测。
