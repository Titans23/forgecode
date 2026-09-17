# 项目目录整理记录

2026-09-17。

## 已完成

- 根目录 25 个 `.test-*.log` 移到 `docs/reviews/logs/legacy-root/`，逐一验证 SHA-256 与整理前一致。它们包含历史测试证据，未丢弃。
- 根目录 19 个测试临时目录移入 `.local/retired-test-runs/`，盘点文件内容合计约 18.16 MiB。部分只读项分第二次移入 `-remaining` 目录，仍可恢复。没有声称这些文件已释放磁盘空间。
- `report.md`、`study.md` 移到 `docs/architecture/`；旧版评测总结移到 `docs/reviews/2026-08-13-evaluation-summary.md`，更新 README 与移动文档中的相对链接。
- 删除过期 `benchmark/analysis/probe_framework_faults_20260917.py`：该脚本断言旧缺陷必须存在，修复后会错误失败。历史输出 JSON 保留，当前测试替代它。
- 回归测试改为长期名称 `tests/runtime/test_framework_repairs.py`，更新文档引用。
- 整理 `check_contracts.py` 模块导入位置；未删除仍被调用的兼容接口、运行时模块和历史基线适配代码。
- 新增 `docs/README.md` 和 `benchmark/analysis/README.md`，明确产品、测试、评测适配、分析脚本的边界。
- 新增 `scripts/test.ps1`：日常日志写入 `.local/logs/tests/`，独立临时目录写入 `.local/t/`，可以指定更短的 TempRoot；pytest 缓存移入 `.local/cache/pytest/`。
- 补充根目录临时日志/测试目录的 Git 忽略规则，删除已无对应目录的 play 忽略例外。

## 保留的内容

原始 benchmark 运行结果、镜像与依赖缓存、`.env`、`.venv`、当前会话状态、所有之前未提交的功能修改均保留。安装产生的 egg-info 是当前开发环境元数据，不作为废代码删除。

## 验证

- 新测试入口运行 33 项针对性测试通过，其中包括上轮路径过长的会话恢复测试。
- README、文档导航与三份移动文档的本地 Markdown 链接检查无缺失。
- 25 个归档日志内容摘要一致。
- 根目录已无 `.test-*.log` 和旧 pytest 临时目录。
- `git diff --check` 通过。

## 删除限制

批量递归删除请求被自动审批拒绝，工具只返回 `blocked by policy`，没有进一步原因；该命令未执行。随后采用可恢复的目录移动完成根目录整理，未换工具绕过删除限制。19 个旧临时目录目前只是归档，尚未删除。

盘点明细：[整理前清单](./2026-09-17-cleanup-inventory.json)。
