# Linux 原生诊断与验收

正式环境为 Ubuntu 22.04/24.04 x86-64。安装根和 Engine control 根必须位于工作区外，
Node/SRT/helper/所有 runtime 文件先按 release-lock 与库存校验。
固定系统依赖为 /usr/bin/bwrap、/usr/bin/socat、/usr/bin/rg 和 /bin/bash、/bin/sh；
必须由 root 拥有且其他用户不可写。不得从项目 PATH 搜索这些管理组件。

只读诊断：`python -m forge.sandbox.doctor --system`。
项目元数据与真实 Bridge 前提：`python -m forge.sandbox.doctor --workspace /absolute/project`。
不会运行项目 python/npm、安装软件、关闭 AppArmor、修改 sysctl 或申请提权。
状态报告包括 OS build、工具实际路径/hash/identity、namespace/AppArmor 观测值、
runtime hash、能力证据状态；未观察到的活动会话/残留明确 not_observed。

原生验收：`python scripts/impl.py verify --task F09`。
或者直接 `python -m forge.sandbox.doctor --native-linux --output /absolute/new-evidence/report.json`。
只使用新建合成 fixture，真实 SRT 初始化、受限 dispatcher、文件读写、受控 loopback/
Unix socket、删除代理环境后的直连、嵌套进程取消与 namespace/start-time 核查。
没有 Ubuntu 或依赖时返回 exit 2 / blocked，checks 为空不能作为 pass。
运行后的 fixture 保留用于残留检查，不凭 PID 或进程名称杀其他任务。

C10 需要明确授权且可控的 HTTP canary endpoint；默认不访问公网。
可在上述 doctor 命令额外指定 `--allowed-endpoint http://controlled.public.example/canary`，
使用实际受限代理，并把该 hostname 加入固定验证策略。示例域名不可用，不是测试 endpoint。
不允许 URL 凭证/query、私网字面值、MITM 或禁用证书校验；HTTP 证据不冒充 HTTPS 验收。
未提供 endpoint 时 C10 单独 blocked，其余边界测试仍执行。

上游 glob 保护与动态 .env 存在已知时间窗口；测试真实创建后读取，若可读则 fail，
不得修改预期来凑通过。强读取白名单、硬资源要求和未验证的隔离能力始终拒绝。
受控验证入口不会把部分测试结果自动提升为生产 verified capability。
Linux 单 execution 可依实际拥有的 PID namespace init + start time 清理后代；
全 session 的代理、临时挂载和异常恢复核查仍需 F12/F25 完成。

当前开发宿主为 Windows 10；Ubuntu 22.04 和 24.04 两套正式原生证据均 blocked。
