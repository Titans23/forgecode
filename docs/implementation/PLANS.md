# ForgeCode 实施任务执行记录

本文件是此仓库的执行记录格式，与产品规范分离。目标是换一个 Codex 会话仍可继续，不依赖隐藏聊天上下文。

## 每次开始

读取当前 AGENTS 指令、主规范第 0—3 章、progress、handoff。使用实际 git status／HEAD 校对记录；确认用户修改。选择依赖已实现的下一任务，读取任务卡和关联章节，不机械依编号执行。安全／平台原生验证门禁仍独立约束发布。

## 每个任务必须记录

- **Progress**：实现状态；各套件／平台实际验证；未运行原因。
- **Discoveries**：真实源码、行为和测试发现，带路径／命令／证据，不把猜测写为事实。
- **Decisions**：与默认规范的偏差、原因、替代设计和影响；重大变化另写 ADR。
- **Outcomes**：已实现行为、剩余缺口、下一工作项和解除阻塞所需资源。

## 开发和验收

先写或补充表达行为的测试，再做最小实现，最后跑现有回归。允许确定性 MockModel 用于离线集成，但不替代真实沙盒／原生安装包／公开基准协议。文档中的 `scripts/impl.py` 需先由 F00/F02 创建。

相同字段只维护一种事实：Journal 管原有副作用，Engine 数据库管受理与状态；文档状态不是执行事实。成功只能引用 runner 产生的 evidence。测试未收集、全 skip、环境缺失不能视为 pass。

## 中断或任务完成

更新 progress、当前任务卡与 handoff；记录 HEAD、dirty 文件、真实命令、限制、下一项。不得在 handoff 放 API key、原始敏感输出或未脱敏私人代码。没有进展也如实记录。

管理员操作、真实 API 支出与发布上传需要明确授权。资源缺失时说明 blocked 并继续无依赖任务，不自动降级安全，也不重复问已经给出的架构选择。

## 2026-10-09 当前合并实施

本轮按用户批准的 [合并与减重方案](consolidation-plan.md) 和 [ADR036](adr/036-consolidation-and-workspace-write.md) 接续。活动目录统一为 Windows `D:\projects\forgecode` 与 WSL `/home/titans/learn_project/forgecode`，旧 V4 目录已归档。整合分支 `codex/forgecode-consolidation` 完成验收后正常合入 main，不强推。资源不足的能力保持关闭；回归失败及未通过的依赖安全检查如实保留，合入条件以用户本轮计划和实际 CI 为准。
