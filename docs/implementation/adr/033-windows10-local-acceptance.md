# ADR033：必需 Windows 验收改为本机 Windows 10

日期：2026-10-08。状态：用户明确指定，已执行目标变更。

用户原文：“将window11验收改为window10验收即在本机验收即可”。
本次决策取代旧文档中“Windows 10 仅作阶段验证，随后必须 Windows 11 验收”的条件。
必需 Windows 目标为当前 x64 Windows 10 22H2、build 19045、NTFS 工作站；Ubuntu
22.04／24.04 原生图形验收和官方 Linux Docker 评测仍按原协议执行。

Python capabilities、Engine、doctor、Bridge probe/setup/native verifier 使用一致的
工作站检查，排除 Server、ARM64 和 build 19045 之前的 Windows 10。既有 Windows 11
运行路径保持兼容，但不是本次必需验收目标。release 与 acceptance 门禁要求 Windows 10
报告、实际 pass、eligible_for_native_pass、用例映射及报告 hash；受控单元 fixture 只验证
门禁行为，不是隔离证明。portable 测试在受支持系统上仅 diagnose，不触发 install/UAC。

本机实际诊断已识别 Windows-10-10.0.19045-SP0／AMD64，锁定运行时与 NTFS 通过。
固定 PowerShell 7 缺失、SRT 只读状态未取得，原生 verifier 为 blocked／0 checks，
所有隔离 verification 保持非 verified。后续需要固定路径 PowerShell 7 和经过独立
确认的 SRT setup；安装、GUI、ACL/Job、清理与能力提升仍须实际证据。

修改规范、平台矩阵、当前任务状态与声明式 Windows runner 标签，不安装或注册 runner，
不修改管理员授权、不调用模型、不重标历史报告。正式结果见
`../evidence/F28-windows10-acceptance-observations.json`。
