# Runtime v2 第一轮中途诊断：为什么通过数没有提升

## 范围和状态

- 候选版本：`e1787de`（内核修改 `5a3bc5b`，评测任务 ID 修正 `e1787de`）。
- 评测：`benchmark/runs/harbor/terminal-bench-2-runtime-v2-c6/2026-09-03__18-25-30`。
- 本文依据 job `updated_at=2026-09-03T10:50:44.420553Z` 的快照和已完成 session；这是中途诊断，不是最终验收报告。
- 7 个 trial 完成、3 个运行。官方原始 reward：1 个为 1、6 个为 0。
- HTML trial 的 verifier 未启动成功；排除该环境异常后，目前是 1/6 个有效评分任务通过。验收目标仍是完整固定十题连续两轮各至少通过五题。
- 本次诊断没有修改生产代码或正在运行的候选版本，也没有向 Agent 注入 verifier 隐藏内容。

## 主要结论

先前实现修复了一部分工具执行和缓存问题，但没有完成计划要求的内核控制权迁移。任务约束没有形成可追溯的验收覆盖关系，自测的退出码仍然被赋予过强的含义。547 个测试通过能够说明已测行为没有报错，不能证明重构计划已落实，也不能证明解题能力提高。此前对完成度的表述过早。

不能据此认定模型能力已经到顶：执行控制、验证分类和任务契约仍存在混杂因素，而且没有完成冻结旧版与新版的严格对照。

## 逐题证据

| 已完成任务 | 原始 reward | 执行记录与 verifier 证据 | 能支持的诊断 |
| --- | --- | --- | --- |
| distribution-search | 1 | 9 次模型请求、11 个工具请求；产物及数值验证通过官方 4 个子测试 | 当前唯一确认通过的任务 |
| break-filter-js-from-html | 0 | verifier 的 curl 下载失败，随后 `/root/.local/bin/env` 不存在、`uvx: command not found`；没有完成官方测试 | verifier 环境失败，不能归因于解法。先前称其为明确代码失败不准确 |
| video-processing | 0 | 本地自测检查自己采用的列表输出及帧号范围；官方执行出现整数与列表比较的 TypeError | 输出接口理解和 verifier 不一致；题面 `[integer]` 写法存在歧义，自测没有独立验证该假设，也未证明跳跃检测准确性 |
| overfull-hbox | 0 | 官方 3/4 子测试通过；最后输出无 overfull 警告，但修改使用了指定 synonyms 文件之外的替换 | 满足排版目标但违反明确编辑约束；约束没有接入完成证据 |
| circuit-fibsqrt | 0 | Agent 修改 `sim.c` 直接计算答案，并生成很小的 gates 文件；本地测试运行的是被修改的模拟器；官方功能测试失败 | 自测对象被改变，无法证明要求的电路成立。另有旧完成门禁在 17 次模型请求时终止 |
| build-pov-ray | 0 | 38 次模型请求、67 个工具请求；大量源码下载/搜索，源码版本问题仍未解决；最终清理/解压命令被 `hard_deny` 后整轮退出 | 解题策略与权限控制两个问题并存，不能断言单修权限就会通过 |
| make-mips-interpreter | 0 | 84 次模型请求、97 个工具请求；本地验证 BMP 结构/尺寸和命令返回；官方运行与图像内容测试均失败 | 文件存在、结构正确没有覆盖真实运行和渲染行为；多次较完整检查被命令分类器判 unknown 后，单纯运行命令反而获准完成 |

逐题证据分别位于上述 run 下各 trial 的 `agent/forgecode.txt`、`agent/session-*.jsonl`、`verifier/test-stdout.txt` 和 `result.json`。原始 reward 不作改写。

## 未落实的架构要求

1. **TurnRunner 还不是主循环的控制者。** `forge/runtime/agent_loop.py:4452` 仍委托 `conversation._stream_impl(prompt)`；后者保留大段恢复计数、强制行动和提前退出逻辑。`TurnState.can_request_model()` 与 `can_request_tool_batch()` 只有测试调用，没有接入生产执行路径；因此 TurnState 主要承担旁路统计，没有成为唯一预算来源。
2. **批次行为仍违背方案。** `agent_loop.py:2092` 会因前一个成功编辑取消后续命令。HTML session 中 `write_file` 成功后，紧接的依赖检查收到 `not_executed_after_workspace_change`。相反，POV-Ray 的一个普通命令失败后，同批次后续命令仍然执行。方案要求的是顺序执行、失败后取消尾部，而不是成功编辑后取消。
3. **验证执行成功与覆盖目标仍未分开。** `forge/tools/verify.py` 的 `verification_coverage` 只是 `verification_quality(command)` 的值，不携带覆盖的需求、假设和限制。`verification.py` 用未经 shell 语法解析的分号正则判断链式命令，用排除列表判断行为验证。只读探针复现：`node -e "const ok = true; if (!ok) throw Error('bad');"` 被判 `unknown`；`python -c "print('ok')"` 被判 `behavior`。
4. **权限解析缺少真实删除目标。** `forge/permissions/risk.py:121` 对匹配的递归删除设 `hard_deny=True`；目标采集只得到 cwd，无法按具体清理路径判断范围和授权。一次拒绝又会使整轮停止。需要保留受保护路径与宽泛删除限制，并把可授权的明确目标交给统一权限层。
5. **日志和状态仍存在顺序错误。** `agent_loop.py:4523` 先写 turn_completed，再在 4525 行更新统计。本轮 distribution-search 的终端 payload 有执行统计，而 session 的 `turn_completed.payload.statistics` 为 `{}`。这不直接解释解题失败，但证明持久化契约尚未完成。
6. **仍残留按题目特征设置的规则。** benchmark 入口仍有 `Overfull` 输出正则，而指定同义词约束没有相应来源明确的验收关系。移除任务特定提示词没有同时完成通用契约机制。
7. **中途汇总误计未完成 trial。** 当前 summarize 把还在运行但没有 result.json 的 trial 记为 MissingResult/agent failure。它不适合直接汇报正在运行的任务，需要先读取 job 完成状态再判断缺失结果。

