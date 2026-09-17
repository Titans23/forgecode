# 2026-09-16：已确认运行时问题的修复与验证

本轮基于 2026-09-15 unseen20 的轨迹、离线复现和当前生产代码实施修复。未调用模型重新评测，未修改历史官方 reward。代码尚未提交或推送；工作区包含此前工作的变更。

## 实现

### 服务交付与进程清理分离

`benchmark/harbor/process_supervisor.py` 增加显式 `--preserve-on-success`。仅当代理进程以 0 正常退出且未收到中断时，允许其服务进程留在一次性评测容器中供 verifier 使用。Harbor 适配器开启该选项；默认监督器仍严格清理。非零退出、超时、中断仍回收后代，容器销毁是服务最终生命周期边界。

这修复了 gRPC 任务在代理内部可访问、代理退出后官方测试连接失败的已复现机制。尚未重新运行该题，不能宣称其官方 reward 已提高。

### Linux 命令的超时与取消

新增标准库入口 `forge/tools/linux_process_worker.py`。每条 Linux 命令拥有独立 subreaper，处理 `setsid` 后代；通过非阻塞管道转发输出，并持续拥有继承 stdout/stderr 的后代，直到 EOF 或取消。已重定向标准流的正常后台服务可交付给外层评测容器。

`shell.py` 对进程等待、stdin 背压、输出收集采用同一命令时间预算，另给清理最多 2 秒。清理超限关闭传输，保存已经收集的输出，标记 `output_incomplete`，避免“已超时但仍无限等待 EOF”。Windows Job Object 路径继续保留。

离线同一复现：请求 0.3 秒超时，原 detached pipe holder 等待约 2.066 秒；修改后约 0.328 秒。普通子进程约 0.338 秒。数值是短探针观测，不是严格实时保证。

### 文件发现与搜索预算

`find_files` 不再先递归收集整个目录树再截取 `max_results`。按目录遍历，匹配数达到上限后停止进入后续子目录；即使没有匹配，也限制扫描条目、深度和耗时。Linux 跳过 `/proc`、`/sys`、`/dev`，避免虚拟文件系统的无限或特殊文件。

默认预算：10 秒、20,000 个扫描条目、深度 32。`grep` 另限制单文件 2 MB、累计读取 16 MB、返回文本 128 KB；仅打开普通文件，使用非阻塞/no-follow 标志，避免文件被换成 FIFO 后阻塞。取消通过事件通知工作线程。

输出包含 `complete`、`stop_reason`、`scanned_entries`、`skipped_paths` 等信息；跳过大文件、不可读路径或达到预算时，不把零匹配描述为不存在。超时是协作检查加异步等待上限，并非对内核阻塞 I/O 或任意 Python 正则回溯的进程级硬隔离。

### 完成验收的有限修复反馈

新增调用方策略 `max_delivery_repairs`，默认 0；Terminal-Bench 设为 2。completed 声明存在验收缺口时，返回具体反馈并允许在原模型、工具、时间预算内修复；同一组缺口重复提交或用尽机会后结束为 partial。显式 partial/failed 不强迫继续。

普通最终文本与 `finish_task` 两条提交路径均覆盖。验收合同及失败断言不删除、不放宽。全局 `require_changes` 改为 False，因为服务/环境交付不一定修改受跟踪文件；验证与验收要求继续保留，任务自身的文件交付约束仍需满足。

### 评测结果统计

超时是独立的运行事实，不能抹去随后 verifier 给出的通过结果。超时且 reward=1 的记录现在仍可计入有效 verifier 结果。

`final_pass_count/rate` 使用原始官方 reward 和全部 trial 分母；另输出 `final_pass_denominator`、`eligible_final_pass_count/rate`，区分整体通过率与排除基础设施问题后的诊断指标。保留历史 `pass_at_1/2` 字段兼容性。

重新汇总历史 unseen20：整体 **9/20=45%**；有资格纳入诊断的结果 **9/19≈47.37%**。旧的 8/18 混淆了排除条件。原始结果与旧报告未覆盖，新汇总位于 `2026-09-16-unseen20-corrected-summary.json`。

## 回归验证

- Windows 首批相关测试：111 项通过。
- 新增搜索预算、协作取消、统计分母、验收反馈与终止测试。
- Fake model 集成覆盖最终文本与 finish_task 的反馈路径，确认模型收到缺口后可提交诚实 partial，且不追加预算。
- Linux 离线容器：`alexgshaw/kv-store-grpc:20251031`，仓库只读挂载，`--network none`；没有模型请求。
- `tests/benchmark/linux_command_regression_probe.py`：stdin 传递、stdout/stderr 保真、大输出归档、stdin 背压、脱离会话的后代超时/取消、独立进程不被误杀、两层监督器正常退出后服务仍可连接，全部通过。
- `tests/benchmark/linux_supervisor_probe.py`：默认成功清理，以及开启服务交付选项后的失败/取消/超时清理，全部通过。
- 初次全量 712 项通过、2 项失败：更新不适合服务交付的旧测试假设，并将 deadline 转发测试固定时钟，消除浮点计时偶发失败；相关 42 项复测通过。
- 最终全量回归：**716 passed，1 warning，266.30 秒**。警告来自飞书依赖 protobuf 的弃用 API；无失败。JUnit 记录：`benchmark/.cache/known-issue-repair-tests-20260916.xml`。

## 尚不能据此解决的失败

WAL 原始输入破坏、ELF 地址解释、Raman 参数、历史检索数据语义、图像与棋局泛化等问题仍涉及实际解题和独立测试质量。本轮修复清理、资源边界、反馈和统计，不把循环自证变成官方正确性保证。需要冻结本轮代码并开展受控复评，才能判断通过率与成本是否改善。

## 存储与后台下载

- 项目、评测轨迹、报告：`D:\projects\forgecode`。
- Docker Desktop 数据磁盘：`D:\DockerDesktop\wsl\disk\docker_data.vhdx`，随着镜像继续写入，本轮查看由约 107.49 GiB 增至 120.39 GiB 文件大小（不是所有镜像的去重逻辑大小）。
- Harbor 题目缓存：`C:\Users\3dimaging\.cache\harbor`。
- 镜像预取：`benchmark/.cache/terminal-bench-2-all-images-20260916/status.json`；最后核实 87/89 ready，pytorch-model-recovery 已完成，剩余 hf-model-inference、mteb-retrieve 的 Docker pull 进程仍在运行。状态以该文件后续更新为准。
