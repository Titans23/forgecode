# ForgeCode V4 当前交接

本文件只保留最新状态；完整阶段历史见 `archive/handoff-72d7fb8-20261008.md`。progress、backlog、任务卡与实际 evidence 保留全部引用，历史通过不替代当前源码验证。

## 当前工作

用户要求继续 F28/F31，先完成当前环境的代码和回归；外部资源缺失记 blocked，继续独立工作。当前分支 `codex/forgecode-v4`，F31测试父HEAD为 `e6bda835253425046068cd905db7d819a538b7c5`；提交后的真实HEAD以git为准。

F28本轮修复FileWorker关闭边界和Bridge并发取消/关闭重复清理。4项新增行为先失败后通过，实际helper组5/5、Bridge组12/12；Node owner替身只证明串行化。source9c0b2502下contracts2/quality4/unit225/portable403/regression1372全pass，五份报告与fresh本机CIgate复算通过。证据 `evidence/F28-admission-observations.json`。

F31已完成本轮代码和回归：显式human_unbounded/null金额配置、实际wizard选择、冻结网络策略一致性、持久请求/用量归属校验及合法迟到usage。16项新增行为先失败，修复后77项定向回归通过。实际contracts/Bridge/Desktop重建后冻结source `457c5613302dc9db0828fabee9be6fa68d18db9ee36fa4ee39728337d669e07d`：contracts2/quality4/unit226/portable418/regression1388全pass，0skip/failure/error。live-eval检查blocked，acceptance110映射有效/156平台证明blocked；七份正式报告及fresh本机CIgate复算通过。证据 `evidence/F31-budget-observations.json`，完整ID见progress。工具session1718已退出0，无待等待本机测试进程；私有finalizer已成功。

F31源码已提交上传 `64032fe475e3eabea06a65bd5653e85c6b173409`，ls-remote一致；实际新run37723899477已完成并复算。F28已上传e6bda835。源码哈希与本机完整回归一致；当前仅补文档证据的checkpoint，原有无关未跟踪文件保留。无运行中的本机测试或CI等待，本阶段代码/回归/上传已完成，F28/F31整体仍in_progress。

## 最近的 hosted 证据

F31 64032fe的实际run37723899477：Ubuntu22.04/24.04、Windows2025各contracts2/quality4/unit226/portable418通过，0skip/failure/error；三个freshCIgate通过，每平台16项新增行为均在实际JUnit中确认通过。四artifact/四日志/六JUnit及安全audit报告复算通过；security实际2high/exit2，整体workflow failure。索引 `evidence/F31-budget-hosted-observations.json`。这是portable证明，不建立原生产品验收。

此前F28 e6bda835的run37721608025三平台各unit225/portable403及契约/质量/CIgate通过，security同样失败；证据 `evidence/F28-admission-hosted-observations.json`。两次CI均保留实际commit，不用历史产物替代当前证明。

## 授权与保护

- 已授权测试过的开发版本上传、现有配置真实模型及金额无上限；费用未知保留unknown，请求/工具/时间仍有限。
- 唯一管理员设置例外：Titans23/forgecode、codex/forgecode-v4分支push、GitHub临时Ubuntu24.04 runner、锁定Electron helper的root:root/4755。记录 `evidence/human-authorization-ci-20261008.json`。本机/原生SRT setup未授权。
- 本轮F28/F31均0新增模型请求、0本机管理员操作、未重建安装包。历史模型两批10attempt/83请求及失败保留，完整批次A3/3、B2/3不是正式Harbor/native证明。
- 保留用户 `.forge`、无关未跟踪文件、旧四份10:33 evidence。侧聊只清理已完成且无引用的旧pytest tmp；不改源码或当前测试产物。

## 剩余门禁与资源

F28/F31均in_progress。F28仍缺原生verified promotion、完整初始化SRT/helper/listener清理及动态敏感路径一致性。锁定上游reset会记录并吞掉部分清理错误，不能凭resolved Promise宣布clean。

F31正式Docker环境/网络隔离与可信宿主逐请求准入/授权仍未完成；账本归属校验不建立该执行权限。预注册仍需实际官方source/model/environment/grader快照，正式独立holdout任务和规模待选择，已有模型授权不重复申请。

具体环境资源：可连接且server OS为linux的Docker daemon；Windows11 x64工作站；Ubuntu22.04/24.04 x64 X11/Wayland原生图形设备。目标设备可用后再列出具体管理员setup命令和权限范围。签名/LICENSE、2项high依赖和metadata额外开销5.2103%>5%仍受阻。

## 恢复读取

先核对实际HEAD/status、本文、progress/current task和对应任务卡。只在调查历史时读取archive与旧日志；不要轮询本文明确已结束的session，也不要把私有准备文件当作已验证公开实现。
