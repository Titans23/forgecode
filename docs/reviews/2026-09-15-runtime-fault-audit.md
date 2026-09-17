# 核心重构后的 Harness 故障审计

日期：2026-09-15。本次只审计当前实现与已完成轨迹，没有修改产品代码，没有调用真实模型或启动评测。

对象：`benchmark/runs/harbor/terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35`。冻结源码位于该 run 根目录 `.source-snapshots/candidate-w_x1iao8/source`。已比较 runner、providers、model_budget、delivery、run_dataset 五个文件的 SHA-256，当前工作树与评测快照完全一致，因此以下机制确实存在于受测版本。

## 结论与证据等级

发现五类具体问题，并新增六个独立离线故障断言：**0 通过、6 失败，无 setup error**。这些断言保留期望行为，不使用 xfail，不修改断言以迎合实现。

测试文件：[benchmark/analysis/test_fault_probes.py](D:/projects/forgecode/benchmark/analysis/test_fault_probes.py)。`pyproject.toml` 默认 `testpaths=['tests']`，因此该诊断套件不加入默认产品 pytest 集合。之前的 649 项通过说明已有测试覆盖范围内未失败，不能覆盖这六个新发现。

最后一次运行耗时 3.28 秒。运行命令为 `.venv/Scripts/python.exe -m pytest benchmark/analysis/test_fault_probes.py -q --basetemp <本次唯一目录> --tb=line`，临时目录位于 `D:/projects/forgecode/benchmark/.cache/audit-tests/fault-*`。最初运行因父临时目录不存在发生两项 setup error；建立指定父目录后重新运行，六项均在实际断言处失败，最终统计不包含准备错误。

| 故障断言 | 期望 | 实际 | 与本轮关联 |
|---|---|---|---|
| 子代理执行受父工具额度限制 | 父额度耗尽后零次执行 | read-only 探针仍执行一次 | 已证实代码缺陷；未证明本轮触发 |
| DeepSeek 截断保留已给 usage | 输入91、输出17 | 全零，unknown_usage=1 | native 路径缺陷；本轮未用 native |
| Responses incomplete 保留 usage | 输入91、输出17 | 全零，unknown_usage=1 | native 路径缺陷；本轮未用 native |
| 交付报告遵守显式检查替代 | 旧失败被标为已解决 | 仍列入 failed_checks | 已证实投影不一致；未证明本轮触发相同替代链 |
| reasoning 进程配置进入评测配置 | high 被读取 | None | wire 与容器配置确认未传递 |
| 单一 Python heredoc 不被当成退出码掩盖 | behavior | unknown | circuit 实际轨迹明确触发 |

## 1. P1：父工具额度只累计，执行入口没有落实父级检查

`TurnState.record_tool_request` 已向父状态累计，`can_request_tool_batch` 也能递归判断父级；但真正 `_batch` 只检查当前 runner 的 `max_tool_calls`。随后 `budget_reason` 检查时间、token、模型次数，仍不检查父工具额度。

证据：[runner.py:430](D:/projects/forgecode/forge/runtime/runner.py:430)、[turn_state.py:139](D:/projects/forgecode/forge/runtime/turn_state.py:139)、[turn_state.py:151](D:/projects/forgecode/forge/runtime/turn_state.py:151)、[turn_state.py:178](D:/projects/forgecode/forge/runtime/turn_state.py:178)。

复现按照真实流程先记录模型提出的 tool request，再进入 runner._batch；父级 max_tool_calls=1 且已经消耗1次，子代理的一个 read-only 工具仍执行。现有 `test_shared_budget.py` 只检查 helper 返回值，没有覆盖真正 dispatch。

建议：在同一执行边界检查所有祖先的已计费请求与额度，区分“请求计数”和“执行预留”；不能在已经记录请求后再把当前 batch 数量重复加一次。增加父额度在 batch 中途耗尽、子局部额度更宽、多层子代理、未执行尾部仍有审计记录的覆盖。

