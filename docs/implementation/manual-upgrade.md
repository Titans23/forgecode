# ForgeCode V4 开发预览构建与人工升级

当前预览构建来自 Windows 10 19045。它不是 Windows 11 或 Ubuntu 原生验收结果；平台状态以 docs/implementation/progress.json 和 evidence 为准。应用自带 Engine／Node／Bridge；项目 Git、Python、Node 仍需按项目实际需要安装，由 doctor 单独诊断。

## 从已锁定的开发环境构建

```text
python scripts/check_contracts.py --check
python scripts/build_bridge.py
python scripts/build_desktop.py
python scripts/package_engine.py --build-id <unique-build-id>
python scripts/assemble_release.py --build-id <same-build-id>
python scripts/impl.py verify --task F27
python scripts/impl.py verify --suite regression
```

使用仓库 .venv 中的 Python；npm ci／uv sync 按已锁定依赖准备。Windows installer 由真实 Squirrel maker 生成；Linux deb 必须在原生 Ubuntu 22.04 x64 且具备 fakeroot／dpkg 的构建机生成，再在 Ubuntu 22.04／24.04 的 X11／Wayland 目标机验收。跨 OS 不能重用 frozen Engine，也不能从 Windows 宣称 deb 通过。产物在 .local/desktop-packages/make，resources 和 release-manifest 在 .local/desktop-resources。Manifest 中保存真实 build、source inventory、平台、许可、SBOM、hash 和签名状态；源码改动后重新构建。

## 人工升级

1. 下载来源只使用项目维护者确认的 GitHub release 页面 https://github.com/Titans23/forgecode/releases，并核对独立可信的版本／签名／校验值。当前 unsigned developer-preview 清单不建立生产真实性；不要把同一下载源的 hash 文本称为签名。没有独立信任依据时不替换已安装组件。
2. 在客户端停止受理，排空或取消全部任务，检查 cleanup／未知副作用已对账，退出客户端和 CLI。独占 writer 未关闭时备份命令拒绝继续。
3. 在旧版成组资源仍完整时，用旧 Engine 创建一致备份。路径替换为真实安装 resources／数据／独立备份目录：

```text
<old-resources>/engine/<old-build>/forge-engine[.exe] upgrade prepare --data-dir <data> --current-resources <old-resources> --target-resources <new-resources> --backup-root <separate-backups>
```

4. 保存命令返回的 backup 路径，核对 target 版本，再按平台安装器人工安装整组 UI／Engine／Bridge，运行 doctor 和启动诊断。安装成功不等同 native sandbox ready；SRT 全局 setup 需单独用户授权。
5. 失败时保留现场和新数据库。回滚到匹配旧版本的备份，新目录恢复，不覆盖现有数据：

```text
<old-engine> upgrade restore --backup <matching-backup> --destination <new-empty-data-directory> --resources <old-resources>
```

恢复后的旧 schema 数据可由匹配旧版读取；旧版遇到更新 schema 必须拒绝写。不要只替换单个 Engine／Bridge，不删除原数据库或 Journal。

## 卸载与当前限制

普通卸载只移除本应用安装范围，默认保留用户工作区、实验、凭证、数据和共享 SRT。删除数据是另一项明确确认；不运行共享 SRT uninstall。安装事件参数已在 Main 提前处理，避免启动 Agent。

当前没有项目 LICENSE、签名证书，且 SRT node-forge 依赖安全阻塞尚未解除。本地构建仅标 developer-preview；正式分发／签名验收 blocked，需维护者明确许可证并补齐独立签名与安全证据。Windows 11／Ubuntu 干净机安装、卸载、系统沙盒边界和人工原生对话框仍待对应环境；测试不会伪造这些 pass。模型 smoke 使用 scripted 响应但经过真实工具与 Harness，0 付费调用，不是公开基准成绩。
