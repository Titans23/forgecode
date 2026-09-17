# 最终 89 题中 ForgeCode 自身缺陷的专项审计

日期：2026-09-17。仅研究原 51 题保留结果 + 38 题重评的最终集合（41 通过、46 零分、2 未判分）。不使用被替换的 server_error 失败作为当前归因。

## 结论与证据等级

确认一个直接阻断任务启动的框架 bug、两个在失败任务中真实发生的权限解析误判，以及一个可复现的验收修复路径缺口。后面三项能说明框架存在额外阻力，但不能把对应失败题直接计为“修好即可通过”。此外发现两项验收设计风险，需要单独对照验证；没有证据把其余算法错误全部归为 ForgeCode bug。

| 问题 | 证据 | 对最终失败的归因强度 | 优先级 |
|---|---|---|---|
| 任务内 benchmark.py 遮蔽 agent 入口包 | 实验日志 + Dockerfile + 离线复现 | 明确导致 portfolio-optimization 未执行 | P0 |
| 权限扫描将 command -v sudo 当作提权 | CompCert 会话 + 源码 + 离线复现 | 确认阻断合法操作；不是该题最终失败的充分原因 | P1 |
| 删除目标解析跨越换行吞入下一条命令 | make-doom-for-mips 会话 + 源码 + 离线复现 | 确认误拒绝并造成重规划；通过收益未知 | P1 |
| 无 output_checks 的旧失败无法使用提示的继承修复路径 | 源码 + 离线复现 + 五题存在相关记录 | 确认恢复接口缺口；具体失败不能全归因于此 | P1 |
| 进程执行一律使证据过期，verify 又被整体豁免 | 源码检查 | 保守失效与漏失效风险，尚未建立具体失分因果 | P2 |
| 多行安全检查被归为 unknown，检查器错误与产物错误混合 | 离线分类 + 轨迹统计 | 交互/设计成本，不是所有拒绝都有 bug | P2 |

抽查 portfolio-optimization、compile-compcert、torch-pipeline-parallelism 的评测源码副本：入口、权限、检查继承、完成判定、验证分类、执行器六个关键文件的内容摘要与当前代码一致。离线复现针对的不是事后不同版本。

## 1. 入口包遮蔽：确定的任务阻断

位置：benchmark/harbor/forgecode_agent.py:284—285。入口从任务 cwd 执行 `python -m benchmark.harbor.process_supervisor`，内部再以同样方式启动 run_forge。

portfolio-optimization 的任务 Dockerfile 将 benchmark.py 复制到 /app；Python 默认优先搜索 cwd。实际日志为：`No module named benchmark.harbor; benchmark is not a package`。该任务模型调用和工具调用均为 0。

离线最小复现：临时目录中创建无害 benchmark.py，以当前解释器寻找真实 benchmark.harbor.process_supervisor；默认搜索失败，隔离 cwd 的对照成功。复现没有运行模型、没有改动任务产物。

修复设计：使用安装好的命名唯一 console entrypoint，或明确隔离 cwd 的 Python 启动方式，并保证所需包在安装环境中可导入。不能只修 supervisor 而遗漏其启动的 run_forge。`-P` 在本次受控 PYTHONPATH 对照中有效，但上线仍应验证实际安装路径，而不是直接假设加一个参数就完成修复。

回归要求：含 benchmark.py、benchmark/、forge.py 等同名文件的任务仍正常启动，项目路径保持原 cwd，真正的缺包仍清晰报错。

## 2. 权限关键字误判：检查 sudo 的存在被拒绝

位置：forge/permissions/risk.py:33—36、119。PRIVILEGE_PATTERN 对 command 和 stdin 原文做关键字搜索，没有区分命令位置、字符串、注释与实际执行。

CompCert 的实际命令包含 `id; command -v apt-get ...; command -v sudo ...; cat /etc/os-release`，被硬拒绝为提权。离线复现中 `command -v sudo` 和仅打印包含 sudo 的文字都 hard_deny=True；真正的 `sudo id` 也被拒绝，后者是正确对照。

框架缺陷是误识别执行语义，不是应该取消提权限制。修复应通过 shell 语法结构辨别命令位置和查询/字面量上下文；复杂无法解析的情况保持保守，真实提权仍按既定策略处理。增加输出文字、注释、存在性检查与实际执行的成对回归。

