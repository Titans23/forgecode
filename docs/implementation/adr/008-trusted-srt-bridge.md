# ADR 008 — 固定资产的单会话 SRT Bridge

日期：2026-10-06。任务：F08。依赖：ADR 001、005、007。

Engine 使用 `forge.sandbox.SrtBackend` 的独立生命周期接口，经固定 Node 24.21.0
启动每个 sandbox session 的独立 Bridge。它不是第二个 Agent loop，也不是既有
Harness ToolExecutionBackend 的宿主执行回退。工具接入由 F11 完成。

`release-lock.json` 记录 Node、原生 helper 和 runtime-manifest 哈希；
`scripts/build_bridge.py` 从 package-lock 的真实依赖闭包及编译输出生成字节清单。
Python 在启动前校验，Node 在动态导入 SRT/契约前再校验。安装根来自程序位置，
不从项目 PATH/node_modules 搜索。管理环境从允许的系统路径重新构建，模型 key、
项目 loader、上游代理变量均不继承。编译输出与相关清单固定 LF。

控制 JSONL 使用 `forge.bridge.v1`，只注册 probe/prepare/execute/status/cancel/close。
任务的 argv/script 作为单独 JSON stdin payload，送给 SRT 内部的固定 dispatcher；
outer spawn 一律 shell=false。Windows 内部使用固定 PowerShell 7，原脚本使用
UTF-16LE EncodedCommand；Linux 内部使用固定 bash/sh。空 argv 参数必须保留，
因此 CommandSpec 改为仅 argv[0] 非空。批处理文件须明确走已批准的 shell_script。

stdout/stderr 仅封装为具有 execution/owner/sequence 的 bridge.output 数据。
保留原始 base64 字节，UTF-8 使用增量解码；配额耗尽和队列满继续 drain，分别累计
discarded bytes。控制响应优先且队列有界，但已写入系统管道的帧不可抢占。
同一 execution ID/command hash 返回原对象，包括未知启动结果；冲突拒绝，绝不重跑。
close 缓存第一次清理结果；EOF 关闭真实 session，不解析任务文本作为控制事实。

实际核对 SRT 0.0.78 的 initialize/wrapWithSandboxArgv/reset 及 Windows status 类型。
Linux 固定 bwrap/socat/rg/seccomp 路径；Windows 固定 srt-win helper。
初始化或包装失败没有普通 spawn 回退。SRT 的 allowRead 不是读取白名单，Windows
系统 DNS 未被 WFP 围栏隔离，glob 只在初始化时展开；这些限制不宣称已解决。

probe 当前报告真实系统/依赖前提，**不把前提存在标为 verified 隔离能力**。
生产 prepare 需要 native boundary/cleanup evidence，缺失则拒绝。
`initializeNative` 是给 F09/F10 受控原生验证器的底层 SRT 初始化入口，不注册 RPC，
不接受 Renderer/model 提供的能力报告。F09/F10/F12 必须补实测及归属/残留核查。
SRT reset 在 Windows 为 best effort；只在从未初始化/启动时 close 可报告 clean，
发生过 native 初始化则报告 unknown，remaining_processes=0 不是证明无残留。

本机 Windows 10 的真实 Node Bridge、dispatcher、参数、编码、输出和控制隔离测试
属于 portable。Windows 11/Ubuntu native 及 sandbox 取消/ACL/动态敏感文件保护仍 blocked。
不批准管理员 setup，不运行模型 API，不解除既有 GHSA 生产发布门禁。
