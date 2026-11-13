# ForgeCode 文档导航

按要完成的工作选择入口。安装和命令用法统一在使用指南维护，实现细节在架构说明维护。

## 使用与扩展

| 需要了解什么 | 文档 |
|---|---|
| 项目定位、安装和最小 SDK 示例 | [项目首页](../README.md) |
| 配置模型、终端会话、权限、后台作业、插件与交付 | [使用指南](install/terminal-quickstart.md) |
| 跟随调用链阅读源码、理解概念和比较同类方案 | [架构 Wiki](architecture-guide.html)，可离线打开 |

## 评测与历史

| 需要了解什么 | 文档 |
|---|---|
| 官方镜像、冻结计划、运行、判分和轨迹导出 | [公共基准指南](../benchmark/README.md) |
| 容器适配、Aider 兼容协议及历史证据归属 | [Harbor 适配与历史记录](../benchmark/harbor/README.md) |
| 两个不可改写的历史协议输入 | [冻结输入说明](implementation/evidence/README.md) |

## 开发与维护

| 需要了解什么 | 文档 |
|---|---|
| 当前内核、可选组件和数据边界 | [架构说明](implementation/architecture.md) |
| 接口 schema、生成代码与兼容边界 | [共享契约](../contracts/v1/README.md) |
| 开发命令、测试和构建 | [使用指南：开发与验证](install/terminal-quickstart.md#开发与验证) |
| 当前范围、已验证基线和未完成项 | [进展](implementation/progress.json) |
| 任务依赖与协作规则 | [任务索引](implementation/backlog.json)、[AGENTS.md](../AGENTS.md) |
| 许可证与上游贡献 | [第三方声明](../THIRD_PARTY_NOTICES.md) |

任务卡保留范围和依赖，逐次开发过程从 Git 查看。临时探针和生成报告放在忽略目录 `.local/implementation`，CI 报告作为 artifacts 保存。历史成绩与当前提交的验证分开；冻结实验输入、测试输入和许可文本保持原版本。
