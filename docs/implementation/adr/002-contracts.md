# ADR 002 — 同源契约与真实验收入口

2026-10-06，F02。保留原 Python Harness/CLI，不在协议层导入模型客户端。

维护源是 contracts/v1 JSON Schema 2020-12 和方法/事件注册表。仓库内 scripts/check_contracts.py 生成三份文件；--check 比较实际内容，漂移返回 1。Python package-data 包含生成 bundle，冻结引擎不依赖仓库外源码。TS 类型从结构生成，格式、长度、唯一性与跨字段关系仍由运行时验证。

forge.canonical.v1 以 Unicode scalar 排序键，避免 JS UTF-16 默认排序与 Python 不同。整数限定 ±(2^53-1)，规范配置的非整数用字符串；1.0 与 1 相同。不会从客户端传入的 hash 或 role 推导信任。Bridge 方法表独立于 Engine；特权方法仅受信 Main 调用，未实现 handler 不列入可调用能力。

补齐 48 个 Engine 方法、6 个 Bridge 方法、41 个事件 payload 及 6 种 RunSpec 引用配置的结构；事件采集仍由 F17 负责，RunSpec 引用解析/持久化由 F19 负责。turn 的 state 与 outcome 分离，保留 Harness native_outcome 的后续映射。

scripts/impl.py 注册 unit/portable、task/case、contracts 和 gate。portable 验证前真实编译 TS，prep argv 与 JUnit/hash 均进入 evidence。没有测试、required skip、未实现 verifier/任务绑定不能成功；有效任务未绑定测试返回 fail=1，未知用法返回 3。原生/桌面/live verifier 后续任务实现，当前没有空成功入口。

implementation gate 检查任务、验证状态和 evidence 报告实际 hash；release 还检查阻塞。最终 CI/跨机证据归档由 F29 完成，目前证据大日志保存在 .local，提交脱敏索引。全量开发未完成时两个门禁均不能 pass。

SRT 已知依赖漏洞、Windows 11/Ubuntu 环境、签名和 live 授权阻塞沿用 F01，不阻止此契约任务。测试样本仅作为校验输入，不当作已执行业务或官方 grader 结果。
