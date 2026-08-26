# ForgeCode

ForgeCode 是一个运行在终端中的 Agent Harness，用于可靠地执行代码仓库中的工程任务。模型负责判断下一步行动，ForgeCode 负责工具执行、权限、上下文、工作区追踪、验证、恢复和完成判定。

它不是只返回文本的代码问答工具。一次变更任务的基本链路是：

```text
用户任务
  → 意图路由与任务状态
  → 模型选择工具
  → 权限和路径检查
  → 读取、修改或执行命令
  → 验证与工作区 revision 更新
  → Completion Gate 判断是否允许完成
```

当前发行版本以 [`pyproject.toml`](pyproject.toml) 为准，目前是 `0.1.1`。详细源码审阅见 [`report.md`](report.md)；本文只保留安装、使用、架构和评测入口，避免重复维护实现细节与历史 Benchmark 数字。

## 快速开始

CLI 需要 Python 3.12、[uv](https://docs.astral.sh/uv/) 和一个 Anthropic 或 Anthropic-compatible 模型接口。普通 CLI 运行不需要 Docker。

```powershell
git clone https://github.com/Titans23/forgecode.git
cd forgecode
uv sync
Copy-Item .env.example .env
```

编辑 `.env`，至少填写：

```env
ANTHROPIC_API_KEY=your-api-key
MODEL_ID=claude-sonnet-4-6
```

然后检查配置并启动：

```powershell
uv run forge config
uv run forge
```

`forge config` 只显示 `API key: configured`，不会回显密钥。使用兼容接口时，可以同时设置 `ANTHROPIC_BASE_URL`。

如果本机没有 `uv`，也可以在已安装依赖的 Python 环境中使用：

```powershell
python -m forge --help
python -m forge --version
```

## CLI

```text
forge                         启动交互会话
forge config                  检查模型配置
forge sessions                列出当前项目的持久化会话
forge integrations            查看办公 Channel 的连接准备状态（不显示凭据）
forge gateway                 启动办公聊天 Channel Gateway
forge --continue              恢复当前项目最近的会话
forge --resume SESSION        按 ID 或名称恢复会话
forge --fork-session          从恢复的会话创建新分支
forge --version               显示版本
```

交互会话中的常用控制命令：

```text
/context                      查看当前上下文统计
/compact                      手动压缩历史
/task                         查看当前任务
/status                       查看会话状态
/history                      查看会话历史摘要
/permission                   查看或切换权限模式
/skills、/skill NAME          查看 Skill
/mcp                          查看 MCP Server 状态
/undo、/rewind                回退代码或会话
/memory list                  查看长期记忆
```

三种权限模式：

```text
plan        只向模型暴露读取和分析工具
supervised  有副作用的操作需要交互审批
auto        低风险操作自动执行，高风险操作仍受规则和审批约束
```

## 办公平台接入

ForgeCode 的办公自动化分为两层：聊天 Channel 接收自然语言请求，MCP 工具调用办公平台 API 执行文档、消息等操作。首期已接入飞书中国版，聊天入口使用飞书官方 WebSocket Channel SDK，办公操作使用飞书 OpenAPI。

```text
飞书消息
  → Channel Gateway（消息接收、身份校验、回复和审批卡片）
  → ForgeCode Conversation / Session
  → MCP 办公工具
  → 飞书 OpenAPI
```

### 飞书配置

1. 在飞书开放平台创建并发布企业自建应用，启用机器人，并按需授予 IM 和 Docx 权限。
2. 在项目根目录的 `.env` 中配置凭据和 Channel 参数。Gateway 会自动加载项目 `.env`，已有进程环境变量优先：

   ```env
   FEISHU_APP_ID=cli_xxx
   FEISHU_APP_SECRET=secret
   FEISHU_CHANNEL_NAME=feishu-main
   FEISHU_ENABLED=true
   FEISHU_TRANSPORT=websocket
   FEISHU_TENANT_ID=company-main
   FEISHU_ALLOWED_USERS=ou_your_open_id
   FEISHU_ALLOWED_CHATS=
   FEISHU_REQUIRE_MENTION=true
   FEISHU_APPROVAL_TIMEOUT_SECONDS=600
   ```

   `FEISHU_ALLOWED_USERS` 和 `FEISHU_ALLOWED_CHATS` 使用逗号分隔，至少配置一个。只填 `FEISHU_ALLOWED_USERS` 可先测试私聊；群聊还需要填 `chat_id`，并在 `FEISHU_REQUIRE_MENTION=true` 时 @机器人。也可以不创建 `.forge/channels.json`，环境变量会生成默认的 `feishu-main` Channel；如果该文件存在，环境变量会覆盖上述飞书配置。不要把密钥写入 `channels.json`、`.mcp.json` 或命令参数。

3. 安装依赖、检查连接准备状态并启动 Gateway：

   ```powershell
   uv sync
   uv run forge feishu setup       # 首次运行：私聊机器人完成一次配对
   uv run forge integrations
   uv run forge gateway --channel feishu-main
   ```

   `forge feishu setup` 只在首次接入时运行：它只接收私聊，不开放群聊；终端会生成一次性验证码，你把终端提示的“绑定 验证码”发送给机器人后，程序会把发送者的 `open_id` 自动写入项目 `.env` 的 `FEISHU_ALLOWED_USERS`。之后直接启动 Gateway 即可。

### 飞书使用教程

#### 1. 检查配对和连接状态

配对成功后，不需要每次重复执行 `forge feishu setup`。可以先检查：

```powershell
uv run forge integrations
```

正常情况下会看到类似：

```text
feishu-main: ready · feishu · websocket · 1 user(s) · 0 chat(s)
```

首次配对时，成功标志是终端出现：

```text
Pairing event received: chat_type=p2p, sender_id=present, code_match=yes
Paired Feishu user: ou_...
Saved FEISHU_ALLOWED_USERS to .env.
```

如果提示 `Feishu is already paired`，说明 `.env` 中已经保存了允许使用的用户。

#### 2. 启动飞书机器人

保持下面的终端进程持续运行：

```powershell
uv run forge gateway --channel feishu-main
```

Windows 下启动成功后应看到 `[Gateway] Windows Ctrl+C direct handler active`，表示退出按键由 ForgeCode 直接接管，不依赖 `uv` 转发。Gateway 会在终端实时显示消息正文预览、话题对应的 Session、模型迭代、工具名称与参数、工具结果摘要、Token 用量、变更路径、回复预览和退出清理过程。`secret`、`token`、`api_key`、`password`、`authorization` 等参数字段会自动脱敏，长内容会截断。首次按 `Ctrl+C` 会执行有界清理；如果第三方 SDK 卡住，10 秒后会强制结束 Gateway 子进程，再按一次 `Ctrl+C` 可立即强制退出。

然后在飞书私聊“ForgeCode 办公助手”。飞书消息会被转发给 ForgeCode，ForgeCode 的回复会返回到原消息所在的会话。

每条飞书顶层消息都会创建一个独立的 ForgeCode 话题上下文；点击该消息下方的“回复话题”继续发送时会复用这个上下文，直接在底部输入框发送另一条顶层消息则会创建新上下文。不同话题、群聊话题和私聊话题互不串线，ForgeCode 的回复也会发送回原话题。

不需要使用特殊命令，直接用自然语言描述任务即可，例如：

```text
你好，请告诉我你可以做什么
```

#### 3. 操作飞书文档和消息

当前内置办公 MCP 支持以下操作：

- `feishu_document_read`：读取文档元数据、正文和稳定 block ID；
- `feishu_document_create`：新建飞书文档；
- `feishu_document_update`：按文档 revision 安全更新文本块；
- `feishu_message_send`：向一个或多个群聊发送相同文本。

可以在飞书中直接发送类似请求：

```text
读取飞书文档 abc123，帮我总结内容
```

```text
新建一个飞书文档，标题是《今日工作记录》，内容包括：
1. 完成飞书接入
2. 测试文档读取
```

```text
读取文档 abc123，找到“项目状态”这一段并改成“已完成”
```

```text
把“部署已经完成”发送到群 oc_xxx
```

其中 `abc123` 是飞书文档 URL 中的文档 token，`oc_xxx` 是群聊 ID。当前文档写入支持正文、标题、无序列表、有序列表、代码块、引用和待办；表格、图片和附件等未支持的块会保留原位。

#### 4. 写操作审批

读取操作可以自动执行。新建文档、修改文档和发送群消息属于高风险写操作，ForgeCode 会先在飞书当前会话发送一次性审批卡片。

只有原始请求者点击“确认执行”后，操作才会真正执行。审批只对本次完全一致的参数有效；参数变化、审批超时或文档 revision 变化后，需要重新确认。

#### 5. 使用群聊

在 `.env` 中配置允许的群聊 ID：

```env
FEISHU_ALLOWED_CHATS=oc_xxx,oc_yyy
FEISHU_REQUIRE_MENTION=true
```

重启 Gateway 后，在群里 @机器人：

```text
@ForgeCode 办公助手 读取这个文档并总结
```

只配置 `FEISHU_ALLOWED_USERS` 时，可以使用私聊；群聊还必须配置 `FEISHU_ALLOWED_CHATS`。启用 `FEISHU_REQUIRE_MENTION=true` 时，群消息必须 @机器人。

#### 6. 查看使用日志

将 Gateway 的终端输出保存为文件：

```powershell
uv run forge gateway --channel feishu-main *> feishu-gateway.log
```

另开终端实时查看：

```powershell
Get-Content .\feishu-gateway.log -Wait
```

ForgeCode 的会话、模型回复和工具调用记录默认保存在：

```text
%USERPROFILE%\.forge\projects\<项目标识>\sessions\
```

`channels\feishu-main\channel-sessions.json` 保存飞书聊天与 ForgeCode 会话的对应关系，`channel-events.log` 只保存消息去重信息，不包含完整消息内容。

当前内置办公 MCP 只支持文档和消息操作，不直接操作飞书日历、审批、多维表格等其他服务。凭据不会显示在 `forge integrations`、Session Journal 或审计参数中，但会话日志可能包含用户消息和模型回复，请妥善保护日志文件。

### 飞书官方 OpenAPI MCP

如需更广泛的飞书 OpenAPI，可在 `.mcp.json` 中额外配置官方 `@larksuiteoapi/lark-mcp` Server。应使用 `APP_ID`、`APP_SECRET` 和 `LARK_TOOLS` 环境变量，而不是在命令参数中放置凭据；同时应为每个启用的远程工具配置明确的 `toolPolicies`。未分类的 MCP 工具默认按高风险写操作处理。

当前不控制个人微信或个人 QQ 客户端，也不采用注入、模拟登录、非官方协议或桌面 RPA。后续企业微信和 QQ 适配器将复用同一套 Channel 接口、会话隔离、白名单、去重和审批规则，并且只使用平台官方开放能力。

## 工具调用蒸馏（可选扩展）

仓库还提供 [`extensions/qwen_tool_distillation.py`](extensions/qwen_tool_distillation.py)，用于把 ForgeCode 的真实模型请求、阶段工具表、工具调用结果、验证证据和 Completion Gate 结果记录为可审计轨迹，再导出 Qwen 工具调用训练数据。它是显式 opt-in 的单文件扩展：不会自动注册到 ForgeCode、修改全局工厂或读取普通 Anthropic 配置，核心 CLI 和基础安装不依赖 OpenAI、Transformers 或 ms-swift。

扩展的主要链路是：

```text
Conversation.stream()
  → RecordingModelClient 捕获已准备好的请求
  → TraceRecorder 写入 provider-neutral JSONL
  → clean_episode 校验来源、许可证、密钥、Schema、权限和独立验证
  → build_sft_rows 导出 next-action SFT 样本
  → 可选 DPO / GRPO rollout / ms-swift recipe
```

默认 endpoint 是 OpenAI-compatible 的 `Qwen/Qwen3.5-4B`；`main`、`router`、`summary`、`explore` 四类角色可以用 `QWEN_DISTILL_<ROLE>_*` 环境变量分别配置。记录格式版本为 `forgecode-qwen-distillation/v1`，SFT 数据集版本为 `forgecode-qwen-sft/v1`。训练数据只接受公开且许可证在允许集合内的来源，Aider Polyglot 固定作为 test holdout；未独立验证或绕过 Completion Gate 的 episode 会被拒绝。

扩展命令不改变核心运行时，可以先做环境检查，再生成训练 recipe 或导出 SFT JSONL：

```powershell
uv run python -m extensions.qwen_tool_distillation preflight
uv run python -m extensions.qwen_tool_distillation write-recipes distill/recipes
uv run python -m extensions.qwen_tool_distillation build-sft data/teacher.jsonl data/train.jsonl
```

推理需要额外安装 `openai`；使用精确 Qwen tokenizer 导出 SFT 需要 `transformers`；训练 recipe 面向可选的 `ms-swift` 环境。仓库当前提供的是采集、校验、转换和训练资产生成能力，不把某次蒸馏训练结果冒充为 ForgeCode 的默认模型或版本基线。

## 当前能力

- 多步 Agent Loop：模型可以在同一用户回合中连续读取、修改、执行和验证。
- 统一工具协议：文件、搜索、Patch、Shell、Git、Verify 和 `finish_task` 都经过 `ToolRegistry`。
- 可靠修改：路径边界、原子写入、精确替换、Patch 预检和任务作用域检查。
- 验证闭环：测试、构建、类型检查、Lint 和 `git diff --check` 可以形成带 workspace revision 的证据。
- 完成门禁：没有真实 Diff、验证过期、越界修改或未解决失败时，普通文本不能伪装成任务完成。
- 失败恢复：区分模型协议、编辑、验证、依赖、停滞和最终化恢复，而不是对所有错误统一重试。
- 上下文工程：WorkingState、项目规则、历史压缩、长期 Markdown 记忆和只读 Explore Agent。
- 会话恢复：append-only Session Journal、文件 Checkpoint、`/undo`、`/rewind` 和会话分支。
- 权限与扩展：用户/项目/会话规则、Hooks、MCP、Skill 和审计轨迹。
- 办公 Channel：飞书 WebSocket 消息接入、白名单、事件去重、按聊天和话题隔离会话、聊天审批。
- 工具调用蒸馏：Qwen-compatible adapter、provider-neutral 轨迹、独立数据清洗和 SFT/DPO/GRPO 资产接口。

## 架构导航

```text
forge/cli.py
  → forge/terminal.py
  → forge/runtime/agent_loop.py
       ├─ model_client.py       Provider 边界和流式协议
       ├─ router.py             语义意图路由
       ├─ workspace.py          工作区快照和 revision
       └─ completion.py         完成门禁

forge/tools/                   内置工具
forge/context/                 上下文、WorkingState、记忆
forge/tasks/                   ActiveTask 和计划
forge/permissions/             风险、规则和审批
forge/hooks/                   生命周期 Hook
forge/sessions/                Journal、Checkpoint、Trajectory
forge/mcp/                     MCP Client
forge/channels/                办公聊天 Channel 和 Gateway
forge/office/                  飞书办公 OpenAPI 与内置 MCP Server
forge/skills/                  Skill 发现和加载
forge/subagents/               只读 Explore Agent
extensions/qwen_tool_distillation.py  可选 Qwen 工具调用蒸馏与 rollout 扩展

evals/                         本地独立评测
benchmark/                     Harbor / Aider Polyglot 适配
tests/                         自动化回归测试
```

CLI 入口由 [`pyproject.toml`](pyproject.toml:22) 注册：

```toml
[project.scripts]
forge = 'forge.cli:app'
```

因此 `uv run forge` 最终会调用 [`forge/cli.py`](forge/cli.py) 中的 Typer `app`。`python -m forge` 通过 [`forge/__main__.py`](forge/__main__.py) 进入同一个对象。

运行时的核心对象是 `Conversation`，而不是简单的消息列表。它协调模型客户端、工具注册表、任务、上下文、权限、工作区、Completion Gate、Session Journal 和 Checkpoint。

## 模型配置

配置从当前目录的 `.env` 和环境变量读取，环境变量优先：

```env
ANTHROPIC_API_KEY=your-api-key
MODEL_ID=claude-sonnet-4-6
MODEL_MAX_TOKENS=8192
MODEL_CONTEXT_WINDOW=128000
MODEL_REQUEST_TIMEOUT_SECONDS=120
ANTHROPIC_BASE_URL=https://api.anthropic.com
```

配置读取和校验位于 [`forge/config.py`](forge/config.py)。当前校验包括：

- API Key 和 Model ID 不能为空；
- `MODEL_MAX_TOKENS` 必须为 1024～32768；
- `MODEL_CONTEXT_WINDOW` 必须大于输出 Token 上限；
- 请求超时必须为 10～600 秒；
- Base URL 必须是绝对的 HTTP 或 HTTPS 地址。

Anthropic SDK 被限制在 [`forge/runtime/model_client.py`](forge/runtime/model_client.py)；其余运行时代码依赖 Provider 无关的 `ModelClient` 和流式事件。

## 本地开发

项目使用 uv 管理虚拟环境、依赖和锁文件：

```powershell
uv sync
uv lock --check
uv run --frozen python -m compileall -q forge tests
uv run --frozen pytest -q
git diff --check
```

`pytest` 的开发依赖在 `pyproject.toml` 的 `dev` 组中。完整测试还会收集 Harbor 适配测试，因此应通过 `uv sync` 安装开发依赖；只验证 CLI、配置和终端时，可以运行：

```powershell
uv run --frozen pytest -q tests/test_config.py tests/test_cli.py tests/test_terminal.py
```

## 本地评测

`evals/` 在 Agent 外部准备临时 Git 工作区并独立验收结果。每个 YAML 用例声明：

- 固定基础 Commit；
- 任务描述；
- setup、build、公开测试和隐藏测试命令；
- 允许和禁止修改的路径；
- 超时和成功条件。

查看用例：

```powershell
uv run python -m evals.runner --list
```

运行一个用例：

```powershell
uv run python -m evals.runner --case python-calculator-001
```

评测器会从固定 Commit 解包 Fixture、移除隐藏测试、启动 Agent，然后在 Agent 外部重新执行构建、公开测试、隐藏测试和 Diff 检查。Agent 自己返回“完成”不构成验收依据。

当前 YAML 用例位于 [`evals/cases/`](evals/cases/)。对应的 `fixtures/` 是评测资产，目录被 `.gitignore` 忽略；如果本地没有这些 Fixture，`--list` 可以工作，但实际 `--case` 运行无法开始。

## Harbor Benchmark

Harbor 是可选的外部评测后端，不是普通 CLI 的运行依赖。运行前需要额外准备 Docker、Harbor 和 Aider Polyglot 数据集。

完整命令和结果解释见 [`benchmark/harbor/README.md`](benchmark/harbor/README.md)。版本化的评测数字和运行边界见 [`report.md`](report.md) 与 [`EVALUATION_SUMMARY_20260813.md`](EVALUATION_SUMMARY_20260813.md)；README 不重复维护容易过期的分数、Token 和测试数量。

## 安全边界和已知限制

- 仓库路径、敏感文件、删除操作和命令风险由应用层规则控制。
- 当前默认面向可信本机仓库和受监督的高风险操作。
- ForgeCode 目前不是操作系统级沙箱；CPU、内存、进程数以及原生文件系统/网络的强隔离仍依赖后续 Sandbox Backend。
- ForgeCode 不内置浏览器、截图或视觉理解工具，网页和游戏项目主要通过源码、命令、构建、测试和 HTTP 资源加载进行验证。
- 大型单响应 Patch 必须完整生成后才执行，超大文件可能增加第一次工具调用延迟。
- 依赖缺失、长任务阶段切换和复杂恢复仍是持续改进方向。

## 进一步阅读

- [`study.md`](study.md)：按运行链路组织的源码学习路线；
- [`report.md`](report.md)：当前源码、测试、评测和设计取舍的详细审阅；
- [`forge/cli.py`](forge/cli.py)：CLI 生命周期和运行时装配；
- [`forge/runtime/agent_loop.py`](forge/runtime/agent_loop.py)：主 Agent Loop；
- [`forge/tools/base.py`](forge/tools/base.py)：统一工具协议和注册表；
- [`forge/runtime/workspace.py`](forge/runtime/workspace.py)：工作区 revision；
- [`forge/runtime/completion.py`](forge/runtime/completion.py)：Completion Gate；
- [`forge/channels/gateway.py`](forge/channels/gateway.py)：办公消息路由、去重、聊天会话和审批；
- [`forge/office/mcp_server.py`](forge/office/mcp_server.py)：窄范围飞书办公 MCP sidecar；
- [`extensions/qwen_tool_distillation.py`](extensions/qwen_tool_distillation.py)：工具调用轨迹记录与数据导出；
- [`evals/runner.py`](evals/runner.py)：本地独立评测 Runner。
