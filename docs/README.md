# ForgeCode 文档

- [安装与使用](install/terminal-quickstart.md)：安装、更新、CLI、Python API 和沙盒要求。
- [当前架构](implementation/architecture.md)：模块职责、调用链和能力边界。
- [当前工作](implementation/progress.json)：功能状态和待办；开发约定见根目录 [AGENTS.md](../AGENTS.md)。

implementation 下的任务卡、依赖表和测试映射由开发检查脚本使用，不记录逐次执行过程。
测试日志和机器报告写入已忽略的 `.local/implementation`，CI 使用短期构建产物保存。
不为单次安装、测试或清理另建文档，历史改动从 Git 查看。
