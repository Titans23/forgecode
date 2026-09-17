# 离线实验分析工具

此目录放实验分析工具，不属于 agent 产品运行时。保留原始实验输入和历史报告，以便核对结论。

- `audit_core_run.py`、`audit_artifact_failures.py`：轨迹和失败产物审计。
- `compare_audited_runs.py`：已审计运行的对照。
- `summarize_experiment_history.py`：历史实验与最终题目结果整理；目前含特定实验路径，运行前检查配置。
- `probe_journal_cost.py`、`probe_unseen20_runtime.py`：专项诊断探针。
- `prefetch_dataset_images.py`：数据集镜像预下载工具，会下载镜像。

原 `probe_framework_faults_20260917.py` 断言旧 bug 必须存在，已随修复移除。旧复现输出保存在 `docs/reviews/2026-09-17-framework-fault-probes.json`，当前修复回归位于 `tests/runtime/test_framework_repairs.py`。