## 2. P1：原生提供方在截断路径丢弃已收到的 token 用量

DeepSeek `_chat` 把 usage 存到局部变量，但在 finish=length 时先抛出截断异常，尚未 yield ModelUsageUpdate。Responses 在 response.incomplete 直接抛错，也没有读取同一 response 附带的 usage。BudgetedModelClient 因而只能记录 unknown，而不是累计提供方已明确给出的费用事实。

证据：[providers.py:193](D:/projects/forgecode/forge/runtime/providers.py:193)、[providers.py:218](D:/projects/forgecode/forge/runtime/providers.py:218)、[providers.py:225](D:/projects/forgecode/forge/runtime/providers.py:225)、[providers.py:247](D:/projects/forgecode/forge/runtime/providers.py:247)。

两个无网络 stream stub 分别带输入91和输出17；两个异常都如期抛出，工具也没有执行，但最终统计都是零，并把 unknown_usage_requests 加一。问题不是让失败响应继续执行工具，而是把“可记录的计费用量”错误地与“可执行的完整输出”绑定。

建议：usage 独立于语义成功状态进入审计和预算；抛协议/截断异常前保留已收到的累计值。使用累计值需避免 chunk 与 terminal 重复相加。补充失败、断流、缺失终态但已有 usage、重试前后 usage 和取消路径。

本轮所有已解析 wire 都走 `/v1/messages`，所以此缺陷不能解释本轮题目失败；它是新增 native provider 支持中的未测风险。

## 3. P2：Delivery 与 CompletionGate 对同一检查替代关系给出相反结论

`checker_revision_covers` 能确认一个修复后的成功检查完整继承旧断言，并解除旧失败。`completion_report` 却仅用 command/cwd/stdin/check_signature 做 latest 分组，忽略 supersedes。由于修复检查通常改变 command，它会在一个报告中同时列出“验收满足”和“仍有失败检查”。

证据：[completion.py:450](D:/projects/forgecode/forge/runtime/completion.py:450)、[delivery.py:36](D:/projects/forgecode/forge/runtime/delivery.py:36)。

离线记录 old-run 失败、fixed-run 成功且完整继承原断言；`unresolved_verification_failures` 返回空，报告仍给出 `failed_checks=('old-run',)` 和 `verification_status='failed_checks'`。

建议：完整保留历史运行，但复用同一 resolved/unresolved 投影；报告另设 resolved_checks，避免把历史事实删除，也避免再次变成当前失败。门禁与 UI 不能各自实现一套义务消解规则。

## 4. P1（评测配置可信度）：reasoning 没有从运行设置传到 benchmark wire

已读取全部 **419 个 wire snapshot**，包括69个外置 payload 文件，并校验外置引用 SHA-256；全部请求路径为 `/v1/messages`。419个请求都没有 `reasoning`、`reasoning_effort`、`output_config` 或 `thinking` 字段。

容器 agent env 仅包含 `FORGECODE_API_KEY`、`FORGECODE_MODEL`、`FORGECODE_BASE_URL`、`FORGECODE_MODEL_MAX_TOKENS`、`FORGECODE_CONTEXT_WINDOW`、`FORGECODE_PROVIDER`。读取过程中没有输出 credential 值。

代码中本地 ForgeConfig 可以读取 `FORGE_REASONING_EFFORT`，AnthropicModelClient 也能把它映射为 output_config.effort，但：

- run_dataset.configured_values 的进程环境覆盖列表不含该键；
- build_command 没有 reasoning 参数和对应 `--ae`；
- agent shell 没有 reasoning 导出。

证据：[config.py:138](D:/projects/forgecode/forge/config.py:138)、[run_dataset.py:157](D:/projects/forgecode/benchmark/harbor/run_dataset.py:157)、[run_dataset.py:137](D:/projects/forgecode/benchmark/harbor/run_dataset.py:137)、[forgecode_agent.py:276](D:/projects/forgecode/benchmark/harbor/forgecode_agent.py:276)。

