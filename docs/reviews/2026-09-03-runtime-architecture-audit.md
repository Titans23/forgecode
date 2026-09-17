ForgeCode 运行时与评测架构审计（2026-09-03）

审计版本：`eff5909`。本轮依据生产代码、Git 差异、固定任务的官方 verifier 输出与 session JSONL，进行只读分析和离线复现；未修改生产代码、未启动新一轮 benchmark。

结论：存在会直接破坏正常调试的严重运行时逻辑缺陷，也存在恢复控制、上下文保留和完成判定之间的架构冲突。此前数轮改动主要扩充门禁和恢复提示，未验证工具声明、实际执行、证据保留这一整条链路。不能把成绩停滞全部归因于模型能力，也不能保证修复这些缺陷就一定达到 5/10。

一、先校准成绩与成本

本地找到的完整 2/10 记录是 [phase2 结果](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-phase2-concurrency2/2026-09-01__13-01-00/result.json)，通过的是 `bn-fit-modify` 和 `build-pmars`。这两题均不在当前固定十题中。旧、新两组仅共同包含 `break-filter-js-from-html`、`build-pov-ray`、`circuit-fibsqrt` 三题。因此，这条记录不能证明同一组任务从 2/10 退化为 1/10。

当前十题从 stage3 开始多轮仅 `distribution-search` 通过，期间还出现过 0/10。这足以支持“没有可验证的能力进展”。

对 [9 月 2 日 16:58 轮](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-stage3-concurrency4/2026-09-02__16-58-35/result.json) 与 [最新轮](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-task-contract-c6/2026-09-03__14-41-48/result.json)，仅比较双方都有最终 ForgeCode 结果的同样八题，排除缺失内部结果的 MIPS 与 protein：

| 指标 | 较早轮 | 最新轮 | 变化 |
| --- | ---: | ---: | ---: |
| 模型调用 | 130 | 420 | 3.23 倍 |
| 工具调用 | 162 | 488 | 3.01 倍 |
| 总输入 token（含缓存读写） | 1,103,595 | 4,657,273 | 4.22 倍 |
| 官方通过的共同任务 | distribution-search | distribution-search | 没有新增 |

这是描述性比较：两轮并发等配置并非严格控制变量，不能单凭它估计某一个补丁的因果效应。工具调用统计还包含被拦截或复用的调用，不等同于实际执行次数。最新轮上述成本也不包括两个超时任务，不能当作全十题总成本。

二、已确认的缺陷与证据

1. 高优先级：工具声明与实际调度互相矛盾，未执行却返回成功。

位置：[phase.py](D:/projects/forgecode/forge/runtime/phase.py:119)、[agent_loop.py](D:/projects/forgecode/forge/runtime/agent_loop.py:1448)。验证失败后进入 `verify/act`；phase 层把 `read_file`、`grep`、`verify` 列为可用，主循环随后又拦截这些调用，返回 `ToolResult.ok`、`status=already_completed`、`verification_read_closed=true`，内容为空。这个分支不检查本次失败后是否真的完成了针对性读取。

最新轮真实轨迹中，这类空成功发生：CompCert 10 次、电路 6 次、POV-Ray 2 次、MIPS 2 次、path-tracing 2 次、overfull 1 次。电路 session 的 sequence 244 请求读取 `sim.c` 的失败相关范围，得到空成功；sequence 280 的 blocked 声明明确提及诊断工具被关闭。CompCert 的 sequence 474 同样无法读到待修改的 Coq 源码。

离线复现：先返回失败 verify，再请求一个从未读取的诊断文件。第二轮工具列表包含 read_file；但实际 read_file 执行次数为 0，返回 success=true、content=""。

影响：模型被要求修复，却拿不到修复所需信息，容易猜测补丁、反复失败或认为工具不可用。前几轮仅扩大工具列表的修复没有消除这一拦截。旧拦截主体可追溯到 `2b4e1b1`；`63fd85e` 将其接入新的 RecoveryState，因此不是最新一次改动才出现的问题。

2. 高优先级：失败命令缓存不识别环境变化，阻止依赖恢复后的正确重试。

位置：[重复调用判断](D:/projects/forgecode/forge/runtime/agent_loop.py:1768)、[调用签名](D:/projects/forgecode/forge/runtime/agent_loop.py:4753)。缓存键只有工作区 revision、工具名和参数；同一调用失败一次就会被拒绝。安装系统或 Python 环境依赖通常不改变 `/app` 文件 revision。

