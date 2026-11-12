# ForgeCode 终端版本

## Agent 组合与可信 Python 插件

独立文件读取、搜索、图片与 Git 查询支持批内并行。Agent JSON 的 `max_parallel_tools` 默认为 10，设为 1 可顺序执行；这是同时运行数量，不是累计调用预算。终端显示各调用的排队、运行、待归档和完成状态，Ctrl+O 展开详情。插件通过 `ctx.register_tool(..., concurrency_safe=True)` 或参数分类函数显式加入并行组；默认独占，权限要求不变。

长命令可用 `run_command(..., run_in_background=true, timeout_seconds=3600)` 启动作业，收到 `job_id` 表示已受理。用 `job_output(job_id, after=上次的 next_cursor, wait_seconds=30)` 获取新输出并等待；退出码成功后才执行依赖步骤。`job_kill` 取消作业。单次超时仍有效，等待输出超时不会终止命令。

终端空闲时输入 `/jobs` 查看本会话作业，`/jobs <id>` 查看输出，`/jobs stop <id>` 停止；这些本地命令不调用模型。退出会话会清理所属作业，恢复历史不会重新启动旧命令。有作业运行时，先等待或停止它，再切换权限/会话历史或进行最终验证。不要让后台命令同时修改同一文件或同一个包管理器状态。

定制、验收和复用入口（`check` 不调用模型）：

```sh
forge plugin init ./my-plugin --name my-plugin
forge plugin check ./my-plugin --cases ./acceptance.json --trust --output ./validation.json
forge plugin develop "实现需求" --directory ./my-plugin --cases ./acceptance.json
# 检查计划后，显式 --execute 才使用模型；失败在原回合内修复，最多两次。
forge plugin develop "实现需求" --directory ./my-plugin --cases ./acceptance.json --execute --max-model-calls 40
forge agent check ./agent.json --cases ./acceptance.json --trust --output ./validation.json
uv build --wheel --out-dir .local/wheels
forge agent export ./agent.json ./delivery.zip --cases ./acceptance.json --receipt ./validation.json --sdk-wheel .local/wheels/forge_code-0.1.2-py3-none-any.whl
forge agent import ./delivery.zip ./new-agent
```

`acceptance.json` 示例：`{"cases":[{"id":"greet","tool":"greet","arguments":{"name":"Ada"},"expect":{"content":"Hello, Ada!"}}]}`。
首版自然语言开发需要调用方提供这份固定验收输入；实现与 `agent.json` 配置可由 Harness 生成/修复，候选代码不能更改控制器中的预期值。
预期支持 `content`、解析后的 `json`、错误码 `error` 和取消 `cancelled`；取消用例另设 `cancel_after_ms`。
`files` 可提供显式文本 fixture，参数中的 `${WORKSPACE}` 指向临时测试目录。
插件 manifest 的 `files` 列出交付源码、测试和元数据；自测使用标准 unittest，未提供或未发现测试时如实标记 unverified。
接口、业务与模型结论分层保存，不是插件安全认证。需要安装 `[validation]`；`[terminal]` 已包含。

在导入目录用 `uv pip install -r requirements.lock` 安装标准依赖（相对 wheel 路径以该目录为准），
配置 `models.json` 引用的环境变量，再运行同一 `agent check`。导入本身不会启用插件或沿用原环境的通过结论。
SDK wheel 必须匹配实际检查的源码；跨平台依赖可能需要重新解析并重新验收，不能把缺失依赖算作通过。
示例位于 `examples/agents/{notes,expenses,coordinator}`：无仓库笔记、CSV 费用规则（XLSX 按需安装 openpyxl）、受管事件子任务。
确定性 check 使用脚本化模型；coordinator 的真实角色模型另行通过 eval 验证。

用户 settings.json 的 `models` 保存模型别名，密钥只写环境变量引用：

```json
{"models": {"fast": {"provider": "openai_chat", "model": "your-model-id", "api_key_env": "COMPANY_MODEL_KEY", "base_url": "https://gateway.example/v1", "input_modalities": ["text"]}}, "agents": {"assistant": {"base": "core", "role_models": {"main": "fast", "summary": "fast", "worker": "fast"}}}}
```

