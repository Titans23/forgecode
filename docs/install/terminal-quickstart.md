# ForgeCode 终端版本

当前 main 提供持续对话的 Python Harness 和真实轻量沙盒。终端、评测入口通过 Profile 选择；Python SDK 直接调用同一核心。源码安装会自动构建并准备运行时，安装程序不会请求管理员权限或自动修改系统策略。

## 拉取源码并自动安装

准备 Git 和 Node.js 22.13+（含 npm），在独立安装目录执行：

```sh
git clone --branch main https://github.com/Titans23/forgecode.git
cd forgecode
npm run setup
```

命令自动准备 uv、Python，按锁文件安装 Node 构建依赖、编译沙盒、构建并校验 wheel/npm 包，再全局安装 `forge`。构建日志保存在 `dist/package-build.log`。包安装器继续自动校验并准备运行时工具，直接复用已有 Node。用户配置保存在安装包之外，升级不会清空旧包正在使用的 Python 环境。Windows x64/NTFS 原生沙盒不需要 WSL 或 Docker。

安装与原生沙盒已在 Windows 10 和 WSL2 Ubuntu 26.04（ext4 工作区）上验证；Ubuntu 22.04 / 24.04 仍待原生验证。Linux 需要系统 Git、bubblewrap 和可用的非特权 user namespace。安装器不会执行 sudo、关闭系统保护策略或在沙盒失败后自动切换为 full-access。

更新时在源码目录执行 `git pull --ff-only`，再执行 `npm run setup`。开发包尚未发布到 npm registry；该命令直接安装 `dist` 中本机生成的包，不需要手动下载 `.tgz`。安装仓库与目标项目分开，在目标项目运行 `forge`。

## 源码开发安装

以下步骤仅用于需要 editable 安装的开发者；日常安装和更新使用上面的 `npm run setup`。开发安装需要 uv、Python 3.12+、Node 22.13+、Git、ripgrep，Windows 还需要 PowerShell 7。在独立目录获取 `main`，不要把 ForgeCode 安装目录当作沙盒项目：

```powershell
git clone --branch main https://github.com/Titans23/forgecode.git forgecode-terminal
cd forgecode-terminal
uv sync --locked
npm ci --ignore-scripts
uv run --no-sync python scripts/build_bridge.py
uv tool install --editable . --force
```

也可以构建包含沙盒代码及其依赖的 Python wheel：

```powershell
uv run --no-sync python scripts/build_bridge.py --bundle-python
uv build --wheel
uv tool install dist/forge_code-0.1.2-py3-none-any.whl
```

wheel 仍使用本机 Node、Git、ripgrep 和平台沙盒前置条件。全局用户工具环境和沙盒项目分开；在项目内部的 `.venv` 安装 ForgeCode 时，安装目录保护可能拒绝该项目的沙盒启动。

## 模型配置

npm 安装自动创建 `~/.forge/.env`，存在时不覆盖。直接安装 Python wheel 的用户可运行 `forge config init`。命令修改用户级配置，不会改写当前项目的 `.env`：

```text
forge config setup
forge config setup --from-env .env
forge config set model your-model-id
forge config set base-url https://your-provider-endpoint
forge config set api-key
forge config path
forge config
```

`setup` 按提示设置协议、模型、服务地址与 API Key；输入密钥时隐藏内容。同一协议下再次设置可以留空保留密钥，更换协议需重新输入。`--from-env` 仅导入模型字段，保留源文件和用户配置中的其他条目。`set` 修改单项；API Key 只通过隐藏提示输入，不放在命令参数中。

用户配置按原样读取，兼容 `ANTHROPIC_API_KEY`、`MODEL_ID` 等原有字段；也支持 `openai_responses` 和 `deepseek` 协议。优先级为进程环境变量 > 项目 `.env` > 用户配置；项目内检查的有效配置可能覆盖刚修改的用户配置。`forge config` 只检查配置，不验证网络连接、不调用模型、不显示密钥。

默认上下文为 256K（262,144 tokens），单请求输出上限为 64K（65,536 tokens），分别由 `MODEL_CONTEXT_WINDOW`、`MODEL_MAX_TOKENS` 覆盖；已有显式配置仍优先。这里配置的是 Harness 预算，所用服务需支持相应容量。参考 Pi，输入超过 245,760 tokens 时触发压缩，保留约 20,000 tokens 的近期原文。64K 是输出上限，实际请求额度随剩余窗口缩小，并留出窗口 1% 的估算余量；大工具输出超过 8,192 字符时保留头 4,096、尾 1,024 字符，完整原文仍可通过受限 artifact 接口读取。压缩规则见 [架构说明](../implementation/architecture.md)。

## 持续对话

