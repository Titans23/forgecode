# ADR035：桌面提供显式本机执行模式

日期：2026-10-09。状态：已接受；依据用户原话“新增可选本机执行模式，明确提示无 OS 隔离”。

## 问题

当前 Windows 安装版能启动 Engine，但 strict 的原生准入仍关闭。重复诊断或安装无法修复已证实的动态敏感文件边界缺陷。用户选择在继续严格沙盒研发的同时，提供可以执行日常任务的桌面入口。

## 决策

- 默认仍为 `strict`。新增可选 `local-trusted`，复用已有 `LocalTrustedBackend` 和 Harness；不增加另一个主循环或沙盒后端。
- “环境诊断 → 切换执行模式…”调用 Main 原生选择框，明确说明命令拥有当前用户权限、可访问该用户的文件和网络，**无 OS 沙盒隔离**。Renderer 不接收任意 mode 参数，也不能颁发确认凭据。
- Main 在选择前后确认没有活动任务，正常 drain Engine 并确认退出/清理后，原子写入用户数据目录的模式偏好，再重启客户端。取消、活动任务、过期窗口或退出未确认均不提交切换。不因 strict 失败自动切换。
- 本机模式报告 `degraded` 和真实 mode，UI 显示“本机执行 · 无 OS 隔离”。项目执行授权、既有工具审批、凭证管理和任务预算保留；这些应用层检查不等于 OS 隔离。
- 历史会话保留创建时的策略。模式不符时拒绝继续；新建会话才采用当前模式。切回 strict 同理。
- Windows 安装版把已完整校验的私有 PowerShell/Git/ripgrep 目录供应给本机命令。应用内部 Python/Node 不当作项目 SDK；具体项目的编译器、依赖仍由项目准备。
- desktop 的本机模式必须由 Main 父进程持有。evaluation profile 继续拒绝本机模式；F31 官方 Harbor/Docker、计费和真实模型实验门禁独立。

## 验证与范围

行为测试覆盖默认值、取消、持久化、活动任务、过期请求、退出未确认、模式参数拒绝和历史策略不变；真实 desktop Engine 通过本地 HTTP provider fixture 驱动实际 SDK、审批、读文件、patch 和前后 unittest。模型响应是固定测试数据，文件操作和测试进程真实执行；没有公共模型请求，不构成模型成绩或原生沙盒证明。

实际安装包分别使用两个独立测试配置检查 strict/local 的 Main→Engine 握手、渲染文案、窗口隔离和正常退出。结果、版本、产物摘要见 [本轮证据](../evidence/F28-desktop-local-mode-observations.json)。原来的原生 fail/blocked 记录保留，S2/S3/S4 继续推进。
