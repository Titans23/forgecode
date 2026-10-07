# CLI、历史数据与 Web 兼容边界

原有 forge 交互、会话恢复／fork、飞书命令和工具 schema 保留。新增命令转发真实入口：

| 命令 | 入口与作用 |
|---|---|
| forge engine --help | Python Engine stdio 与 profile 参数 |
| forge sandbox --help | 只读系统诊断与显式原生验收选项 |
| forge eval list --json | 现有 benchmark catalog；不发起模型调用 |
| forge web --help | 默认关闭的可选 loopback HTTP 服务 |
| forge history prepare/import/list/inspect | 原始历史只读扫描、备份、确认和查询 |

CLI 与桌面使用相同物理项目 OS 锁；不同 profile 数据根不绕过项目互斥。原 CLI 为受信任本地入口，保持既有行为。Engine/Web 的 strict 与 local-trusted 参数明确；local-trusted 需显式选择，不能作为原生隔离通过的证据。

## 历史导入

先运行 forge history prepare PROJECT SOURCE --data-dir DATA。SOURCE 是旧 SessionStore 数据根，PROJECT 是原会话所属项目。prepare 不改写旧 index、Journal 或配置；生成逐文件 hash、独立备份、legacy path/hash → 新 opaque ID 映射和 preview_sha256。

审阅返回预览后，运行 forge history import PROJECT SOURCE --data-dir DATA --confirm-sha256 SHA。源文件变化、备份缺失／损坏或确认 hash 不匹配会拒绝。相同 profile/source 重复导入不产生重复映射或事件。用 list 与 inspect 查询；offset 以 100 条分页。导入记录是 imported 只读历史，不自动恢复执行，也不进入可信费用账本或 grade。

MCP、Hook、频道和权限配置的未知顶层字段会提示字段名与支持字段；不打印未知值，不删除原值。现有嵌套校验仍保留。
权限规则的未知字段也会提示；保存新的明确授权保留原有 version、未知值与 opaque entries，损坏文件拒绝覆盖。

## Web 开发入口

按 uv.lock 安装 web extra（uv sync --extra web --group dev --group desktop-build），并构建固定 UI 资产（python scripts/build_desktop.py）。显式执行 forge web --data-dir DATA，可由可信 CLI 追加 --project PROJECT --authorize-project 和 --from-cli-config。后者将现有 provider 配置放进该进程内存；页面不能读取或修改密钥。实际模型调用仍需要用户预算授权，此任务未执行付费实验。

服务仅绑定 127.0.0.1 随机端口，无公网 host 参数。控制台分别给出地址与五分钟有效、只能使用一次的登录凭据；浏览器登录表单建立 HttpOnly/SameSite 会话。关闭 CLI 结束服务。凭据不作为 URL 参数，也不写入项目文件。默认 strict；F28 原生 backend 未组装时执行返回 blocked。仓库构建资产是开发入口，独立安装包的固定资源与启动组装留 F27/F28。

Web 与 Desktop 使用同一 React 页面、公共业务 DTO 和 reducer。Native chooser、workspace authorize、approval、credential read/write、setup、敏感导出等特权操作要求 Desktop/CLI，返回 DESKTOP_OR_CLI_APPROVAL_REQUIRED。只读 snapshot/report/trace、已授权项目任务、作用域 SSE 与受控 artifact ID 使用真实服务。

## 扩展执行边界

| 路径 | 当前执行边界 | strict eval |
|---|---|---|
| 原 CLI / 飞书 ChannelGateway | 现有 Python Harness；本地主机为受信任入口，原 Hook/MCP 仍保留 | 不宣称该入口是原生沙盒 |
| Hook / 外部 MCP server | 可执行可信项目配置和外部进程；不能冒充受限内置工具 | Engine 任务 bindings 禁用 trusted_extensions |
| Engine / Desktop / Web 内置工具 | 共用 Harness adapter；strict 要求通过验证的 backend，local-trusted 明示主机权限 | 未验证能力拒绝，不降级 |
| Benchmark Harbor / 公开模型实验 | 独立 execution/grading/cleanup 与来源证据；环境、策略和预算门禁 | 未授权 API / 未验证 native policy 保持 blocked |

实际 Windows 10 开发测试包含 HTTP/Chromium、Engine、文件、Journal 与数据库行为；脚本模型是离线 fixture。Windows 11、Ubuntu X11/Wayland、原生 isolation/cleanup、安装包和公开模型成绩须分别验收。