协议支持 `anthropic`、`openai_responses`、`openai_chat`、`deepseek`、`gemini`；按需安装对应
`[anthropic]`、`[openai]`、`[gemini]`。`default` 保留给既有配置。`forge config models` 或 `/models`
列出别名；空闲回合使用 `/model fast`，SDK 使用 `await session.set_model('fast')`。
切换不调用模型；原始 Journal 保留，历史工具交互转换为可见文本并保留图片，私有续传状态不跨模型复用。
含图片的历史要求目标别名显式声明 `input_modalities: ["text", "image"]`；窗口或压缩预留不足时拒绝切换。
小窗口可在 Agent 的 `context_options` 显式配置 `reserve_tokens` 和 `keep_recent_tokens`。
Chat Completions 网关可设置 `chat_token_field: "max_tokens"`，默认使用 `max_completion_tokens`。
未声明凭据引用时，同提供方别名继承当前默认连接；跨提供方使用各自环境变量。端点禁止嵌入凭据和查询参数。

插件可通过 `ctx.register_provider(name, factory)` 注册提供方；factory 接收 ForgeConfig 和
`max_tokens`/`max_retries`，返回现有 ModelClient。使用 `ctx.run_child(prompt, definition, role='worker')`
创建前台受管子任务；权限不能扩大，模型/工具用量汇总到父回合。插件自行绕过核心发出的请求不在此保证中。
协议夹具通过不等于真实云模型实测；上述接入不需要执行付费测试才能检查安装和配置。

`forge plugin init <新目录> --name hello` 创建本地插件；`forge plugin enable <目录> --trust`
显式信任宿主 Python 代码。`list` 查看、`disable <id>` 停用，新配置只影响新会话。
安装型插件使用 Python entry point 组 `forge.plugins.v1`，源写为 `entrypoint:<name>`；依赖由 pip/uv 管理。

在用户 `.forge/settings.json`（或 `FORGE_DATA_DIR/settings.json`）中加入组合，例如：

```json
{"agents": {"notes": {"base": "core", "system_prompt": "Help manage notes.", "plugins": ["hello"]}}}
```

保留原有其他配置字段；使用 `forge --agent notes` 启动，默认 `forge` 仍使用 coding。
SDK 使用 `AgentDefinition` 和 `PluginSpec(source=..., trusted=True)` 显式选择插件。
core 无需代码工作区；coding 使用原来的文件工具及权限。

执行提供方按 Agent 选择：`sandbox_provider` 默认随 base 解析为 coding 的 `"native"` 或 core 的 `null`。
普通 coding 默认 `danger-full-access`；配置 `{"base":"coding","sandbox_provider":null}` 则完全不使用沙盒提供方。显式自定义提供方或写入范围保持受限模式，也可用 `--sandbox` 明确选择。
显式 `read-only`/`workspace-write` 必须有提供方，初始化失败不回退宿主。审批与用量统计独立于提供方，受管 coding 子任务不能更换父提供方。

可信插件可在 activate 中用 `ctx.register_sandbox_provider(name, factory)` 注册提供方，然后在 Agent 配置中填写该名称。
`factory` 是异步函数，接收 `forge.execution.ExecutionRequest`（工作区、数据目录、模式、读写范围、时限，不含模型凭据），返回 `ExecutionSetup(backend, policy)`。
backend 使用现有工具执行、检查与关闭接口；policy 使用现有 sandbox-policy 契约。可复用 `forge.sandbox.provider.open_native_execution(request)`。
提供方负责创建失败时的部分资源清理；成功返回后由会话关闭 backend，再释放插件资源。此注册不证明第三方提供方具备 OS 隔离能力。

