# 2026-09-06 固定十题、并发 10 评测分析

## 结论与证据边界

本轮不合格，也不是完整有效轮次。8 题已有官方结果：3 个 reward=1、5 个 reward=0；另 2 题 agent 已退出，但缺少官方验证结果。3 个原始通过中 overfull-hbox 同时发生 Harbor AgentTimeoutError，因此无异常的已知通过是 HTML 和 distribution-search 两题。必须保留原始 3 个 reward=1，不能把缺失结果写成 0，也不能把 3/8 宣称为十题成绩。

本次更值得优先处理的是运行时回归：新增验收要求机制让同一原文的不同措辞不断生成新 ID，而失败检查缺少安全的修订与替代流程。POV-Ray、protein、video 和 overfull 均出现反复验证、声明完成、被拒绝的循环。Windows/Linux 各 603 项单测通过没有覆盖这个长程交互问题；上一轮修复的验收关联设计需要修正，不能因为单测通过就认为设计已经可靠。

模型协议方面，9 题可恢复的会话中共有 609 个请求收尾事件：607 completed，2 次到达任务期限时取消；未观察到上轮的 empty_model_response 或 incomplete_tool_call。但 overfull 缺少导出的会话，且这不是控制变量实验，不能证明供应商问题已消失或全部改善由代码造成。

分析只读取运行记录和代码，没有修改候选快照、补写官方成绩、恢复 verifier、停止容器或启动新一轮。

## 本轮身份与运行状态

- 运行目录：[2026-09-06__12-54-44](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-runtime-v2-reliability-c10-20260906-125422/2026-09-06__12-54-44)
- 候选快照内容 SHA-256：`987dc372efdbd7c7c4b0ba3064480697f6105f34be9baf4c22ef23d48af61810`；基于 390af0b 的未提交修复，不能仅以 HEAD 标识候选。
- 固定十题；并发 10；gpt-5.6-luna；每题内核预算 120 次模型调用、240 次工具调用、1800 秒。Harbor 另有数据集原生外层超时，两者不能混为一谈。
- 12:54 启动；13:26:18 是 job.log 的最后写入时间，最后一份已完成题结果为 13:26:50 的 CompCert。
- 13:30 首次检查时本地 Python/Harbor 控制器已不存在；13:44 复核，两残留容器只有 sh/sleep，无 agent/verifier。POV-Ray 在 13:27:45、protein 在 13:29:26 写出 exit_code=0、timed_out=false。
- 两题的 kernel 实际均为 failed / time_budget_exhausted，约 1800 秒。进程退出码 0 表示程序成功输出结构化结果，不表示任务成功；timed_out=false 也只说明外部 timeout 命令未以 124 退出。

由此能确认控制器未完成验证和收尾，不能确认具体退出机制。控制器 stdout 仅有快照路径，stderr 为空；所查 13:20–13:31 Application 错误事件未返回可归因的记录。没有证据可认定 OOM、用户终止、Docker 崩溃或并发 10 是原因。先前用 Start-Process 启动的链条未记录最终 exit code，也缺少持续存活记录，这是诊断盲区。

## 逐题结果

表内模型/工具用量取 kernel 最终 payload；缺失标为未知。

| 任务 | 官方 reward；测试 | kernel 终止 | 模型/工具 | 内核耗时 |
|---|---|---|---:|---:|
| HTML | 1；1/1 | completed | 38/42 | 978 秒 |
| distribution-search | 1；4/4 | completed | 18/19 | 428 秒 |
| overfull-hbox | 1；4/4 | payload 缺失；Harbor 超时 | 未知 | 外层上限 750 秒 |
| circuit-fibsqrt | 0；2/3 | tool_protocol_exhausted | 67/77 | 1012 秒 |
| CompCert | 0；2/3 | time_budget_exhausted | 88/106 | 1800 秒 |
| MIPS | 0；0/3 | model_budget_exhausted | 120/204 | 1360 秒 |
| path-tracing | 0；3/5 | failed | 19/33 | 371 秒 |
| video | 0；3/5 | failed | 73/70 | 1541 秒 |
| POV-Ray | 缺失 | time_budget_exhausted | 112/123 | 1800 秒 |
| protein | 缺失 | time_budget_exhausted | 74/83 | 1800 秒 |

每题证据位于运行目录对应子目录：`result.json` 是官方结果；`verifier/ctrf.json` 和 `test-stdout.txt` 是官方断言；`agent/forgecode.txt` 是内核终止记录；`agent/session-*.jsonl` 是请求、工具和任务状态证据。

