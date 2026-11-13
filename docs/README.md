# ForgeCode 1.0.0 文档

| 文档 | 用途 |
|---|---|
| [项目首页](../README.md) | 定位、安装与主要入口 |
| [使用指南](install/terminal-quickstart.md) | 配置、会话、权限、SDK、插件与升级 |
| [当前架构](implementation/architecture.md) | 内核、可选能力、数据流和生命周期 |
| [架构 Wiki](architecture-guide.html) | 概念与实例、插件和评测、源码追踪，以及 Codex / Claude Code / DSH / pi 对比；可离线打开 |
| [公共基准](../benchmark/README.md) | 官方镜像、冻结计划、独立判分和导出 |
| [Harbor 适配说明](../benchmark/harbor/README.md) | 容器接入与历史 Aider 协议 |
| [共享契约](../contracts/v1/README.md) | JSON Schema、TypeScript 和旧 RPC 接口 |
| [当前进展](implementation/progress.json) | 已实施能力、最近验证和未完成范围 |

开发约定在 [AGENTS.md](../AGENTS.md)。任务卡只描述范围和依赖，逐次改动从 Git 查看；自动日志和报告放在忽略目录 `.local/implementation` 或 CI artifacts。

历史成绩、冻结任务、许可文本和测试输入保留原始版本及字节。发布版本 `1.0.0` 不改变插件 API、Journal、ATIF 或 RPC 的独立格式版本，也不把历史验证升级为当前版本验收。