插件目录中的 `forge-plugin.json` 声明 `id`、`api_version: 1`、`entry_point: "plugin.py:activate"`
及 `requires` 插件 ID。`activate(ctx, config)` 可同步或异步，返回资源释放函数或 None。
`ctx.register_tool(name, description, object_json_schema, callback, effect="read_only")` 注册工具；
callback 接收参数 dict，返回字符串、JSON 值或 `ToolResult`。非只读操作必须声明 effect。
`register_service`/`service` 共享显式服务；`effect(dispose)` 登记清理；`on(event, handler)`
监听现有 HookEvent，返回 `HookOutcome` 或 None。可变会话状态放在 activate 的闭包中，不放在模块全局。

加载前检查依赖，加载失败撤销注册，关闭时逆序释放。插件代码是可信宿主代码；即使文件工具使用
workspace-write，也不能把插件本身描述为受 OS 沙盒隔离。模型可见的插件执行结果标记
`trusted-plugin-host` 和 `sandbox_covered: false`。这些能力不改变现有原生隔离范围。

当前 main 提供持续对话的 Python Harness 和真实轻量沙盒。终端、评测入口通过 Profile 选择；Python SDK 直接调用同一核心。源码安装会自动构建并准备运行时，安装程序不会请求管理员权限或自动修改系统策略。

## 拉取源码并自动安装

准备 Git 和 Node.js 22.13+（含 npm），在独立安装目录执行：

```sh
git clone --branch main https://github.com/Titans23/forgecode.git
cd forgecode
npm run setup
```

命令自动准备 uv、Python，按锁文件安装 Node 构建依赖、编译沙盒、构建并校验 wheel/npm 包，再全局安装 `forge`。构建日志保存在 `dist/package-build.log`。包安装器继续自动校验并准备运行时工具，直接复用已有 Node。用户配置保存在安装包之外，升级不会清空旧包正在使用的 Python 环境。Windows x64/NTFS 原生沙盒不需要 WSL 或 Docker。

历史安装与原生验收覆盖 Windows 10 和 WSL2 Ubuntu 26.04（ext4 工作区）；当前 DSH 会话适配的验证范围见 progress.json。Linux 由 DSH 选择 bubblewrap/user namespace 或 Landlock。安装器不会执行 sudo、关闭系统保护策略或在沙盒失败后自动切换为 full-access；macOS 控制进程所有权层尚未接入。

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

wheel 仍使用本机 Node、Git、ripgrep 和平台沙盒前置条件。全局用户工具环境和沙盒项目分开；在项目内部的 `.venv` 安装 ForgeCode 时，安装目录保护可能拒绝该项目的原生命令。

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

默认上下文为 256K（262,144 tokens），单请求输出上限为 64K（65,536 tokens），分别由 `MODEL_CONTEXT_WINDOW`、`MODEL_MAX_TOKENS` 覆盖；已有显式配置仍优先。这里配置的是单请求模型容量，所用服务需支持相应容量。沿用 Pi 的预算方式，总预留设为 48K（49,152 tokens），输入超过 212,992 tokens 时触发压缩，保留约 20,000 tokens 的近期原文。64K 是输出上限，实际请求额度随剩余窗口缩小，并留出窗口 1% 的估算余量；大工具输出超过 8,192 字符时保留头 4,096、尾 1,024 字符，完整原文仍可通过受限 artifact 接口读取。压缩规则见 [架构说明](../implementation/architecture.md)。

## 持续对话

```text
forge --profile terminal
forge --continue
forge --resume SESSION_ID
forge --continue --fork-session
```

会话中使用 `/context`、`/compact`、`/task`、`/history`、`/rename NAME`、`/resume`；Ctrl+C 停止并清理进程。既有 Journal 保存已提交上下文和执行事实；中断且没有完成记录的工具不会自动重放。

输入后立即出现动态状态，分别提示准备请求、等待模型、思考、回答和工具执行。状态区显示当前请求耗时、本轮耗时和实际已接收 KB；超过五秒没有新的可见内容时显示等待时长。工具参数仍在生成时会标注“未执行”。思考只显示模型接口实际提供的可读内容或摘要，不把不透明签名当作思考文本。等待动画不代表额外的模型调用，也不保证模型持续产出。

