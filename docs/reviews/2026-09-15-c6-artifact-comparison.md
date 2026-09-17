# 2026-09-15：修复前后四道题的产物对比

本报告只读取两轮 journal 和官方 verifier 输出，没有运行题目产物、请求模型或重新评测。旧轮为并发 2 的 `terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35`；新轮为并发 6 的 `terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01`。代码与并发同时变化，单次轨迹只能说明观测差异，不能识别修复的因果效果。

| 题目 | 旧轮官方子测试 | 新轮官方子测试 | 可证实的变化 |
|---|---:|---:|---|
| build-pov-ray | 3/3 | 2/3 | 渲染和版本仍通过，SSIM 两轮均 0.8731；源包身份检查新失败 |
| circuit-fibsqrt | 2/3 | 2/3 | 行为测试内通过的输入从 2/28 降为 0/28；最终报告更坦诚 |
| video-processing | 3/5 | 3/5 | 公开例片的起跳已正确、落地更接近；另一视频起跳仍错误且偏差更大 |
| make-mips-interpreter | 0/3 | 0/3 | 两轮都没有首帧；新轮提前报告未完成，未形成可见功能进步 |

这里的子测试比率只用于解释失败位置，各题正式 reward 仍按整题计算。

## Build POV-Ray：退分发生在源包身份，渲染能力没有下降证据

旧轮从官方 FTP 的 `Old-Versions/Official-2.2` 目录下载 `POVSRC.TAR.Z`、`POVDOC.TAR.Z`、`POVSCN.TAR.Z`，见 [旧 journal 第 404 行](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c2-core-redesign-20260915-133329/2026-09-15__13-33-35/build-pov-ray__BbH3erp/agent/session-e1ec7652824649c6be2e347d.jsonl:404)。旧轮三个官方测试全部通过。

新轮改从 Internet Archive 的 OS/2 ISO 中提取 `GRAPHICS/POV22F.ZIP`，见 [新 journal 第 380 行](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01/build-pov-ray__6wMKBnZ/agent/session-0ae3f99432d247bb9117c7df.jsonl:380)；第 480 行将其复制进最终目录并记录来源。官方 `file_id.diz` 的 MD5 首先不符，导致 `test_povray_built_from_correct_source` 失败；因为测试在首个错误断言处停止，不能认定其余身份文件都正确，也不能仅凭此断言认定所有源代码都错误。见 [官方输出第 104 行](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01/build-pov-ray__6wMKBnZ/verifier/test-stdout.txt:104)。

两轮图像 SSIM 都为 **0.8731**，版本检查都通过。这一道从通过变失败的直接证据是下载包和所需字节身份不一致，并没有显示编译或渲染功能退化。新轮 `review_delivery` 后追加的检查确认了目录、README 版本和来源记录存在，但这些代理条件没有确认所需源包身份。仅增加提交前检查次数不保证查到了关键差异。

## Circuit：报告质量改善，通用算法没有改善

旧轮产物退化为 17 个输入的查表，其自测也使用同一组输入；官方 28 个输入中仅 1、4 通过，剩余输出 0。旧轮细节已有 [审计报告](D:/projects/forgecode/docs/reviews/2026-09-15-failure-systems.md)。

新轮官方 **28 个输入全部输出 0**，依旧只通过文件存在和行数限制。见 [新官方输出](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01/circuit-fibsqrt__yTbYbiY/verifier/test-stdout.txt)。新轮最终没有再宣称行为正确，而是明确报告电路的 19,336 行满足规模限制，但两个公开例子仍返回 0、逻辑/初始化/时序缺陷未解决，见 [新 journal 第 1114 行](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01/circuit-fibsqrt__yTbYbiY/agent/session-2d7d809335294f2990487200.jsonl:1114)。

因此可确认的积极变化是失败披露更准确；不能把 `partial` 或不再自报成功当成解题质量提高。

## Video：公开例片局部进步，泛化仍失败

旧轮公开例片内部最终输出为 **94/109**，官方在起跳断言处失败；新轮输出 **50/68**，其中起跳 50 落在官方允许的 50–54 内，落地 68 仍超出允许的 62–64。见 [新官方输出第 236 行](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01/video-processing__wpPqcuK/verifier/test-stdout.txt:236)。公开样本两个输出都更接近动作边界，是这几道题中可见的局部功能进步。

另一视频起跳旧轮为 **309**，新轮为 **104**，均不在 219–223，且新结果距允许区间更远。见 [新官方输出第 316 行](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01/video-processing__wpPqcuK/verifier/test-stdout.txt:316)。测试在起跳处停止，两轮都不能由此判断该视频的落地是否准确。

新轮增加了在视频开头填充静止画面的检查，见 [journal 第 212 行](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01/video-processing__wpPqcuK/agent/session-9acea688c7014067957a452d.jsonl:212)，但实际只断言字段和起落顺序，没有断言输出随内容平移的关系。最终第 244 行仍用 `ordered_in_range` 绑定起跳和落地要求；这能证明输出有序，不能证明边界检测准确。新自测比旧轮丰富，但核心语义覆盖缺口仍在。

## MIPS：诊断和收尾更明确，首帧目标仍完全未达成

两轮官方测试均在约 30 秒内未见 `/tmp/frame.bmp`，三个子测试全部失败。见 [新官方汇总第 321 行](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01/make-mips-interpreter__K8hBKQY/verifier/test-stdout.txt:321)。

旧轮在约 1,772 秒时由时间预算终止。新轮约 1,636 秒主动结束，并明确写出：运行陷入 Doom 的 `I_Error` 路径、`pc=0x40e6d4`、指令限额耗尽、无首帧。见 [新 journal 第 1229 行](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-luna-c6-repair-20260915-162956/2026-09-15__16-30-01/make-mips-interpreter__K8hBKQY/agent/session-6f5c4cc7660b4381a81f42b4.jsonl:1229)。第 469、560、763、820、910、956、1106、1163、1209 行多次验证仍出现同一 PC 的指令限额错误。

新轮的字节端序小检查、输入哈希和语法检查各有价值，最终没有将这些成功升级为“Doom 已运行成功”。但重复相同失败尚未收敛到有效修复，所以这里只能认定诊断与失败交付更清楚，不能认定解释器能力或算法调试效率提高。

## 综合判断

产物侧出现了公开例片边界更接近、失败声明更准确等局部改善；主要复杂任务仍没有新增通过。新增提交前审查尚未可靠改变检查的语义覆盖：源包身份仍被版本文字代理，动作识别仍被有序整数代理，持续相同程序错误仍未转化为收敛修复。后续衡量应分别记录正式 reward、关键行为子检查、失败披露和资源开销，并用同并发、同模型配置的重复对照识别改动收益。
