# 开发版本安全与发布状态

当前 channel 为 unsigned `developer-preview`。生产发布受到原生平台验收、依赖安全、签名及项目 LICENSE 条件阻挡；验证开发目录和安装包构建不建立生产信任。

客户端通过 Main/Preload 的具名接口访问私有 stdio Engine。Renderer 无任意文件、Shell、RPC 或密钥读回接口；Main 校验当前窗口、frame、origin 与 schema。静态 UI 使用固定资源协议、CSP 和共享 schema 的预编译校验器；目录选择、审批和文件导出由 Main 进行。原子导出拒绝覆盖已有文件、链接祖先和失效窗口授权。

每个 strict turn 单独创建 Bridge 与 FileWorker，绑定 workspace、owner、immutable policy hash。准备成功之前不创建模型；失败保留真实 cleanup 报告。当前原生能力没有完整 verified promotion，初始化后的隔离、动态敏感路径与后代清理仍待实现验证，因此 strict 执行继续拒绝。当前开发演示的 local-trusted 仅证明真实 Harness/工具/Journal 路径。

Electron 实际 fuse 检查关闭 RunAsNode、NODE_OPTIONS、Node CLI inspect 与 file protocol extra privileges，启用 only-ASAR；Windows 开启 embedded ASAR integrity，Linux 不声明支持此项。加固 smoke 读取实际二进制 fuse wire，并通过真实注入正向对照和应用拒绝测试验证。凭证 helper 使用 Electron utility process，安装产物的实际 helper roundtrip 已测；Linux/Windows 目标设备上的系统 keystore、锁定与用户隔离仍需原生验收。

自动测试默认不发真实模型请求。用户已明确授权本次升级使用配置中的真实模型，金额预算无上限；正式记录与显式 live 入口一起约束实际运行，并保留有限请求、工具和时间预算。JSON 自行声称授权不能替代该人类记录。Windows 10 本地 Harness 成对回归、连接探测与正式 Harbor/Linux 实验分别报告；正式环境仍缺少 Docker daemon 和可验证策略，不能因模型可连接而解除。费用缺少可信账单或价格时记 unknown，不虚构零费用。离线 demo、协议夹具和 bundle 复算保留自己的来源，不能作为真实模型成绩。

仓库不会提交 `.env`、凭证和用户 session/data。原始 Journal/SQLite/artifacts 保留在私有数据目录；导出需选择隐私范围，脱敏副本不覆盖 grader 原始证据。未知请求费用保持 unknown，缺失 grader 证据保持 unscored，写入结果不确定时保持 indeterminate。

依赖以实际 lock 与 npm 官方 audit 为准。F32真实audit为2条high依赖记录：SRT和node-forge，关联同一GHSA-86w9-cpqp-85rv（node-forge <=1.4.0的RSA PKCS#1 v1.5验证问题）。audit给出的fixAvailable为将SRT降至0.0.50，不能在缺少目标平台证明时用强制降级替代当前锁定的0.0.78及其Windows职责；没有伪称已修复，production继续blocked。安装说明与最终报告见 [开发构建说明](../install/development.md)、[平台支持矩阵](../platforms/support-matrix.md) 和 [最终审查](../implementation/final-report.md)。
