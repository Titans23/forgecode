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

日常工作区为D:\projects\forgecode的main，只维护当前说明和任务。历史通过和旧授权不能证明当前能力。虚拟环境、npm依赖、沙盒构建和.local报告均可重建且不提交。

运行contracts、quality和regression；改变原生边界时再运行terminal-native。清理不授权付费模型、管理员设置或生产签名。
