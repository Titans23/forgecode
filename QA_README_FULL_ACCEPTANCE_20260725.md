# ForgeCode README 全功能验收报告

日期：2026-07-25
项目版本：ForgeCode 0.1.0
模型端点：Anthropic-compatible，本地端点，模型 `gpt-5.4-mini`
测试原则：已实现项执行功能验收；README 中未勾选的 M7 路线图只做一致性检查，不伪装为已实现能力。

## 1. 结论

- 修复后完整自动化测试：`467 passed in 143.07s`。
- Agent Loop、Completion Gate、Workspace 与评测器相关回归：`122 passed in 94.90s`。
- 新增四项根因回归测试：`4 passed in 7.53s`。
- 修复后真实模型固定评测：`3/3` 通过。
- 三个真实任务的公开测试、隐藏测试、构建和 `git diff --check` 均通过，先前 `2/3` 的 Completion Gate 假拒绝已经消失。
- 当前版本不满足 README 的 M7/v1.0 验收条件：只有 3 个固定评测任务，尚未达到 30 个，也未完成四组消融实验等未勾选项目。

## 2. 真实场景

| ID | 场景 | 主要覆盖 | 结果 |
| --- | --- | --- | --- |
| R01 | Python 计算器除零 Bug 修复并补测试 | 多轮工具、Patch、新文件、Verify、公开/隐藏测试、Diff Gate | 修复后通过 |
| R02 | TypeScript Todo 按 ID 完成功能 | 多文件修改、失败验证恢复、构建、公开/隐藏测试、最终完成 | 通过 |
| R03 | Java 订单金额按数量计算并补测试 | Maven、多文件 Patch、双 Verify、公开/隐藏测试 | 修复后通过 |
| R04 | Plan 模式请求写文件 | 权限模式、effectful 工具隐藏、拒绝后立即停止 | 自动化通过 |
| R05 | Supervised 会话授权 | 一次/会话/项目/用户范围、审计 | 自动化通过 |
| R06 | Auto 模式危险命令 | `.env`、越界、网络、安装、删除、提权、危险 Git | 自动化通过 |
| R07 | Patch 上下文不匹配与恢复 | 结构化错误、一次聚焦读取、写工具收敛 | 自动化通过 |
| R08 | 超过 30,000 字符的新文件 | `write_file` 拒绝、`write_file_chunk` 原子分块 | 自动化通过 |
| R09 | 大 Git Diff | 单文件分页、游标顺序、Diff 变化失效、未跟踪文件 | 自动化通过 |
| R10 | 20 轮工具历史与大结果 | 磁盘 artifact、消息对完整、cheap compaction | 自动化通过 |
| R11 | 重复读取与停滞 | covered read、grep 新证据、重复调用短路 | 自动化通过 |
| R12 | Session 崩溃恢复 | JSONL 尾损坏、完整工具对恢复、未知工具结果不重放 | 自动化通过 |
| R13 | `/resume`、fork、rename、history、status、branch、clear | 会话切换、Slash 补全、模型隔离 | 自动化通过 |
| R14 | Checkpoint、undo、rewind | 代码/对话/双回滚、外部改动冲突、路径边界 | 自动化通过 |
| R15 | Hooks 全生命周期 | 9 个事件、参数改写、拒绝、超时、输出上限、审计 | 自动化通过 |
| R16 | MCP stdio 与 HTTP | 配置合并、动态发现、list_changed、权限、断线未知结果 | 自动化通过 |
| R17 | Explore Agent | 只读工具集、隔离上下文、结构化报告、预算限制 | 自动化通过 |
| R18 | ActiveTask 与持久化计划 | task_plan/update、续接、恢复、阻塞/完成状态 | 自动化通过 |
| R19 | 项目规则与 Memory | AGENTS/FORGE/rules、相关性选择、秘密拒绝、管理命令 | 自动化通过 |
| R20 | CLI 配置与安装一致性 | `--help`、`--version`、`config`、`sessions`、`uv lock --check` | 通过 |

## 3. README 功能覆盖矩阵

