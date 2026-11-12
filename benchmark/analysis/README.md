# 离线实验分析工具

本目录用于已有实验的诊断，不是 Agent 的第二套运行时。分析时保留原始输入、判分、源码与模型条件；脚本输出放在忽略目录，不把历史结果改标为当前版本。

| 工具 | 用途 |
|---|---|
| `audit_core_run.py`、`audit_artifact_failures.py` | 轨迹与失败产物审计 |
| `compare_audited_runs.py` | 已审计运行的比较 |
| `summarize_experiment_history.py` | 历史运行与最终题目结果整理；先检查特定实验路径 |
| `probe_journal_cost.py`、`probe_unseen20_runtime.py` | 特定问题的诊断探针，先检查输入与副作用 |
| `prefetch_dataset_images.py` | 下载任务镜像，执行前确认所选数据集和引擎 |

常规导出优先使用 `forge eval report/export/compare`，见 [benchmark 指南](../README.md)。旧故障探针从 Git 历史读取；当前修复覆盖在 `tests/runtime/test_framework_repairs.py`。分析脚本不能自动重跑付费任务或修改冻结证据。