Anthropic 兼容接口的完整思考块及签名会随工具结果续传，并在 `/resume` 后保留。需要退出旧进程、重新运行全局 `forge` 才能使用更新；旧版本已经丢失的历史思考无法自动恢复。此修复不会降低配置中的思考强度或输出额度。

高频文本与参数日志会合并落盘，终端仍逐段显示。模型工具参数格式错误会在没有执行该响应任何工具的前提下，请求模型显式纠正，至多两次；不会猜测参数或重放已执行操作。普通工具失败不再取消同批后续调用，各调用仍单独校验与授权；拒绝授权、取消和清理不确定保持停止边界。

思考和工具默认折叠。运行中按 `Ctrl+O` 或输入 `/details` 展开/收起当前预览；长内容只显示屏幕能容纳的尾部。回复结束后按 `Ctrl+O` 或 `/details` 打开最近一轮的完整详情：方向键、PgUp/PgDn 滚动，Ctrl+F 搜索，Esc 或 Ctrl+O 返回。正文按 Markdown 流式显示，最终用一行汇总用时、输入/输出 token 和请求次数；详细用量放在详情中。详情属于当前进程的最近一轮，切换或清空会话会清除旧详情。输入草稿在回复完成和审批暂停后保留。

交互终端执行中可以直接输入调整意见，在下次模型请求前应用；`/follow 任务` 排队到当前回合之后，`/cancel` 停止当前回合并保留会话。审批提示会暂时独占输入。取消后用 `/pending` 查看待处理输入、`/next` 继续一项、`/discard 输入ID` 删除一项。待处理输入随 Journal 保存；文本重定向等非交互输入保持顺序处理。

`@path` 可附加 UTF-8 文本或 PNG/JPEG/GIF/WebP。文字合计不超过 256 KiB，最多 8 张图片，单张不超过 512 KiB。较大图片先裁剪或缩小。发送图像前请选择支持视觉的具体模型；适配器支持图像传输不代表服务端任意模型都支持视觉。

项目根目录的 `AGENTS.md`、`FORGE.md` 和 `.forge/rules/*.md` 会在每轮开始时加载。工具访问子目录时，会自动加载该目录及祖先目录的 `AGENTS.md`、`FORGE.md`，供下一次模型请求使用；新规则尚未送达模型时，相关写入或进程工具会先返回提示。也可用 `@src/AGENTS.md` 显式附加规则。

新交互会话在首次提交对话时才保存；直接退出或仅查看历史不会生成空会话。
`/resume` 按更新时间倒序显示已保存会话，最近的在前；当前会话保留标记但不强行置顶。新建但尚未提交的会话显示为“新会话”，不会因此写入空历史。选择当前会话不会重新加载或重放工具。
权限选择器不显示编号，方向键选择、Enter 确认，Y 同意一次，N/Esc/Ctrl+C 拒绝；完整操作参数仍在确认前展示。
普通 coding 直接调用主模型，不做前置意图分类、全盘基线扫描或自动检查点；写入和命令执行也不会触发全盘扫描。文件工具只观察实际目标及其版本。普通回复不经过强制完成验收；显式 TaskPolicy、strict Agent 和插件完成检查继续执行调用方要求。没有独立验收时，completed 表示回合完成，不代表已经通过测试或调用方验收。

文件写入、替换和补丁没有原来的 30,000 字符上限；不会强制分块或解析 JSON 内容。已有文件需要先读取，版本变化时必须重新读取；新文件不会覆盖并发创建的内容。普通目标观察只传指纹，不受检查点 512 KiB 内容配额限制。显式检查点、工具传输和资源预算仍有各自的上限。

任务和工具证据等动态上下文放在请求末尾，系统提示和较早历史保持稳定，有利于提供方复用前缀缓存。缓存命中不减少模型生成代码所需的输出 token，也不能保证所有请求命中；工具定义变化、长历史压缩及提供方缓存状态仍会影响结果。
恢复会话默认显示最近 10 轮，`/history all` 显示完整可用对话；工具历史以摘要展示，
不会重新执行。`/clear` 保留旧会话，新会话等待下一次对话再保存。

