# Windows 原生沙盒

基准环境为本机 Windows 10 x64 22H2（build 19045）、NTFS、固定 PowerShell 7。
用户于 2026-10-08 将必需 Windows 验收目标改为本机，不再要求 Windows 11 设备。
应用 Node / helper 来自 release-lock 与完整
运行库存；不从项目 PATH 查找安装器，不使用 WSL / Docker，不安装 MITM CA。

Python 与 Bridge 统一接受 build 19045 起的 x64 工作站，保留既有 Windows 11 运行路径；
Windows 必需证据门禁只接受本次指定的 Windows 10 原生报告。Server、ARM64 和旧版
Windows 10 不满足本基准；OS 符合要求不等于隔离能力 verified。

本机已确认 OS、锁定运行时与 NTFS 可用；doctor 已修复误选源码根旧安装版清单的问题，
SRT 只读状态为 observed，账户/组/凭证尚未配置。固定
`C:\Program Files\PowerShell\7\pwsh.exe` 仍缺失。用户已明确授权本机管理员设置，
官方 PowerShell7.6.6 MSI 已核验 hash 与微软签名，但首次 UAC 返回取消，尚未安装。
补齐 PowerShell 后走已有固定 SRT 首次安装流程，再实测边界、Job/ACL 清理和 GUI；
用户授权不等于原生 verifier 通过，实际结果见最新 handoff。

`python -m forge.sandbox.doctor --system` 只读获取 OS、卷、运行时 hash、固定 pwsh 路径和
真实 `srt-win --srt-win status`。BFE `cannot-read` 不等于不存在：SRT 的固定受控 WFP
egress 探针用于检查前提。前提可用仍不等于 verified 能力，系统 DNS 隔离不受支持。

Main 的 `createSetupBroker` 只接受 install / repair / diagnose。install 先检查现状，
显示原生取消为默认的窗口确认，再调用固定安装器的真实 UAC。用户拒绝或 setup 失败
保持 blocked。该 Main 模块已编译验证；接入桌面主入口 / IPC 与 GUI 实测由 F13/F14 继续。

重复 install 会轮换共用沙盒用户密码；现有共享安装不会自动刷新。repair 返回明确
blocked，因为锁定上游没有只读共享 ACL holder 列表。需要独立 VM 或经审核的管理员
共享组件对账；不自动 force、uninstall、acl recover，也不删除其他应用的账户或 WFP。

本应用所有 profile 共用 OS Known Folder 下的 Windows worker。原生会话受 OS 文件锁
串行；固定 grants 持续到 session close。未知/残留清理保留带 owner / binding hash 的
session marker，后续 worker 阻断；不靠残留 PID 自动删除或杀进程。此锁不能声称控制
其他应用的共享 SRT 活动。重启对账 / 完整 ACL 与 Job 清理由 F12/F25 提供原生证据。

`python scripts/impl.py verify --task F10` 运行 unit / portable / sandbox-windows。
直接运行：`python -m forge.sandbox.doctor --native-windows --output 新目录/report.json`。
保留新的合成 fixture、policy / owner / runtime hash；缺 OS / pwsh / NTFS / 预装 SRT 为
blocked，不提权、不回退宿主、不把零检查算 pass。

验收器实际使用 SRT 初始化 / argv dispatcher / reset：中文空格目录、长路径 / CRLF、
PowerShell UTF-16 脚本与 UTF-8 输出 / 非零退出码、外部合成文件拒绝、动态 .env、直连 /
移除代理、named pipe、嵌套进程取消、会话不可变和 DNS 强要求拒绝。动态 .env 可读必须
fail；受控公共 HTTP endpoint 需显式授权 --allowed-endpoint。用户级工具授权、GUI UAC、
崩溃 ACL 对账、共享 holder 与重建会话清理当前单独 blocked，不拿类型检查代替。
