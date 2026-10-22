# ForgeCode 当前架构

## 0. 产品范围

持续对话终端与 Python Harness API。terminal/evaluation 启动 Profile 选择入口；`bundles` 是入口描述，不是插件依赖注入系统。SDK 直接调用 API。原生权限由 SessionOptions 单独选择，预算配置不提升权限。

## 1. 目录与调用链

| 目录 | 当前职责 |
|---|---|
| forge/cli.py、launch.py、api.py | 终端、入口组合和程序调用 |
| forge/protocol.py、tool_types.py、messages.py | 无产品层依赖的协议、结果和文字/图片输入契约 |
| forge/runtime、context、tools、sessions | Harness、上下文、工具和Journal事实 |
| forge/sandbox、permissions、sandbox_bridge | 授权及Windows DSH/Linux bubblewrap执行 |
| forge/application、engine | CLI、RPC和评测实际使用的服务与状态投影 |
| benchmark、evals、extensions | 评测适配、任务和后训练导出 |
| contracts、packages/contracts | 维护源与TS契约校验器 |
| scripts、tests、docs | 构建、真实验证和当前说明 |

forge.launch选择forge.cli或benchmark.cli。CLI和forge.api共用open_runtime，调用runtime.factory.create_runtime → Conversation.stream → TurnRunner → ToolExecutor → 原生工具。Journal记录会话副作用；Engine负责受理、调度与投影，不复制Journal事实。

`create_runtime` 是唯一运行时装配函数，新建与恢复使用同一构造路径；项目模型配置读取传入的 root，不隐式读取其他工作目录。可选渠道/MCP 组合位于 `forge/integrations.py`。旧 `forge.application.models` 保留协议导入兼容。

| 状态/资源 | 唯一责任方 |
|---|---|
| 已提交消息、持久化句柄、验证历史、待处理输入 | SessionController；Conversation 保留兼容属性与会话操作接口 |
| 当前回合预算、待执行调用、临时模型响应 | TurnRunner / TurnState |
| 模型客户端和扩展连接 | Conversation 的幂等 runtime_close |
| 文件/进程后端 | 创建它的会话或 ApplicationServices；Explore 明确借用，不关闭父后端 |
| 执行事实 | fsync Journal；SQLite 是受理/调度状态及可重建的事实投影 |

`scripts/check_architecture.py` 防止公共契约反向依赖产品模块、核心依赖 CLI/旧应用协议模块，以及沙盒导入 Conversation 实现。工具路径解析独立于循环。兼容参数 repeated_tool_limit/stagnation 等已无运行时效果，自定义值会发出弃用警告；使用真实回合预算。

## 2. 保留接口与能力边界

python -m forge.engine的stdio RPC及原main/renderer角色保留兼容；当前不交付Electron或Web客户端。未来客户端接入API/RPC。test_refs必须指向实际测试，生成文件从contracts/v1产生。

workspace-write提供已验证的写限制；OS读取和网络继承宿主。初始化失败拒绝任务，不回退宿主。full-access须显式选择。离线、原生、wheel、模型和训练分别验收。

## 3. 开发与验证

验证器修正可使用 `verify(check_id=原检查ID, command=修正命令, revision_reason=原因)`；检查 ID 和执行记录 ID 均可引用，原断言、需求和工作目录自动继承。仍拒绝降低阈值、替换来源或改变工作目录；没有结构化断言的旧检查不能通过改写内联命令自动消除失败记录。

`view_image` 通过现有文件授权边界读取 PNG/JPEG/GIF/WebP（最多 512 KiB），向 Anthropic/Responses 传递原生图像内容；DeepSeek 文本适配器不提供该工具。Journal 保存原始图像消息，近期图片不作为长文本截断；历史压缩移除活跃上下文中的旧图片并保留重新读取提示。图片上下文预算使用估算，实际 token 以服务端用量为准。

API 接受字符串或类型明确的 text/image blocks，CLI 的 @path 支持文字和图像；输入限制为文字 256 KiB、最多 8 张图、单图 512 KiB、总附件 4 MiB。MCP 图片进入相同内容链路，音频仍不支持原生输入。适配器传输能力与具体模型能力分开，未知模型模态保持未知。

子目录 AGENTS.md/FORGE.md 随实际访问路径加载，祖先到后代排序并标明作用域。作用域规则尚未进入模型上下文时，写入和命令先返回提示，再允许模型依据新规则重试；不通过加载规则扩大权限。超出规则预算明确报错。

