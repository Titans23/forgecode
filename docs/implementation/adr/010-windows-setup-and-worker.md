# ADR 010 — 固定 Windows setup 与未解决 grants 阻断

日期：2026-10-06。任务：F10。

使用锁定 SRT 0.0.78 的真实 checkWindowsSandboxStatusAsync、verifyWindowsWfpEgress、
installWindowsSandboxAsync 和 SandboxManager。安装通过 Main 原生窗口确认与上游 UAC；
不把 boolean approved、工具输出或 Renderer 提供的命令当管理员授权。独立固定 launch
flag 不注册 Bridge RPC；完整 Node / dependency inventory 在 SRT import 前验证。

上游 .d.ts 明确重复 install 会轮换密码；native acl --help 提供 stamp/grant/revoke/
restore/recover，但没有只读共享 holder inventory。现有组件不自动 repair/force/uninstall，
  返回 shared_activity not_observed / blocked。此次只读实际 status 与 help 审计没有管理员安装。

首次 setup 以实际账户 / group / credential / marker 全部缺失为前提；非提权 BFE 返回
cannot-read 不单独阻止首次 UAC。固定安装调用不设置 force；若上游发现冲突须失败。
已存在账户 / group / marker 不刷新。本应用 Main 原生确认不声称其他应用共享活动为零。

WindowsWorkerLease 复用 DirectoryLock；OS Known Folder 定位应用级 worker，多个 Engine
profile 共用。会话 marker 在创建后 fsync，owner 和冻结 policy/native-verifier binding
hash 固定。真实清理确认且 owner/文件身份/body 一致才能删除；unknown 或崩溃保留 marker。
跨进程文件锁测试真实执行，证明本应用 worker 串行，不宣称其他应用共享状态已控制。

Windows deny 目录显式带尾部 separator，避免上游缺失目录 placeholder 被建为文件；
已有 .git worktree 文件保持文件类型。只读授权固定 bundled Node、dispatcher、contracts /
runtime dependencies，禁止授予整个开发仓库；应用控制根与 runtime 根保持分离。
glob 对未来敏感路径的效果不能猜测，真正 native canary 可读即 fail。

Windows / Linux 复用一个受控 native canary 实现，报告不同系统能力与未完成检查。
强 DNS 要求拒绝；原生 cancel/close 没有完整 Job/ACL 证明时仍 unknown。F12/F25 接续。
Main 模块类型检查不是 GUI 原生验收，F13/F14 接入并实测。当前宿主 Windows 10，正式
Windows 11、管理员 setup 和双平台接受均缺环境；真实模型实验未进行。
