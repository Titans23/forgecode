# 终端分支真实审计 · 2026-10-12

基底 `913446f5fcf0275c2c839617fef9a5b2bbe4280f`，分支 `codex/terminal-harness`。新 checkout 最初无 dirty 文件，1406 个已跟踪文件。原目录 `D:/projects/forgecode` 保留原分支与未提交用户内容，所有实现/构建在独立 checkout 执行；没有导入私人会话或凭据。

应用创建 worktree 的忽略指令扫描停滞，终止该只读 Git 扫描后登记失败；独立 checkout 本身已完成并被核对，直接使用此任务创建的目录。应用侧重新附着也失败，不能将 UI 登记声称为成功。

## 真实入口和事实来源

| 责任 | 已核对的实际入口 |
|---|---|
| 启动组合 | `forge.launch.main / PROFILES` → terminal 或 benchmark CLI，按需 import |
| 终端 | `forge.cli.run_interactive_chat / _run_interactive_chat` → `forge.api.open_runtime` |
| 程序入口 | `forge.api.HarnessSession / SessionOptions / RunProfile / event_dict` |
| 原 Harness | `forge.runtime.factory.create_runtime` → `Conversation.stream` → `TurnRunner.run` |
| 权限/副作用 | `PermissionManager.authorize` → `ToolExecutor.execute` → `FileToolBackend.execute` |
| 实际边界 | `NativeBackendFactory.open` → `WorkspaceWriteBackend.prepare/execute/cancel/close`；Windows DSH AclSandbox，Linux bubblewrap |
| 子进程 | 既有 `LocalProcessOwner`、Windows Job、Linux subreaper；输出是数据 |
| 恢复事实 | `SessionJournal.append / SessionStore.open/load`，既有 checkpoint 与模型/工具预算 |
| 评测 | `evals.runner`、`benchmark.cli`、`benchmark.harbor.run_forge.run_turn`、RunSpec/ResultBundle 与独立 verifier |
| 后训练 | `TraceRecorder / RecordingModelClient / RecordedConversation / ForgeCodeRolloutEnvironment / RewardEvidence / clean_episode / build_sft_rows / build_dpo_row` |

没有新的代理循环。程序 API 与终端共享组装；外部消费者使用结构化事件/TurnResult/取消接口。兼容 stdio Engine 作为显式入口保留；HTTP adapter、Electron/React、SRT 兼容执行代码及其客户端专用构建/验证已移除。旧 V4 文档/证据放在历史范围，当前验收映射单独维护。

## 依赖和工具

本机 Python 3.12.13、Node 22.22.0、Git 2.52.0；Python >=3.12 保持。Node 使用与 npm engines 一致的 22.13+；固定沙盒 JS 依赖及完整清单校验保留。工具在受理前解析为安装在项目外的绝对路径，不从任务提供的环境继承凭据。

默认 Python 依赖去除 lark-channel-sdk；channels 为可选 extra。Harbor 从 dev 默认组移到 evaluation extra。删除 PyInstaller 组、Electron/React/Vite 依赖和旧桌面资源锁；安装 wheel 携带验证过的沙盒代码。模型/训练框架/Harbor 不由普通终端初始化加载。

## 已发现的问题与修复

原 CLI 未启用原生沙盒，Windows 工具仍依赖桌面资产。现共享实际边界并使用明确的本机前置条件；初始化失败在模型请求前拒绝任务。

Windows pytest 默认临时目录拒绝访问；保留首轮 219 pass/350 setup errors。独立目录的基线 567 pass/2 fail，分别为可选 Harbor 缺失和长路径 artifact。已按需安装评测测试依赖，修复 private_storage_path 的读写。

原生测试目录 OWNER RIGHTS 导致受限进程无法读取/取得 inode；仅对新建的合成测试目录添加当前用户 ACE，未修改系统目录 ACL。scope SID 包含规范化写范围，实际验证较宽历史 grant 不授权新窄范围。

扩展回归 560 pass/25 fail 暴露旧后端无 close 的接口差异；共享生命周期现在按实际支持的接口清理，保留模型 close 异常。Web 验证和不可运行 strict Bridge 按当前产品范围退出；CLI 参数旧前缀与工具 schema 继续按兼容基线验证。

wheel 漏收隐藏文件/隐藏目录两次被真实内容检查拒绝，已修正打包清单；检查每个沙盒资源字节/hash，独立安装解释器验证原生工具与清理。

## 验收界线

所有模型为确定性离线 fixture 或受控 loopback；公网付费调用为零。Windows10 真正运行 DSH/file worker；SDK/CLI tests 不替代原生证明。WSL 只有 Ubuntu26.04，Node/rg 缺失；Ubuntu22/24 原生验收 blocked。没有管理员安装、正式训练、付费评测或 main 合并。

最终运行计数、源码指纹、wheel hash、失败历史与限制以 T01 evidence/progress/handoff 的最终索引为准；本审计中的中间计数不会冒充最终源码回归。

## CI 与交付收尾

干净 CI 缺少构建产物，安全审计任务已补构建；离线代码完整性检查与实际运行时工具受理拆开，缺 Git/rg/PowerShell 的运行时仍拒绝启动。诊断测试根据实际工具是否存在验证 pass/blocked，并增加缺 rg 的拒绝行为。深目录审计 clone 使用局部 core.longpaths，不修改系统策略。

全量回归1330项通过（5条警告保留），其后 CLI 测试按去除 ANSI 和布局换行后的可见文字断言，并限制测试启动等待；普通CLI28和彩色文字2项补测通过。全部运行时代码与 wheel 输入未改变。强制 FORCE_COLOR 的整套本机管道测试曾因缺少 Windows console 失败、出现测试 fixture 名称错误；失败和中断报告保留，不计通过。

Git 已保存多个账户但没有选择默认账户，导致交互账户选择而非网络故障；此前关于 HTTPS 卡住的判断被此观察修正。按用户新增请求选择本仓库 Titans23，并移除另一项本机登录，无交互推送成功。只修改仓库 Git 配置及明确指定的本机凭据；原工作文件、模型凭据和会话不变。
