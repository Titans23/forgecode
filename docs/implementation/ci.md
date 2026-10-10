# 持续集成与证据门禁

F29 保留真实 Python Harness／CLI。所有检查调用当前源码与固定依赖，不从仓库历史 JSON 推断本次 CI 成功。测试报告、命令、退出码、平台、HEAD、源码 inventory hash 和 report SHA256 一起保存。

`portable.yml` 在 push／PR 上执行 Ubuntu 22.04、Ubuntu 24.04、Windows Server 2025 矩阵。Windows hosted runner 仅建立 portable 证据，不能建立 Windows 11 原生验收。固定 Python 3.12.13、Node 24.21.0、uv 0.12.5；第三方 Actions 固定官方 tag 对应的完整 commit SHA。`uv sync --locked --all-groups --all-extras` 和 `npm ci` 后，`scripts/ci_bootstrap.py` 校验官方 Node archive／binary SHA，将运行时放入项目 `.local`；用固定 Node 显式运行锁定 Electron 的官方 installer 并检查实际版本／平台，再生成 contracts、Bridge 和开发 desktop assets。Electron44 的实际 package.json 没有二进制 postinstall，不能依赖任意测试模块的冷导入顺带下载。bootstrap 不安装系统服务或执行管理员 setup。

随后 `scripts/ci_run.py --layer portable` 运行 contracts、quality、unit、portable，逐项保留 evidence，并把本轮 ID 显式传入 `impl.py gate --name ci`。Linux hosted 路径通过现有 `xvfb-run` 为真实 Chromium 集成测试提供图形会话；缺少该工具时命令失败，不自动安装，也不建立原生 X11／Wayland 资格。缺项、另一 HEAD／平台、过期源码 hash、非零退出、报告被改、零测试、required skip、unknown verdict 都会拒绝通过。JUnit testcase 数量由实际 XML 复算；JSON 必须有非空且全部通过的 checks。回归独立执行 `impl.py verify --suite regression`；不将其历史结果冒充本轮 CI 结果。

2026-10-08 的实际 run37714511897 确认 Ubuntu24.04 Chromium 因 SUID helper 权限不正确退出 -5。用户已明确授权唯一的 CI 设置例外：仅 `Titans23/forgecode` 的 `codex/forgecode-v4` 分支 push、GitHub 临时 `ubuntu-24.04` runner，在现有校验器物化锁定 Electron 后，将其实际 `chrome-sandbox` 设置为 `root:root`、`4755` 并检查结果。条件不满足时不执行该步骤；PR、fork、其他分支和本机不取得此授权。没有关闭 Chromium 沙盒或更改全局系统参数。授权记录见 [human-authorization-ci-20261008.json](evidence/human-authorization-ci-20261008.json)；本机／原生 SRT 管理员 setup 仍未授权。

quality 包括真实 Python AST 语法、全工作区 TypeScript、Node 行为测试、contracts 漂移、npm 精确版本与 release lock hash、任务依赖 DAG、任务卡／章节／case 测试引用以及高确定性 secret 模式扫描。扫描只输出路径、行号和种类。模式扫描通过并不表示已穷举所有秘密格式。`npm run lint` 可独立运行离线检查。

限定设置已随 `12cc51d` 上传并实测：run37716477000 的三个 portable job 均通过 contracts2/quality4/unit225/portable400，零测试跳过/失败/错误；原 Ubuntu24 浏览器用例通过。设置步骤仅在获批 job 执行成功。独立 audit 仍有 2high，整体 workflow 仍 failure；原始报告及执行范围复算见 [F29-electron-sandbox-hosted-observations.json](evidence/F29-electron-sandbox-hosted-observations.json)。

`native-manual.yml` 仅允许仓库 `Titans23/forgecode` 的 `main` 分支手动 dispatch。native／packaged 分开，使用已准备的 Windows 11、Ubuntu 22.04／24.04 X11／Wayland 专用 self-hosted runner 与命名 environment；运行入口再次检查事件、仓库和 ref。token 为 contents-read，checkout 不保留 Git 凭据；不可信 fork PR 只能进入 hosted portable 路径。workflow 未配置模型 API 或签名 secrets，也没有系统自动修复。命名 environment 本身不是 server-side protection 的证明：required reviewers、branch policy 和专用 runner 实际配置目前未核验，原生运行保持 blocked。

`impl.py gate --name implementation` 使用每个 task／suite／platform 的最新真实证据，保留历史失败；最新失败不能被旧通过遮蔽。F29 完成后还要求当前源码有 contracts／quality／unit／portable／regression 的通过证据。`--name release` 另外核对 required case／platform evidence，拒绝用开发 smoke 替代原生资格，并要求实际安全审计、production 签名核验、项目 LICENSE。未完成任务返回 fail；资源缺失与安全问题返回 blocked，均为非零。

安全检查通过独立 `dependency-security` CI job 调用真实 verifier，不用 continue-on-error 隐藏结果。显式调用 npm 官方 audit 接口（当前开发镜像没有 audit endpoint），保存实际 JSON。2026-10-07 的固定依赖仍有 2 个 high 受影响 dependency entries；官方 GHSA-86w9-cpqp-85rv 尚无 patched node-forge 发布版本。此项不会通过修改 lock 的声明消失，正式发布门禁保持 blocked。Portable CI 验证代码正确性；安全／原生／签名／付费模型结果分别记录。

失败与 blocked 同样上传机器报告和日志。Artifacts 不包含 `.forge`、用户会话、credentials 或全部临时目录。真实模型实验仍需独立明确授权／预算；该工作流没有付费模型路径。