最新 video session 的完整链路：sequence 31 因缺少 toml 验证失败；sequence 35 执行 `python -m pip install toml` 成功；sequence 39 重跑原命令被 `repeated_tool_call` 拦截；sequence 43 模型改写命令后才能继续。

离线复现得到相同结果：依赖安装 stub 已执行，验证 stub 本应成功，但只执行过第一次失败调用。

影响：正常的“安装依赖—重跑同一个测试”被当作停滞；促使模型改变命令文字，而非保持稳定验证目标。需要区分文件版本、执行环境变化与外部状态变化；有副作用或依赖外部环境的命令不应按纯函数缓存失败。

3. 高优先级：成功编辑按整个 turn 去重，回滚后重新应用会丢失。

位置：[成功编辑复用](D:/projects/forgecode/forge/runtime/agent_loop.py:1645)、[写入缓存](D:/projects/forgecode/forge/runtime/agent_loop.py:1932)、[不含 revision 的 identity](D:/projects/forgecode/forge/runtime/agent_loop.py:4772)。`completed_workspace_calls` 只记录调用参数，命中后跳过执行，不检查目标当前内容。

离线复现 `alpha → beta → alpha → beta`：三次调用均报告成功，工具实际只执行两次，最终内容仍为 alpha。正常的回退、比较方案、再应用变更因此不可靠。

CompCert sequence 478 也出现了相同编辑被跳过的记录；仅凭这条日志不能证明当时内容确实需要重写，故“该次跳过是否直接导致失败”仍未确认。机制错误由离线复现独立证实。

修复原则：编辑复用必须验证当前内容满足后置条件，或绑定目标内容哈希；不能用“历史上执行成功过”替代“现在已经生效”。

4. 高优先级：上下文压缩丢失调试记忆，且会再次删除压缩摘要。

位置：[默认窗口](D:/projects/forgecode/forge/context/compactor.py:44)、[直接裁剪](D:/projects/forgecode/forge/context/compactor.py:376)、[清空旧工具输出](D:/projects/forgecode/forge/context/compactor.py:602)、[摘要前清空输出](D:/projects/forgecode/forge/context/manager.py:337)。

默认超过 24 条消息就裁剪，保留最近约 16 条与最多 4 个文件读取单元；这与 128k token 上限无关。旧的 run_command/verify 输出超过 120 字符，除最近 3 个工具结果外，可以直接替换成清空标记，失败输出也不例外。正式生成摘要之前同样先做这种清空，摘要模型可能看不到真正的报错原因。

正式摘要以首条普通 user message 保存，但 `keep_first_messages=0`，没有摘要锚定规则。离线构造摘要加 30 条后续消息后，摘要不再出现在模型可见历史中。

需要区分：原始任务 goal 会由 [TaskManager](D:/projects/forgecode/forge/tasks/manager.py:387) 重新注入，并非整个任务原文必然消失。真正没有可靠保存的是调试发现、已经失败的方法、选择某个方案的理由、完整诊断和摘要本身。WorkingState 的失败列表仅保留最近五个“工具名+错误码”，不足以代替这些内容。

最新 CompCert 做过一次正式压缩，MIPS 两次；这些长任务暴露于该机制。可确认信息丢失风险与实现行为，但不能从现有日志精确归因每一次后续错误。

5. 高优先级：权限分类被拿来推断任务进展，误拦正常清理后验证。

位置：[权限分类](D:/projects/forgecode/forge/permissions/risk.py:29)、[删除批次判定](D:/projects/forgecode/forge/runtime/agent_loop.py:1282)、[拦截](D:/projects/forgecode/forge/runtime/agent_loop.py:1514)。包含删除动作的整条命令被分类为 file.delete，主循环进一步把它解释成“本轮只删除了文件，没有建设性修改”。

MIPS 最新轨迹有 14 次这类拦截，包括 `rm -f /tmp/frame.bmp && node vm.js && test -s /tmp/frame.bmp` 及带 BMP 断言的完整验证链。清理上次产物本来是证明本次运行确实产出新帧的步骤，却被拒绝。verify 自己还具有另一套禁止文件变动的过滤规则，单独改掉前一层仍可能被后一层拦住。

权限判断“这里包含删除，应检查目标和授权”是合理的；把该权限类别等同于“整个操作没有实现任务”则错误。应独立处理安全授权、执行效果与任务完成，不应放宽宽泛目录删除或其他真正危险操作。

6. 中高优先级：完成判定试图从命令和总结措辞推断语义，既漏判又误判。

位置：[verification.py](D:/projects/forgecode/forge/runtime/verification.py:10)、[CompletionGate](D:/projects/forgecode/forge/runtime/completion.py:122)、[总结文字门禁](D:/projects/forgecode/forge/runtime/agent_loop.py:3936)。