### HTML：通过，但本地证据表达仍有歧义

官方浏览器检查通过；最终产物 out.html。模型经历 pytest 缺失、测试硬编码 /tests/filter.py 路径和最初 alert 未触发等失败后完成修复。最终检查声称无需用户交互，但诊断中出现 browser_interactions_before_alert=1，不能仅凭 coverage 文本认定该字段的行为语义。官方通过有效；这同时说明 requirement_ids + expected_source 只是关联与声明，不是行为语义证明。

### distribution-search：本轮最稳定的成功

4 个官方测试全过，18 次模型请求、19 次工具调用，约 428 秒完成。要求数量稳定为 6，没有同源措辞膨胀。它证明当前门禁存在可完成路径，但不能排除复杂任务中的死循环。

### overfull-hbox：产物通过，收尾耗时异常

官方 4/4，明确比上一轮 reward=0 改善。Harbor exception 写明 `Agent execution timed out after 750.0 seconds`，agent 阶段约 12 分 35 秒结束，而不是内核 1800 秒。最终 payload 和导出 session 缺失，不能报告其精确用量。

forgecode.txt 多次记录 verify 成功、finish_task 失败，模型解释原有要求不断出现重复 ID，绑定数量从 3 增至 6、9、12，后续称已达 17。还出现检查器把保护文件 SHA-256 抄错、纠正后仍不能完成的轨迹。ID 数量来自模型文本，可信度低于另外三题可直接检查的 task_state，但循环本身由工具日志确认。这里应修复收尾，而不是延长外层 750 秒掩盖问题。

### circuit-fibsqrt：计算错误是主因，失败协议又产生次生错误

gates.txt 有 25,464 行，满足 <32,000，存在性与大小测试通过；官方模拟测试输出反复为 0。模型自己已发现 208 和 20000 应为 377、1407432322，实际都是 0，因此不能归咎于隐藏测试。

最终模型试图报告 failed，但仅在 summary 说明理由，缺少 blocked_reasons；Pydantic 校验拒绝，最后触发 tool_protocol_exhausted。参数描述只强调 blocked 需要 blocked_reasons，实际 validator 对 failed 也强制要求，造成误导。改进失败出口只能修正终止语义，不能把错误电路变成通过。算法层需先用独立模拟器验证常量、位序、引用、加法、平方根、Fibonacci 等小构件，避免反复重写整张门表。

### CompCert：已生成编译器，但链接运行库缺失

本轮已达到 ccomp 可执行阶段，不能沿用上一轮“没有 ccomp”的结论。官方功能测试报 `/usr/bin/ld: cannot find -lcompcert`，无法完成正向编译链接。最后轨迹在检查 runtime、compcert.ini，重新配置 libdir 后 make -j2，最终时间耗尽，含 1 次 indeterminate、4 个取消工具。

工具耗时约 1263 秒，模型耗时约 522 秒，与 POV 的模型交互瓶颈不同。应围绕配置、编译器、运行库、安装路径、编译并运行最小 C 程序分阶段验证，保留有效中间构建，优先解决链接路径和实际安装产物，不盲目增加预算。

### MIPS：预算耗尽前仍未生成帧

120 次模型调用耗尽，官方等待 frame.bmp 超时，三个测试均失败。最后仍在修补启动 ABI、guest fopen、IWAD 文件发现和路径解析，出现针对具体 PC 地址的兼容分支。说明执行基础设施和应用启动路径尚未可靠；仅增加请求数会继续扩大探索成本。

建议将指令语义、分支延迟槽、内存端序、syscall/文件语义、ELF 启动 ABI、完整帧生成分阶段验证。诊断可参考本题暴露的层级，但不把特定地址或题目捷径写入 ForgeCode 内核。

### path-tracing：依赖原有程序的包装器，隔离执行失败

模型生成约 122 字符 C 包装器，调用预置 orig 程序；本地比较声称 similarity=1，但这不是自包含的重建。官方隔离环境没有生成 /jail/reconstructed.ppm，导致产物和图像相似度测试失败。模型最终主动报告依赖既有可执行文件不满足约束。

这是策略与可移植性问题，本轮未再出现 incomplete_tool_call。应将“仅携带交付产物到干净环境运行”作为通用检查方式，分别核对参考数据可读性、可执行依赖和目标输出；不能用禁用某个文件名的启发式替代完整依赖验证。

### video：真实定位错误与验收循环同时存在

官方 example 要求起跳帧 50–54、落地 62–64；产物输出 55/61，起跳已越界，落地也不在给定范围。测试视频要求起跳 219–223，实际 104，明显不是单帧容差问题。文件、导入和 TOML 结构通过，核心时序定位失败。

