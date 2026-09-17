# ForgeCode 下一窗口无损交接提示词

> 将本文件全文作为新窗口的首条上下文提示词。本文记录的是项目事实、用户目标、约束、证据与下一步，不代表对删除文件、停止进程、推送代码、创建任务或重新发起付费评测的额外授权。除非用户在新窗口明确要求，否则不要自动启动新一轮 benchmark。

## 1. 你的身份与最高目标

你正在继续维护 `D:\projects\forgecode` 中的 ForgeCode。请使用中文与用户协作，先基于证据分析，再实施架构级、可回归验证的修复；不要用浅层提示词微调或无分析重复跑分代替工程工作。

用户最初的明确 `/goal` 是：读取上下文，将固定评测并发上限设为 6，持续优化 ForgeCode、修复其中的 bug，直到当前指定的 10 个评测任务达到验收条件。

最终验收条件不是偶然一次 5/10，而是：

1. 同一个冻结代码版本，在完整、有效的十题评测中达到至少 5/10；
2. 第一次达到 5/10 后不得修改候选代码，立即再完整运行相同十题；
3. 连续两轮均至少 5/10 才算完成；
4. verifier 未真正完成、模型服务异常或基础设施失败的轮次不得冒充有效达标轮次；必须保留官方原始 reward，并区分失败类别。

用户特别强调：每轮结束后必须详细总结、逐题研究、定位底层代码或架构问题，然后再进行有证据的修复与回归验证。用户不接受“简单改一改就接着跑”，也不接受在通过数没有增加而成本上涨时只提高预算重跑。

## 2. 当前准确状态

- 仓库：`D:\projects\forgecode`
- 当前分支：`codex/runtime-v2`
- 当前最新 tracked 提交：`390af0b fix: ground verification in executed tests and explicit output assertions`
- 旧版冻结基线：提交 `eff5909`，tag `codex/runtime-v1-baseline`
- 旧版独立 worktree：`D:\projects\forgecode\benchmark\runs\baselines\eff5909`
- 当前没有运行中的 benchmark/controller/Docker 评测容器；最近一轮已经结束。
- tracked 工作区在最后核对时是干净的。
- 用户拥有的未跟踪内容：`.tmp/`、`docs/reviews/2026-09-03-runtime-architecture-audit.md`。不得擅自编辑、删除或提交。
- 本交接文件是后来新增的文档；处理它时也不要误把其他未跟踪文件纳入提交。

进入新窗口后的第一步应当是：阅读本文件和第 8 节列出的关键报告，核对 `git status --short`、`git log -1 --oneline`，并在准备运行前确认没有残留评测进程或容器。不要先重跑。

## 3. 不可改变的产品与架构边界

本次重构的原则是消除“多个组件重复决定是否执行、是否恢复、是否完成”的冗余控制，最终只维护一个生产内核，不长期保留新旧两套执行路径。

必须保留：

- CLI、飞书、MCP、Skills、Hooks；
- 会话恢复、记忆、checkpoint 与回退；
- 现有命令、配置、历史会话兼容；
- 必要权限边界、真实执行记录、内容哈希冲突保护；
- 模型适配、文件工具、持久化与外部入口能力。

允许调整内部 Python API 和目录结构，但所有入口必须接入同一个运行时工厂与同一个执行内核。benchmark 不应从 CLI 层导入运行时初始化。

职责边界：

- `Conversation`：兼容 CLI、飞书、会话命令和既有调用方，不包含调试恢复或工具执行分支。
- `TurnRunner`：组织模型请求、顺序工具批次、统一预算与终止，不自行解释具体文件操作、验证命令或权限。
- `ToolExecutor`：参数校验、Hooks、二次校验与授权、checkpoint、执行、工作区观察、结果持久化，不判断模型是否“做够了”。
- `ContextManager`：请求上下文、摘要、失败诊断、artifact 和 token 预算，不隐藏工具或推进任务状态。
- `TaskState / EvidenceLedger`：目标、计划、执行证据、验收状态；计划步骤或总结关键词不能决定执行权限。

唯一主循环：

`准备任务与上下文 → 请求模型 → 顺序执行工具批次 → 记录事实 → 判断继续或结束`

不得重新引入：恢复阶段工具白名单、分散的强制行动分支、同 turn 编辑去重、失败命令封锁、按消息条数裁剪、完成关键词门禁、三次完成倒计时、按题名写特殊解法。

## 4. 已实现的内核能力

到提交 `663ecd6` 为止已完成：

