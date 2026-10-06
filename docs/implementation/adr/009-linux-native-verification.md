# ADR 009 — Linux 真实探针与保守能力结论

日期：2026-10-06。任务：F09。

复用 F08 的 srt-adapter，不新增第二份 SRT adapter。`initializeNative/executeNative`
属于只供受控 native verifier 导入的底层入口，不注册 Bridge RPC；生产 prepare 的
verified 能力门禁保持。所有执行仍经过真实 SandboxManager.initialize、
wrapWithSandboxArgv、固定 dispatcher 和 reset，没有 FakeSandbox 或宿主执行回退。

Doctor 输出 OS build、Ubuntu 版本、固定系统工具路径/hash/身份、namespace/AppArmor
只读观测、固定 bwrap user namespace 的实际探针及 runtime 哈希。依赖由 root 所有且
非其他用户可写。项目模式只读取目录元数据/Bridge 前提，不执行项目工具、不提权、
不关闭 AppArmor、不修改 sysctl、不装 CA。未观测活动会话/残留为 not_observed。

native fixture 仅创建新的合成项目/敏感目录/外部文件。检查实际授权读写、保护路径、
直连、删除代理变量、workspace 内受控 Unix socket、嵌套进程取消，以及动态 .env。
网络探针只访问新建的受控 loopback 服务；C10 需用户明确指定可控公共 HTTP canary URL，
缺失时单独 blocked，不在默认测试中访问公网。不宣称 HTTP 证据覆盖 HTTPS。

LinuxExecutionOwner 以实际 ChildProcess、procfs、启动 tick、父子关系及 PID namespace
绑定 execution。只能向观察到且身份仍相符的 namespace init 发信号；namespace init
退出由内核处理其后代。观察失败、PID 身份变化、未捕获 namespace 或未确认结束均
unknown/residual，不按名称或孤立 PID 杀进程。该代码在 Windows 只进行了 parser/
schema/编译测试，尚未获得 Ubuntu 内核实测，不能以此标 process_cleanup verified。

完整 session 的 SRT proxy/临时挂载与崩溃恢复清理仍需 F12/F25，close 保守 unknown。
动态 .env 的真实 canary 若可读，必须 fail；不能把上游 glob 时间窗口改成通过预期。
部分 native 测试不自动提升为生产 verified CapabilityReport。

`impl.py verify --task F09` 包含 unit、portable、sandbox-linux。
native report 保留实际命令、平台、退出码、report hash、checks 数；缺平台为 exit 2/
blocked，零检查/任意非 pass/eligible_for_native_pass 不为 true 均不能 native pass。
Ubuntu 22.04 和 24.04 两套证据、授权 C10 endpoint、全 session cleanup 当前均未验证。