离线验证发现：`python -c "raise SystemExit(1)"; printf OK` 被归类为 behavior；给定该 shell 链最后退出 0，positive verification 门禁允许完成。普通的“All tests pass and the public API still works.”又因为包含 still，被判断为尚有未解决缺陷。

最新内部 completed 有四题，官方只有 distribution-search 通过：HTML 的本地 sanity check 没证明浏览器行为；video 仅验证输出结构和帧号范围，没有验证真实起落时刻；overfull 验证了无溢出，却未满足允许的文字替换约束。这些都是语义或覆盖不足，追加命令关键词无法解决。

此外，[agent_loop.py](D:/projects/forgecode/forge/runtime/agent_loop.py:3452) 在确定性门禁满足后启动仅 3 次的完成决策倒计时，达到限制会关闭工具。弱验证一旦被认可，后续合理诊断也可能被催促结束。这是架构风险，尚未证明它直接造成最新三题的错误完成。

建议：将命令执行证据与任务验收覆盖分开。保存实际执行、退出状态、产物和断言；只有明确配置的验收条件才能做硬门禁。未知命令应表示证据类型未知，不能默认视为充分行为证明。普通总结中的单个词不应决定执行权限或成功状态。

7. 中高优先级：恢复状态与“进展”判断未形成一致的收敛机制。

`agent_loop.py` 共 5,854 行；单个 stream 方法 3,564 行，包含 232 个 if 分支。行数本身不是缺陷，但上面的工具声明/执行冲突说明拆出 phase.py 后仍保留了第二套甚至多套决策。

位置：[失败后重置计数](D:/projects/forgecode/forge/runtime/agent_loop.py:3184)、[任何文件变化清空验证债务](D:/projects/forgecode/forge/runtime/agent_loop.py:2094)、[恢复指纹](D:/projects/forgecode/forge/runtime/recovery.py:100)。每次失败验证重新激活恢复并将 calls_without_progress 清零；任意被追踪文件变化又会清空部分恢复状态。指纹实际常收到“Verification exited with code 1”，没有关键 stderr，因此既难区分不同根因，又可能把日志或生成物变化当作有意义进展。

结果是系统对“再次读文件”“同命令重试”等正常动作过严，却允许大量无效微小编辑延续到调用或时间上限。CompCert 121 次模型调用、电路 73 次、overfull 97 次；MIPS 与 protein 超时。不能简单把预算加大当作修复。

修复应记录根因、尝试过的策略、环境变化、实际产物进展；重复失败时要求重新解释假设和选择下一步，而非把工具隐藏或假报成功。恢复预算应覆盖整段恢复过程，不能每遇到一次失败就重新获得预算。

8. 评测与回归测试存在盲区。

最新 path-tracing 官方 reward 为 0，但 [verifier 输出](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-task-contract-c6/2026-09-03__14-41-48/path-tracing__oUuvJCr/verifier/test-stdout.txt) 显示下载 uv 失败，随后 `uvx: command not found`，并没有真正完成测试；trial 的 exception_info 却为 null。这一轮不能写成“已确认相似度不达标”。agent 自身也未验证成功，因此同样不能据此补记通过。应标记“官方记录 0，verifier 基础设施失败，产物正确性未知”。

当前 [summarize.py](D:/projects/forgecode/benchmark/harbor/summarize.py:110) 主要靠 exception_info 区分基础设施失败，因此这种脚本级环境错误可能被算成普通代码失败。AgentTimeoutError 也不应一律解释成外部基础设施故障：它可能来自 agent 无效循环。

此前 544 项通过不能证明真实调试链正确。[测试差异](D:/projects/forgecode/tests/runtime/test_m2_agent_loop.py:1659) 显示 `9451fa8` 的验证恢复集成测试移除了失败后实际读取的中间步骤，改为直接编辑；新增 phase 测试主要断言工具在列表里，没有断言真正执行并返回内容。因此“宣称工具开放，实际吞掉调用”恰好不在覆盖范围。

三、其他值得保留的风险，尚不作为本次成绩的确定根因

