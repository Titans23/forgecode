# ADR037 · 轻量写入限制与 SRT 移除

日期：2026-10-10。状态：按用户批准方案实施。取代 ADR034/036 中日常模式默认 strict 和 Linux 经过 SRT 的选择；历史证据不变。

新桌面配置首次经 Main 原生确认后保存 `workspace-write`。取消进入只读兼容入口，不保存同意；已有偏好不自动改写。切换仍要求空闲、原生确认、实际清理确认及重启。旧 strict 策略可查看，执行返回不可用，不转换为轻量策略。CLI 保持原有行为，Web 使用可信 CLI 显式指定 `--execution-mode workspace-write`。

Windows 保留锁定 DSH ACL 0.2.1-alpha.1 的底层 runner。Linux 直接以 argv 调用系统 `/usr/bin/bwrap`：宿主根只读、工作区和私有临时目录可写、user/PID namespace、私有 proc/dev、删除 capabilities、继承宿主网络。无自动回退。工具、Shell 和验证命令共用选定后端。缓存与临时写入私有目录，不开放宿主 home 写入。

从依赖、构建和安装资产移除 SRT、node-forge、专用代理/helper 和 socat；strict RPC/设置入口只保留拒绝执行的兼容处理，不访问或卸载共享账户、WFP、凭据及旧授权。构建和质量门禁拒绝锁文件再次引入 SRT/node-forge，安全审计继续使用完整 npm audit。

能力为 partial，读取和网络沿用宿主权限。动态敏感名称没有 OS 读取隔离；文件工具继续拒绝敏感路径及链接。动态硬链接不是 inode 隔离边界。实际观测中 WSL 的动态硬链接可以修改外部同 inode 文件；Windows 该次写入被拒绝，也不能据此提高声明。

锁定 DSH 的受限令牌不能打开 libuv 管道子进程的写端，Windows Node `spawn/spawnSync` 默认捕获 stdio 会 EPERM。原失败证据保留，该能力明确 blocked；继承标准流或文件输出的子进程可用。没有修改第三方运行时、开放全局命名管道或转为无隔离执行。此限制在诊断 UI 和能力报告中显示。

关闭以实际所属进程查询、临时授权撤销和目录不存在为依据；无法确认返回 unknown。工作区常驻授权单列，不当作会话临时资源撤销。watcher、reset 和 runner 返回不作为清理证明。

实际冻结 Engine 工作负载暴露出每次固定 worker 启动都重新校验整套桌面与工具包，三个 Git 子进程会重复读取无关资源。服务启动仍完整校验；process/external/file worker 仅校验其完整 Engine 加载闭包、契约及组件分组，核心工具调用仍校验所选工具闭包。保留原 60 秒任务预算和完整性断言。该修复不代表此前未复现的普通任务基线等待异常已解决。

安装版首次启动另有独立问题：冷目录对照中 Main 在 85 秒内读取约 977 MB，仍未启动 Engine，90 秒验收超时；缓存命中后同一资源校验约 9 秒。完整资源只有约 550 MB，工具和 Engine 被重复校验。Main 改为最多 8 个文件并发验证，每个文件仍检查路径、链接、字节数与哈希；工具目录继续拒绝未列出的文件和链接，去掉已在完整 inventory 中校验过的重复哈希。无跨启动缓存、跳过完整性检查或延长超时。冷目录验收另行记录，不能用热启动通过替代。

F31 按用户要求暂缓，正式实验关闭且继续拒绝 workspace-write/local-trusted。Ubuntu 22.04/24.04 原生 GUI/安装、签名、项目许可和既有性能门禁独立保持；WSL 和 hosted CI 不替代它们。无付费模型调用或本机管理员设置。

当前验证和未解决事项集中于 progress、handoff 及 F28 任务卡。扩展仅新增 SandboxBackend 实现并在工厂明确选择，不新增注册中心或调度层。
