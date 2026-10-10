# ADR 011 — 固定 file-worker 与授权观察

状态：代码实现；Windows 11 / Ubuntu 原生验收 blocked。

`python -m forge.engine file-worker` 在解析 Engine 服务参数和加载配置前分派。
每个实际 helper 子进程只接收一个有界请求，校验 workspace 文件身份、冻结 policy hash、
固定文件工具和原有 Pydantic 参数；不会启动 RPC、Hook、MCP 或模型客户端。
工具包延后导入控制模块，原 CLI 的公开工具导出及默认运行行为保留。

文件、搜索、观察和 checkpoint 使用同一个 PathPolicy。保护覆盖 `.git`、`.forge`、
敏感文件和 Engine 提供的控制根；拒绝不支持的链接、硬链接及路径形式。
文件工具读取限定到显式 read_roots，这一额外应用层约束不提升 SRT 的全盘读取能力。
实际读取以打开后的 inode/文件身份和锚点复核；查询不完整不得报告工作区未变化。

Patch 复用现有 envelope/unified 解析器，先核对已观察哈希和全部目标，再逐文件临时写入、
fsync、原子替换并返回实际观察。保留 CRLF 和现有文件 mode。多文件 patch 不承诺文件系统
事务：中途失败且已有修改时为 INDETERMINATE，不自动回放或覆盖外部编辑来回滚。

FileWorkerClient 的 native 路径只能调用已准备的 SrtBackend，固定 Engine argv 和任务
stdin 经限制内的 dispatcher 传输；不拼接任务数据到宿主 shell。显式 local-trusted
路径仅供兼容/可移植真实 IO 验证，native 失败绝不会选择它作为 fallback。
Bridge 原有六方法和 Engine RPC 分离，helper 观察不能决定预算、审批或成绩。

WorkspaceTracker 从授权 scan/snapshot 更新 revision；文件缓存不在控制面读取内容。
CheckpointStore 只持久化 helper 的授权内容及匹配哈希，blob 与 Journal 位于控制根。
递归删除先在 helper 内授权并收集全部后代。受限 checkpoint 拒绝旧宿主 restore，
后续受控恢复动作由 F16 接入；并未把这个拒绝写成恢复成功。

严格模式禁用宿主 Hook/MCP。未迁移的技能/命令/扩展有明确拒绝与 sandbox_covered=false，
Explore 的子工具继承同一 backend。命令、服务、期限和会话生命周期由 F12 接续。
当前原生能力证据未齐，strict prepare 仍拒绝；Windows Engine 安装资产可读授权及
动态敏感路径、Shell/helper 一致性与 OS TOCTOU 检查需要真实平台验收，不由 portable 代替。

实际新增测试使用独立 Python 子进程、文件、NTFS 硬链接、原始字节、真实 Harness、Journal
及 checkpoint；模型源为 scripted，未运行付费模型实验或生成 benchmark 成绩。
