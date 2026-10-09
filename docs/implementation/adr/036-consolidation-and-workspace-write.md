# ADR036：统一 ForgeCode 与显式原生写入限制模式

日期：2026-10-09。状态：已采纳，按用户批准的合并、减重与轻量沙盒方案实施。

## 决定

源码与历史统一到 ForgeCode，日常开发主线为 `main`。Windows 唯一活动目录为 `D:\projects\forgecode`，WSL 使用独立的 `/home/titans/learn_project/forgecode`。整合在 `codex/forgecode-consolidation` 完成，验收后正常合入主线，保留旧分支、迁移增量和私有数据备份。V4 仍作为历史设计文档的版本名称，不再作为第二套产品或活动目录。

保留 Harness、CLI、协议、研究工作台和可选评估入口。减重删除已核实无调用的五个函数与空占位模块；通用文件选择移入 `forge/file_selection.py`；持久事件由 Store 写入，调度器复用；stdio/Web 共用显式应用组装。验证批次构建一次，再校验资产清单并复用产物；源码变化使批次失效。单独运行检查仍保留准备步骤。

## 三种执行模式

| 模式 | 默认及用途 | 承诺 |
|---|---|---|
| `strict` | 默认；保持 ADR034 | 原有读取、写入、网络、清理门禁；证据不足拒绝执行 |
| `workspace-write` | 用户原生确认后选择；显示“原生沙盒：写入限制” | 有限原生写入限制；读取沿用宿主权限，网络沿用宿主能力；不保证动态 `.env` 的 OS 读取隔离 |
| `local-trusted` | 用户原生确认后选择；保持 ADR035 | 无 OS 隔离 |

旧会话、策略快照和公开 CLI 保持原义。新策略用 `filesystem.read_mode=host_default`、`network.mode=inherit` 明确表达轻量语义；不能拿严格策略隐式降级。模式切换仍须原生确认、空闲、关闭、清理确认、保存和重启。初始化失败没有普通进程后备路径。F31 官方执行只接受 strict 的 Harbor/Docker 路径，明确拒绝另外两种模式。

## 平台接入与证据

Windows 锁定 `@deepseek-ai/dsh-sandbox-windows-acl@0.2.1-alpha.1`，参考 [DSH 固定源码](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/sandbox/sandbox-windows-acl/README.md)。只调用 ACL grant 与 runner，不启动 DSH Harness 或插件调度器。上游包的运行依赖进入同一资产清单。使用 WRITE_RESTRICTED/Low token、工作区常驻 grant、每次执行独立 temp SID；工作区 grant 的常驻状态明确记录。

父侧使用既有 gated worker 和 Windows Job，实际查询 ActiveProcesses=0，再撤销临时 grant、删除并确认所拥有的临时树不存在。临时目录采用明确的当前用户 SID，解决 Python 0700 的 OWNER RIGHTS 与受限令牌的兼容问题。文件工具保持应用层敏感路径、链接和身份检查。Shell 在该令牌内改用匿名管道适配 Python 异步 named pipe 的实际拒绝，仍受 Job、输入输出和时间预算管理。

Linux 使用现有 SRT 0.0.78 的 bubblewrap 文件系统封装，允许工作区与独立临时目录写入；沿用宿主网络，不创建网络代理，也不增加后端回退链。父侧继续使用现有 subreaper，核实后代回收及临时树移除。参考 [Pi 固定示例](https://github.com/earendil-works/pi/blob/ce950d78f424dcaf9f5d6a03ce80ab141130eb1d/packages/coding-agent/examples/extensions/sandbox/index.ts)。缺少可用 bwrap/user namespace 时拒绝准入。

每次准备执行真实工作区写入、边界外拒写、临时文件和 stdin canary；能力报告的写入限制为 `partial`，不能满足严格的 `verified` 门禁。DSH 的 Everyone/环境 ACL、硬链接及控制台限制保留；同名动态敏感文件的旧失败记录仍有效。watcher、reset、runner 返回和父 PID 退出都不证明清理完成。失败或超时无法核实则返回 `unknown`，保留证据与资源位置。

Harbor stop/down 返回只作事实回执。F31 清理新增独立 Docker 查询，绑定 daemon ID 和 Compose project，查询容器、网络、命名卷以及停止前实际挂载的匿名卷；同一 daemon 上确认资源不存在才能记 clean。查询失败、缺少停止前清单或 daemon 改变保持 unknown；查到残留返回 residual。

## 验证与边界

`python scripts/workspace_sandbox_probe.py --output PATH` 是不调用模型的原生轻量验收入口，和严格 sandbox doctor 分开。Windows、WSL、Docker 的环境、源码和结果分别记录于当前 handoff/progress 索引。合入开发主线不等于严格沙盒、目标 Ubuntu 桌面安装、签名发布或正式实验通过；缺失资源的能力保持关闭。
