# ForgeCode V4 交接 — 2026-10-06

- 当前完成 F00；下一任务 F01（阅读 tasks/F01.md、主规范第 3、20 章）。
- 实际基线 HEAD：d5c08a3158c764d4e797b2a1e2907e391f6414a8。
  接续时运行 git status / git rev-parse HEAD，以实际提交为准。
- 实施包原件在 update_implementation_pack/forgecode-implementation-v4，
  工作规范在 docs/implementation；原件未修改。
- 用户原有 untracked：.forge/、build/、三张 architecture 图、svg_probe.txt、
  update_implementation_pack/；保持原样，不加入本次提交。
- 已实现 scripts/impl.py audit [--write]、doctor --scope development
  [--check-network]、status、verify --task F00 / --suite audit / --suite regression。
  异目录定位、AST 签名核验、缺工具／网络 blocked、JUnit 判定、真实 evidence 索引。
- 基线 powershell -NoProfile -File scripts/test.ps1 -q --tb=short：exit 0，740 passed。
- 新行为 verify --task F00：exit 0，7 passed，evidence 20261006T091942Z-d39f0b28。
- 回归 verify --suite regression：exit 0，747 passed、0 skipped，
  evidence 20261006T092001Z-c7f13bd9；日志和 JUnit 留在 .local/implementation。
- 两次旧 evidence 采集因扫描缓存太慢被取消；已修正并重跑，不计通过。
- 工具：项目 Python 3.12.13，Node 22.17.1、npm 10.9.2、uv 0.12.5，
  Git 2.52.0.windows.1。gh / pip / py 不可用；uv pip 可用。
- 本机 Windows 10 build 19045。Windows 11、原生 Ubuntu、GUI 安装版、签名、
  SRT 管理员 setup 和付费模型预算均未验收／未授权，不冒充 pass。
- 用户授权每个开发版本测试后上传 GitHub；未授权付费模型实验、管理员 setup。
- 下一步 F01：npm workspaces、精确 release-lock、资产校验与构建 smoke。
  已查官方 registry：Electron 44.5.1、Forge 8.0.1、SRT 0.0.78；仍须实际安装
  和兼容测试后才能锁定。显式 --registry=https://registry.npmjs.org；
  不修改本机全局 npm 默认 npmmirror 设置。
- G0 的工具链锁尚待 F01；其他门禁未运行。保留 Python Harness、CLI、MCP、
  Hook、Explore、Feishu 和已有 Harbor runner，不复制或重写主循环。