```text
forge --profile terminal
forge --continue
forge --resume SESSION_ID
forge --continue --fork-session
```

会话中使用 `/context`、`/compact`、`/task`、`/history`、`/rename NAME`、`/resume`；Ctrl+C 停止并清理进程。既有 Journal 保存已提交上下文和执行事实；中断且没有完成记录的工具不会自动重放。

交互终端执行中可以直接输入调整意见，在下次模型请求前应用；`/follow 任务` 排队到当前回合之后，`/cancel` 停止当前回合并保留会话。审批提示会暂时独占输入。取消后用 `/pending` 查看待处理输入、`/next` 继续一项、`/discard 输入ID` 删除一项。待处理输入随 Journal 保存；文本重定向等非交互输入保持顺序处理。

`@path` 可附加 UTF-8 文本或 PNG/JPEG/GIF/WebP。文字合计不超过 256 KiB，最多 8 张图片，单张不超过 512 KiB。较大图片先裁剪或缩小。发送图像前请选择支持视觉的具体模型；适配器支持图像传输不代表服务端任意模型都支持视觉。

项目根目录的 `AGENTS.md`、`FORGE.md` 和 `.forge/rules/*.md` 会在每轮开始时加载。工具访问子目录时，会自动加载该目录及祖先目录的 `AGENTS.md`、`FORGE.md`，供下一次模型请求使用；新规则尚未送达模型时，相关写入或进程工具会先返回提示。也可用 `@src/AGENTS.md` 显式附加规则。

新交互会话在首次提交对话时才保存；直接退出或仅查看历史不会生成空会话。
恢复会话默认显示最近 10 轮，`/history all` 显示完整可用对话；工具历史以摘要展示，
不会重新执行。`/clear` 保留旧会话，新会话等待下一次对话再保存。

## 权限

```text
forge --sandbox read-only
forge --sandbox workspace-write --write-root ./src --write-root ./tests
forge --sandbox workspace-write --read-root ../reference
forge --sandbox full-access --approval supervised
forge --sandbox workspace-write --approval plan
```

默认 `workspace-write` 允许所选项目写入；指定 `--write-root` 后只授予列出的既有子目录。普通文件编辑复用已选写入范围，删除、命令及其他风险仍经过权限决策。`read-only` 禁止修改项目，但允许每次执行的私有临时文件；执行结束后清理。`full-access` 显式使用宿主执行。`plan/supervised/auto` 是独立的审批策略，切换它不会改变沙盒范围。

在 `full-access` 下启用 Hooks 时，Hook 结束或取消会清理它启动的后台子进程。命令应使用明确的解释器和参数：PATH、普通环境变量及 `FORGE_HOOK_*` 保留，凭据与 `PYTHONPATH`、`NODE_OPTIONS` 等加载器变量会被清除。stdout 与 stderr 合计超过 1 MB 或无法确认清理完成时，Hook 报错；阻断类 Hook 会拒绝后续操作。

