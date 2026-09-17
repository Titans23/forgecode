# 最终未通过题目的主因分类

口径：最终 89 题（未 server_error 的 51 题 + 38 题重评），41 通过、46 零分、2 未判分。本分类覆盖后两者共 48 题。按当前直接失败证据为每题指定一个主要类别，不重复计数；这是人工诊断分类，不等于已经完成全部代码级根因证明。

相似度/性能明确阈值未达标单列；没有有效构建或运行产物单列；验证阶段故障优先单列，避免对无法充分判分的产物作能力结论。个别任务存在多个问题，修复当前主阻塞不保证整题通过。

| 主要失败类别 | 题数 | 占 48 题 |
|---|---:|---:|
| 算法、数值或输出语义错误 | 19 | 39.6% |
| 构建、依赖或执行未完成，缺少可用产物 | 10 | 20.8% |
| 性能、精度或质量指标不达标 | 5 | 10.4% |
| 文件交付或修改范围不符合约束 | 4 | 8.3% |
| 服务没有以要求的状态交付 | 3 | 6.2% |
| 验证阶段故障，尚不能正常判定产物 | 6 | 12.5% |
| ForgeCode 入口故障，任务没有开始执行 | 1 | 2.1% |

这套分类与先前“agent_failure / agent_timeout”分类用途不同：超时是停止方式，此处描述停下后暴露出的具体结果问题。例如一个超时任务也可能已有产物但数值错误，不再统统归到“时间不足”。

## 逐题分类

### 算法、数值或输出语义错误（19 题）

| 题目 | 当前直接失败证据 | 停止原因 |
|---|---|---|
| [chess-best-move](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/chess-best-move__LW4yNXQ) | 输出走法不正确 | acceptance_unmet |
| [circuit-fibsqrt](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/circuit-fibsqrt__ACPPhc6) | 电路功能检查失败 | failed |
| [db-wal-recovery](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/db-wal-recovery__oHDUxnB) | WAL 更新值没有正确恢复 | partial |
| [dna-assembly](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/dna-assembly__QcN2v5c) | 引物缺少要求的识别位点 | time_budget_exhausted |
| [dna-insert](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/dna-insert__o5q9XPV) | 正反引物熔解温度差超过限制 | partial |
| [extract-elf](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/extract-elf__sUzx7Y6) | 预期提取值覆盖率为 0% | acceptance_unmet |
| [mteb-leaderboard](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/mteb-leaderboard__UJS8rpA) | 提交的数据答案错误 | completed |
| [gcode-to-text](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/resume-20260916-150000/2026-09-16__15-02-26/gcode-to-text__ScjeZ6B) | G-code 解码文字错误 | completed |
| [count-dataset-tokens](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/count-dataset-tokens__bWjkHYX) | token 计数错误 | acceptance_unmet |
| [filter-js-from-html](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/filter-js-from-html__EUjMX5m) | 过滤恶意内容与保留干净 HTML 的行为检查失败 | completed |
| [gpt2-codegolf](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/gpt2-codegolf__DMY7fn7) | 程序生成的文本不符合要求 | failed |
| [mcmc-sampling-stan](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/mcmc-sampling-stan__hhphCUG) | 后验参数数值错误 | time_budget_exhausted |
| [model-extraction-relu-logits](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/model-extraction-relu-logits__AUggVNM) | 提取矩阵多个行不匹配 | completed |
| [mteb-retrieve](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/mteb-retrieve__jfXXQW7) | 检索结果对应内容错误 | completed |
| [raman-fitting](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/raman-fitting__TefMQLU) | 峰位置等拟合参数错误 | acceptance_unmet |
| [rstan-to-pystan](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/rstan-to-pystan__AH9xddd) | 要求三个参数值，输出只有一个 | time_budget_exhausted |
| [regex-chess](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/regex-chess__BfK6vgh) | 输出棋局变化不属于合法走法 | partial |
| [torch-tensor-parallelism](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/torch-tensor-parallelism__cw47QrR) | 分片计算/梯度结果不匹配 | partial |
| [video-processing](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/video-processing__PbCPM6U) | 起跳帧定位错误 | acceptance_unmet |

### 构建、依赖或执行未完成，缺少可用产物（10 题）

| 题目 | 当前直接失败证据 | 停止原因 |
|---|---|---|
| [build-pov-ray](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/build-pov-ray__C6nDdHj) | 没有完成要求的构建产物 | time_budget_exhausted |
| [caffe-cifar-10](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/caffe-cifar-10__crwZeR7) | 模型及训练产物缺失 | time_budget_exhausted |
| [compile-compcert](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/compile-compcert__aE3E7yd) | 缺少 ccomp 可执行程序 | time_budget_exhausted |
| [crack-7z-hash](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/crack-7z-hash__aLecZLh) | 未生成 solution.txt | time_budget_exhausted |
| [make-mips-interpreter](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/make-mips-interpreter__9LVQy9A) | VM 执行和帧产物检查失败 | time_budget_exhausted |
| [protein-assembly](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/protein-assembly__iSBd7ff) | 未生成 gblock.txt | failed |
| [extract-moves-from-video](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/extract-moves-from-video__mEEDuWM) | 未生成 solution.txt | acceptance_unmet |
| [adaptive-rejection-sampler](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/adaptive-rejection-sampler__TgEe9Cz) | Rscript 未安装完成，无法执行验证 | time_budget_exhausted |
| [make-doom-for-mips](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/make-doom-for-mips__6FC9QZJ) | 运行失败，帧文件为空 | time_budget_exhausted |
| [sam-cell-seg](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/sam-cell-seg__GeQ7sYL) | 脚本运行失败，要求的 CSV 未生成 | partial |

