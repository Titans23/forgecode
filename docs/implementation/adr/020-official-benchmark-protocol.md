# ADR020 — 包装既有官方 Runner 与冻结执行协议

采用 Harbor 0.18.0 官方 Task / JobConfig / TrialResult 和独立 verifier；现有 Python Harness、CLI 与 Aider 默认反馈协议保留。新 adapter 分开 describe、兼容性验证、真实进程执行、不可变产物采集与官方 reward 归一化，接入 F19 的 owner epoch、预算和三轴终态。

V4 Job 每题一个 attempt / 一个 worker / 零 Harbor retries；冻结源码、Git commit / binary diff、uv.lock、模型参数、父 Harness 总预算与 grader 配置。受控安装固定 Python 3.12.13 / uv 0.12.5 / frozen lock，不能从项目 .env 或配置加载 Hooks / MCP。未支持的 external feedback、sampling 与缓存配置明确拒绝。旧 Runner 默认路径继续兼容。

Runner 非零退出不抹掉独立 reward；Agent completed 不代表 grader pass。没有 reward 为 unscored，verifier 错误为 grader_error。实际 patch 字节参与 cache key；随机 trial 目录不参与 patch 身份。子进程采用真实 owned process group / taskkill，日志有界。容器 cleanup 只接受 Agent mount 外的实际 start / stop-delete receipt；缺失清理证据为 unknown，禁止盲目重试。

实际三题 Aider Polyglot 1.0 小集固定到 harbor-datasets commit f30b14415dd733c83627204bad0af69a89ceb46f；真实源码内容、官方 checksum 和实际 Harbor --print-config 已验收。配置检查使用显式 unconfigured-awaiting-selection draft，不创建环境、模型或 grade。Docker 服务、付费授权与 F30 控制未就绪，正式 probe 保持 blocked；测试 fixture 始终标 synthetic。

官方 Linux Docker 是 official-environment，不替代 Windows 原生日常执行。生产 executor 在未验证 native policy / capability 与显式授权预算前阻止 paid API。远端 raw journal 暂为 private artifact，不进入可信本机 ledger；费用未知保持未知。

依据：[Harbor](https://github.com/laude-institute/harbor)、[官方任务 registry](https://github.com/laude-institute/harbor/blob/main/registry.json)、[固定公开任务源码](https://github.com/laude-institute/harbor-datasets/tree/f30b14415dd733c83627204bad0af69a89ceb46f)、[uv 固定版本安装](https://docs.astral.sh/uv/getting-started/installation/)。验收见 F20 任务卡与 20261006T223459Z-232a8d30 公开来源／配置报告；未作真实模型改善结论。