- 默认工具集只提供文本读取和字符串结果，没有直接把图片或视频帧送入模型的内置工具。对图像/视频任务会限制诊断方式，但不能由此断言这些任务无法用程序分析完成。[工具注册](D:/projects/forgecode/forge/tools/__init__.py:56)、[文本读取](D:/projects/forgecode/forge/tools/filesystem.py:281)。
- 无 Git 时每次工具后遍历并哈希工作区；有 Git 时对脏文件也重复哈希。大型源码、视频和生成物可能增加开销；本轮未做性能剖析，不能把超时直接归因于这里。[workspace.py](D:/projects/forgecode/forge/runtime/workspace.py:139)。
- 进程输出限额保留开头，超过 1 MB 后的末尾诊断可能在进入上下文系统前就丢失。后续上下文 head/tail 摘取无法恢复已经被丢弃的尾部。[shell.py](D:/projects/forgecode/forge/tools/shell.py:127)。
- 最新版本加入了按具体任务内容匹配的提示，以及所有 benchmark 共用的 Overfull 正则。这些提醒没有写隐藏测试答案，但更接近任务专用适配，无法证明通用 agent 能力改善。[forgecode_agent.py](D:/projects/forgecode/benchmark/harbor/forgecode_agent.py:46)、[run_forge.py](D:/projects/forgecode/benchmark/harbor/run_forge.py:34)。

四、建议的修复顺序与验收方式

第一阶段：先恢复执行语义可信度。把工具授权与调度集中到一个执行入口；模型可见工具与执行策略一致。未执行必须明确标记未执行，不能返回普通成功。修复环境变化后的重试和编辑缓存失效；合理的任务产物清理按确切目标授权。先用本报告的离线链路作为回归门槛。

第二阶段：修复上下文生命周期。永久保留当前摘要、用户约束和未解决问题；失败诊断先提取根因与尝试结果，再做压缩；不能让便宜裁剪删除正式摘要。文件缓存仅表示可恢复的内容，必须真能返回内容，不能一面删除历史一面要求模型复用被删除的证据。

第三阶段：缩减恢复与完成门禁职责。安全授权、预算、执行事实适合确定性控制；调试策略与语义判断需要模型和任务证据。恢复优先给出失败事实与建议，不默认关闭诊断工具。完成判定区分“执行过验证”与“覆盖了目标”，移除总结关键词硬判断和未经目标确认的强制结束倒计时。保留 revision 绑定、真实权限边界、明确任务约束等有效设计。

第四阶段：做可解释的对照。固定当前十题、模型、上下文与输出上限、调用和时间预算、数据集版本及并发 6；记录代码快照。先比较“修复上述缺陷的版本”与“保留相同工具及权限、缩减强制恢复/结束规则的版本”，每次仅变化一个因素。若差别只有一题，安排配对重复而非宣称显著提升。每轮报告官方 reward、verifier 是否真正执行、内部完成误报、真实执行/被抑制调用数、失败根因与成本。

只有这些契约级复现通过后，才值得投入下一轮完整十题评测。当前发现说明应先修执行基础与状态设计；无需因此重写文件工具、模型适配和所有已有模块，也没有证据支持继续叠加 benchmark 提示就能达到 5/10。

五、可复查的离线诊断

[诊断脚本](D:/projects/forgecode/.tmp/architecture_audit_probes.py) 执行了 8 项离线探针，未调用模型、网络或 benchmark。它输出观察结果；进程 exit 0 表示探针运行完毕，不表示生产代码正确。观察到的 8 项结果均与本报告相符，其中清理命令探针验证分类，其实际拦截由真实 session 佐证。

```text
python .tmp/architecture_audit_probes.py

failed_verify_then_read: read_file 在工具列表中，实际执行 0 次，返回成功和空内容
dependency_retry: 安装后原 verify 仍被 repeated_tool_call 拦截
reapply_after_revert: 预期 beta，实际 alpha，第三次编辑被当作已完成
summary_survival: 31 条消息裁剪到 17 条，摘要不再存在
failed_evidence_survival: 前两条失败输出被清空标记替换
cleanup_and_verify_classification: 清理+运行+断言整体被归为 file.delete
masked_failure_gate: 被归为 behavior，完成门禁 allowed=true
successful_summary_false_rejection: 含 still works 的成功总结被误拒绝
```

[轨迹统计脚本](D:/projects/forgecode/.tmp/audit_forgecode.py) 可重算结果与查看具体调用：

```text
python .tmp/audit_forgecode.py runs
python .tmp/audit_forgecode.py compare
python .tmp/audit_forgecode.py sessions --task circuit --contains verification_read_closed --limit 6
python .tmp/audit_forgecode.py sessions --task make-mips --contains delete_only_batch --limit 14
python .tmp/audit_forgecode.py sessions --task video --limit 13
```

两个脚本及微型测试夹具位于 `.tmp`，便于复查；报告位于 `docs/reviews`。没有修复生产代码，也没有提交、推送或启动新评测。