- `forge/runtime/runner.py` 中真实单一 `TurnRunner`；旧 `_stream_impl` 已移除。
- 路由、压缩、模型调用、工具调用纳入统一预算；顺序工具批次与明确的未执行尾项。
- 普通最终回答与结构化结束走统一完成检查，并支持 `failed`。
- 验证历史关联 workspace revision 和 environment epoch。
- benchmark 明确任务契约由入口直接传入。
- turn/session ID、稳定事件 ID，防止跨 turn 重用 provider tool-call ID。
- 原始用户指令锚点持久保存；摘要、失败诊断、artifact 可恢复。
- 大 stdout/stderr 流式保存为 artifact，并提供受控分页读取。
- Windows JobObject 子进程约束与后代清理。
- checkpoint 后置持久化失败时标记 `indeterminate`。
- scope hints 不升级为授权；关键词门禁删除；diff 检查显式化。
- Python 3.12.11 与运行时依赖精确 hash lock；源代码快照、基线适配器和数据集 digest 可复现。
- 当时 Windows/Linux 完整测试均为 562 passed。

提交 `b9a3d7b` 增加评测驱动可靠性：

- Docker artifact transport 上限 120 秒，cleanup 上限 60 秒。
- Linux subreaper supervisor 能拥有并回收 setsid 后代，在成功、取消、超时时清理。
- verifier 依赖下载超时单独分类。
- no-model Harbor smoke：reward 1、exit 0、容器完全清理。
- Windows/Linux 完整测试均为 570 passed。

提交 `390af0b` 增加证据可靠性：

- `forge/tools/verification_checks.py` 可保守识别 `python file.py` 只是定义测试函数却未调用的情况；命令仍真实执行，但不能登记为有效验证。只分析 workspace 内、最大 200 KB 的 AST，不擅自切换为 pytest。
- `verify.output_checks` 可对 stdout 最终 JSON 对象执行确定性 `eq/ge/le` 检查；输出缺失、类型错误、非有限值或阈值失败时返回 `verification_not_established`，保留 exit code 0，但 `evidence_valid=false`。
- `VerificationEvidence` 增加 `evidence_valid`、`evidence_issues`、`check_signature`，并保留旧记录兼容默认值。
- `successful` 必须同时要求证据有效。
- 不同命令不再因 `covers` 标签相同而擦除旧失败；验证键包含 command/cwd/stdin/check signature；同一实际检查重跑成功才可清除。
- runtime prompt 显示剩余模型、工具、时间预算。
- 通用 benchmark 指导强调独立、可证伪的要求、阈值断言、真实运行测试、避免从实现本身推导期望值。
- 没有题名分支、隐藏答案或特定任务解法。
- Windows/Linux 完整测试均为 575 passed；仅有既存飞书 protobuf 弃用警告，无 skip；compileall 与 diff-check 通过。

## 5. 工具、缓存、证据与上下文的硬约束

- 所有内置和 MCP 工具统一经过：参数校验 → pre-hooks → 对改写参数重新校验和授权 → checkpoint → 实际执行 → 工作区观察 → post-hooks → 持久化。
- 状态必须区分 `executed`、`cached`、`rejected`、`cancelled`、`indeterminate`；未执行不能伪装普通成功；读取缓存必须返回真实内容。
- 编辑、`run_command`、`verify`、外部副作用不得缓存。只读缓存必须有可验证有效性和失效机制。
- 工具批次默认顺序执行；某项失败后，剩余项明确未执行，由模型重规划；不隐式回滚已成功操作。
- 合法清理旧产物不能因为含删除动作就被视为无建设性；宽泛删除、未授权路径与受保护路径仍拒绝。
- `verify` 与普通命令共享执行能力，只额外登记验证证据；不得用第二套文件访问正则阻挡已经授权的验证。
- 普通编译/测试失败可以继续诊断；同类失败三次只追加一次策略复盘提示，不假报成功、不强制立即编辑。参数/协议错误最多纠正两次，程序失败不套用此上限。
- 完成检查只执行明确任务契约：要求产物、禁止修改范围、必须检查及其有效结果。验证命令“执行成功”和“覆盖用户目标”必须分开记录。
- 依赖安装或可能改变环境的普通命令使相关旧验证证据失效，但原命令允许重跑；历史失败不能被无关成功覆盖。
- 上下文按模型容量管理：约 80% 时压缩至约 60%；固定保留当前目标、用户约束、最新结构化摘要、未解决事项、失败原因、尝试与结果。工具调用与结果成对保留；大输出进入 artifact。
- 执行开始/结束/取消/预算耗尽/最终结果由内核自身持久化，不能依赖 CLI、飞书或 benchmark 消费事件后补写。

