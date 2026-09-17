# 2026-09-15 并发 2 评测：三项产物失败的只读取证

本报告分析 `terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35` 中 video-processing、protein-assembly、path-tracing，并以 build-pov-ray、distribution-search 为成功对照。只读历史日志及生成工具的输入；没有执行题目产物、访问模型、联网获取题目答案或重新评测。官方测试中的参考值只用于事后诊断，不应输入下一次 agent 评测。

## 结论

三个失败均有具体的产物错误，不能用“模型不够强”或“基础设施失败”替代解释：视频检测连公开例片也识别错误；序列产物只有一个组件缺失其参考序列前缀；C 程序把计算委托给交付环境之外的可执行文件。共同薄弱点是**把容易断言的代理指标，当成了用户真正要求的行为**。

本次完成报告已经避免简单声称全部成功，但验收失败后直接终止，尚未把真实缺口转化为一次有预算约束的修复动作。同时，它会把正确产物也标记 `partial/failed_checks`，因此当前内部验收既没有可靠预测官方得分，也没有持续促成修复。这里支持优先修改证据、验证与终止之间的关系；这三题本身不能证明需要推翻整个项目。

## 数据和证据定位

以下简称均指完整 journal；`L` 是物理行号，脚本已检查其与 `sequence` 一致。

- V：[video journal](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35/video-processing__X2E6baX/agent/session-14a084b6f2c4487e8113124d.jsonl)。[官方测试输出](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35/video-processing__X2E6baX/verifier/test-stdout.txt)。
- P：[protein journal](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35/protein-assembly__ZMHnUsS/agent/session-a84ab31daa474b6f8d64f581.jsonl)。[官方测试输出](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35/protein-assembly__ZMHnUsS/verifier/test-stdout.txt)。
- R：[path-tracing journal](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35/path-tracing__JWkBJWH/agent/session-77ed8dad05344bc9a661f0c8.jsonl)。
- B：[build-pov-ray journal](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35/build-pov-ray__BbH3erp/agent/session-e1ec7652824649c6be2e347d.jsonl)。
- D：[distribution-search journal](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35/distribution-search__mTqCyFe/agent/session-2ce0141048e3471da189b0dd.jsonl)。

统计来自各任务 `turn_completed`，不是外部模型计费表。`input` 与 `cache_read` 按日志原字段分别列出，不能从中直接推断收费或模型内部推理档位。

| 题目 | 官方结果 | 模型请求 | 工具请求 | agent 内核耗时 | 模型/工具耗时 | input / output / cache_read |
|---|---:|---:|---:|---:|---:|---:|
| video-processing | 0 | 22 | 25 | 385.1 s | 345.1 / 24.5 s | 570,884 / 14,463 / 112,128 |
| protein-assembly | 0 | 48 | 53 | 1,370.0 s | 926.7 / 392.5 s | 1,913,394 / 38,773 / 295,936 |
| path-tracing | 0 | 20 | 36 | 313.4 s | 267.4 / 32.4 s | 488,929 / 10,078 / 104,448 |
| build-pov-ray | 1 | 69 | 72 | 1,264.7 s | 560.3 / 572.8 s | 2,243,064 / 14,408 / 336,384 |
| distribution-search | 1 | 17 | 20 | 269.8 s | 230.2 / 29.4 s | 210,246 / 9,156 / 94,208 |

出处：V L263、P L559、R L283、B L808、D L208。实际启动命令的有效时间预算分别约为 video 1,800 s、protein 1,769.59 s、path-tracing 1,769.67 s（各题 trial.log 中的 `--max-turn-seconds`）。这三项失败都在时间预算耗尽之前主动结束；video/path-tracing 仍分别约有 1,415 s、1,456 s 余量。耗时不是简单卡死证据。

## video-processing：错误答案通过格式和有序性检查

**确定事实**：官方 5 项测试中 3 项通过、2 项失败。例片输出 takeoff=94，官方容许 50–54；隐藏视频输出 takeoff=309，容许 219–223。分别见官方测试 L252、L332，汇总 L343。因为测试在 takeoff 断言处停止，日志没有证明隐藏视频 landing 是否正确。

模型在 V L89 的探索总结已经写下“跳跃区域约 49–65 帧”。随后 V L100 编写完整检测器，第一次运行发现 `toml` 不存在（L126），第二次发现无效 GaussianBlur 参数（L191），删除该调用后运行返回 **94/109**（L214）。后者明显不在此前观察范围，模型却未重新查看检测轨迹或解释冲突。

V L210 的“端到端检查”只验证：程序运行、文件两行和字段正确、整数满足 `0 <= takeoff < landing < frame_count`。输出 `ordered=true` 和固定例片帧数 120 绑定了“检测跳跃”的 requirement。任何错误但有序的帧对都能通过此检查；它没有测量 takeoff/landing 语义。V L255 仍以这些条件请求 `completed`。

因此准确的根因不是“只没做好隐藏数据泛化”，而是**公开样本的语义检验缺失，而且没有处理已有观察与最终输出的冲突**。本地没有现成真值标签并不等于只能验证排序；可以检查视频片段/轨迹、记录模型不确定性与人工可见证据，但不能把顺序约束升级成精准动作检测。

