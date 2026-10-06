# ADR 001：锁定构建栈与独立运行时

日期：2026-10-06。决定：沿用 Python Harness／CLI，新增 npm workspaces。
构建 Python 固定 3.12.13；公共最低版本仍为 >=3.12。独立 Bridge Node 固定
24.21.0，不能依靠 Electron 的 Node 模式或运行期 npx。开发主机已有 Node
22.17.1／npm 10.9.2，实际测试表明可以完成以下构建。

| 组件 | 精确版本 |
|---|---|
| Electron | 44.5.1 |
| Electron Forge / Vite plugin / makers | 8.0.1 |
| Vite | 8.3.3 |
| React / React DOM | 19.3.0 |
| TypeScript | 7.0.2 |
| AJV | 8.20.0 |
| Sandbox Runtime | 0.0.78 |
| PyInstaller / hooks | 6.22.3 / 2026.8 |

精确 npm 版本、来源、SRI、许可、runtime 要求与补丁列表记录在 release-lock；
全部传递版本记录在 package-lock 和 uv.lock。原有 Python 依赖版本未升级。
Node 两个平台归档按官方 SHASUMS256 校验，仅提取固定 runtime 成员；SRT
Windows helper、Linux seccomp helper 与 Java proxy agent 保存 SHA-256。
资产缺失、内容变化、依赖锁变化或 installed version 漂移都会拒绝构建。
native helper 不执行管理员 setup。二进制留在 .local，不加入源码提交。

选择依据：[Electron 支持策略](https://www.electronjs.org/docs/latest/tutorial/electron-timelines)、
[Forge Vite 模板](https://www.electronforge.io/templates/vite-%2B-typescript)、
[SRT 官方说明](https://github.com/anthropics/sandbox-runtime)。Vite plugin 与
Windows SRT 的成熟度限制仍保留，不能以类型检查代替原生验收。

F01 的 packaging/smoke 是真实构建探针：PyInstaller onedir 导入实际
Conversation、ToolExecutor、SessionJournal、create_runtime；Electron Forge
打包进程实际启动冻结探针，检查 API 签名、组件版本与 React 渲染。
它不构造模型客户端、不发出模型请求，也不宣称是 F13 的桌面产品。
冻结构建保留 console／stdio；Squirrel 生命周期先处理。

## 当前平台证据

| 环境 | 构建 smoke | 正式平台验收 |
|---|---|---|
| 当前 Windows 10 x64 build 19045 | 实际通过 | blocked：规范要求 Windows 11 |
| Windows 11 x64 | 未运行 | blocked：需要原生设备／VM |
| Ubuntu 22.04 x64 | 未运行 | blocked：需要原生 GUI runner 与 bwrap/socat/ripgrep |
| Ubuntu 24.04 x64 | 未运行 | blocked：需要原生 GUI runner 与 bwrap/socat/ripgrep |

验证器仍执行全部本机构建断言，然后因 OS 不在声明矩阵返回 blocked / exit 2，
同时独立保留 development_smoke=pass。签名与最终安装／卸载验收属于 F27。

## 发布阻塞

npm audit --omit=dev 发现 node-forge 1.4.0 的高危签名校验问题；
[GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv)
截至本日期列出的 patched versions 为 None。SRT 的新 Windows 路径依赖该包。
不采用 audit fix 建议的 SRT 0.0.50 降级，因为会丢失所需 Windows 方案；
不自行编写未经审查的密码学补丁，不把漏洞标成已解决。
release-lock 的 security=blocked，生产 --release 门禁拒绝；开发 smoke 可继续。
解除要求：获得修复版本或经过审查的最小补丁、锁定完整性并新增漏洞回归。
F02 等不执行该网络证书路径的离线开发可继续。

## 可恢复命令与限制

使用项目 Python；uv sync --group desktop-build --link-mode=copy，
npm ci --registry=https://registry.npmjs.org --no-audit --no-fund，
python scripts/resolve_release.py，npm run typecheck，
python scripts/impl.py verify --task F01。生产门禁：node packaging/verify-release.mjs --release。
安装与打包串行执行，Windows 会锁住已加载的 native node 模块。
首次 uv 构建扫描旧缓存慢，已为 setuptools 排除本地缓存／构建／node_modules；
重新 uv sync 成功。一次误并行 npm ci 造成文件占用和缺模块，已串行重装与重验，
失败 evidence 保留，不计 pass。不得运行未经授权的 SRT windows-install。