## 权限

```text
forge --sandbox read-only
forge --sandbox workspace-write --write-root ./src --write-root ./tests
forge --sandbox workspace-write --read-root ../reference
forge --sandbox danger-full-access --approval supervised
forge --sandbox workspace-write --approval plan
```

默认 `danger-full-access`（兼容原名 `full-access`）使用宿主执行，文件和命令不受工作区写入围栏限制，审批默认 `never`。显式 `workspace-write` 允许项目和平台临时目录写入；指定 `--write-root` 后使用列出的既有子目录。`read-only` 拒绝文件修改，命令仍可在原生只读边界中运行。受限模式默认 `ask`，扩大权限及显式规则才请求审批；`never` 拒绝需要审批的请求。`supervised/auto/plan` 保留旧调用兼容，它们不是 DSH 的 Auto review 插件。

内置提供方采用 DSH 的文件/进程分离机制：启动和普通文件操作使用可信 helper 的路径策略，不申请工作区 ACL；命令、verify 或 Git 工具交给发布的 DSH sandbox-local/subprocess-local。普通执行不再先跑 Forge 写入探针。实际 confinement 失败作为该调用的错误返回，会话和文件操作仍可继续，命令不自动回退宿主。文件策略不是 OS 隔离，结果标记为 `*-file-policy`；评测在模型请求前显式预检进程沙盒。

命令、verify、Git 查询和文件修改工具共享 DSH 式单次授权：在真实沙盒拒绝或初始化失败后，模型可为同一操作设置 `sandbox_permissions: "danger-full-access"` 和非空 `justification`。审批展示完整参数与理由，明确本次不受工作区沙盒限制；只有“仅允许这一次”或拒绝，不保存为会话/项目规则。文件修改的快照与执行使用同一个临时后端；授权不会进入下一次调用或已保存的 verify 定义。`read-only` 也可请求单次 `workspace-write`。显式禁止规则、任务约束和外部工具自带策略继续生效；普通内置工具不再按凭据文件名或命令关键字自动拒绝。无审批通道或选择 `never` 时拒绝扩大权限。结果记录实际 `execution_mode`、`sandbox_override: "allow_once"` 及清理状态；宿主执行不标为 OS 隔离。

日常权限只需 `/permission`：交互终端显示当前模式与选择器，非交互输出列出可用命令。也可直接输入预设，或在 SDK 使用 `await session.set_permissions(name)`。

| 预设 | 含义 |
|---|---|
| `workspace-write`（默认） | 工作区内操作，扩大权限时逐次询问 |
| `read-only` | 默认不修改，需要更宽权限时逐次询问 |
| `danger-full-access` | 本会话使用宿主权限，不询问扩大权限 |

切换仅作用于当前空闲会话，不调用模型。沙盒与审批策略一起保存，重复选择当前预设不产生变更；恢复沿用保存值，分支继承，清空回到启动默认值。单次审批只有“允许这一次 / 拒绝”，不生成会话或项目记住规则；已有显式规则仍然有效。配置过的 `--write-root` 在只读期间不授予写入，切回工作区模式后仍保留原范围。

高级接口 `set_sandbox()`、`/sandbox`、`/approval` 及旧审批名称保留兼容，不再出现在常用菜单。分别修改底层值产生的非预设组合显示为“自定义”，不会自动扩大权限。旧 Journal 继续读取；新组合用一条 `permissions_changed` 保存。准备、清理或保存失败时不发布新权限。

交互终端在生成过程中实时显示回复预览，可继续输入调整或 `/cancel`；完成后只保留一份 Markdown 回复及用量。重定向输出和 `TERM=dumb` 直接逐段输出 Markdown 原文，不重绘历史。默认简单对话只需一次主模型请求；等待阶段不会循环打印提示。
工具参数仍在生成时，状态行显示已接收 KB 和“未执行”；这不是文件已写入的大小。只有完整参数到达并进入执行阶段，才显示“正在运行”。
普通任务的变更列表仅含已观察的工具目标，不宣称枚举了命令产生的全部文件；未运行测试仍是 unverified。工具失败会返回模型并记录事实，但不会自动把最终回复降为 partial。显式验收策略及完成检查仍可阻止完成。