| README 功能域 | 验收证据 | 状态 |
| --- | --- | --- |
| M1 Agent Loop、流式、多工具、协议恢复、结构化错误 | 完整套件及 runtime 分类套件 | 通过 |
| 文件、搜索、30k 写入、分块写入、精确替换 | tools 分类套件 | 通过 |
| Patch、Git 状态、Diff、分页、未跟踪文件 | tools 分类套件 | 通过 |
| Shell、Verify、超时、输出截断、环境净化 | tools/runtime 分类套件 | 通过 |
| M2 Workspace、Revision、Completion Gate、评测器 | 467 项自动化及 3/3 真实评测通过 | 通过 |
| M3 plan/supervised/auto、规则、审计 | 自动化通过 | 通过 |
| Checkpoint、Undo、Rewind | sessions/CLI 自动化通过 | 通过 |
| M4 Session、Resume/Fork、Slash 补全、损坏恢复 | CLI/terminal/sessions 自动化通过 | 通过 |
| M5 Context、artifact、compact、结构化摘要、熔断 | context/runtime 自动化通过 | 通过 |
| 项目规则、Memory、ActiveTask、WorkingState | context/tasks/runtime 自动化通过 | 通过 |
| M6 Hooks | hooks/runtime 自动化通过 | 通过 |
| MCP stdio/HTTP、动态工具与权限 | MCP 集成测试通过 | 通过 |
| Explore Agent | subagents/runtime 自动化通过 | 通过 |
| M7 评测矩阵、30+ 任务、四组消融、SWE-bench Lite | README 明确未勾选；当前仅 3 个案例 | 未实现 |

## 4. 发现的问题

### B1 — Java 测试路径被 Completion Gate 漏识别（高，已修复）

`is_test_file_path()` 支持 `tests/` 和 `/tests/`，但不支持 Maven/Gradle 常见的 `src/test/java/...`。真实任务已经修改 `src/test/java/io/forgecode/orders/OrderServiceTest.java`，公开测试、隐藏测试和构建都通过，`finish_task` 仍被拒绝为“Diff contains no test file”。

修复结果：按目录段识别 `test`、`tests`、`__tests__` 和 `*.Tests`，并覆盖 Maven/Gradle、Python、TypeScript、Go 与 .NET 路径；Java 真实评测已通过。

### B2 — Verify 自己生成文件会让自己的证据立即过期（高，已修复）

Python 的 `verify` 成功后生成 `__pycache__/*.pyc`。工具结果绑定 revision 2，随后工作区刷新把这些副作用记为 revision 3，Completion Gate 因“code changed after verification”拒绝完成。

修复结果：Verify 证据改为绑定命令结束并刷新后的 workspace revision；评测临时仓库额外排除 `__pycache__/` 与 `*.py[cod]`，同时仍保留对其他构建副作用的追踪。Python 真实评测已通过。

### B3 — TypeScript 任务的工具与 Token 浪费（中，已显著改善）

修复前使用 19 次模型调用、28 次工具调用、76,310 input tokens，并出现 6 次工具失败。加入“依赖验证串行执行、编辑后优先最小验证、失败后只检查失败目标”的短提示后，复测降为 9 次模型调用、13 次工具调用、27,775 input tokens 和 1 次工具失败，任务保持通过。

### B4 — 固定评测规模与 README v1.0 目标不一致（中）

当前只有 Python、TypeScript、Java 共 3 个 YAML 案例，README 的 v1.0 条件要求 30 个以上任务、至少四组消融和多个真实仓库。此项属于未完成路线图，不是回归，但不能宣称 README 全部能力已经验收通过。

## 5. 真实评测效率

| 案例 | 状态 | 模型调用 | 工具调用 | Input | Output | 工具失败 | 完全重复 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Java | completed | 9 | 15 | 31,077 | 1,978 | 0 | — |
| Python | completed | 7 | 10 | 20,730 | 1,358 | 0 | — |
| TypeScript | completed | 9 | 13 | 27,775 | 2,369 | 1 | — |
| 合计 | 3/3 accepted | 25 | 38 | 79,582 | 5,705 | 1 | — |

## 6. 可复现命令

```powershell
uv lock --check
uv run forge --help
uv run forge --version
uv run forge config
uv run forge sessions
uv run pytest -q
uv run pytest -q tests/test_cli.py tests/test_terminal.py tests/sessions tests/tasks tests/context
uv run pytest -q tests/hooks tests/mcp tests/tools tests/runtime/test_hooks.py tests/runtime/test_workspace_completion.py tests/runtime/test_model_boundary.py
uv run pytest -q tests/runtime tests/evals tests/subagents
uv run python -m evals.runner
```

评测结果和 JSONL 轨迹保存在 `.forge/evals/`。
