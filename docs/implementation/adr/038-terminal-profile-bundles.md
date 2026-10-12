# ADR038 · 入口组合与 Harness API

terminal/evaluation为已实现的静态Profile，选择core+terminal或core+evaluation依赖组合，延迟导入对应入口。SDK直接调用forge.api，不增加独立服务或插件注册框架。

启动Profile与原生沙盒权限分开。terminal和API共同使用原create_runtime、Conversation、Journal、预算、取消与清理，评测使用现有runner与正式协议。

当前不交付Electron或Web客户端；未来客户端接入已有API或stdio RPC。旧main/renderer通道角色保持兼容，不恢复客户端构建依赖。

实现见[当前架构](../architecture.md)和[T01](../tasks/T01.md)。Linux原生验收、真实模型和训练均独立记录。