### 性能、精度或质量指标不达标（5 题）

| 题目 | 当前直接失败证据 | 停止原因 |
|---|---|---|
| [largest-eigenval](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/largest-eigenval__sXPZZLY) | 多项加速测试未达到要求 | partial |
| [train-fasttext](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/train-fasttext__oLJSqHx) | 准确率未达标且模型大小超限 | time_budget_exhausted |
| [path-tracing](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/path-tracing__U5Pv2fv) | 图像相似度约 0.7735，要求 0.99 | time_budget_exhausted |
| [path-tracing-reverse](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/path-tracing-reverse__xdyaGx6) | 图像相似度约 0.9541，要求 0.995 | partial |
| [winning-avg-corewars](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/winning-avg-corewars__QtQbWug) | 对测试对手胜率 12%，要求至少 33% | failed |

### 文件交付或修改范围不符合约束（4 题）

| 题目 | 当前直接失败证据 | 停止原因 |
|---|---|---|
| [overfull-hbox](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/overfull-hbox__6CTDVAk) | 输入修改超出允许的词语替换范围 | time_budget_exhausted |
| [polyglot-rust-c](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/polyglot-rust-c__DF2Qjx5) | 指定目录遗留额外编译文件 | time_budget_exhausted |
| [polyglot-c-py](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/polyglot-c-py__Vu42yvq) | 指定目录遗留 cmain；会话另有计算错误 | partial |
| [sqlite-with-gcov](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/sqlite-with-gcov__N8KW4Cg) | 缺少要求的 .gcda 覆盖率数据 | completed |

### 服务没有以要求的状态交付（3 题）

| 题目 | 当前直接失败证据 | 停止原因 |
|---|---|---|
| [install-windows-3-11](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/install-windows-3-11__FTSmxcn) | QEMU 运行参数/monitor socket 不满足验证需求 | acceptance_unmet |
| [configure-git-webserver](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/configure-git-webserver__j5WHq7C) | 部署后的 HTTP 请求返回 404 | time_budget_exhausted |
| [pypi-server](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/pypi-server__DUZ2hnt) | 本地索引服务未常驻交付，官方 pip 安装连接失败 | completed |

### 验证阶段故障，尚不能正常判定产物（6 题）

| 题目 | 当前直接失败证据 | 停止原因 |
|---|---|---|
| [code-from-image](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/code-from-image__5d5w6or) | 验证器 uv bootstrap 故障 | acceptance_unmet |
| [qemu-alpine-ssh](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/qemu-alpine-ssh__zPTXnak) | 验证器 uv bootstrap 故障 | partial |
| [qemu-startup](D:/projects/forgecode/benchmark/runs/harbor/tb2-se9-c4-0917-095024/2026-09-17__09-50-41/qemu-startup__nY8NhX9) | 验证器 uv bootstrap 故障 | time_budget_exhausted |
| [tune-mjcf](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/tune-mjcf__APzKu9p) | 验证器 uv bootstrap 故障 | time_budget_exhausted |
| [query-optimize](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/query-optimize__nMhCPWj) | VerifierTimeoutError，无 reward | time_budget_exhausted |
| [torch-pipeline-parallelism](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/torch-pipeline-parallelism__WQ5WcUD) | VerifierTimeoutError，无 reward | partial |

### ForgeCode 入口故障，任务没有开始执行（1 题）

| 题目 | 当前直接失败证据 | 停止原因 |
|---|---|---|
| [portfolio-optimization](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/portfolio-optimization__VW4NoNY) | 任务 benchmark.py 遮蔽 benchmark.harbor 入口包 | 无内部终态 |

## 归因边界与修复重点

- 前三类共 34 题：实现语义、运行交付或目标指标未满足。不能全部归因于模型能力，也不能在缺乏反事实验证时全部归为 ForgeCode bug。应从任务轨迹检查是否有工具误拒绝、错误执行、预算浪费和验证遗漏。
- 文件/服务交付两类共 7 题：适合增加交付清单与消费者视角的端到端检查。但这是工程改进机会，不等于 7 个已确证的框架 bug。
- 验证阶段故障 6 题：不以当前结果推断模型能力。两个超时仍需排查是否由提交产物挂起导致，其余依赖失败也不保证产物正确。
- 入口故障 1 题：有实验日志、源码与离线复现，明确是 ForgeCode 自身缺陷直接阻断执行。
- 额外的框架贡献因素：CompCert 存在 sudo 存在性检查误拒绝；make-doom-for-mips 存在换行删除目标解析误判；若干任务存在检查器修复接口缺口。这些作为横向标签，不再重复计入主分类。
- polyglot-c-py 的目录遗留是官方首先报告的阻塞，会话还记录 F500 数值错误；不能认为清理文件即可通过。pypi-server 的脚本主动终止服务，当前没有证据归因于 ForgeCode supervisor 误杀。

建议先修入口和权限解析等已证实缺陷，再处理验证环境；随后针对交付类建立回归，最后对算法、数值与性能类做独立验收和逐题诊断。不要把分类数字直接当成可挽回通过数。

相关：[最终全量报告](./2026-09-17-final89-after-servererror-retry.md)、[ForgeCode 自身缺陷审计](./2026-09-17-framework-bug-audit.md)、[原始合并数据](./2026-09-17-experiment-history-data.json)。