Windows 沙盒命令中的 `python` 通过会话级小型虚拟环境复用本机解释器和依赖，使默认临时目录继承已隔离的私有 ACL；会话退出后清理该环境。普通启动、`python -I` 及其 Python 子进程支持此适配。显式 `-S`、其他绝对路径解释器或在私有临时范围之外创建 `0700` 目录不经过适配，仍可能受 [CPython 的 Windows 受限令牌兼容问题](https://github.com/python/cpython/issues/134587)影响。

Windows 原生 Python 命令的 pytest 缓存默认放入本次执行的私有临时目录，cacheprovider 保持启用；不修改全局配置，显式 `PYTEST_ADDOPTS` 和命令行选项按 pytest 的既有优先级覆盖。缓存随执行清理，不承诺跨命令的 `--lf` 或缓存持久化。

文件工具按读取/写入范围限制访问，并拒绝凭据、控制目录及不安全链接。当前 OS 沙盒主要限制写入，读取及网络继承宿主；Windows ambient ACL、硬链接和 Node 管道子进程、Linux 动态硬链接等限制仍存在，能力记录为 partial。初始化使用真实边界探针，失败直接拒绝任务。强隔离的正式评测要求不能由此替代。

`forge sandbox --json` 是只读环境诊断，未经项目初始化时会报告未验证/blocked；实际沙盒证明在启动项目会话时产生。

## Profile、Bundle 与程序入口

```text
forge --show-profile
forge --profile evaluation list --json
forge --profile evaluation run aider-polyglot --help
forge --profile evaluation run terminal-bench-2 --n-tasks 1 --dry-run
forge --profile evaluation run terminal-bench-2 --task terminal-bench/make-mips-interpreter --concurrency 4 --max-model-calls unlimited --max-tool-calls unlimited --max-turn-seconds unlimited --dry-run
forge --profile evaluation status <run-dir>
forge --profile evaluation cancel <run-dir>
forge --profile evaluation report <run-dir>
forge --profile evaluation export <run-dir> --format atif
forge --profile evaluation compare <baseline> <candidate> --change harness
forge --profile evaluation export <qualified-training-run> --format sft --tokenizer-dir <local-tokenizer>
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

API 同时接受 `[{'type': 'text', 'text': '分析图片'}, image_block(png_bytes)]`，`image_block` 从 `forge.messages` 导入。执行中使用 `session.queue_input('追加约束')`；`follow_up=True` 排队新任务。`session.pending_inputs` 返回待办，`await session.run_next()` 明确执行下一项。同一会话不能并发启动多个回合。

`RunProfile(intent_routing=True)` 控制前置意图分类。工具批次按顺序执行，`max_parallel_reads` 参数已移除，旧调用应删除该参数。`session.conversation.capabilities` 区分适配器图片传输能力与未知的具体模型模态。完成报告中的运行完成、验证结果与调用方验收是独立字段，未配置验收不会显示为已验收。

## 5 分钟 Harness 演示

先完成源码开发安装。在安装仓库运行下面的公开示例；它会创建与安装目录分开的新临时项目，无需用户模型配置，也不调用提供方：

```text
uv run --no-sync python examples/harness_demo.py --output .local/implementation/api-demo-001
```

重复演示时换一个新的输出目录。示例使用 `HarnessSession.stream`、真实原生文件/进程工具和同一个 TaskPolicy：修改 `value.txt`，运行行为检查，再执行命令使验证过期；第一次 `finish_task` 被拒后，在原 8 次请求预算内重新验证并完成，共 6 次脚本请求。`max_delivery_repairs=2` 只启用已有回合内反馈，不放宽新鲜度或断言。

检查输出的两个文件：

- `events.jsonl`：版本化事件，包括 WorkspaceChanged、两次 VerificationCompleted、工具结果和最后的 TurnCompleted。
- `summary.json`：`status=completed`、`shared_model_calls=scripted_requests=6`、`original_request_limit=8`、`provider_api_calls=0`、`client_closed=true`、原生 `cleanup.state=clean` 和 `fixture_retained=false`。脚本 usage 是协议夹具值，不能当真实 token 成本。

示例还用 `SessionStore.load` 只读重建会话，确认前后 Journal SHA-256 相同、不重放工具、不新开回合。原生清理未确认时保留新建夹具并写失败 summary；清理失败以非零状态退出。当前 Windows 原生实测约 75 秒；Linux 同一入口仍依赖 bubblewrap，不能由 Windows 结果替代验收。

已有真实任务结果时，还能通过现有评测 CLI 在新副本独立核验保存的候选，不重新执行 Agent：

```text
uv run --no-sync --with pyarrow==19.0.1 python -m evals.native_swe_campaign --replay-outcome <保存的结果.json> --output <新的判分目录>
```

该命令需要对应的冻结任务缓存，输出 `replay.json` 和原生判分/清理证据；它不加载模型客户端，原尝试的 partial/failed、历史未知清理及完成率保持原样。只读状态重建、候选重新判分和同回合修复是三项不同能力。原 Agent 跨进程保留剩余预算续跑尚未实现；`forge --resume` 开启新回合。

展示贡献时，底层 Windows 写限制归于 DSH，Linux 归于 bubblewrap；ForgeCode 复用已有 Python Agent 循环，工作集中在共享运行时接入、权限/预算传递、验证门禁、Journal、评测与生命周期证据。脚本模型演示和 `.env` 真实任务结果分别展示，不据此宣称自研 OS 隔离或稳定模型收益。

## 评测与后训练

`evals.runner` 保留 `EvalCase/EvalOutcome`、可调用的离线用例接口和可复现的仓库版本/命令证据；`benchmark.cli`、`benchmark.harbor.run_forge.run_turn`、RunSpec/ResultBundle 及独立 verifier 保持原协议。终端初始化不加载 Harbor，正式评测需要兼容的外部环境和单独的模型预算授权。

原生真实仓库任务会安装独立的固定 Python 环境，任务文本中的 `Task Python` 给出其路径；Python 命令和导入检查使用该解释器，公开测试通过它的 `-m pytest` 运行。裸 `python` / `pytest` 仍属于 Harness 环境，版本和依赖可能不同。适配器明确展示环境，不自动改写任意命令或全局 PATH。

公开基准可先用 `run terminal-bench-2 --task terminal-bench/cancel-async-tasks --prepare-only --output-dir <准备目录>` 准备依赖，不读取模型配置或调用模型；多题重复指定 `--task`。准备阶段复用 Debian/Ubuntu 官方预构建镜像，缓存 Git/curl、uv 0.12.5、Python 3.12.13 和锁定的 Harness 依赖，并缓存标准 uvx 判分入口所需的官方 uv 0.9.5、Python 和包依赖。派生镜像通过断网检查，判分缓存使用各容器独立的写时复制层，原始镜像及官方测试脚本不改动。正式运行增加 `--prepared-images <准备目录>/prepared-images.json`，启动前核对所选任务、源码及缓存身份；源码变化后需重新准备，下载缓存仍可复用。非标准判分依赖命令会在准备阶段明确报错，不承诺任意任务集均已支持预热。任务自身的系统包、业务数据和网络操作仍遵循原任务，不能视为完全离线评测；未指定清单的既有安装路径保留。

可以让助手通过内置 `view_image` 查看本地 PNG/JPEG/GIF/WebP，单张最多 512 KiB；较大图片先裁剪或缩小。图片沿用现有文件读取权限，支持 Anthropic 和 Responses 适配器；DeepSeek 文本适配器不提供此工具。

依赖准备支持 Linux x86_64；Debian 11 使用官方安全更新归档，仍校验 TLS 与仓库签名。

补丁采集在模型启动前记录 Git 基线，空目录也创建空提交；最终差异以这个固定提交为准，包含模型途中提交的代码。采集错误保留返回码及简短 stderr，不用空补丁代替失败。

后训练复用 `extensions.qwen_tool_distillation`：`TraceRecorder/RecordingModelClient` 记录实际模型请求及工具事件，`RecordedConversation` 接收独立 outcome validator，`ForgeCodeRolloutEnvironment` 提供 reset/step 和注入式执行/完成门禁，`RewardEvidence` 保存奖励组成。`clean_episode`、`build_sft_rows`、`build_dpo_row` 和 split manifest 保留版本、来源、许可、脱敏与数据划分检查。采集与导出需要显式调用，不加载 transformers/ms-swift、不启动训练集群或付费实验。

`compare` 必须声明 `--change harness|model`，逐题展示原始判分变化，并检查任务内容、预算、协议、判分器、依赖和实际 Docker 镜像及资源限制。缺少环境证据或存在额外变化时不视为受控比较；单次比较不提供统计显著性结论。旧运行缺少的证据不会自动补造。

预算参数显式设为 `unlimited` 时，取消对应的 ForgeCode 额外上限，冻结配置记录为 `null`；官方任务期限仍由 Harbor 执行。任务 ID 使用注册表完整名称，如 `terminal-bench/make-mips-interpreter`。`--taskset-dir <已冻结任务目录>` 复用已经下载的官方任务，重新核验内容与任务校验和后复制到新运行，避免重复网络下载；必须匹配数据集及显式指定的版本，未指定版本时采用该目录的冻结版本。WSL 读取 Git 差异超时会明确记入源码清单，实际冻结文件仍逐一进行哈希校验。判分器复用已有 uv 缓存，并采用 120 秒下载等待；这些设置进入冻结配置，官方判分期限保持原值。

Terminal-Bench、SWE-bench Verified 的任务永久为 `evaluation-only`，修改 `usage/split` 不能用于训练导出；可用 ATIF 作脱敏诊断。SFT 从已有 Journal 离线转换为 Qwen/ms-swift 格式，保留上下文 loss mask，不新增执行日志。当前只验收导出接口及合成夹具，没有接入真实训练任务集。

训练输入契约在 `experiment.json.training` 中：`sources` 用 Journal SHA-256 关联 `EpisodeSource`，`tokenizer` 包含 `id/revision/files_sha256`。来源 metadata 必须绑定 `task_content_sha256` 和 `overlap_check`（`status=passed`、方法、包含训练/保留集的 assignments，以及该列表排序键 JSON 的 SHA-256 `reference_sha256`）；导出重新检查仓库、任务和内容跨划分重叠。来源须公开且许可获准，独立判分必须成功，轨迹和工具执行证明必须完整；缺少证据一律拒绝。只从显式本地目录加载目标 tokenizer，校验其字节摘要，不自动下载。旧扩展的 `build-sft` 同样要求本地 tokenizer 和这些来源约束。

## 验证

```text
uv sync --locked --all-extras --group dev
uv run --no-sync python scripts/impl.py verify --suite regression
uv run --no-sync python scripts/impl.py verify --suite terminal-native
npm run test:unit
```

确定性回归、原生沙盒、安装 wheel 和真实模型实验分别验证。自动报告保存在 `.local/implementation`，不写入文档目录；当前待办见 [progress.json](../implementation/progress.json)。
