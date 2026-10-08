# ForgeCode V4 当前交接

本文件只保留最新状态；完整阶段历史见 `archive/handoff-72d7fb8-20261008.md`。progress、backlog、任务卡与实际 evidence 保留全部引用，历史通过不替代当前源码验证。

## 当前工作

用户要求继续 F28/F31，先完成当前环境的代码和回归；外部资源缺失记 blocked，继续独立工作。当前分支 `codex/forgecode-v4`，本轮测试父 HEAD `72d7fb89d94de437cd21c5549d5423d7ea7629e3`；提交后的真实 HEAD 以 git 为准。

F28 本轮关闭边界修复已完成五套件，工具 session12713 已退出0，无待等待验证进程。source `9c0b2502127d0996af6a609bccea9d47cdd707908cc1a230ff1e8cb32185e490`：contracts2、quality4、unit225、portable403、regression1372全部pass，0skip/failure/error；5份报告复算和fresh本机CIgate通过。证据 `evidence/F28-admission-observations.json`，完整ID见progress F28.current_evidence_ids。本轮未重建安装包、未新增模型请求、未做本机管理员操作。

接下来先精确提交/推送 F28，然后应用私有 F31 准备补丁 `.local/F31-budget-implementation.patch`。私有 `test_f31_budget_binding.py` 的15项真实SQLite/契约/导入导出行为已复现；`test_f31_budget_ui.py` 的实际React控件测试也已复现。准备文件尚不是已合入实现。将测试接入真实 tests 后生成contracts、重建Bridge/Desktop，并运行 `.local/F31-budget-verify.py`。本轮F310真实模型/管理员调用。

## 最近的 hosted 证据

实际 GitHub run37716477000（12cc51d）：Ubuntu22.04、Ubuntu24.04、Windows2025各contracts2/quality4/unit225/portable400通过，0skip/failure/error；三个fresh CI gate和浏览器用例通过。安全audit仍2high/exit2，整体workflow failure。该结果不属于新的F28关闭边界源码。索引 `evidence/F29-electron-sandbox-hosted-observations.json`。

## 授权与保护

- 已授权测试过的开发版本上传、现有配置真实模型及金额无上限；费用未知保留unknown，请求/工具/时间仍有限。
- 唯一管理员设置例外：Titans23/forgecode、codex/forgecode-v4分支push、GitHub临时Ubuntu24.04 runner、锁定Electron helper的root:root/4755。记录 `evidence/human-authorization-ci-20261008.json`。本机/原生SRT setup未授权。
- 原始模型两批10attempt/83请求及全部失败保留，不挑选重跑。历史完整批次A3/3、B2/3，不是正式Harbor/native证明。
- 保留用户 `.forge`、无关未跟踪文件、旧四份10:33 evidence。侧聊只清理已完成且无引用的旧pytest tmp；不改源码或当前测试产物。

## 剩余门禁

F28/F31均in_progress。F28仍缺原生verified promotion、完整初始化SRT/helper/listener清理及动态敏感路径一致性。锁定上游reset会记录并吞掉部分清理错误，不能凭resolved Promise宣布clean。

F31下一小步补齐显式无上限金额表示、冻结网络一致性和账本归属校验；正式Docker环境/网络隔离与可信宿主逐请求准入/授权仍未完成，不以账本校验冒称执行授权。

Windows11 x64工作站、Ubuntu22/24 x64 X11/Wayland原生设备和Linux Docker daemon缺失；签名/LICENSE、2项high依赖和metadata额外开销5.2103%>5%仍受阻。新原生管理员动作需具体申请。

## 恢复读取

先核对实际HEAD/status、本文、progress/current task和对应任务卡。只在调查历史时读取archive与旧日志；不要轮询本文明确已结束的session，也不要把私有准备文件当作已验证公开实现。
