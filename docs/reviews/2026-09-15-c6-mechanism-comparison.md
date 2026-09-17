# 修复机制在 c6 真实评测中的表现

比较对象：修复前 c2（`terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35`）与修复后 c6（`terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01`）。全部只读分析，没有运行任务产物或调用模型。外置日志 payload 通过 `benchmark.analysis.audit_core_run.payload` 校验 SHA-256 后读取。

结论：部分修复在真实轨迹中明确生效，但尚未转化成新增通过题。交付检查提供了改进机会，代理主要补充形式化验证，仍没有解决最终结果正确性。

## 能直接确认的改善

- **空白规范化真实生效。** 本轮至少 10 个引用片段并非原始文本的逐字符子串，但仅空白不同且被成功接受：CompCert 的 task_plan 2 个、protein-assembly 的 task_plan 7 个、distribution-search 的 finish_task 1 个。证据分别在各自会话的 tool_requested 第 11、11、172 行及相应 tool_completed。CompCert 和 protein-assembly 后来受服务故障中断，不能把引用修复的收益等同于最终通过。
- **Heredoc 识别真实生效。** 将本轮验证命令传给修复前冻结源码和当前分类器作纯函数对照，4 条命令从 unknown 变为 behavior：break-filter-js-from-html 会话第 99、146、264 行，video-processing 第 212 行。前两条执行退出 1，后两条退出 0 且证据有效。修复没有把失败命令变成通过。复杂 shell 链仍保持 unknown，build-pov-ray 的 2 条、overfull-hbox 的 10 条和 video-processing 的其余 4 条均未被该局部修复覆盖。
- **预提交检查被实际调用。** `review_delivery` 在 build-pov-ray 第 708 行与 video-processing 第 220 行各调用 1 次，共 2/10 题。两次均成功返回可继续执行的验收缺口；此前仅最终提交时出现的反馈，现在能在提交前得到。

## 尚未解决的闭环问题

| 题目 | review_delivery 后发生的动作 | 最终状态 |
|---|---|---|
| build-pov-ray | 补 1 次 verify；首次 finish_task 参数无效后再次提交。没有修改交付代码 | 自报 completed；内部 partial、acceptance unmet；官方 reward 0 |
| video-processing | 第一次 verify 同时继承并改写断言，被 check_contract_conflict 拒绝；第二次去掉继承后补 verify，再 task_update/finish_task。没有修改交付代码 | 自报 completed；内部 partial、acceptance unmet；官方 reward 0 |

video-processing 的旧验收证据仍 stale。最终验证证明字段与帧序有序、范围合法，不能证明起跳和落地的语义准确。这说明 review_delivery 的输出确实触发后续操作，但后续操作主要完善验证记录，未实现纠正算法的闭环。不能把“增加一个检查工具”当作“代理已学会独立验收”。

本轮仍有 4 次 task_plan 引用拒绝（circuit-fibsqrt、make-mips-interpreter、path-tracing、video-processing，各会话第 14 行），对应引用包含新增省略号、引号或文本改写，超出空白规范化范围。它们不是这次空白修复失效的证据，但说明接口仍容易让模型构造出无效引用，且整个计划可能被拒绝。

检查替代机制有调用痕迹（overfull-hbox 第 408 行含 supersedes），但本次未证明所有历史失败都被有效解除。该题最终仍有 6 个 failed_checks。与 video-processing 的继承冲突一起看，机制存在和模型正确使用机制需要分别评价。

内部交付信号仍很保守：10 题全部 acceptance unmet，包含 3 道官方通过题；5 题自报 completed，只有 3 题官方通过。`passed_checks` 只描述当前记录中的检查执行，不等于完整需求满足或官方通过。不能把该字段作为总成绩代理。

## 服务故障与并发

以下时间为北京时间，来自 model_request_started/finished 时间戳；统计仅覆盖本次评测，不包含其他程序的外部负载。

| 题目 | server_error 请求次数 | 失败响应时间 | 结束方式 |
|---|---:|---|---|
| compile-compcert | 4 | 17:02:39.828；17:03:09.891、11.154、12.961 | 先一次可恢复错误，随后三次连续失败耗尽重试 |
| path-tracing | 3 | 17:02:45.856、47.162、48.957 | 三次连续失败耗尽重试 |
| protein-assembly | 3 | 17:02:59.552；17:03:00.654、02.315 | 三次连续失败耗尽重试 |

十个 server_error 响应集中约 33 秒内。首次错误请求于 17:02:36.706 开始时，本轮仅上述 3 个 agent 存活，观察到同时发出的模型请求为 2 个；CompCert 最后三次请求开始时本轮只剩 1 个 agent、1 个模型请求。因此，不能据“配置并发 6”直接推断服务被 6 个请求压垮。共享服务或上游瞬时故障、外部负载、累积限制等都需要额外服务端证据；现有客户端日志不能区分。

另有 video-processing 两次约 122.6 秒的 timeout 后成功重试，以及 circuit-fibsqrt 一次 empty_model_response。它们应与最终 server_error 分开统计。

## 证据

- [提取后的机制证据数据](D:/projects/forgecode/docs/reviews/2026-09-15-c6-mechanism-data.json)：每题原始会话绝对路径、事件行号、交付检查及后续请求、拒绝原因、验证记录、请求错误和最终交付报告。
- [本轮总成绩](D:/projects/forgecode/docs/reviews/2026-09-15-c6-repair-summary.json)。
- [修复实现和离线验证边界](D:/projects/forgecode/docs/reviews/2026-09-15-repair-implementation.md)。

父子预算、原生接口截断用量等修复在本轮没有可明确归因的触发案例，继续以离线回归作为证据。推理配置记录为 provider_default_unknown；贯通配置不代表本轮启用了某个已知推理档位。
