# ForgeCode v0.1.1 测评总结

> 发布确认：225 题全部结束，最终 reward 211/225；其中 1 题是 Harbor 基础设施错误，代码题按最终 reward 为 211/224 = 94.20%，按 Harbor 原始 225 分母显示 93.78%。

## 结论

ForgeCode v0.1.1 已接入 Harbor 的 Aider Polyglot 225 题基准，并使用真实模型 `gpt-5.6-luna` 完成了一次完整运行。按 Harbor 最终 verifier reward 统计，225 条记录中 211 条通过；剔除 1 条基础设施错误后，代码题最终准确率为 **94.20%（211/224）**。若按 Harbor 原始 225 题分母报告，则为 **93.78%（211/225）**。

该结果包含 Aider Polyglot 允许的一次 verifier-feedback repair。严格首轮成功率为 **43.30%（97/224）**；另有 114 题在反馈后修复通过。因此首轮 pass@1 不能替代允许修复协议下的最终代码正确率。

## 测评配置

| 项目 | 配置 |
| --- | --- |
| 基准 | Aider Polyglot / Harbor |
| 题目数 | 225 |
| 模型 | `gpt-5.6-luna` |
| Harbor | `0.18.0` |
| Docker | `29.7.2` |
| 数据集 commit | `f30b14415dd733c83627204bad0af69a89ceb46f` |
| 并发 | 1 |
| 最大重试 | 3 |
| Agent setup timeout multiplier | 12 |
| 安装重试 | 8 |
| 构建策略 | `--force-build` |
| 运行时间 | 2026-08-12/13 |

运行目录：`benchmark/runs/harbor/full-225-luna-rerun/2026-08-12__11-53-03`

机器可读汇总：`benchmark/runs/harbor/full-225-luna-rerun/summary.json`

原始 Harbor 结果：`benchmark/runs/harbor/full-225-luna-rerun/2026-08-12__11-53-03/result.json`

## 总体指标

| 指标 | 数值 | 说明 |
| --- | ---: | --- |
| Harbor 原始 reward | 211/225 = 93.78% | 保留基础设施错误在分母中 |
| 代码题最终 reward | 211/224 = 94.20% | 排除 1 条基础设施错误 |
| 严格首轮 pass@1 | 97/224 = 43.30% | 不含反馈修复 |
| 反馈后修复通过 | 114 | 首轮失败、最终通过 |
| 基础设施错误 | 1 | `RewardFileNotFoundError` |
| 模型调用 | 3,488 | ForgeCode 轨迹汇总 |
| 工具调用 | 3,840 | ForgeCode 轨迹汇总 |
| 输入 token | 42,090,702 | 含缓存读写遥测字段 |
| 输出 token | 885,602 |  |

## 分语言结果

最终 reward 按剔除基础设施错误后的语言分母统计：

| 语言 | 通过 / 计分题 | 最终准确率 | 严格首轮 pass@1 | 反馈修复数 |
| --- | ---: | ---: | ---: | ---: |
| C++ | 24/25 | 96.00% | 8/25 = 32.00% | 16 |
| Go | 37/39 | 94.87% | 14/39 = 35.90% | 23 |
| JavaScript | 49/49 | 100.00% | 34/49 = 69.39% | 15 |
| Java | 39/47 | 82.98% | 9/47 = 19.15% | 30 |
| Python | 32/34 | 94.12% | 8/34 = 23.53% | 24 |
| Rust | 30/30 | 100.00% | 24/30 = 80.00% | 6 |

Java 是当前主要改进方向：最终准确率 82.98%，且首轮 pass@1 只有 19.15%，说明 Java 项目布局、构建工具和 API 发现仍会显著消耗修复预算。

## 最终未通过题目

代码级最终 reward 为 0 的 13 题如下：

- Python：`forth`、`go-counting`
- Go：`hexadecimal`、`robot-simulator`
- C++：`crypto-square`
- Java：`rational-numbers`、`rest-api`、`sgf-parsing`、`forth`、`wordy`、`zebra-puzzle`、`ocr-numbers`、`ledger`

另有 1 题基础设施错误：C++ `circular-buffer`，Harbor `RewardFileNotFoundError`。

## 本轮暴露的 ForgeCode 问题与处理

### 已处理

1. **Windows 长命令行限制**：Harbor 适配器改用 `--message-file` 上传任务说明，避免 WinError 206。
2. **Rust 工具链缓存污染**：完整运行支持 `--force-build`，避免旧镜像层继续使用失效的 `rustc` PATH。
3. **任务 scope 锚定错误**：任务管理器会把唯一的文件名提示解析到真实仓库路径，例如将 `WordProblemSolver.java` 解析为 `src/main/java/WordProblemSolver.java`，同时保留 glob 和歧义路径的安全行为。
4. **验证命令恢复**：当 `pytest` 或 `python` 可执行文件不存在时，验证器会尝试 `python3 -m pytest` 或 `python3` 等安全等价命令，并记录实际执行命令。
5. **自然语言误判 scope**：benchmark 的 `supplied files:` 声明优先于普通名词，避免把“within a grade”等文本误判为目录。
6. **反馈与汇总可靠性**：无 reward、编译失败、基础设施异常与代码失败分开归类；汇总器同时输出最终 reward、严格首轮 pass@1、修复数、语言统计和调用遥测。

本地回归验证：`448 passed`，`uv lock --check` 通过。

### 仍需改进

剩余失败主要集中在：

- 从测试和现有 stub 推断精确 API 签名；
- Java `src/main/...` 多文件工程与顶层类型关系；
- 复杂状态机/并发协议（例如 SGF、robot-simulator）；
- Gradle wrapper 下载等外部依赖不可用时的验证降级；
- 工具或 skill 加载失败后的 repair 恢复路径。

这些问题应作为 v0.1.2 的工程任务，并在同一 Harbor 协议下做针对性复测和完整 225 题回归。

## 复现

```powershell
uv sync
docker version
uv run harbor --version

uv run python -m benchmark.harbor.run_aider `
  --model gpt-5.6-luna `
  --concurrency 1 `
  --max-retries 3 `
  --agent-setup-timeout-multiplier 12 `
  --install-retries 8 `
  --install-retry-delay-seconds 30 `
  --force-build `
  --output-dir benchmark/runs/harbor/full-225-luna-rerun

uv run python -m benchmark.harbor.summarize `
  benchmark/runs/harbor/full-225-luna-rerun/<job-timestamp> `
  --output benchmark/runs/harbor/full-225-luna-rerun/summary.json
```

评测使用真实模型和本地代理，报告中未记录 provider 返回的费用字段；token 数字仅用于运行量审计，不能据此推断实际账单。

## 版本确认

- ForgeCode package version：`0.1.1`
- 版本发布结论：225 题全部结束；最终 reward 211/225；1 条 Harbor 基础设施错误；代码题最终准确率 211/224 = 94.20%；Harbor 原始分母 211/225 = 93.78%。
- 该报告与基线原始 JSON 同步提交，便于后续复核。
