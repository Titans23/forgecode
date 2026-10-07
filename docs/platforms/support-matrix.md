# ForgeCode V4 平台支持与验收状态

截至 F32 开发审查阶段，发布仍受阻。用户在 2026-10-07 明确要求当前阶段先在 Windows 10 验收，并授权现有配置中的真实模型测试，金额预算无上限。下列状态区分本阶段实际验收、后续平台和生产发布；Windows 10 结果不转算为 Windows 11 或 Ubuntu。

| 环境 | 已有实际结果 | 产品验收 |
|---|---|---|
| Windows 10 x64 build 19045（当前阶段验收机） | Python CLI/Harness、私有 stdio Engine、SQLite/Journal、真实文件及进程工具、Electron 开发窗、frozen Engine、加固安装目录 smoke，现有配置实际模型连接 | 用户指定的当前验收阶段；生产安装与严格原生沙盒仍未通过 |
| Windows 11 x64 客户端 | 已有 Windows adapter、Main 入口与 setup broker；实际平台判断排除 Server | blocked：没有干净 VM／设备与 setup 授权 |
| Ubuntu 22.04 x64 X11／Wayland | Linux adapter、deb 构建基线约束、受控 Bridge/FileWorker 与打包入口 | blocked：没有专用原生／图形验收设备 |
| Ubuntu 24.04 x64 X11／Wayland | 同上；Linux 包必须在较老 Ubuntu 22.04 构建 | blocked：没有专用原生／图形验收设备 |
| GitHub Ubuntu 22.04／24.04、Windows Server2025 portable jobs | 已上传3a51097；实际run37642586381 attempt2：Ubuntu22.04/Windows2025各unit225、portable382通过，Ubuntu24.04 unit225通过、portable381通过/1浏览器report缺失失败；security继续2high。初次startup_failure及全部重跑artifact/log保留 | hosted portable CI不建立原生产品能力；当前workflow仍failure |
| macOS／Windows Server／WSL | 无产品支持声明；Server 不满足 Windows 11 workstation 判定 | unsupported |

每个 strict turn 现在创建独立实际 SRT Bridge 和 FileWorker，配置／prepared policy hash 必须一致，准备成功才创建模型。准备失败记录真实 owner-bound cleanup，不自动改用 local-trusted。当前 capability verification 尚不提供可提升为 verified 的完整原生证明，初始化后的边界／后代清理也未完成原生验收，因此 strict 执行继续拒绝。此处既有环境限制，也有尚待原生实现验证的代码限制，不能称为已可用原生沙盒。

开发演示明确使用离线 scripted model 与 local-trusted，经过实际 Harness、文件修改、测试命令、Journal、SQLite、Main/Preload/UI。它不证明隔离能力或模型成绩。

F28 两次完整固定负载均为 100000 条事件、10000 个 span、100 MiB 输出。元数据中位数额外耗时从 8.0771% 降为 5.2103%，仍超过 5% 目标；这一项继续 blocked，保留两次原始报告。复测 source Engine 冷启动中位数 3.2679 秒／热 RPC 0.76535 毫秒，SQLite 投影 55.6679 秒，实际 React 列表从 10000 项只渲染 14 行。React SSR 实际使用 PATH Node 22.17.1，与 locked Node 24.21.0 和图形帧性能分开报告。另实际 Windows host run_process 输出 100 MiB，完整归档摘要相同、显示仅 4096 bytes；这不是原生隔离吞吐证明。

加固使用实际 Electron fuse plugin，关闭 RunAsNode、Node options、Node CLI inspect 和 file protocol extra privileges；开启 only-ASAR，Windows 开启 embedded ASAR integrity。Linux 的 embedded integrity 不声称可用。独立 smoke 读取实际二进制 fuse wire，先证明 NODE_OPTIONS 探针能在私有 Node 中运行，再检查安装应用拒绝注入及调试入口，且实际 credential helper 仍能运行。当前产物 unsigned developer-preview，独立 smoke 通过不使其变为 production。

原生管理员 setup、干净安装／卸载／升级、Windows Job/ACL/canary、Linux namespace/network/keystore、真实 suspend/resume、签名和用户选择的项目 LICENSE 仍需资源或授权。SRT 引入的 node-forge 高危依赖安全门禁继续 blocked；Docker daemon 未可用，官方 Harbor 成绩为空。真实模型已授权：连接1请求335/10 tokens；F31两批10attempt/83请求，完整usage864743 input/15909 output、cache0。第一批source_changed全部保留且不可比；第二批冻结6attempt/50请求，独立Windows grader A3/3、B2/3，三task-cluster B−A−1/3，exploratory95%[-1,0]，不能声明收益。成本unknown，正式Harbor/holdout/native证据仍为零；详见F31-real-model-observations与final-report。F32新检出副本的12条实际文档步骤/24份日志hash、demo原始artifact与3锁摘要已复算；Node24资源实际新下载，三pinnedGit任务hash/checksum一致，0模型请求。这是当前Windows10源码复验，不是干净目标平台安装。金额无上限仍保留有限请求、工具和时间预算。没有运行管理员安装。
