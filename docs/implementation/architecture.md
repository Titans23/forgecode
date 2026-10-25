# ForgeCode 当前架构

## 0. 产品范围

持续对话终端与 Python Harness API。terminal/evaluation 启动 Profile 选择入口；`bundles` 是入口描述，不是插件依赖注入系统。SDK 直接调用 API。原生权限由 SessionOptions 单独选择，预算配置不提升权限。

当前优先目标是 Agent/Harness 实习项目的可验证展示：先盘点现有能力，再用小型冻结任务与失败注入验证模型→工具→验证→恢复闭环，最后准备 5 分钟 CLI/API 演示及贡献说明。完整跨平台 OS 强隔离仍是未完成扩展，移出这条关键路径；此优先级也约束旧 goal 的后续选题，不代表原沙盒目标已完成。保持无 Docker 的日常运行和现有权限/取消边界，不新增训练扩展或未经授权的模型调用。

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

`evals.runner` 同样使用 `HarnessSession`，通过共享配置解析与模型工厂选适配器，复用预算、权限、TaskPolicy、Journal 和关闭路径，不再直接创建 Conversation/Anthropic 客户端。默认采用原生 workspace-write；不可用即失败，full-access 必须显式选择。初始化中途失败时，API 也关闭已经创建的模型客户端和后端。

夹具的 setup、public/hidden verifier 和 diff check 是评测器独立触发的命令，走同一已选择执行边界，不消耗 Agent 的模型/工具预算，受各自命令时限约束。隐藏测试在 Agent 回合结束后恢复，拒绝链接目标；不能把这描述成 partial OS 边界下的绝对保密保证。`EvalOutcome.passed` 才是最终验收结果，Agent 声称完成不等于成功。每次尝试保留 run_id 报告和 Journal/trajectory；latest 只是同一尝试的便捷视图，不应重复计数。取消也保存失败记录；缺少终态聚合用量时留 null。清理未知时拒绝成功并保留工作区，避免在活跃进程可能存在时删除它。

真实仓库任务冻结在 `experiments/native-swe-lite.json`，目前是公开 SWE-bench Lite 的 Windows 原生适配预检，不是官方成绩或模型完成率。`scripts/prepare_native_swe.py` 校验官方数据/源码摘要，使用任务独立解释器和依赖，先施加测试补丁复现失败，再只施加参考修复验证全部规定回归。`evals/swe_lite_grader.py` 校验冻结的原标签→全部实际 node ID 映射，拒绝缩小参数覆盖、跳过、xfail 和收集错误；命令生命周期复用 LocalProcessOwner，不证明 OS 强隔离。

参考工作区、补丁、原始日志和 `runner-source.json` 只属于评测控制器；后者不是可运行 EvalCase。`evals.native_swe` 复用 HarnessSession/runner：所属夹具根中分开源码和依赖，清理未知时一并保留；Agent 结束后保存带摘要的增删改候选，在新副本施加测试补丁并独立判分。Journal 审计可见的答案来源访问，发现后标为 invalid；当前原生读取/网络继承宿主，因此输入分离不等于绝对保密。`evals.native_swe_campaign` 使用既有 OS 锁和 Journal，持久化槽位准入，串行执行冻结计划，禁用提供方重试，停止后只允许显式续跑未开始槽。预算耗尽不算成功，成功费用单列，未知用量不填零；候选可以不调用模型独立重放。具体预算和模型固定在 `experiments/native-swe-lite-plan.json`。

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

read-only 同样有每次执行独立的私有临时写入范围，项目写权限不因此增加。Windows 用未授予项目目录的隔离 SID 与独立临时 SID 组合，Linux 仅额外绑定私有临时目录。Windows 命令使用的小型 Python venv 复用本机解释器和依赖，在私有临时目录下保留继承 ACL，避免 CPython `0700` 重设 DACL 后丢失临时写入能力；它是兼容处理，不是安全边界，原生令牌仍负责授权。Python `-S`、外部解释器和临时范围之外的 `0700` 不在适配范围。进程树清理完成后才撤销临时授权、删除临时文件和会话 Python 环境。

沙盒保持原生轻量路线，安装和日常运行不依赖 Docker。试验性的 Docker 执行层、镜像构建入口及相关 CI 已移除。现有 DSH/bubblewrap 仍只提供已验证的部分隔离；`strict` 在完整能力具备前继续拒绝准入。后续原生能力审计、网络白名单、资源配额和完整 CLI/SDK 验收见 T01，不能用历史容器测试证明原生能力。与运行平台无关的 `workspace_transfer.py` 保留有界输入快照、敏感路径过滤及冲突检查回写，供原生隔离工作区使用；它本身不构成 OS 沙盒。单文件替换原子化，多文件应用不是文件系统事务，I/O中断明确返回已应用路径及 INDETERMINATE，不自动重放。