还存在独立的 harness 负担：初始 task_plan 因路径后句号前的空格不完全一致被拒绝（L11、L15）；一次 replace_text 少 path，一次 run_command 携带 verify 参数；AST 允许导入集合误排除标准库 os/sys（L222、L226）。修正 checker 后成功（L247），旧失败仍列为 unresolved（L263）。这些缺陷增加交互和诊断噪声，但不是 94 帧答案错误的直接原因。

## protein-assembly：同名组件被替换成不同的序列版本

**确定事实**：官方唯一测试失败于 `flag_idx < donor_idx < dhfr_idx < acceptor_idx < snap_idx`，具体比较为 `0 < -1`（官方 L206、L210、L214）。该错误本身看起来像顺序错误；静态解码生成产物后，可以把原因缩小到一个组件。

审计脚本读取 P L516 `write_file` 的 DNA 文本，使用标准密码表进行纯字符串解码，不执行设计命令。结果为 2,583 nt、861 个氨基酸；按模型自己使用的四个 GS linker 分段后，长度为 8、238、158、235、182。与官方测试日志中的参考组件字符串比较：

| 组件 | 参考长度 | 实际长度 | 官方参考全串在产物中的位置 |
|---|---:|---:|---:|
| antibody binder | 8 | 8 | 0 |
| donor | 259 | 238 | -1 |
| DHFR | 158 | 158 | 266 |
| acceptor | 235 | 235 | 434 |
| molecule binder | 182 | 182 | 679 |

进一步的字符串断言通过：**实际 donor 恰好等于官方参考 donor 去掉前 21 个字符**，剩余字符完全相同。其余四段完全匹配。因此不能把本轮失败笼统归为生物学设计能力差或组件顺序全错；这里有明确的组件版本/前缀丢失。

证据链是：P L333、L344 已取到带前缀的 PDB FASTA；L373 和 L505 改用 FPbase 的 canonical `seq` 生成 donor；L527/L539 的最终验证只检查 DNA 格式、GC、长度、FLAG、DHFR 和四段 linker，未逐个比较 donor/acceptor/molecule binder 的来源序列。FPbase 对光谱名称有用，不自动满足用户对 PDB exact sequence 的要求。

注意边界：当时取得的 PDB FASTA 含 `X` 未定残基，而官方参考把相关部位展开为确定字符；因此不能只建议“原样编码 X”。应保留来源版本和映射，明确处理不确定位置，同时保留不允许丢失的序列前缀。此次前 21 字符缺失是无歧义且可独立定位的问题；隐藏 checker 的所有剩余规则没有全部执行，不能声称补前缀就一定整题通过。

最终报告 P L551/L559 把 `partial` 主要归因于抗体目标身份不确定。然而 L487 已得到高相似序列命中，L498 已返回 anti-FLAG M2 的结构标题；静态比对显示实际 FLAG 正确，donor 才是首个官方失败。这暴露了**完成报告没有根据现有证据准确分配不确定性**。

效率方面有 41 次 run_command，其中 10 次命令使用 Google/Bing/Brave/DuckDuckGo 文本搜索（L207、218、229、240、252、263、318、351、406、439）；更直接的序列查询直到 L483 才执行。不能把这些请求全部判为浪费，但重复低信号网页检索后未及时转换到结构化查询，给出了可优化的具体轨迹。

## path-tracing：结果相似度 1.0 掩盖了外部依赖

**确定事实**：R L146 生成的 `image.c` 只有 193 字符。它调用 `system("cd /tmp&&/app/orig ...")`，然后把 `/tmp` 里的输出 rename 为 `reconstructed.ppm`。C 文件没有实现渲染计算，而依赖已有 `/app/orig` 与 shell。

R L168/L172 的验证在完整 `/app` 环境中编译运行，得到 similarity=1.0、gzip=165 bytes。它检查的是源代码是否出现连续字符串 `image.ppm`，但源代码使用 `"/tmp/image" ".ppm"` 拼接。**“没有某个字面量”和“没有外部输入依赖”是不同性质**，不能互相替代。L168 的 limitations 自己也承认：没有证明在缺少 `/app/orig` 时能运行，没有证明算法位于交付源码中。

官方测试仅复制编译出的 `image` 到 `/jail` 再 chroot 运行，退出码为 1；随后的图像相似度检查因 `/jail/reconstructed.ppm` 不存在而失败。见 [官方运行断言 L107](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35/path-tracing__JWkBJWH/verifier/test-stdout.txt:107)、L205，汇总 L215：3 通过、2 失败。静态依赖与官方隔离运行失败一致。没有执行该二进制，也不声称已单独分离 shell 缺失与 `/app/orig` 缺失对退出码的贡献。

L225 尝试通过 check_id 重跑时改了 covers/limitations，被不可变 checker 定义拒绝（L226/L227），之后又做了反汇编探索，但没有替换最初的 C 产物。L275 再次宣称 completed，L283 降为 partial 并停止。初期四个工具因批次前项失败而取消（L51/L52/L66/L67）；这些都是独立的可用性问题，核心仍是交付产物缺少独立实现。