本地检查只证明区间有前景运动、升高且先后有序，不能证明找到真实起跳/落地；检查与实现依赖相似的运动启发式，独立性不足。模型最终声称“实现正确，外部 wrapper 阻塞”，这不是事实结论。

另一方面，task_state 确认 4 条原文解释增长为 33 条要求：脚本路径 3 条、输出契约 12 条、起跳 10 条、导入限制 8 条。21 次 finish_task 调用，第一次后仍有 43 个模型请求、2,290,355 输入 token。单次反馈被归档前达到 32,627 字符。该回归确实浪费了后期纠错机会，但不能推断移除门禁就能通过。

### POV-Ray：本地渲染成功，反复收尾后到期，官方未知

最终检查显示安装可执行文件、源归档身份、保护输入哈希、渲染返回码 0 和统计输出均成立，但没有官方参考图对比，不能认定通过。原始 3 条原文衍生 15 个 ID：源/安装 6、渲染 5、输入保护 4。16 次 finish_task；第一次声明完成之后又有 84 个模型请求，4,854,620 输入 token、54,708 输出 token。

早期检查故障包括 python3 不存在、JSON 不是最终 stdout 行、脚本执行权限以及渲染退出 53。后续修正脚本形成不同签名，旧失败仍被要求重新验证。最终脚本出现 source_tree_duplicate_3、stats_present_duplicate_4 等重复字段，是验收身份膨胀迫使检查器追逐形式的直接证据。

### protein：序列已写出，本地有限检查成功，不能推断生物学正确性

最终本地报告为 2535 nt、四个内部 GS linker，50 nt 滑窗 GC 0.38–0.60，gblock.txt 已写出。无官方 verifier，不能认定蛋白身份、PDB 序列一致性、结合能力等完整要求通过。本次仅分析执行记录，不独立评价生物学设计。

登记原文只覆盖文件、连接子、长度三类，改写为 6 个 ID；更丰富的身份/序列要求没有因此变成完整合同。早期 checker 有缺少 GAT 的密码子表错误，以及断言失败；后期成功检查却还被旧检查义务阻塞。13 次 finish_task，首次之后 21 个请求、1,399,175 输入 token，最终耗尽时间。

## 根因分层与修复优先级

### P0：要求身份与完成操作耦合，导致非幂等收尾

代码定位：[state.py](D:/projects/forgecode/forge/tasks/state.py:12)、[manager.py](D:/projects/forgecode/forge/tasks/manager.py:116)、[runner.py](D:/projects/forgecode/forge/runtime/runner.py:513)。

anchored_criterion 用 task_id、source_quote、condition 共同计算 ID；_merge_acceptance 永久保留旧解释；finish_task(completed) 又在判断完成之前注册新解释。于是“重新措辞的完成声明”会改变待验收集合。模型认为沿用原要求，内核却增加新义务。单测只证明相同文本身份稳定、旧要求不被删除，未证明同源改写不会无限增长。

改进应把来源要求 ID 与解释版本分离；完成声明以只读方式引用已经登记的 ID。允许一个来源包含多个明确子要求，用稳定 clause identity 区分，不能简单只按整段 source_quote 合并。显式补充/修订是独立状态变更，返回差异，不在 finish 时隐式添加；审计保留历史版本。增加重复完成、同源改写、压缩恢复、跨工具登记的长程契约测试。

### P0：失败检查与检查器修订缺少可审计解除路径

代码定位：[completion.py](D:/projects/forgecode/forge/runtime/completion.py:408)。义务 key 包含完整 command、cwd、stdin hash、check_signature；旧失败只能由完全相同 key 的成功解除。更正 JSON 字段、断言元数据、stdin 中错误字典甚至命令格式，都可能产生新 key。对于自身含错的检查器，重跑旧检查不是可行解决方案。

不能直接让“任何新成功”清掉旧失败，这会放过真实回归。应引入稳定 Check ID 与版本、明确 supersedes 关系，区分环境未执行、检查器错误、产物断言失败、证据过期。替代需要保留原要求映射、输入与范围、断言/阈值差异、修订理由和真实新运行记录；删断言或放宽阈值不能静默消除义务。环境缺依赖应作为可恢复执行状态而非自动成为永久产物失败。

### P0：控制器生存与阶段收尾缺少独立审计