## 6. 固定十题与不可变评测配置

固定任务：

1. `break-filter-js-from-html`
2. `build-pov-ray`
3. `circuit-fibsqrt`
4. `compile-compcert`
5. `distribution-search`
6. `make-mips-interpreter`
7. `overfull-hbox`
8. `path-tracing`
9. `protein-assembly`
10. `video-processing`

配置：

- 模型：`gpt-5.6-luna`
- context：128000；max output：16384
- 十题总并发上限：**6**
- 每题模型调用预算：120；工具调用预算：240；时间预算：1800 秒
- Harbor retries：3
- setup multiplier：12
- 安装重试：8；间隔参数：30 秒
- 缓存：`D:\projects\forgecode\benchmark\.cache\harbor`
- 不使用 `--force-build`
- 数据集：`terminal-bench/terminal-bench-2@sha256:c6fc2e2382c1dbae99b2d5ecd2f4f4a60c3c01e0d84642d69b4afd92e99d078b`
- 不增加默认第二 verifier 智能体，不增加图像工具、长进程工具或新多智能体框架。
- 不加入隐藏测试答案，不按任务名称写解决方案，不用 benchmark 泄漏换分。

## 7. 评测账本（绝不能混淆）

### 7.1 旧候选 `e1787de`，2026-09-03

路径：`benchmark/runs/harbor/terminal-bench-2-runtime-v2-c6/2026-09-03__18-25-30`

- 9 个官方结果，CompCert 卡住。
- 原始通过 1 题：distribution。
- HTML verifier bootstrap 无效。
- 不是最终单内核，也不是有效验收轮。

### 7.2 旧基线 `eff5909`，2026-09-04

首次路径：`benchmark/runs/harbor/terminal-bench-2-baseline-eff5909-c6-20260904/2026-09-04__10-11-38`，代理中途消失，轮次无效。

恢复路径：`benchmark/runs/harbor/terminal-bench-2-baseline-eff5909-c6-restored-20260904/2026-09-04__10-33-13`

- 7 个官方结果均 0。
- distribution 因 NumPy 下载超时、video 因 OpenCV 下载超时，属于 verifier 环境失败。
- POV、CompCert 超时；protein 被旧恢复工具限制卡住。
- 用户批准停止；不是有效完整轮。

### 7.3 完整有效候选 `b9a3d7b`

路径：`benchmark/runs/harbor/terminal-bench-2-runtime-v2-b9a3d7b-c6/2026-09-04__11-55-36`

- 十题完成，无模型服务或 verifier 环境失败，无重试，无残留容器。
- 官方原始通过 **2/10**：`build-pov-ray`、`distribution-search`。
- 失败：HTML、circuit、CompCert、MIPS、overfull、path-tracing、protein、video。
- 聚合负载：352 model calls、460 tool calls、累计输入（含 cache）12,805,195、输出 147,720；这不是完整账单成本。
- 关键诊断：
  - HTML 仅执行一个只定义测试函数、没有调用测试的 Python 文件，空输出 exit 0 后错误完成。
  - path-tracing 打印 similarity 约 0.809，但没有阈值 assert 就完成。
  - video 只检查 TOML schema/range，未检查事件语义。
  - overfull 没有独立核验输入到输出的替换约束。
  - protein 的期望值由同一组已选择组件构造，属于循环验证。
  - circuit 是诚实失败。
  - MIPS/CompCert 主要是算法、构建和时间问题。
  - distribution 的独立断言较强。
  - POV 虽通过但成本高。

这些证据直接促成 `390af0b` 的证据修复。

### 7.4 `390af0b` 首轮与暂停轮（均无效）

首轮路径：`benchmark/runs/harbor/terminal-bench-2-runtime-v2-390af0b-c6/2026-09-04__15-35-21`

- 十题目录完成但模型服务严重不稳定：约 8 个 `server_error`、2 个 stream interruption；全部官方 reward 0。
- circuit 还存在 verifier uv bootstrap 失败。
- 不能用来判断代码退化，也不是有效验收轮。

暂停轮路径：`benchmark/runs/harbor/terminal-bench-2-runtime-v2-390af0b-c6-rerun1/2026-09-04__15-58-46`

