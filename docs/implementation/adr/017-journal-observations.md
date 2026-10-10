# ADR 017 · 真实 Journal 观察与可信查询投影

状态：代码已实施并通过可移植／开发版回归；原生与发布验收 blocked。
测试父 HEAD bc27f9780b179097d0e2bb9f732e93748ce3709c。

继续使用 SessionJournal append / fsync 与原 Runner / Executor / BudgetedModelClient。
规范观察作为 observation 记录追加，不解析模型文本或工具 stdout。事件与原始 record UUID、sequence、hash
绑定；SQLite 只在首次插入后更新派生 spans / model_requests / usage_ledger。幂等回放不执行工具、
不新增 token 或费用。重复逻辑请求、父 span 或来源身份冲突回滚并隔离。

受理事务创建 turn 根 trace；真实输入快照、实际请求尝试、最终工具参数、验证与完成接入原边界。
工具 intent 写盘成功前不调用 backend；审批与工具共享 execution identity。
Explore 复用父 recorder / Journal、独立 branch 与真实父预算；摘要和重试保留父子关联。
恢复创建新的 trace 并 link 旧 trace，保留 unknown 副作用，仍要求 reconciliation，不自动重跑。

JournalProjector 默认 imported；只有 private data/harness 内由 Engine 产生且业务身份一致的 observation
可以作为内部事实。外部 import 使用 import: 命名空间，不能占用内部 producer，不能自行提升 origin。
工具输出只记录实际字节／丢弃计数；普通模型文本合并字节块，无原文和隐藏思考属性。
完整模型输入和原始工具结果仍属于私有可恢复 Journal，F18/F24 管脱敏与确认导出；不声称这些原文可公开。

模型 request ID / invocation / retry attempt 来自真实调用边界；未知 returned model / usage 保持 null / unknown。
固定预算记录当前观察的剩余量，无上限用 null，费用保持未知，F18 接续计价。
上下文消息指纹是当前快照派生身份；不冒充 provider message ID。压缩记录真实字符量与明确未知的 token 估计。
验证开始使用 pending evidence identity，完成绑定实际 native verification UUID，并保留 pending 关联。
下一 turn 显式失效旧证据，不将旧 numerical revision=0 当作新证据。

本地映射 forge.otel.genai.v1 固定到官方 GenAI 文档提交
[cb10b70](https://github.com/open-telemetry/semantic-conventions-genai/blob/cb10b70c15c099ccab144e8316d934c9699da0fd/docs/gen-ai/gen-ai-agent-spans.md)。
未有标准映射的机制使用 forge.*；本任务不启动 exporter，F18 负责有界异步 OTLP。

SRT prepare / denial / cleanup 使用真实 capability/ownership；原生命令 execution 是 Harness 工具 span 的子项，
输出保留 Bridge source sequence。当前原生运行器不可用，child execution 原生验收继续 blocked。
可信 grader 回调记录实际返回、输入产物与 grader bytes 的 hash，评分协议／受理／完整评测由 F19—F21 接续；
此观察不会直接写 grades，也不会修改任务预算或结果。