CompCert 后来继续进行了工具探测与构建尝试，所以此次拒绝不是整题失败的唯一原因。configure-git-webserver 也出现过提权拒绝，但不能仅因同属该错误码就认定同一个误判；需检查完整被拒命令，不能批量放行。

## 3. 多行删除命令解析错误

位置：forge/permissions/risk.py:188 起。shlex 使用 `;&|` 作为标点边界，换行按空白吞掉，导致后续命令 token 被纳入 rm 的目标列表。

make-doom-for-mips 的真实请求先删除明确构建产物，下一行使用 SRC=$(sed ...) 计算构建参数。权限层把后续内容当成删除目标，并报“broad or unresolved recursive deletion target”。

离线对照（只分类，没有实际删除）：

- `rm -rf build_mips` → 目标只有 build_mips，不硬拒绝。
- 同一命令后增加换行 `SRC=$(printf hello)` → 目标变成 build_mips、SRC=$(printf、hello)，触发硬拒绝。

这是真实解析 bug。修复必须识别 shell 语句边界，同时正确处理引号中的换行、续行、here-document、命令替换和嵌套命令；不能简单按所有换行字符串切分，更不能因此跳过删除路径保护。回归需确认真正宽泛/未解析的删除仍被拦截。

## 4. 检查器修复接口缺口

位置：forge/runtime/check_contracts.py 的 inherit_check_arguments、forge/runtime/completion.py:448 起的 checker_revision_covers，以及 completion.py 的 stale obligation 提示。

工具允许用命令内部 assert 验证，不强制提供 output_checks；但旧检查只要没有结构化 output_checks，inherit_checks_from 就报“Unknown verification or no recorded assertions”。旧失败又可能因 requirement_ids 进入持久验收义务，系统提示仍让模型使用该继承路径修复。

离线复现：绑定需求的旧检查失败；改正检查命令后提供 supersedes 和 revision_reason，旧失败仍保留；调用推荐继承路径被拒绝。同一原命令后来成功可以清除，说明不是所有失败都永远无法解决，缺口集中于检查命令/输入本身需要修正时。

最终失败记录中，polyglot-c-py、rstan-to-pystan、sam-cell-seg、torch-pipeline-parallelism、tune-mjcf 出现了“绑定需求、无 output_checks 的失败检查”。其中 torch-pipeline-parallelism 的记录含 `./ .venv/bin/python` 这样的命令拼写问题；它恰是需要修检查器命令而不应改变产物要求的情况。但仍不能据此认定该题 verifier 超时就是此缺口造成的。

设计上应允许明确区分 checker_error 与 artifact_failed：修复检查器时保留源需求与验证义务，重新获得可信行为证据。不能让任意新命令以 exit 0 自动清除旧失败，也不能把所有没有结构化断言的失败都扔掉。

## 5. 两个需要验证的设计风险

### 5.1 环境代际失效过粗且不对称

executor.py:265—270 对任何非 verify 的 process 工具调用无条件增加 environment_epoch。即使只运行 pwd、版本查询，也可能使此前所有证据过期；completion.py 要求环境代际完全相同。

相反，verify 本身可以启动/停止服务、安装依赖或修改环境，却被这一失效逻辑整体豁免。文件改动仍会走 workspace refresh，但服务状态不一定被文件 revision 捕获。

这是证据有效性设计风险。建议将证据依赖细化为文件版本、运行环境、服务身份/存活状态；不应通过“模型自己声明只读”就放宽保护。当前尚未量化由此单独造成多少失败，不将它列为已证明的失分 bug。

### 5.2 验证分类与模型交互成本

`set -eu
python check.py` 被归为 unknown，而 `python check.py && python second_check.py` 被归为 behavior。识别多行 shell 存在保守理由，因为 shell 退出码可能掩盖中间失败；但当前给模型的通用 shell 工具与严格验收分类之间存在摩擦。优先提供清晰、稳定的 checker 执行接口，降低模型反复迁移到 stdin/JSON 协议的成本。

对最终未通过/未判分 48 题扫描得到：25 次 check_contract_conflict 分布在 12 题；36 次 invalid_arguments 分布在 21 题；99 次 not_executed_after_failure 分布在 26 题。这些是工具事件，不是 160 个 bug。后续工具在前序失败后取消可能是正确的依赖保护；不能为了减少错误计数直接并行执行全部工具。应重点区分错误提示不清、schema 使用难和真正执行失败。