- 用户要求“全部暂停”，已停止 controller 和 4 个活动容器，没有删除结果。
- 停止时 6 个官方 0、4 个未完成，重复 stream interruption/empty response。
- 当前没有残余 controller/container；该轮无效且不得继续当成完整结果。

### 7.5 最近网络恢复后的 `390af0b` 完整轮，2026-09-05

根目录：`benchmark/runs/harbor/terminal-bench-2-runtime-v2-390af0b-c6-network-restored-20260905`

job：`2026-09-05__14-24-02`

- 14:24 开始，15:10:29 结束；十题都有官方原始结果，没有残余容器。
- 官方 raw reward 为 **2/10**：
  - PASS `break-filter-js-from-html__VkRWGYt`
  - PASS `distribution-search__PSdMQNT`
  - 其余 8 题 raw 0
- summarizer 最终输出：10 trials、0 missing、1 infrastructure failure、9 scored、2 passes、6 agent failures、1 agent timeout、401 model calls、530 tool calls、累计 input 15,209,058、output 173,731。
- protein verifier 下载 uv 时 `curl (18)`，随后 `/root/.local/bin/env` 和 `uvx` 缺失，属于 `VerifierEnvironment:uv_bootstrap`，所以该题未真正完成 verifier。整轮虽 Harbor finished，但只有 9 题有效计分，不是可用于最终验收的完整有效轮。
- 逐题运行状态：
  - HTML：agent completed，31 model / 34 tools，官方通过。它从 b9 的假验证失败转为真实通过，与 dormant-test 防护后的行为一致，但没有完整因果实验时不要夸大因果。
  - distribution：completed，21/22，稳定通过。
  - POV：completed，48/77，官方失败，表现相对 b9 随机回退；先看日志和 session，不能据此写题名特判。
  - circuit：`incomplete_tool_call`，66/70。
  - CompCert：`time_budget_exhausted`，25/42，未到 verifier，也没有得到 ccomp。
  - MIPS：`empty_model_response`，68/95，后期高成本失败。
  - overfull：completed，51/58，有 PDF 且无 overflow，但官方输入约束仍失败。
  - path-tracing：`incomplete_tool_call`，35/69。
  - protein：agent completed，37/42，但 verifier 环境失败，不能断言其实现正确或错误。
  - video：completed，19/21，官方语义检查失败。
- 当前 summarizer 有已知分类缺陷：它输出 `model_service_failures: 0`，但 kernel payload 明确出现 `empty_model_response` 和 `incomplete_tool_call`；因此不能只依赖 Harbor exception 文本分类。它在某些旧场景还可能把同一个 timeout 从 Harbor exception 与 payload 重复计数。

当前结论：最新代码并非仍只有 1 题，最近原始结果是 2 题；但距离 5 题还有明显差距，而且最近轮有 verifier 基础设施失败，不能作为最终有效达标轮。

## 8. 关键证据文件

优先阅读：

- `D:\projects\forgecode\docs\reviews\2026-09-04-candidate-evidence-audit.md`
- `D:\projects\forgecode\docs\reviews\2026-09-04-baseline-stop-and-driver-repair.md`
- `D:\projects\forgecode\docs\reviews\2026-09-03-runtime-v2-first-run-diagnosis.md`
- `D:\projects\forgecode\docs\reviews\2026-09-03-runtime-architecture-audit.md`（用户未跟踪文件，只读）
- 最近结果：`D:\projects\forgecode\benchmark\runs\harbor\terminal-bench-2-runtime-v2-390af0b-c6-network-restored-20260905\2026-09-05__14-24-02`

相关代码：

- `D:\projects\forgecode\forge\runtime\runner.py`
- `D:\projects\forgecode\forge\tools\verification_checks.py`
- `D:\projects\forgecode\benchmark\harbor\summarize.py`
- `D:\projects\forgecode\benchmark\harbor\process_supervisor.py`
- `D:\projects\forgecode\benchmark\harbor\bounded_docker.py`

不要只读聚合 summary；逐题诊断时需同时查看官方 reward、verifier 输出、kernel final payload、session/trajectory 和实际改动。不要把 verifier bootstrap 失败算成 agent 逻辑失败。

## 9. 下一阶段的严格优先顺序

除非用户明确改变方向，下一阶段应按以下顺序推进，而且在完成修复与回归前不要再发起昂贵全量评测：

### A. 修复评测分类器，先让测量可信

