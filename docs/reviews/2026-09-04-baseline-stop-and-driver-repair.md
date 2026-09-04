# 旧基线停止与评测驱动修复

## 事实与边界

本轮路径：`benchmark/runs/harbor/terminal-bench-2-baseline-eff5909-c6-restored-20260904/2026-09-04__10-33-13`。被测内核为 eff5909，原驱动为 663ecd6，固定十题和数据集摘要不变。

用户批准后停止三个明确残留容器，没有删除容器、日志或产物，没有修改官方 result.json。停止前原控制会话已不可用，按本轮输出目录筛选也没有找到控制进程；因此不能断言所有延迟都来自仍存活的 Harbor 控制循环。控制进程消失和缺少可恢复的最终状态同样是问题。原自动跟进更新返回已不存在，没有创建替代自动化。

原官方状态仍是 7 完成、3 运行，但这是陈旧的调度状态，不是当前 Agent 进程状态。

| 任务 | 官方记录 / 执行证据 | 结论 |
| --- | --- | --- |
| distribution-search | reward 0；verifier 下载 NumPy 超时，未进入测试 | 验证环境失败，不能判断产物正确性 |
| video-processing | reward 0；verifier 下载 OpenCV 超时，未进入测试 | 验证环境失败，不能判断产物正确性 |
| break-filter-js-from-html | reward 0；实际浏览器检查未观察到预期行为 | 功能失败 |
| circuit-fibsqrt | reward 0；文件存在/大小通过，电路行为失败；Agent 改动 sim.c | 自测对象与交付要求不一致，不能靠修改模拟器证明电路正确 |
| overfull-hbox | reward 0；编译、排版通过，但输入变换约束失败 | 目标部分实现，明确约束未满足 |
| make-mips-interpreter | reward 0；官方执行超时，没有生成目标图像 | 执行/算法失败，不是 verifier 安装异常 |
| path-tracing | reward 0；源码、编译、执行通过，图像精度失败 | 结构自测不能证明渲染精度 |
| build-pov-ray | 无最终 trial result；Agent exit 124、timed_out=true；只剩保活进程 | Agent 超时，收尾缺失 |
| compile-compcert | 无最终 trial result；Agent exit 124；仍有 opam/make/coqc 进程 | Agent 超时且有残留计算，收尾缺失；进程来源尚不能仅凭名称判为 verifier |
| protein-assembly | 无最终 trial result；Agent exit 0，但 payload status=stuck，无产物 | 旧恢复工具限制导致无法推进，exit 0 不代表任务成功 |

七个有最终 trial result 的 payload 合计：模型请求 199、工具请求 232、含缓存累计输入 3,481,815 token、输出 77,472 token。protein 的独立 payload 另有 67/100 次、948,264 含缓存输入、23,080 输出；两个超时任务没有最终 payload，不能将上述数字当作全轮账单。无价格来源，不估算美元成本。

本轮不是有效完整验收轮次，也不能证明新版通过率。停止旧基线，不再用完整重跑旧版来验证驱动修复。

## 驱动变更及依据

- Harbor 0.18 的 compose 文件复制和停止调用可没有 timeout；收尾包含 shield 等待。新增仓库内 Docker 环境适配，通过官方 import-path 接口接入，不修改 site-packages。
- 传输/目录检查上限 120 秒，环境收尾上限 60 秒；已有更短超时保留。普通 exec 和 verifier 的明确时间预算不变。
- 外层任务 timeout 不能单独保证回收另起会话的子进程。新增仅用于 Linux benchmark 的子进程监督器，使用 subreaper 收养并回收自身后代，在成功、取消和超时时均清理；不扫描杀死无关容器或其他任务进程。
- verifier 的 uv 依赖下载超时使用配对安装诊断识别，保留官方 reward，不计为有效代码评分。
- 产品 forge/ 内核不改动。本次修复不等于已证明任务算法正确或网络下载可靠。

## 验证

相关契约最终 49 项通过。新增短进程 Linux 离线探针验证成功、取消、超时三种情况，包含 setsid 后代及继承输出管道，全部通过。补充 artifact 目录检查、下载和收尾挂起的五个超时测试通过。Linux Python 3.12.11 全量 570 passed（62.50 秒）；Windows Python 3.12 全量复跑 570 passed（197.97 秒），各有 1 条已有飞书依赖弃用警告，无跳过。compileall 和 git diff --check 通过。

无模型完整 Harbor 冒烟任务：`driver-lifecycle-smoke-utf8-20260904/2026-09-04__11-52-34`，16 秒完成，reward=1，无 exception，CLI exit=0，容器退出。此前首次直接调用 Harbor 的任务本身成功，但最后摘要打印遇到 GBK 编码错误；重验采用正式 run_dataset 已有的 UTF-8 环境设置。此合成基础设施任务不计入固定十题成绩，不证明模型解题能力。

下一步必须确认两平台回归和新驱动最小完整 trial 能正常结束，再运行固定新版十题。若 verifier 下载仍失败，保留无效分类，不把重跑后的结果拼接成同版本连续两轮达标。