## 成本证据及解释边界

在新旧都已有最终 Agent payload 的前六个同题上，旧轮次是 274 次模型请求、339 个工具请求；新版是 132 次、169 个。含缓存读取的累计输入 token 从 3,217,129 降为 2,688,999；输出从 89,795 降为 44,416。

这些数字只能作描述性比较：少调用部分来自提前退出，并未带来通过数提升；旧轮次也不是本次冻结基线的严格对照。新版 MIPS 另消耗 4,028,756 个累计输入 token，仍未通过。移除消息数量裁剪后，长轨迹每次请求携带的历史更大；需要检查压缩触发、重复上下文和诊断保留，不能用重新裁剪失败证据或增加预算代替分析。

## 接下来按依赖顺序完成的工作

1. 等本轮完成并补齐最后三题的官方结果、session 和环境分类；冻结原始记录。
2. 将实际模型/工具循环迁到 TurnRunner，生产执行路径只接受 TurnState 预算和一个停止入口；删除旧 `_stream_impl` 的恢复决策分支。通过带真实小进程的集成测试验证，而非只测状态对象。
3. 统一顺序批次语义与拒绝反馈；明确授权目标经过路径解析、权限决策和 checkpoint，再执行。拒绝一个操作时保留重新规划能力。
4. 把执行结果、检查覆盖声明、明确约束及其来源分开记录；未知命令不自动成为充分验收。为被要求保留的输入/模拟器和允许的编辑建立契约检查，同时不把模型 scope hints 升级为用户权限。
5. 修复 shell 语法分类和日志落盘顺序，验证强检查不会被弱检查替换后获得更高可信度。
6. 用这些失败轨迹建立通用契约回归后再完整测试、再评测。保持固定模型和预算，不加入题目名称分支、隐藏答案或默认第二 verifier。

## 19:07 补充：真实执行回归探针

新增 `tests/runtime/test_turn_contract.py`，针对 `e1787de` 执行结果为 **6 failed**。这是刻意保留的待修契约，不是新的评测成绩：

- 写入 `value.txt` 后同批进程本应读到 B，实际被取消。
- 第一个进程退出 7 后，同批后续写入仍创建了文件。
- 没有 UI 消费者时，最终 JSONL 的 statistics 是空字典，返回结果却有统计。
- 模型预算为 1 时，路由已消耗 1 次，主模型仍被请求。
- 明确要求两条检查时，仅第一条成功即可完成。
- 纯目录读取仍触发一次全工作区 refresh。

其中前两项使用真实 Python 子进程，其他项通过真实 Conversation/SessionJournal/CompletionGate/ToolExecutor 入口断言。没有把旧版的错误输出设为预期成功。

本轮现有 9 个正式结果，仍只有 1 个 reward=1。新完成的 path-tracing 是 renderer 精度未达到用户目标，Agent 最后声明 failed；protein-assembly 的序列身份/融合组件不符合目标，Agent 本地只验证了通用 DNA 结构、长度、GC 和标记条件，旧完成门禁又将其终止为 stuck。编译任务还在运行，暂不写成失败。生物任务官方诊断中的具体序列不进入提示词或解题规则。

### 已删除的旧行为测试及理由

删除 `test_phase_recovery.py` 以及 15 个只断言旧恢复阶段的测试：临时工具白名单、完成倒计时、重复工具封锁、失败编辑次数上限、停滞强制合成、占位写入门禁和超大写入后的阶段性工具暴露。这些断言正是方案要求移除的重复控制机制，继续保留会迫使新内核重新实现旧缺陷。

对应的用户可见边界没有一并删除：`test_turn_contract.py` 已覆盖顺序批次、失败尾部取消后重新规划、统一预算、完整必做检查、日志统计和只读观察；另补权限拒绝后由模型重新规划、明确失败/阻塞、真实小进程、受控 artifact 读取和精确/宽泛递归删除的权限测试。模型协议纠正、上下文压缩、checkpoint、Hooks、会话恢复和回退冲突测试仍保留在原测试集中。

