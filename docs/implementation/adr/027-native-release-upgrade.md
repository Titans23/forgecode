# ADR 027 — 原生冻结 Engine、成组资源与人工升级

日期：2026-10-07。状态：已实现；Windows 10 开发构建与支持平台原生验收分开。

保留 Python Harness／原 CLI，使用 PyInstaller console onedir 真实冻结 Engine。Main 隐藏 Windows 控制台，file-worker／process-worker／doctor／upgrade／cli／office-mcp 在服务加载前分派。项目 Python 独立发现，不能把 frozen sys.executable 当解释器。外部命令通过专用 worker 清理 DLL／LD_LIBRARY_PATH 与私有启动变量，避免并发修改 Engine 的进程级 DLL 状态。Windows external-worker 在启动命令前把自身纳入非继承、kill-on-close Job；内核退出关闭 handle，强制终止 worker 也终止子树。现有 gated 工具 worker 仍由父进程先分配 Job 再释放 gate。

Main、Bridge、Engine 均验证同一平台资源清单及契约指纹，全部 runtime/dependency/UI/许可/SBOM 文件逐个核对 hash／size，拒绝逃逸链接、重复路径和组件错配。Linux onedir 内部受信任相对链接保留；应用资源与项目工作区的链接政策分别执行。构建记录实际 source inventory，包括未提交的新代码，不把父 HEAD 当作二进制源码版本。

Squirrel NuGet 名称固定为 ForgeCode，与 npm workspace @forgecode/desktop 分开；原安装事件路径提前退出，不启动 Agent。deb 只在 Ubuntu 22.04 最老承诺基线构建。构建出产物不代表真实安装／卸载验收，Windows 10 预览也不代表 Windows 11 已通过。

人工升级先校验成组资源、排空／对账工作、独占数据目录，再用 SQLite backup 获取 WAL 一致快照，流式复制 Journal／artifact／加密凭证。备份记录旧 build／契约／schema／完整 hash；恢复到新目录并验证匹配旧 release，不覆盖现有数据，也不直接让旧版写新 schema。卸载保留用户项目、实验、凭证与共享 SRT。没有自动 updater、全局卸载或管理员修复。

预览 hash 只能证明清单内完整性，不能证明下载真实性；production 验收明确拒绝仅靠可修改 manifest 中的 verified 字段。无签名证书、已知 SRT node-forge advisory 未解除、源仓库缺少项目 LICENSE，均阻止正式发布声明。实际第三方许可随构建分发；未臆造 ForgeCode 许可证。支持平台原生边界／干净系统／签名／真实模型实验缺失继续 blocked。
