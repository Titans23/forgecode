# ForgeCode 当前架构

## 0. 产品范围

持续对话终端与Python Harness API。terminal/evaluation由启动Profile选择依赖组合；SDK直接调用API。原生权限由SessionOptions单独选择，Profile不提升权限。

## 1. 目录与调用链

| 目录 | 当前职责 |
|---|---|
| forge/cli.py、launch.py、api.py | 终端、入口组合和程序调用 |
| forge/runtime、context、tools、sessions | Harness、上下文、工具和Journal事实 |
| forge/sandbox、permissions、sandbox_bridge | 授权及Windows DSH/Linux bubblewrap执行 |
| forge/application、engine | CLI、RPC和评测实际使用的服务与状态投影 |
| benchmark、evals、extensions | 评测适配、任务和后训练导出 |
| contracts、packages/contracts | 维护源与TS契约校验器 |
| scripts、tests、docs | 构建、真实验证和当前说明 |

forge.launch选择forge.cli或benchmark.cli。CLI和forge.api共用open_runtime，调用runtime.factory.create_runtime → Conversation.stream → TurnRunner → ToolExecutor → 原生工具。Journal记录会话副作用；Engine负责受理、调度与投影，不复制Journal事实。

## 2. 保留接口与能力边界

python -m forge.engine的stdio RPC及原main/renderer角色保留兼容；当前不交付Electron或Web客户端。未来客户端接入API/RPC。test_refs必须指向实际测试，生成文件从contracts/v1产生。

workspace-write提供已验证的写限制；OS读取和网络继承宿主。初始化失败拒绝任务，不回退宿主。full-access须显式选择。离线、原生、wheel、模型和训练分别验收。

## 3. 开发与验证

验证器修正可使用 `verify(check_id=原检查ID, command=修正命令, revision_reason=原因)`；检查 ID 和执行记录 ID 均可引用，原断言、需求和工作目录自动继承。仍拒绝降低阈值、替换来源或改变工作目录；没有结构化断言的旧检查不能通过改写内联命令自动消除失败记录。

`view_image` 通过现有文件授权边界读取 PNG/JPEG/GIF/WebP（最多 512 KiB），向 Anthropic/Responses 传递原生图像内容；DeepSeek 文本适配器不提供该工具。Journal 保存原始图像消息，近期图片不作为长文本截断；历史压缩移除活跃上下文中的旧图片并保留重新读取提示。图片上下文预算使用估算，实际 token 以服务端用量为准。

交互新会话延迟到首轮输入才持久化，API 和评测仍使用原有即时会话生命周期。
恢复展示从 Journal 原始消息重建，独立于压缩后的模型上下文；默认最近 10 轮，支持完整历史。

模型配置由 forge.config 读取与保存。npm 安装只创建缺失的用户 `.forge/.env`；`forge config setup/set` 写入用户文件，`forge config` 检查进程、项目和用户配置合并后的有效值。密钥隐藏输入，写入不覆盖其他配置条目；配置命令不调用模型。

公开评测通过 benchmark CLI 冻结实验后调用 Harbor 0.18.0：固定 Terminal-Bench 2.0 或 SWE-bench Verified 的内容摘要、显式任务范围、源码和依赖锁。模型复用 ForgeConfig；任务默认单并发、一次、零自动重试。运行保存在用户 evaluation 数据目录，官方 verifier 负责判分，既有 RPC/RunSpec 路径保留其未完成项阻断。

`--prepare-only` 在模型执行前缓存已安装 Harness 的派生 Docker 镜像，断网检查 Python 导入并记录官方基础镜像、源码和安装镜像身份。`--prepared-images` 在完整清单通过校验后启动，保持官方任务内容及资源限制。Git 基线在模型调用前确认，判分前差异引用固定基线；空工作区和模型自行提交均可采集，失败保留具体诊断。

准备阶段还缓存 Terminal-Bench 的固定版本 uv 官方安装脚本、安装包及可解析的 uvx 判分依赖，并断网验证依赖启动。判分阶段仅将该安装脚本的精确下载请求导向缓存，安装包使用本地 URL，uv 禁止联网；官方测试脚本与断言不改写。无法解析的依赖命令在准备阶段明确失败，不宣称已缓存。任务自身的系统包和业务数据下载不属于此缓存。补丁采集使用独立临时 Git 索引，不修改或解锁 Agent 的工作索引，失败同时保存合并输出与 stderr。

Journal 是执行事实源；ATIF 和 SFT 都是离线投影。版本比较核对冻结配置与主机采集的 Docker 镜像/资源证据，缺失证据保留未知。公开测试任务隔离于训练数据；SFT 复用 Qwen 转换与 loss mask，独立检查来源、许可、划分、重叠、成功验收和本地 tokenizer。当前训练导出只完成接口，尚无真实训练数据集。

日常工作区为D:\projects\forgecode的main，只维护当前说明和任务。历史通过和旧授权不能证明当前能力。虚拟环境、npm依赖、沙盒构建和.local报告均可重建且不提交。

开发时只跑新增行为、失败用例与相关模块，稳定后运行一次相关回归以及 contracts/quality，再推送并检查 CI。
仅公共核心变化或未覆盖风险触发全量回归，不在 Windows、WSL 和 CI 默认重复全量；原生验收按阶段需求执行。
CI 的 unit/portable 覆盖指定单元与集成目录，不等于全部 tests。根据 JUnit 耗时合并重复准备，保留关键边界测试。
清理不授权付费模型、管理员设置或生产签名。