默认提供方不因无关子目录含符号链接、Junction 或硬链接而拒绝整个工作区。文件操作先解析实际目标：读取继承宿主可读范围，`workspace-write` 只允许实际目标位于已授权写入范围，`read-only` 拒绝修改。链接指向外部共享依赖时，可以读取，越界修改需要单次授权。`--read-root` 保留兼容，不作为默认读取白名单；内置提供方不再叠加按文件名或安装路径生成的文件工具黑名单。调用方明确配置的禁止规则仍然有效。

显式全盘观察记录链接本身及其目标标识，不递归扫描外部链接目录；普通文件工具按实际目标记录证据。严格验收中的超时、配额耗尽和观察失败仍是不完整证据。显式启用的检查点可以记录完全访问模式下的外部文件，但不会开放受限检查点的宿主直接恢复。旧的无显式 execution_mode 策略仍保留原有链接检查。

在 `full-access` 下启用 Hooks 时，Hook 结束或取消会清理它启动的后台子进程。命令应使用明确的解释器和参数：PATH、普通环境变量及 `FORGE_HOOK_*` 保留，凭据与 `PYTHONPATH`、`NODE_OPTIONS` 等加载器变量会被清除。stdout 与 stderr 合计超过 1 MB 或无法确认清理完成时，Hook 报错；阻断类 Hook 会拒绝后续操作。

Windows workspace-write 的私有临时目录由 DSH 按会话持有；read-only 不额外授予临时写权限。进程范围退出后才撤销临时授权。工作区 standing ACL grant 遵循 DSH 的保留语义，运行原生命令仍可能写入项目 ACL 和低完整性标签。