启动器必须记录 PID/创建时间、退出时间/退出码、stdout/stderr 与阶段事件；控制器丢失应显式标记 interrupted，不能继续显示 running=2。恢复前先封存日志和产物，确认没有 agent/verifier 并行执行。若有独立恢复验证能力，记录为同快照产物的恢复验证，并保留原中断事实，不把它悄悄改写成未中断有效轮。

外层任务 deadline 应写入启动清单，同时列明内核和 cleanup 上限；按最近的有效截止时间给 agent 明确预算，并在取消路径持久化会话与 usage。不要为匹配“每题 1800 秒”的描述擅自修改数据集原生 750 秒上限。

### P1：失败出口描述与 validator 不一致

[finish.py](D:/projects/forgecode/forge/tools/finish.py:18) 对 failed 强制 blocked_reasons，名称和描述都偏向 blocked。可引入语义清楚的 failure_reasons 并兼容旧字段，或允许 failed 从已有 summary 提取原因；终止声明的补齐不能消耗业务工具参数纠错预算直至覆盖真实失败。保留明确失败结果，不把它伪装成完成。

### P1：缺失官方结果导致用量漏计和故障信息丢失

[summarize.py](D:/projects/forgecode/benchmark/harbor/summarize.py:91) 只在遍历已有 result.json 时收集 payload，缺失目录只读取状态文件。因此 POV/protein 显示 AgentExit0，却遗漏明确的 kernel time_budget_exhausted，也遗漏其全部用量。

应先扫描所有 trial 的 request/session/kernel 记录，再单独叠加官方 result 和 verifier 状态。进程退出、内核终止、官方验证、控制器状态是四个独立事实。主分类可为收尾中断，但 observed_faults 必须保留内部 timeout；有 request_id 时按 attempt 去重，不能把生命周期统计和最终 payload 重复相加。缺会话的 overfull 应保持 usage_unknown。

### P2：任务策略与通用验证质量

优先纠正上面的运行时回归，再处理算法/构建策略。建立小构件验证、阶段交付检查、干净环境复现和与实现相互独立的行为证据。登记要求数量不等于语义覆盖：protein 登记不全、video 弱运动证据、path 的外部依赖都说明形式完整不代表行为正确。

## 成本复核及与上轮比较

当前 summarize 输出 423 次模型、551 次工具、19,941,347 输入、216,346 输出，只含已有官方 result 且有 payload 的 7 题。补入 POV/protein 后，9 题已知总量如下；overfull 未知，因此均是本轮下界，不是完整总账。

| 指标 | 当前汇总 | 9 题已知下界 | 漏计量 |
|---|---:|---:|---:|
| 模型调用 | 423 | 609 | 186 |
| 工具请求 | 551 | 757 | 206 |
| 输入 token | 19,941,347 | 27,229,371 | 7,288,024 |
| 输出 token | 216,346 | 336,536 | 120,190 |

另有 cache_read_input_tokens 2,447,360，不将其擅自折算为费用。上轮记录为 401 次模型、530 次工具、15,209,058 输入、173,731 输出。本轮仅已知下界已分别增长约 51.9%、42.8%、79.0%、93.7%；统计覆盖不同且缺 overfull，不能宣称精确账单涨幅或归因于并发变化。

POV/protein/video 在首次 finish_task 后合计仍发起 148 次模型请求，消耗 8,544,150 输入、122,544 输出 token。这是“首次完成尝试后的花费”，包括真实补验与修正，不应全部称为浪费，但与重复门禁反馈、ID 膨胀共同构成强烈成本证据。

原始通过从上轮 2 增至目前 3，新增 overfull；但两题缺验证、overfull 有外层超时、整体成本显著上升，不能据此认定优化成功。并发从 6 改为 10 同时伴随候选代码变化，且无资源利用率/服务排队证据，不能声称并发 10 提升或降低了通过率。

## 下一轮之前的可验证门槛

1. 先为同源要求改写、finish 非幂等、检查器修订无法解除义务、缺 result 用量漏计写出旧代码失败的回归场景。
2. 用本轮四条收尾轨迹做离线重放：重复 finish 不增加要求；真实产物失败仍阻止完成；合法 checker 修订有审计链且能终止；不能靠弱检查删除旧义务。
3. 控制器异常退出、外层 timeout 与 session 导出分别做进程级测试；保证不会把空闲残留容器当成活跃 agent。
4. 保持固定十题、并发 10 和现有预算，完成 Windows/Linux 全量与长程验证后冻结新候选，再评测。不得以增加模型次数、吞掉失败或写入题目特判替代修复。
5. 连续两轮完整有效且同一冻结版本 >=5/10 的总目标仍未达成。
