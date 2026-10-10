# ADR 029：当前源码证据与分层 CI

## 状态

已实施；当前源码 contracts2／quality4／unit189／portable341／全仓1274及本机 fresh CI gate 通过。原生 runner／保护策略／GitHub hosted matrix和依赖安全分别未实测或 blocked。

## 观察

父 HEAD 为 779d74aa19aad7d083191b69e367e42f77eedd7a。原 gate 只信 JSON status。首次 9 项行为测试实际 6 fail／3 pass：空 JUnit、skip／failure／error 可被声明 pass 遮蔽，Windows 11 必需证据缺失仍通过，历史失败永久污染最新成功。修复后这些行为通过。后续又真实复现 unknown JSON verdict 被当成通过，补充封闭 verdict 校验；原失败报告保留。

## 决定

复算实际报告和 hash，按 task／suite／platform 取最新，保留失败历史。另建显式本轮 ID 的 CI gate，绑定 HEAD／平台／当前源码清单，避免干净 checkout 复用仓库历史报告。源码 hash 排除 progress／handoff 等记账文本，因此测试后记录证据和提交不会改变被测源码身份。最终实施 gate 要求当前源码的五类检查通过。

CI 分 hosted portable 与手动专用 native／packaged。运行入口和 workflow 都约束 trusted main dispatch；不提供 paid API／signing secrets，不自动管理员 setup。Actions 固定官方完整 commit；Python／Node／uv 和 lock 固定。跨平台安装路径／binary hash 有差异的开发 inventory 在 CI 中按真实安装重新生成。

保留已有 release security blocker，用实际 npm 官方 audit 报告佐证。纠正 Bridge 项目资产缺少 LICENSE 却写 Apache-2.0 的声明为 NOASSERTION；不替用户选择开源许可。

## 限制

本机 Windows 10 的通过结果不建立 Win11／Ubuntu 原生资格。尚未实际配置／核验 GitHub environments required reviewers、branch protection 或专用 runner，workflow 文件不能证明这些外部策略已生效。签名、项目 LICENSE、SRT setup、Docker daemon 和真实模型实验保持独立 blocked，不影响可执行的离线开发任务。