## 6. 已排除或不能直接归于 ForgeCode 的问题

### PyPI 服务不是本轮 supervisor 误杀

最终 pypi-server 轨迹中，多次验证用临时服务，随后明确执行 server.terminate()/server.wait()；最后完成消息还让用户手动运行 python serve_index.py。没有常驻服务交付，官方 pip 安装连接失败。因此本轮证据指向模型提交的生命周期/交付方式错误，不是再次证明框架强制杀掉正常服务。此前后台服务保留修复不应无证据重复归罪。

### polyglot-c-py 不是只差清理目录

官方首先遇到额外 cmain 文件，但会话自测还显示 F500 实际值与正确值不符。cmain 是模型验证命令生成并遗留的文件，不是 ForgeCode 自动生成的文件。修目录不保证算法通过。

### R 安装超时不能直接归于超时实现 bug

adaptive-rejection-sampler 请求 apt 安装 R，600 秒后得到 command_timeout，后续安装被总期限取消。当前轨迹支持依赖安装未完成，未证明工具实际越过期限或杀错进程。改善依赖预检与网络恢复有价值，但不能把所有安装失败都记作 ForgeCode 代码错误。

### verifier 依赖故障不是模型能力问题，也不等于 ForgeCode 产品 bug

四题 uv bootstrap 和两题 verifier 超时属于评测基础设施/验证阶段问题。任务自己的 test.sh 会访问网络安装 uv 等依赖。ForgeCode 的评测集成可改进缓存、预检和错误记录，但不应通过改官方测试放宽判分。两个 verifier 超时仍可能受产物行为影响，需进一步隔离。

### 数值、矩阵、梯度、拟合和性能错误尚无框架因果证据

这些结果确实失败；除非能证明工具截断输入、写坏文件、错误缓存或执行错误命令，否则不能因为用户希望排除模型能力就把它们归于 harness。

## 7. 建议修复顺序与验证标准

1. P0 修入口命名隔离，做同名任务文件的无模型启动回归。
2. P1 修权限语法解析，保留全部真实危险操作边界，用成对正负样例验证。
3. P1 修检查器错误的恢复接口与提示一致性，保留断言与原始需求，验证不能弱化验收。
4. P2 设计证据依赖失效与服务交付检查；先用小型离线任务做状态变更对照，再决定是否调整核心状态机。
5. 修复后优先对受影响任务做定向回归，再冻结版本做全量对照。修复前后必须使用相同模型/预算/官方测试，不把采样偶然收益算成确定 bug 收益。

当前可确证的是“一题因入口 bug 未执行”和“若干失败轨迹中存在框架误拒绝/恢复缺口”，不能诚实地宣称修复后必定从 41 提升到某个通过数。

## 复现与资料

本次新增只读审计数据与离线诊断脚本，未修改运行时代码、评分或启动付费评测。

- [历史复现结果](./2026-09-17-framework-fault-probes.json)（旧缺陷断言脚本已移除，当前回归见 `tests/runtime/test_framework_repairs.py`）
- [复现输出](./2026-09-17-framework-fault-probes.json)
- [48 个失败/未判分任务的工具错误统计](./2026-09-17-framework-failure-audit-data.json)
- [最终 89 题分析](./2026-09-17-final89-after-servererror-retry.md)

关键原始轨迹：

- [portfolio-optimization](D:/projects/forgecode/benchmark/runs/harbor/tb2-r2-0916-155118/2026-09-16__15-52-29/portfolio-optimization__VW4NoNY)
- [compile-compcert](D:/projects/forgecode/benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647/2026-09-16__11-06-53/compile-compcert__aE3E7yd)
- [make-doom-for-mips](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/make-doom-for-mips__6FC9QZJ)
- [torch-pipeline-parallelism](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/torch-pipeline-parallelism__WQ5WcUD)
- [pypi-server](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/pypi-server__DUZ2hnt)
- [polyglot-c-py](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/polyglot-c-py__Vu42yvq)
- [adaptive-rejection-sampler](D:/projects/forgecode/benchmark/runs/harbor/tb2-se38-c4-0916-173742/2026-09-16__17-37-48/adaptive-rejection-sampler__TgEe9Cz)
