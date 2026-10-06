# ForgeCode V4 交接 — 2026-10-06

- 当前完成 F00、F01；下一任务 F02（阅读 tasks/F02.md、主规范第 5、6、8、21、22 章）。
- F00 已推送：3355ee07fd05978cd19284f0460694d10d4d9086，分支 codex/forgecode-v4。
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
- F01 完成 npm workspaces、package-lock / uv.lock / resolved release-lock、资产
  loader、固定 Node 和 Python 运行时、PyInstaller onedir / Forge Vite 真构建探针。
  uv sync --group desktop-build --link-mode=copy 和官方 npm ci 成功。构建与安装串行。
- F01 unit：9 passed（内含 4 个真实 Node 行为断言），20261006T094338Z-9ee68b20。
  回归：748 passed，20261006T093957Z-8e28ebc0；新增固定 Python 拒绝行为另有 unit 证据。
  开发 smoke 6 checks pass，20261006T094341Z-a9e7179e；实际 packaged 验收 exit 2
  / blocked，因为当前 Windows 10 不属于声明平台。npm run typecheck 通过。
- 一次误并行 npm ci 占用 native 模块导致失败 20261006T093844Z-5d88174b，已重装重验。
- GHSA-86w9-cpqp-85rv：node-forge 1.4.0 尚无已发布补丁，SRT 0.0.78 依赖它。
  security=blocked；node packaging/verify-release.mjs --release 拒绝生产发布。
  不运行 npm audit fix 的旧 SRT 降级；不自造密码学补丁。详见 ADR 001。
- 下一步 F02 共享契约；保留原始四个 seed 正反例，补齐方法与结果／Bundle schemas，
  做 Python／TS 同判定与严格 JSON／canonical hash、case registry、证据门禁。
- G0 pass；G7 blocked；其他门禁未运行。保留 Python Harness、CLI、MCP、
  Hook、Explore、Feishu 和已有 Harbor runner，不复制或重写主循环。
