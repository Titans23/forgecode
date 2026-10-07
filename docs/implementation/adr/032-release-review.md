# ADR 032：实际发布审查与可恢复用户命令

## 决定

使用真实 CLI/stdio Engine/Harness 和现有构建链，提供固定 Node 资产及 preregistered Git 任务的物化入口。固定 archive/member/hash 校验不改锁、不覆盖现有资产；任务校验 Git/content/Harbor checksum，不执行模型或 grader。所有文档命令须在新检出副本实际执行。

原子文件发布保留 Main 的窗口/父目录身份校验，但纯文件模块不导入 Electron，使冷安装测试只验证实际文件行为。Windows 测试按真实 canonical path 比较，PowerShell 显式脚本保留有限冷启动预算及进程诊断。CI 已同步全部开发依赖后使用 --no-sync，避免后续入口改变环境。

最终 gate 增加 remaining_implementation 校验：阶段接口 implemented 与完整必需功能分别表示。F28 native promotion/清理/敏感路径，F31 正式执行绑定未完成时，implementation 与 release 都保持 blocked；security/native/signing/LICENSE 条件继续独立阻挡。

## 依据

实际 F28 hosted CI 的冷 Electron 导入、Windows 8.3 alias 和 PowerShell 冷启动失败可复现；public F32 定向 Python 50、Node24 21 检查已经通过。新检出副本、完整构建及最终回归随后记录各自实际证据，不把这些定向结果当原生产品验收。

## 后果

固定资源可重复恢复，未知或被修改的字节 fail closed。用户得到可运行开发版本与明确缺口；unsigned developer-preview 不会因阶段标签变成正式稳定发行。保留全部失败历史和实际模型请求，费用 unknown、正式 Harbor/holdout/native 缺失分别报告。

## 最终验证

候选79b43a10c7a6b59a1c1fe47ac19441300ca655d6/source bbd5f9a...：unit223、portable381、全仓1348、contracts2、quality4全部通过，零skip。实际冷安装/打包并行首轮的10秒Renderer initialize超时与900秒portable无JUnit失败均保留；只给初始化60秒、Bridge父90秒、portable验证器1800秒，未放宽业务权限或模型/工具预算。相关2专项9.75秒通过，串行port8:41/reg13:37通过。源码副本12命令、24日志hash、3锁和demo原产物已复算。当前implementation/release gate各blocked、0errors：代码缺口与产品资源条件真实保留。