命令使用原解释器；Forge 不再创建私有 venv、修改 Python mkdir 或注入 pytest 缓存选项。[CPython Windows 受限令牌兼容问题](https://github.com/python/cpython/issues/134587)仍可能影响 0700 目录和默认缓存，需要命令本身选择适当配置。

文件工具按所选模式校验实际目标，明确配置的拒绝规则继续生效；不再生成凭据文件名或安装路径黑名单。OS 沙盒主要限制写入，读取及网络继承宿主；Windows ambient ACL、硬链接和 Node 管道子进程等限制仍存在，能力保守记录为 partial。未运行显式探针不标记 OS 隔离已验证。强隔离评测要求不能由此替代。

`forge sandbox --json` 是只读环境诊断，可报告未验证/blocked；不会触发项目 ACL 探针或自动修复。

若运行命令时报 `SetNamedSecurityInfoW failed (Win32 5): grantWrite(...)`，是 Windows 拒绝 DSH 设置目录 ACL 与低完整性标签。目录可编辑不代表能设置进程沙盒：DSH 还要求目录归当前用户所有且授予 `WRITE_OWNER`；仅有 Modify 或所有者隐含的 `WRITE_DAC` 不足。错误会保留后端原因和诊断目录，详细记录为 `boundary-failure.json`。默认 CLI 的启动和文件操作无需这项授权；原生命令仍需满足权限条件。不修改目录权限时，可以批准上面的单次 `danger-full-access` 请求继续执行该命令；也可显式选择 `forge --sandbox full-access --approval supervised` 让整个会话使用宿主执行。两者都不代表修复了原生 ACL 隔离，也不会自动切换。
确认 `command_started=false` 的沙盒准备失败会标记为 rejected，不推进命令环境版本或重扫工作区。错误向模型明确提供单次授权的参数与拒绝后的停止要求；更换命令本身不能解决目录授权条件，不能把未启动记为已执行。

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

`RunProfile.intent_routing` 默认 False；旧调用方可显式设 True 使用兼容路由，它会增加模型请求。工具批次按顺序执行，`max_parallel_reads` 参数已移除。`session.conversation.capabilities` 区分适配器图片传输能力与未知的具体模型模态。运行完成、验证结果与调用方验收是独立字段，未配置验收不会显示为已验收。

Harness 不再接受 `max_model_calls`、`max_tool_calls`、`max_iterations`、`max_turn_input_tokens`、`max_turn_seconds` 或 Profile 的 `wall_seconds`。回合持续到完成、明确失败或取消，模型/工具次数、token 与耗时仍记录。`run_command` 和 `verify` 的 `timeout_seconds` 默认 120 秒，可显式设为 7200 等更长的正有限值，不再截断到 600 秒或回合预算；Ctrl+C 仍取消并清理所属进程。工具传输容量和模型上下文容量保持独立。

模型网络故障默认最多重试两次；有效 `Retry-After` 优先，超过 60 秒停止自动重试，不提前请求。收到正文、思考或工具调用后不透明重试。等待期间可以取消。旧累计预算配置必须删除，不能靠保留旧参数限制新版本；历史有预算的实验应使用当时冻结的源码重放。

## 5 分钟 Harness 演示

先完成源码开发安装。在安装仓库运行下面的公开示例；它会创建与安装目录分开的新临时项目，无需用户模型配置，也不调用提供方：

```text
uv run --no-sync python examples/harness_demo.py --output .local/implementation/api-demo-001
```

重复演示时换一个新的输出目录。示例使用 `HarnessSession.stream`、真实原生文件/进程工具和同一个 TaskPolicy：修改 `value.txt`，运行行为检查，再执行命令使验证过期；第一次 `finish_task` 被拒后，在同一回合内重新验证并完成，共 6 次脚本请求。`max_delivery_repairs=2` 只启用已有回合内反馈，不放宽新鲜度或断言。

检查输出的两个文件：

- `events.jsonl`：版本化事件，包括 WorkspaceChanged、两次 VerificationCompleted、工具结果和最后的 TurnCompleted。
- `summary.json`：`status=completed`、`shared_model_calls=scripted_requests=6`、`original_request_limit=8`、`provider_api_calls=0`、`client_closed=true`、原生 `cleanup.state=clean` 和 `fixture_retained=false`。脚本 usage 是协议夹具值，不能当真实 token 成本。

示例还用 `SessionStore.load` 只读重建会话，确认前后 Journal SHA-256 相同、不重放工具、不新开回合。原生清理未确认时保留新建夹具并写失败 summary；清理失败以非零状态退出。当前 Windows 原生实测约 75 秒；Linux 同一入口仍依赖 bubblewrap，不能由 Windows 结果替代验收。

已有真实任务结果时，还能通过现有评测 CLI 在新副本独立核验保存的候选，不重新执行 Agent：

```text
uv run --no-sync --with pyarrow==19.0.1 python -m evals.native_swe_campaign --replay-outcome <保存的结果.json> --output <新的判分目录>
```

该命令需要对应的冻结任务缓存，输出 `replay.json` 和原生判分/清理证据；它不加载模型客户端，原尝试的 partial/failed、历史未知清理及完成率保持原样。只读状态重建、候选重新判分和同回合修复是三项不同能力。原 Agent 进行中回合的跨进程续跑尚未实现；`forge --resume` 开启新回合。

展示贡献时，底层 Windows 写限制归于 DSH，Linux 归于 bubblewrap；ForgeCode 复用已有 Python Agent 循环，工作集中在共享运行时接入、权限传递和用量统计、验证门禁、Journal、评测与生命周期证据。脚本模型演示和 `.env` 真实任务结果分别展示，不据此宣称自研 OS 隔离或稳定模型收益。

## 评测与后训练

`evals.runner` 保留 `EvalCase/EvalOutcome`、可调用的离线用例接口和可复现的仓库版本/命令证据；`benchmark.cli`、`benchmark.harbor.run_forge.run_turn`、RunSpec/ResultBundle 保留任务冻结与独立 verifier 判分，累计预算字段已移除。终端初始化不加载 Harbor，正式评测需要兼容的外部环境和单独的模型预算授权。

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