离线测试设置 process FORGE_REASONING_EFFORT=high，使用自行创建的不含密钥的配置文件，configured_values 返回中不存在该设置。

**限制：没有显式 reasoning 字段不等于后端没有进行推理，也不能据此认定模型名称映射错误。** 网关可能有自身默认值，现有证据不能推出实际模型思考量。但本轮不能声称测到了某个显式推理强度，也不能用客户端界面里选的强度代替评测 wire 事实。

建议：评测配置保存 provider、model、显式 reasoning 或明确 default、输出上限、上下文配置；将同一配置穿过 build_command→容器→实际 wire 的离线集成测试补齐。不要自动替用户改模型或启动新的对照评测。

## 5. P1（验收误判）：合法 Python heredoc 被当成 shell 掩盖失败

`has_unsafe_shell_chain` 遇到引号外换行就返回 True；它没有识别 heredoc 边界，因此 stdin 内的 Python 换行会被误当作 shell 多命令连接。一个不含失败掩盖、明确断言并打印 JSON 的单一 Python heredoc 被分类为 unknown。

证据：[verification.py:53](D:/projects/forgecode/forge/runtime/verification.py:53)、[verification.py:66](D:/projects/forgecode/forge/runtime/verification.py:66)。

此缺陷在最新 circuit-fibsqrt 真实轨迹中可直接核对：

- 序号607：run `3ef453801f0c4a62bc467c7411ee3519`，exit0，但最后输出不符合所声明 JSON 断言要求；这一次 evidence_valid=False 有真实原因，不能一概放行。
- 序号619：run `0cc7da1c967c4031861747ed46fe3438`，修正输出后 exit0、evidence_valid=True，带两条具体 coverage；命令仍是 `python3 - <<'PY'`，静态分类仍是 unknown。
- 该第二次记录明确声明了 gate文件格式/数量以及运行 supplied simulator 对照独立 Fibonacci 计算两类覆盖；因此结束时“没有 declared coverage”应解释为“当前分类规则不承认这些 coverage”，不能说模型没提供 covers。

不需要执行 benchmark 产物即可复现：对原命令调用 verification_quality，结果 unknown。新增诊断用缩减后的安全单程序 heredoc 保留同一触发条件。

建议：优先使用结构化 program+stdin，绕开 shell 文本启发式；兼容 heredoc 时正确解析外层 shell 语法，只判断外层控制流。不要简单把全部多行命令改为可信。对 Python body 中分号、引号、管道字符、嵌套 shell 与真实 `false; true` 分别建立对照测试。

## Freshness 与交付行为的审计边界

新的跨 turn evidence 恢复已保留历史 ID，且把恢复证据标为 unknown，避免 revision=0 误判当前；与上次架构评审相比，这是实际改善。

但 process freshness 仍沿用工具名规则：非 verify 的任意 process 全局增加环境 epoch，verify 的任意 process 不增；ignored 文件、环境安装及工作区外允许副作用的依赖仍不被精细跟踪。见 [executor.py:265](D:/projects/forgecode/forge/runtime/executor.py:265)。这是已知设计限制，本次没有以一个期望尚未定义的新 freshness 断言宣称发现新回归，也没有把它直接等同于本轮官方失分。

当前 finish submission 结束运行并单独报告 acceptance 的方向可以保留；不能因为四道官方通过内部都 partial，就回到无限拒绝 finish。应先区分真实未满足、分类误判、过期证据和模型自评，不把“降低内部 partial 数量”作为性能目标。

后续优先顺序：修复评测配置传递及真实 heredoc 分类误判；统一 delivery/gate投影；补父级执行预算与 native失败usage；随后用已有轨迹回放和离线协议测试验证。是否启动新评测由用户另行决定，本次没有启动。
