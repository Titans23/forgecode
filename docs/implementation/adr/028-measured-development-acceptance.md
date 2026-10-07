# ADR 028：以实际开发证据完成 F28 验收审查

F28 保留固定的 100000 条事件、10000 个 span 与 100 MiB 负载。Journal 基线与 recorder 分别执行相同 fsync append；比较的是预构造 envelope 与实际元数据生成／验证开销，不是端到端模型耗时。真实 SQLite 投影、分页和 React SSR 有界列表单独报告；SSR 不建立图形帧性能结论。

第一轮完整测量的元数据中位数额外开销为 8.07714390646%，超过 5% 目标，保存原报告。随后只编译可证明无动态／相对引用作用域的事件 schema，并将简单 nullable string union 转成等价 type union；保留原始生成 schema、完整 JSON 树校验与 pattern／length／非零 ID 约束。新增测试同原始 Draft202012Validator 比较 envelope 字段和所有事件载荷。性能复测仍保留完整固定规模。

Electron 使用实际 Forge fuse plugin。独立 smoke 读取实际可执行文件 fuse wire，运行可执行的 NODE_OPTIONS 正向探针，测试应用拒绝注入及调试入口，并检查实际凭据 helper 仍可运行。加固后的凭据辅助进程使用 Electron utility process，不依赖已禁用的 RunAsNode。

严格任务在创建模型前建立独立 owned Bridge／FileWorker，失败记录实际 cleanup 并拒绝降级。现有 probe 尚未实现完整的 verified capability promotion，初始化后的清理也缺少完整原生证明。审查不能把这些代码缺口归为仅缺少平台环境；严格沙盒仍受阻。

用户指定本阶段先在 Windows 10 验收，后续 Windows 11 和 Ubuntu 原生验收保留。用户明确授权现有配置中的真实模型，金额预算无上限；已实际完成一次连接请求，保留请求、最终 usage 与费用 unknown，完整 Harness 判分继续 F31。管理员 setup 未授权，签名、用户选择的 LICENSE 和可用 Docker daemon 未提供；依赖高危安全门禁继续拒绝正式发布。F28 完成标准允许明确发布受阻，因此当前阶段验收与生产发布结论分别记录。
