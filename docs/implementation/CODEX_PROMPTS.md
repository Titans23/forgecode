# Codex 交接提示词

## 首次实施

```text
请按 docs/implementation/forgecode-v4.md 实际开发 ForgeCode，不要只重新输出计划。
先读取仓库各级 AGENTS.md、docs/implementation/PLANS.md、progress.json、handoff.md，
并阅读主规范第 0—3 章。先完成 F00，核对当前代码、入口、函数签名和基线测试。
随后按照 backlog.json 的依赖执行任务，每项阅读对应 tasks/Fxx.md 与规范章节。
保留现有 Python Harness 与 CLI；实现 Windows／Linux 客户端、原生轻量沙盒、
Harness 可观测性和评测闭环，不以静态页面或 Mock 替代真实实现。
每项新增行为测试并运行回归，更新 progress、任务卡、evidence 与 handoff。
区分代码实现、双平台验收与真实模型实验；缺环境／授权时保持 blocked 并继续无关任务。
不得无授权发起付费 API、管理员安装、发布或破坏性 Git 操作。
完成一个任务后继续下一个满足依赖的任务；中断前留下可恢复的交接记录。
```

## 继续实施

```text
继续 ForgeCode V4 实施。先校对真实 HEAD／git status，再读 AGENTS、PLANS、
progress.json、handoff.md 以及当前任务卡。不要假设前一会话的描述都已落到代码。
从未完成且依赖满足的任务继续，运行对应真实测试并回填证据。
不要重复已完成的副作用，不把 blocked 当 pass。结束前更新 handoff。
```

## 原生平台接力

```text
在当前真实操作系统完成 ForgeCode V4 原生／安装版验收。
先运行已经实现的 doctor，确认 OS、runtime、沙盒、工具链与所需权限。
读取 progress 里属于此平台的未验证／blocked 项，按任务和场景执行测试。
系统 setup 只有得到用户授权才执行；不要关闭系统安全机制。
保存实际安装产物、版本、命令、退出码和边界证据，发现缺陷后修复并回归。
不能用 WSL、另一平台或 FakeSandbox 代替原生测试。
```

## 最终交付复核

```text
按 V4 第 24 章运行实现和发布门禁，逐项核查必须行为和证据。
报告 implemented、native_windows、native_linux、packaged、live_eval 的真实状态，
列出所有未满足项、失败、限制与解除条件。不要仅根据复选框宣布全部完成。
给出安装／运行／复现实验命令、原生支持矩阵、上游复用与个人实现边界。
```
