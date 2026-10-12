# ForgeCode 终端版本

当前 main 提供持续对话的 Python Harness 和真实轻量沙盒。终端、评测入口通过 Profile 选择；Python SDK 直接调用同一核心。安装环境需要 Python 3.12+、Node 22.13+、Git、ripgrep；Windows 需要 PowerShell 7 和 NTFS，Linux 需要 bubblewrap 及可用的非特权 user namespace。安装程序不会请求管理员权限或自动修改系统策略。

## 从主分支安装

在独立安装目录检出 `main`，不要把 ForgeCode 安装目录当作沙盒项目。

```powershell
git clone --branch main https://github.com/Titans23/forgecode.git forgecode-terminal
cd forgecode-terminal
uv sync --locked
npm ci --ignore-scripts
uv run --no-sync python scripts/build_bridge.py
uv tool install --editable .
```

也可以构建包含沙盒代码及其依赖的 Python wheel：

```powershell
uv run --no-sync python scripts/build_bridge.py --bundle-python
uv build --wheel
uv tool install dist/forge_code-0.1.2-py3-none-any.whl
```

wheel 仍使用本机 Node、Git、ripgrep 和平台沙盒前置条件。全局用户工具环境和沙盒项目分开；在项目内部的 `.venv` 安装 ForgeCode 时，安装目录保护可能拒绝该项目的沙盒启动。

## 持续对话

在另一个代码项目目录设置模型连接，例如在项目 `.env` 中填写 `ANTHROPIC_API_KEY` 和 `MODEL_ID`；也支持 `FORGE_PROVIDER=openai_responses` / `deepseek` 及对应 API key、`FORGE_MODEL`、`FORGE_BASE_URL`。`forge config` 检查配置，不回显密钥。

```text
forge --profile terminal
forge --continue
forge --resume SESSION_ID
forge --continue --fork-session
```

会话中使用 `/context`、`/compact`、`/task`、`/history`、`/rename NAME`、`/resume`；Ctrl+C 停止并清理进程。既有 Journal 保存已提交上下文和执行事实；中断且没有完成记录的工具不会自动重放。

## 权限

```text
forge --sandbox read-only
forge --sandbox workspace-write --write-root ./src --write-root ./tests
forge --sandbox workspace-write --read-root ../reference
forge --sandbox full-access --approval supervised
forge --sandbox workspace-write --approval plan
```

默认 `workspace-write` 允许所选项目写入；指定 `--write-root` 后只授予列出的既有子目录。普通文件编辑复用已选写入范围，删除、命令及其他风险仍经过权限决策。`read-only` 拒绝文件修改；`full-access` 显式使用宿主执行。`plan/supervised/auto` 是独立的审批策略，切换它不会改变沙盒范围。

文件工具按读取/写入范围限制访问，并拒绝凭据、控制目录及不安全链接。当前 OS 沙盒主要限制写入，读取及网络继承宿主；Windows ambient ACL、硬链接和 Node 管道子进程、Linux 动态硬链接等限制仍存在，能力记录为 partial。初始化使用真实边界探针，失败直接拒绝任务。强隔离的正式评测要求不能由此替代。

`forge sandbox --json` 是只读环境诊断，未经项目初始化时会报告未验证/blocked；实际沙盒证明在启动项目会话时产生。

## Profile、Bundle 与程序入口

```text
forge --show-profile
forge --profile evaluation list --json
forge --profile evaluation run aider-polyglot --help
```

`terminal = core + terminal`，`evaluation = core + evaluation`；启动器只导入选中入口。需要 Harbor 时安装 `forge-code[evaluation]`；飞书兼容命令使用可选 `forge-code[channels]`。默认安装没有 Electron、React 或运行 Web 的入口。后续客户端/SDK 使用 Python API；本轮没有发布新的 GUI、HTTP 服务或 SDK 服务器。

```python
import asyncio
from pathlib import Path
from forge.api import HarnessSession, SessionOptions, RunProfile, event_dict

async def main():
    options = SessionOptions(Path.cwd(), sandbox="workspace-write", profile=RunProfile())
    async with await HarnessSession.open(options) as session:
        print(session.session_id)
        async for event in session.stream("解释这个项目的入口"):
            print(event_dict(event))
        result = await session.run("继续解释会话恢复")
        print(result.text, result.status)

asyncio.run(main())
```

`run` 返回既有 `TurnResult`；`stream` 返回既有结构化事件；`event_dict` 提供 `forge.harness.event.v1` JSON 载荷并隐藏 opaque provider continuation、脱敏常见凭据字段。`session.cancel()` 取消当前消费任务，调用方处理 `asyncio.CancelledError` 后可继续下一轮。消费流时用 `contextlib.aclosing` 保证提前离开也关闭流；关闭会话返回真实 cleanup report。审批回调可通过 `approval_handler` 传入，接口不会解析 CLI 文本。

## 评测与后训练

`evals.runner` 保留 `EvalCase/EvalOutcome`、可调用的离线用例接口和可复现的仓库版本/命令证据；`benchmark.cli`、`benchmark.harbor.run_forge.run_turn`、RunSpec/ResultBundle 及独立 verifier 保持原协议。终端初始化不加载 Harbor，正式评测需要兼容的外部环境和单独的模型预算授权。

后训练复用 `extensions.qwen_tool_distillation`：`TraceRecorder/RecordingModelClient` 记录实际模型请求及工具事件，`RecordedConversation` 接收独立 outcome validator，`ForgeCodeRolloutEnvironment` 提供 reset/step 和注入式执行/完成门禁，`RewardEvidence` 保存奖励组成。`clean_episode`、`build_sft_rows`、`build_dpo_row` 和 split manifest 保留版本、来源、许可、脱敏与数据划分检查。采集与导出需要显式调用，不加载 transformers/ms-swift、不启动训练集群或付费实验。

## 验证

```text
uv sync --locked --all-extras --group dev
uv run --no-sync python scripts/impl.py verify --suite regression
uv run --no-sync python scripts/impl.py verify --suite terminal-native
npm run test:unit
```

确定性回归、原生沙盒、安装 wheel、真实模型实验分别留证。本轮 Windows10 原生及独立 wheel 检查已有真实证据；Ubuntu22/24 原生验收和真实模型实验的状态见 `docs/implementation/progress.json`，不使用模拟结果替代。
