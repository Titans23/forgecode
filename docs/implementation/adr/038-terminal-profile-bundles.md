# ADR038 · 终端分支与按入口组合

2026-10-12，用户指定 codex/terminal-harness；取代本分支旧 V4 的客户端产品目标。

默认交付是原 Python Harness、终端和真实轻量沙盒。删除桌面及运行 Web 的源码和专用依赖/验证，不继续旧桌面 backlog。保留独立的原生验收和模型实验记录。

采用 DSH 的基础 Bundle 加入口 Bundle 思路：terminal = core + terminal；evaluation = core + evaluation。`forge.launch.PROFILES` 是明确的当前启动清单，按选择导入入口，无插件发现或补丁解释器。后续客户端和 SDK 可调用 `forge.api`；本轮没有 GUI/Web 服务。

RunProfile 只声明有限模型/工具/时间预算；SessionOptions 明确沙盒模式、审批策略、文件权限范围及独立数据目录。Profile 不授权模型支出、主机访问或管理员操作。原评测容器与协议适配保持独立；普通终端不会加载 Harbor/训练框架。

沿用 create_runtime、Conversation、TurnRunner、ToolExecutor、Journal 与 checkpoint。外部消费者接收原有事件和 TurnResult；JSON 导出带版本，opaque provider continuation 不导出。取消使用当前 asyncio 任务，清理由现有 owned Job/subreaper 与沙盒负责，未知清理继续报告未知。

本机已安装工具是明确前置条件，沙盒 JS 依赖使用 npm 锁及完整内容清单。移除桌面冻结资产路径，不自动下载系统工具或管理员安装。Windows 使用已有 DSH AclSandbox；范围 SID 包含规范化写目录，历史较宽 grant 不会授权新范围。Linux 继续直接 bubblewrap argv。读取/网络继承宿主和动态硬链接限制明确保留。
