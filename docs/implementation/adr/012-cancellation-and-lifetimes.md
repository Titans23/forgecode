# ADR 012 — 取消、期限与有所有权的资源生命周期

状态：实现完成，最终全量回归 956 pass / 0 skip；Windows 11 / Ubuntu 原生验收 blocked。

Engine 的调度器只领取持久化且未取消的 work item。Main 管道 EOF 请求取消所有排队和
在途任务；排队取消不创建模型客户端。协议写入有独立期限，控制队列满或输出管道丢失
会停止领取并请求取消。显式 system.shutdown drain 保留排空语义，离线“丢失响应”演示
先记录 drain 请求，再断开管道。原有 bare EOF 继续执行行为已按第 15 章修正。

只读工具返回 cancelled 后，Runner 立即结束取消传播，不进入下一次模型请求。
有副作用的中断沿用原 Journal 的 indeterminate 记录，停止自动重规划与工具回放。
模型流、子 Agent 与审批等待使用同一异步任务取消链；Agent 外层期限包含审批等待时间。

Deadline 同时保存 UTC 期限和 monotonic 起点，剩余时间取两者的最小值。UTC 回拨不增加
预算，睡眠恢复后 UTC 已过期会触发取消。migration 003 分开保存 agent / grader /
environment 期限、cancel_state、cleanup_state 和原始清理观察；旧 epoch 未完成清理
重开后为 unknown / indeterminate，并进入已有 reconciling 流程。

固定 file-worker 增加原 run_command / verify 实现，保持公开参数和 ToolResult。
native 路径经原 SRT dispatcher，任务 stdin 与宿主 argv 分离；local-trusted 路径显式使用
Windows gated Job 或 Linux subreaper。受限 helper 对两条输出流持续排空并保留有界摘要，
不在受保护的项目 .forge 中创建输出 archive，也不产生无法访问的 artifact 引用。
verify 的 revision / environment epoch 从可信 Harness Tracker 绑定，实际命令与断言在 helper 执行。

LocalProcessOwner 只持有本次实际启动的进程和 Job。Windows TerminateJobObject 后查询
活动进程数到零才记录本地进程树 clean；Linux 的固定 subreaper 在正常和取消退出时
都回收所拥有的后代，未确认或超时为 unknown。历史 PID 不作为回收授权。
该本地进程树结果和 SRT 原生 Job / ACL 的验收各有范围。API 依据
[Microsoft Job 查询](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject)
和 [Job 终止](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject)。

PhaseLifecycle 持有可信的 live close callback。明确标记保留的交付服务可以继续到 grader
结束或环境最终期限；Agent 的模型预算保持原期限。grader_result / grade_state 与 cleanup
分开：清理错误保留已取得的评分结果。先关闭的资源报告也参加最终汇总，重复 close 共用
同一次回收。评测适配器在 F20 / F21 接入该边界，真实公开基准和原生服务验收待对应环境。

ApplicationServices 在取消确认前结束 Harness 并取得 backend 清理观察；清理 unknown 的
取消保持 indeterminate。模型关闭失败仍尝试 backend 回收和 Journal 投影；已完成的
任务保留结果及独立 unknown 清理状态。投影失败保留 reconciling，后续 F25 对账。

实际测试启动 Engine、isolated helper、洪泛输出及嵌套进程、两个独立 HTTP 服务；检查
模型调用次数、真实写入停止、服务评分可达性、Job 活动计数和 SQLite 状态。模型源为 scripted。
直接在原测试进程打开刚被强杀的 Windows WAL 库曾出现 disk I/O error；实际重新启动
Engine 的恢复路径通过，持久状态为 reconciling / unknown。该 I/O 诊断限制交给 F25 扩展。
SRT 初始化后的 session / ACL 清理仍保守 unknown，双平台 native checker 当前 blocked / 0 checks。