1. 从 kernel final payload 识别 `server_error`、`stream_interrupted`、`empty_model_response`、`incomplete_tool_call` 等模型服务/协议故障，不只依赖 Harbor exception 文本。
2. 同一 timeout 不得被 Harbor exception 和 kernel payload 重复计数。
3. 原始 reward、有效计分数、agent failure、agent timeout、model/protocol failure、verifier environment failure 必须同时保留、互不冒充。
4. 用这几轮真实日志模式写回归测试。

### B. 研究模型流与协议可靠性

最近即使网络恢复仍出现 circuit/path 的 `incomplete_tool_call` 和 MIPS 的 `empty_model_response`。应检查流事件、持久化边界和重试语义。

安全原则：只有在没有持久 assistant 输出、没有完整或部分工具调用、没有已发生副作用时，才可能安全重试空/断流请求。若已有部分输出或副作用，不得自动重放以免重复执行。不要为提高分数盲目重试所有 partial stream。

需要区分：供应商/网络故障、协议拼装 bug、模型真实返回空、预算耗尽和 agent 策略失败。为每类加入确定性单元/契约测试。

### C. 解决重复出现的“计划要求没有落到证据”问题

overfull、video 在新增通用提示后仍以 completed 结束但官方语义/约束失败，说明问题不只是措辞，而可能是计划验收标准没有被结构化地关联到完成证据。

可研究通用的 requirement-to-evidence reconciliation：

- 任务合同中的明确要求必须有对应证据或明确未验证状态；
- 来源必须可追溯到用户原始任务/入口合同，不能让模型计划自动扩大用户授权；
- 不依赖自然语言关键词或脆弱的 covers 标签匹配；
- 不按题名、文件名或隐藏答案写规则；
- 对独立断言、输出阈值、输入到输出约束、测试实际执行提供通用机制。

先对 HTML、overfull、video、protein、path 的 session 做对比：哪里提出了正确验收标准，哪里丢失，哪里错误地被 completion 接受。根据事实决定是 ContextManager、EvidenceLedger、completion contract 还是工具结果结构需要调整。

### D. 算法/时间类问题最后处理

- CompCert：时间预算内没有形成 ccomp，先分解下载/配置/编译/修补时间和 agent 决策成本，不能只加 1800 秒。
- MIPS：高成本后 empty response；先解决协议可靠性，再判断算法策略。
- POV：b9 通过、390 回退，先比较两轮轨迹找稳定性差异，不进行题名特判。
- circuit/path：先排除 incomplete tool-call 的内核/模型协议问题，再评价算法能力。

### E. 修改后的门槛

每项改动都必须：

1. 有具体日志证据指向根因；
2. 写失败回归测试，确认旧代码可复现问题；
3. 做最小但架构正确的实现；
4. 先跑相关测试，再跑 Windows/Linux 完整测试、compileall、diff-check；
5. 记录修改对成本、可靠性和兼容性的影响；
6. 完成一轮详细审查后，用户明确要求时才启动下一轮十题。

## 10. 操作与安全规则

- 使用 PowerShell；当前时区 Asia/Shanghai。
- 文件编辑使用 `apply_patch`。
- 搜索优先 `rg` / `rg --files`。
- 保留用户工作树改动；遇到重叠先分析，不用 `git reset --hard` 或 `git checkout --`。
- 不读取或输出 `.env`、token、代理密码等秘密；最多报告配置键是否存在。
- 停止或删除进程/容器前解析精确目标；递归删除前校验绝对路径位于预期 workspace；不要宽泛杀进程或删除缓存。
- 运行 benchmark 时监控实际 agent/controller 状态，不能仅因容器存活就宣称仍在工作。
- 工具工作超过约 60 秒要给用户简短进展，但不要反复汇报无变化状态。
- 当前没有要求创建新 Codex task、自动化或 heartbeat；不要自行创建。
- 当前目标尚未达成，不要标记 complete。

## 11. 新窗口建议的首条回复

可以这样回复用户：

“我已完整读取交接上下文。当前没有运行中的评测；分支是 `codex/runtime-v2`，候选提交为 `390af0b`。最近网络恢复后的十题轮次 Harbor 已结束，官方 raw 2/10（HTML、distribution），但 protein verifier bootstrap 失败，因此只有 9 题有效计分，不能作为最终有效轮。下一步不应立即重跑，而应先修正结果分类器，深入排查 `empty_model_response` / `incomplete_tool_call` 的安全恢复边界，并对 overfull、video 等‘计划要求未绑定有效证据却完成’的问题做通用架构修复。若你让我继续，我会先从真实 session 和回归测试入手。”
