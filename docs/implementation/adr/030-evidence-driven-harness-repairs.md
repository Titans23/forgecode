# ADR 030：按实际失败修复 Harness

## 状态

已实现并通过正式回归；原生验收与真实模型实验分别 blocked。

## 依据

当前分支的11项行为测试先运行：7 fail、4 pass。真实 loopback 在取消被旧 client 吞掉后收到一次请求；两种过期成功遮蔽当前失败；实际修复上限2、终态却报告剩余8；适配器未接受已有 task_policy。父子累计usage只计一次、共享请求上限、长工具输出归档/全压缩后配对与原约束引用，以及固定预算repair0/2路径本来通过。

## 决定

请求 observer 首先检查当前 asyncio Task 的 pending cancellation。保持实际请求和usage边界，不新增重试。完成展示只让当前revision/epoch证据解除当前失败；不在 checker_revision_covers 增加跨turn数值假设，因为新turn的revision会归零。

有效repair cap集中在TaskPolicy，仍最多2次、只修不同gap集合、消耗同一parent budget。终态remaining引用有效值。Conversation和HarnessAdapter输出实际参数名/配置键/默认值/上限/有效值/共享预算capability；Adapter把可选策略交给已有create_runtime，不创建新主循环。通用默认0、既有Harbor显式2保留；Harbor冻结配置超过2仍拒绝。

另有真实Git行为证据：整个workspace被父仓库忽略时，未watch的外部文件修改不被Git status发现。该场景before1 fail/2 pass；每次观察先用实际check-ignore判断workspace是否可由Git观察，ignored/无Git时使用已有内容hash文件扫描。仍排除运行时控制数据，不纳入父仓库文件。

## 验证范围

新增测试使用真实TCP、Python工具、文件、Journal、已有Harness/Adapter与上下文压缩，只替换模型。repair0与repair2固定model5/tool2/wall30，不同时放大实验预算。没有付费API、官方grader成绩或原生沙盒通过声明。外部运行仍需独立预算授权、兼容Docker/native环境及真实policy reconciliation。

正式contracts2/quality4/unit190/portable353/regression1287 pass，零skip；before/after、原始候选夹具错误、报告hash、源码hash与F29实际GitHub CI失败详情见 ../evidence/F30-harness-observations.json。CI跨平台问题由F28继续处理，本机通过不推广为远端通过。