待处理输入先记为 input_queued；steering 只在请求边界进入上下文，user_message 同时记录消费 ID。follow_up 等待下一回合。取消将未消费 steering 转为待办，不自动重放工具；恢复从 Journal 重建队列。运行中不得切换会话历史。

纯内置独立读取可按 max_parallel_reads 有界并行，结果按模型顺序提交；有 Hooks、副作用或未声明并行能力的后端保持串行。原生 helper 的单输出流仍串行。实时 Engine 投影只消费当前写入者已 fsync 的新增记录，结束与恢复仍全量检查。预算估算依据已报告 input usage 保守校准，真实模型收益需要独立实验。

交互新会话延迟到首轮输入才持久化，API 和评测仍使用原有即时会话生命周期。
恢复展示从 Journal 原始消息重建，独立于压缩后的模型上下文；默认最近 10 轮，支持完整历史。

模型配置由 forge.config 读取与保存。npm 安装只创建缺失的用户 `.forge/.env`；`forge config setup/set` 写入用户文件，`forge config` 检查进程、项目和用户配置合并后的有效值。密钥隐藏输入，写入不覆盖其他配置条目；配置命令不调用模型。

公开评测通过 benchmark CLI 冻结实验后调用 Harbor 0.18.0：固定 Terminal-Bench 2.0 或 SWE-bench Verified 的内容摘要、显式任务范围、源码和依赖锁。模型复用 ForgeConfig；任务默认单并发、一次、零自动重试。运行保存在用户 evaluation 数据目录，官方 verifier 负责判分，既有 RPC/RunSpec 路径保留其未完成项阻断。

`--prepare-only` 在模型执行前缓存已安装 Harness 的派生 Docker 镜像，断网检查 Python 导入并记录官方基础镜像、源码和安装镜像身份。`--prepared-images` 在完整清单通过校验后启动，保持官方任务内容及资源限制。Git 基线在模型调用前确认，判分前差异引用固定基线；空工作区和模型自行提交均可采集，失败保留具体诊断。

准备阶段还缓存 Terminal-Bench 的固定版本 uv 官方安装脚本、安装包及可解析的 uvx 判分依赖，并断网验证依赖启动。缓存冻结为镜像层，准备好的任务使用各自容器的写时复制缓存，不挂载共享可写缓存。判分阶段仅将该安装脚本的精确下载请求导向缓存，安装包使用本地 URL，uv 禁止联网；官方测试脚本与断言不改写。无法解析的依赖命令在准备阶段明确失败，不宣称已缓存。任务自身的系统包和业务数据下载不属于此缓存。补丁采集使用独立临时 Git 索引，不修改或解锁 Agent 的工作索引，失败同时保存合并输出与 stderr。

Journal 是执行事实源；ATIF 和 SFT 都是离线投影。版本比较核对冻结配置与主机采集的 Docker 镜像/资源证据，缺失证据保留未知。公开测试任务隔离于训练数据；SFT 复用 Qwen 转换与 loss mask，独立检查来源、许可、划分、重叠、成功验收和本地 tokenizer。当前训练导出只完成接口，尚无真实训练数据集。

日常工作区为 D:\learn_project\forgecode 的 main，只维护当前说明和任务。历史通过和旧授权不能证明当前能力。虚拟环境、npm依赖、沙盒构建和.local报告均可重建且不提交。

开发时只跑新增行为、失败用例与相关模块，稳定后运行一次相关回归以及 contracts/quality，再推送并检查 CI。
仅公共核心变化或未覆盖风险触发全量回归，不在 Windows、WSL 和 CI 默认重复全量；原生验收按阶段需求执行。
CI 的 unit 自动发现非 native/fixture 的功能测试目录，portable 覆盖实现集成与协议测试；有测试收录检查防止遗漏。quality 同时运行 Ruff、公共输入/结果类型的 mypy 和架构依赖检查。根据 JUnit 耗时减少重复准备，保留关键边界测试。
离线存储性能基准：`uv run --no-sync python scripts/benchmark_architecture.py --records 100 --output .local/implementation/architecture-performance.json`。RunProfile.intent_routing=False 可用于单独测量前置路由开销，不改变默认权限。真实模型消融必须固定任务、模型、预算和环境后另行授权。
清理不授权付费模型、管理员设置或生产签名。
