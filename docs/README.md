# 文档导航

## 开发与架构

- [源码审阅](architecture/report.md)：详细实现说明，部分历史内容需结合当前代码阅读。
- [源码学习路线](architecture/study.md)：按调用链阅读项目。
- 产品实现：`forge/`；产品评测支持：`evals/`；Harbor 适配与调度：`benchmark/harbor/`。
- 测试：`tests/`；日常运行脚本：`scripts/`；离线实验分析：`benchmark/analysis/`。

## 当前实验与优化

- [最终 89 题结果](reviews/2026-09-17-final89-after-servererror-retry.md)
- [48 题失败分类](reviews/2026-09-17-final48-failure-categories.md)
- [框架缺陷审计](reviews/2026-09-17-framework-bug-audit.md)
- [优化路线](reviews/2026-09-17-optimization-roadmap.md)
- [第一批修复](reviews/2026-09-17-framework-repair-implementation.md)
- [历史 Aider 评测](reviews/2026-08-13-evaluation-summary.md)

## 文件存放规则

| 内容 | 位置 |
|---|---|
| 历史审计报告及其数据 | `docs/reviews/` |
| 原根目录历史测试日志 | `docs/reviews/logs/legacy-root/` |
| 新的日常测试日志 | `.local/logs/tests/`，不进入 Git |
| 新测试临时文件 | `.local/t/`，不进入 Git |
| 本次移出的旧测试目录 | `.local/retired-test-runs/`，可恢复，尚未删除 |
| 原始 benchmark 运行证据 | `benchmark/runs/`，保留 |
| 依赖与镜像准备缓存 | `benchmark/.cache/`，保留 |

Windows 推荐运行 `./scripts/test.ps1 -q`，或追加 pytest 路径。该入口将日志和临时目录集中保存；路径过长时可以用 `-TempRoot D:/fc-tests` 指定更短的临时根目录。不要将已有数据目录直接作为 pytest 的 `--basetemp`。

兼容入口和历史基线代码只有在确认调用方、测试与复现实验都不再需要时才删除；不能仅以文件旧、未提交或名称含 legacy 判断无用。