适合的通用验证是：在不损坏原输入的独立目录或隔离环境中，只提供声明的交付文件及允许依赖，执行一次最终功能检查。不能针对这道题硬编码禁止 `/app/orig`，更不能把隐藏测试原样加进解题提示。

## 成功对照揭示内部验收的误报

B L792 最终真实执行渲染、返回 0，并比较受保护场景文件修改前后哈希一致；D L180/L192 在保存的数组上直接计算两种 KL 偏差，都约为浮点舍入误差。这些检查覆盖了目标行为的关键部分，官方均得 1。

但 B L808、D L208 仍显示 `status=partial`、`acceptance_status=unmet`、`verification_status=failed_checks`。两个失败候选 V/R 的内部 status 也是 partial，P 也是 partial。不能把“变成 partial”当作产物质量改善证据；这五个样本中它不区分通过与失败。

当前代码可以解释一部分机制：

- [runner.py L603](D:/projects/forgecode/forge/runtime/runner.py:603) 只在模型选择 completed 时计算验收缺口；模型主动 partial 时不会产生完整缺口列表。L619–626 将未满足验收降级，并立刻设置 terminal。
- [verify.py L110](D:/projects/forgecode/forge/tools/verify.py:110) 由模型给出的 requirement_id 和非空 expected_source 形成 `asserted_requirement_ids`；[verification_checks.py L29](D:/projects/forgecode/forge/tools/verification_checks.py:29) 验证 JSON 值，无法证明“这条断言确实足以检验该要求”。这是能力边界，不应包装成自动证明。
- [completion.py L144](D:/projects/forgecode/forge/runtime/completion.py:144) 会把未通过显式修订关系解决的旧失败保留成 obligation。需要区分产物失败、checker 错误及失效证据，避免旧 checker 的异常与真实产物失败混为一类。
- [verification.py L105](D:/projects/forgecode/forge/runtime/verification.py:105) 把未引用的换行归为 unsafe chain；包含 `set -eu` 和 heredoc 的正向检查会被降为 unknown。此类文本启发式不应生成“肯定掩盖失败”的强断言。

这些行号引用当前工作树，具体评测行为以 journal 为准；产品代码若在后续修复中改动，应继续以冻结运行源码复核。

## 修复优先级与离线验证设计

1. **P0：证据必须保留其实际证明范围。** 把 format、range、identity、behavior、isolation 等类型和来源记录为可审计事实；不要凭一个模型选择的 req ID 升格为全条要求已验证。添加“错误但有序的帧号”“少前缀的同名组件”“调用外部 renderer 的高相似度产物”三类反例，要求报告缺口而不是高置信成功。
2. **P0：失败后可修复，终止仍有界。** 对 completed 但存在可操作的验收缺口，允许有限次数、限定剩余时间的 reconcile→repair→recheck；比较产生的新产物/新证据，连续无进展才结束 partial。不可恢复旧版无限 finish 拒绝循环。模型主动 partial 也应计算已知缺口，不让 final 文案成为唯一诊断源。
3. **P1：checker 的错误与产物失败分开。** `ModuleNotFoundError`、错误 import 白名单、不完整密码表等 checker/runtime 错误应有独立状态及显式修订路径。修复命令、解释文字或同一断言的来源展示不应不必要地创建永久失败；真正降低期望值则必须被保留和明确报告。
4. **P1：最终交付环境检查。** 收集执行依赖与交付路径，使用独立目录验证程序不依赖探索阶段偶然存在的文件。对需要精确身份的数据组件，保留源 URI/版本/哈希及逐组件比较，名称一致不是内容一致。
5. **P1：基于低信号证据转换检索方式。** 多次网页查询没有产生可用实体或可验证断言时，提醒使用结构化 API、局部精确搜索或声明未知；只做建议和预算控制，不能把成功率提高归功于尚未测试的策略。
6. **P2：收集最终交付物。** 三题 `artifacts/manifest.json` 都是 `/logs/artifacts` 为空。当前能通过 write_file 事件恢复部分文本，但无法完整复核所有最终文件、二进制与图像。增加声明产物的只读归档与哈希，保证不收集凭据和无关文件。

上述是面向通用 agent 的测试类别；不建议为十道固定题加入特判或直接使用隐藏参考答案。

## 本次实际执行的检查

[审计脚本](D:/projects/forgecode/benchmark/analysis/audit_artifact_failures.py) 已运行通过，生成 [机器可读数据](D:/projects/forgecode/docs/reviews/2026-09-15-artifact-audit-data.json)。它检查了五条 journal 的行号/事件序号一致性，复核统计，静态证明视频内部观察冲突、组件前缀缺失和 C 程序外部依赖。外置 `payload_ref` 事件被明确列出，没有把它们当空事件或重复计算；本报告使用的关键工具请求、验证记录和 turn_completed 均有内联 payload。

这不是重新执行官方评测，也不是已经修复三题的证据。静态检查能确定本轮失败机制；改造收益仍需要在冻结代码、同模型配置与既定十题范围上重新评测。