Windows 的 ACL 授予和撤销在 Win32 调用处使用扩展路径形式，避免长控制目录中的临时授权失败；策略路径比较及 SID 派生仍使用原规范路径。环境变量和应用路径 API 不全局重写，例如 Node `mkdtemp` 自身处理长路径时仍需使用其支持的扩展路径形式。

Windows 原生会话的内部控制与执行临时目录放在 OS 解析的 LocalAppData/ForgeCode/native 下，按会话 UUID 独立持有，避免长产物路径使 Python 子进程启动超时。该根在策略冻结前加入保护范围；原产物目录保留定位文件和边界诊断。只有 Job、临时授权及所属目录均确认清理后才发布 clean，未知清理保留现场；不修改系统 TEMP，也不清理其它会话。

任务的旧版 Python 也复用私有临时目录兼容包装，保留内置函数的调用方式并更新已缓存的 pathlib 入口。评测器将 pytest 缓存放入所属临时目录，避免项目目录内 `mkdir(0700)` 被拒后反复重试；缓存功能、冻结测试映射及断言不变。任意项目路径的 `0700` 语义仍不在该兼容范围内。

原生文件观察复用 Git 改动路径与授权文件指纹，并枚举普通 ignored 文件；`.gitignore` 不作为安全白名单，已声明的运行缓存仍排除。Git 配置包含过滤器、include 或工作树重定向时使用既有有界扫描；输出截断、超时和观察缺失不算完整。已确认的文件工具目标直接刷新前后指纹，避免每次修改都重扫仓库；初始基线不完整时仍拒绝完成。受限观察不启用宿主 Git diff 子进程。输出生产者在有界队列成功入队后让出调度，消费者缺失、缓慢或配额耗尽仍报告丢弃。

## 3. 开发与验证

验证器修正可使用 `verify(check_id=原检查ID, command=修正命令, revision_reason=原因)`；检查 ID 和执行记录 ID 均可引用，原断言、需求和工作目录自动继承。仍拒绝降低阈值、替换来源或改变工作目录；没有结构化断言的旧检查不能通过改写内联命令自动消除失败记录。绝对路径解释器的 compileall/py_compile 也只算结构检查；评测提示允许在禁止修改测试时读取、运行已有公开测试。EvalCase 可显式启用已有 require_task_verification/require_positive_verification 门禁，实际策略记录在运行配置；默认不升级日常任务，功能验收仍依赖行为检查及独立判分。

`view_image` 通过现有文件授权边界读取 PNG/JPEG/GIF/WebP（最多 512 KiB），向 Anthropic/Responses 传递原生图像内容；DeepSeek 文本适配器不提供该工具。Journal 保存原始图像消息，近期图片不作为长文本截断；历史压缩移除活跃上下文中的旧图片并保留重新读取提示。图片上下文预算使用估算，实际 token 以服务端用量为准。

API 接受字符串或类型明确的 text/image blocks，CLI 的 @path 支持文字和图像；输入限制为文字 256 KiB、最多 8 张图、单图 512 KiB、总附件 4 MiB。MCP 图片进入相同内容链路，音频仍不支持原生输入。适配器传输能力与具体模型能力分开，未知模型模态保持未知。

每轮开始时加载项目根目录 AGENTS.md、FORGE.md 和 `.forge/rules/*.md`，以及相关项目记忆。工具访问路径及显式 cwd 会激活其祖先目录的 AGENTS.md/FORGE.md，按从浅到深的顺序注入下一次请求；未访问的兄弟目录不加载。若写入或进程工具涉及模型尚未收到的新规则，则返回规则更新提示，待下一次模型请求后再尝试；同一批中先读后写也不能绕过此检查。规则文件沿用链接限制和大小预算，工具权限及用户任务范围检查保持独立。

待处理输入先记为 input_queued；steering 只在请求边界进入上下文，user_message 同时记录消费 ID。follow_up 等待下一回合。取消将未消费 steering 转为待办，不自动重放工具；恢复从 Journal 重建队列。运行中不得切换会话历史。

工具批次按模型索引顺序执行，失败或取消后不执行后续工具。实时 Engine 投影只消费当前写入者已 fsync 的新增记录，结束与恢复仍全量检查。上下文压缩沿用完整请求大小与输出预留的固定估算阈值；真实 token 用量独立用于回合预算，不再校准下一次压缩阈值。

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

每个独立验收点完成相关检查后单独 commit、push，并报告 SHA/链接、验证范围和实际 CI 状态。源码存在、离线脚本模型通过、真实原生测试和真实模型收益是不同证据；完整完成率包含所有已尝试任务，预算耗尽、取消及基础设施失败不能算成功。成功任务成本与双成功配对另列，不用删掉失败后的均值代表总体改进。当前 P3 七个双成功配对的 token -3.4%、耗时 +1.6% 未证明稳定收益，不能摘取前者作为简历性能结论。