## 内核切换后的验证与未完成项

本次实际删除 `_stream_impl`，`Conversation.stream` 现在调用独立的 `runtime/runner.py`。阶段恢复模块已退出源码；真实模型请求（含路由和压缩）通过共享预算记录。成功编辑不再取消批次尾部，失败后未执行调用有明确状态；最终统计由内核持久化。旧测试更新为新契约：预算耗尽返回 failed，完成拒绝后可继续诊断，读取返回真实内容，用户消息在协议失败后仍保留。

Windows 完整回归为 **536 passed**，另有飞书依赖弃用警告；`compileall` 和 `git diff --check` 通过。此结果仅证明本阶段已测执行契约，不代表 Linux 门槛、完整重构计划或 benchmark 目标达成。下一阶段仍需审计进程树清理、验证执行/覆盖分类、压缩预算、摘要和 artifact 恢复，以及源码冻结和异常汇总。

首轮现场更新：compile-compcert 重试的 `forgecode-status.json` 在 19:42:13 记录 exit_code=124、timed_out=true。Docker 容器仅剩 sh/sleep 保活进程，没有 Agent；job 仍缺最终收尾结果。不能把它描述为正常运行，也不能编造第十题 reward。该重试复制的 `agent_loop.py` Git blob 为 `e2acf6dedbd6f20522b6e87409dce87645a5c65a`，与 e1787de 一致，未混入本次新内核。但评测适配器是在每次安装时复制源码，下一轮必须使用统一冻结快照。

### 本轮九个最终 payload 的成本快照

| 任务 | 内部状态 | 模型请求字段 | 工具请求字段 | 含缓存输入 token | 输出 token |
| --- | --- | ---: | ---: | ---: | ---: |
| break-filter-js-from-html | completed | 5 | 7 | 31,251 | 957 |
| build-pov-ray | blocked | 38 | 67 | 1,238,486 | 9,296 |
| circuit-fibsqrt | stuck | 17 | 17 | 232,144 | 17,229 |
| distribution-search | completed | 9 | 11 | 65,098 | 3,309 |
| make-mips-interpreter | completed | 84 | 97 | 4,028,756 | 24,218 |
| overfull-hbox | completed | 51 | 54 | 966,566 | 8,558 |
| path-tracing | failed | 77 | 84 | 3,324,175 | 24,083 |
| protein-assembly | stuck | 49 | 53 | 2,354,553 | 77,013 |
| video-processing | completed | 12 | 13 | 155,454 | 5,067 |

合计 342 / 403 次、12,396,483 输入 token、169,730 输出 token。以上是旧候选最终 payload 的字段合计，不是完整账单；不含 CompCert 两次超时的消耗，也不应假定旧计数器计入了每个 HTTP 重试。MIPS、path-tracing、protein 三题占这些输入 token 约 78.3%，成本集中于长轨迹及反复携带历史，不应增加预算掩盖问题。

官方原始结果仍是九个 trial 中一个 reward=1；八个可判定代码表现的评分中一个通过，七个失败；另有一个 verifier 环境失败及一个已观察到 Agent 超时但缺正式 trial 结果的任务。本轮不满足有效完整轮次条件。

### 第二批修复与尚未封闭的门槛

新增统一源码快照、原始 reward 保留与未收尾状态区分；verify 不再猜测缺失 package.json 而拦截实际执行，支持 stdin 和独立的 covers/limitations；artifact 支持按偏移取回中间内容。修正多目标权限规则的部分匹配放行、嵌套 shell 宽泛删除检测、嵌套 shell 退出码掩盖和不同 stdin 检查互相覆盖的问题。进程 deadline 现在包含 stdin 回压和输出排空；POSIX 子进程继承管道由进程组终止测试覆盖。

这些修复不等于自动证明语义正确：covers 仍是模型提供的解释；自然语言中的允许变换和不可修改输入尚需建立更完整、来源明确的验收关系。Windows 父进程先退出时的后代清理、多次压缩后目标约 60% 的预算、未执行日志的跨 turn 身份等边界仍需进一步审计。在这些门槛封闭及固定配置对照完成前，不宣称通过率提升，不启动新的付费十题评测。

本批最终验证：Windows / Python 3.12 全集 **552 passed, 1 skipped**（POSIX 进程组测试）；Linux / Python 3.13.7 全集 **553 passed**。两端仅有飞书依赖同一条弃用警告。Linux 使用本地 `alexgshaw/build-cython-ext:20251031` 镜像，独立临时容器和只读源码挂载，第二次安装之后可离线复用 D 盘缓存；完整脚本位于 `scripts/test-runtime-linux.ps1`。首次 Linux 尝试的挂载缺失和 Windows 引号测试缺陷已修正，未跳过 MCP 真进程集成测试。此 Linux 结果是跨平台契约验证，不是固定评测的 Python 3.12 生产环境等价对照。
