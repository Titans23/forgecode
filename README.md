# ForgeCode 1.0.0

ForgeCode 是轻量、可扩展的 Python Agent Harness，用来构建、运行、验证和复用 Agent。Coding Agent 和终端 CLI 是默认应用；Personal、Robot 等领域通过同一内核和可选插件扩展，领域适配需要各自验证。

核心提供模型与工具循环、并发调度、权限、上下文、会话和完整运行轨迹。自然语言插件开发、可移植 Agent 配置和 benchmark 评测使用同一运行路径。项目不内置训练或蒸馏框架。

## 安装

先安装 Git 与 Node.js 22.13+，在 Windows x64 或 Linux x64 上从标签构建：

```sh
git clone --branch v1.0.0 https://github.com/Titans23/forgecode.git
cd forgecode
npm run setup
forge --version
forge config setup
```

`setup` 准备 uv、Python、锁定依赖和本机安装包，再安装全局 `forge`。安装不调用模型，保留已有 `~/.forge/.env` 和会话。日常使用无需 Docker，Windows 无需 WSL。当前提供源码与本机构建安装包；npm/PyPI registry 发布是独立流程。

升级到其他标签时执行 `git fetch --tags`、`git checkout <版本标签>`、`npm run setup`。开发者使用 `main`，准备命令见 [使用指南](docs/install/terminal-quickstart.md#开发与验证)。

纯 SDK 使用 Python 3.12+，在源码目录按需安装：

```sh
uv venv
uv pip install -e '.[anthropic]'
```

基础 SDK 不要求工作区、Node、CLI、YAML 或模型 SDK。可选依赖：`anthropic`、`openai`、`gemini`、`cli`、`coding`、`mcp`、`validation`、`observability`、`channels`、`evaluation`；`terminal` 包含默认 coding CLI 所需依赖。

## 使用

在目标项目目录启动：

```sh
forge config
forge
forge --continue
forge --resume SESSION_ID
```

`forge config` 只检查有效配置，不显示密钥或调用模型。配置优先级为进程环境 > 项目 `.env` > 用户 `~/.forge/.env`。API Key 通过 `forge config setup` 或 `forge config set api-key` 的隐藏输入设置。

终端实时显示等待、模型可读思考和工具状态。`Ctrl+O` 展开详情，`/resume` 按最近活动排序并标记当前会话，`/jobs` 查看后台命令，`Ctrl+C` 取消当前回合。

普通 coding 默认 `danger-full-access` + `never`，使用当前用户宿主权限；不初始化原生沙盒、独立意图路由、全盘扫描或强制完成验收。需要限制执行时选择：

```sh
forge --sandbox workspace-write
forge --sandbox read-only
```

会话内 `/permission` 同时切换沙盒和审批策略。扩大权限须逐次授权，不自动回退宿主。Windows 原生 workspace-write 按 DSH 机制设置 ACL/完整性标签，需要相应目录权限；普通启动和默认宿主执行不做这项设置。原生能力仍为 partial，不能宣称完整读取、网络和资源隔离。

## Python API 与 Agent 组合

以下示例会调用已配置模型：

```python
import asyncio
from forge.api import AgentDefinition, HarnessSession, SessionOptions
from forge.config import ForgeConfig

async def main():
    options = SessionOptions(
        agent=AgentDefinition.core("assistant"),
        config=ForgeConfig.from_env(),
    )
    async with await HarnessSession.open(options) as session:
        result = await session.run("你好")
        print(result.text)
        print(session.journal.path)

asyncio.run(main())
```

`core` 不创建仓库、任务、检查点或命令作业组件。`coding` 可通过 `features` 选择 `tasks/repository/skills/hooks/mcp/checkpoints`，通过 `tools` 选择具体工具。安全声明允许的工具默认最多并发 10 个；`max_parallel_tools=1` 恢复串行。使用 `completion="strict"` 或追加完成检查表达独立验收要求。

## 插件、评测与轨迹

| 需求 | 入口 |
|---|---|
| 创建和自然语言开发插件 | `forge plugin init/develop` |
| 独立检查与跨环境交付 | `forge agent check/export/import` |
| 冻结并运行公共基准 | `forge eval plan/run` |
| 查看、取消和比较评测 | `forge eval status/cancel/report/compare` |
| 导出成功或失败轨迹 | `forge eval export --format atif` 或 `--format openai` |

可信 Python 插件须显式信任，由 PluginHost 管理注册和清理；这种信任不等于恶意插件隔离。自然语言开发使用调用方持有的固定验收，完成后不自动启用插件。

公共基准已接入 Terminal-Bench 2、SWE-bench Verified 和 Aider Polyglot。任务、源码、模型和运行条件冻结，官方 grader 独立判分；默认直接使用官方任务镜像。Docker 只用于相应评测环境。历史成绩保留原模型、提交和协议归属，不代表 1.0.0 的能力成绩。

Journal 是默认执行事实源，记录实际模型请求、思考、工具结果、重试、用量、失败和取消。ATIF 与 OpenAI messages JSONL 是离线脱敏投影，导出不重放工具或修改原记录。Harness 不设置累计请求、工具、token 或回合时长预算；单次操作超时、取消和外部评测期限独立保留。

## 文档与验证

- [安装、配置、CLI、SDK 与插件](docs/install/terminal-quickstart.md)
- [内核与扩展架构](docs/implementation/architecture.md)
- [架构 Wiki：概念、源码、流程与同类产品对比](docs/architecture-guide.html)
- [benchmark 运行与历史成绩](benchmark/README.md)
- [当前工作和验证范围](docs/implementation/progress.json)
- [开发约定](AGENTS.md)与[文档索引](docs/README.md)

CI 分别运行契约、质量、跨平台回归、最小 SDK/插件交付和依赖检查。确定性测试、原生隔离验收与真实模型实验分开报告；完整原生隔离和新模型评测仍需单独验证。

ForgeCode 自有代码采用 [MIT](LICENSE)；[第三方声明](THIRD_PARTY_NOTICES.md)保留 DSH、pi、依赖和基准的原有权利。
