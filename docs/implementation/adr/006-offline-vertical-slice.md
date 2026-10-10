# ADR 006 — 真实工具的离线纵向切片

F06 复用 F05 的 test profile。脚本必须 forge.scripted-model.v1 / origin=scripted，最多 32 步、64 次工具调用，并有显式工具白名单。脚本审批只匹配已声明的调用，hard deny 仍生效；生产审批由后续可信主进程流程实现。

forge.testing.demo 把公开错误项目复制到新目录，真实 RPC 创建 session/受理/订阅并驱动既有 Harness。实际读取源文件和测试、运行 unittest 得到失败、apply_patch 修复、再次运行同一测试得到成功，完成报告引用该版本的验证 ID。Native Journal、SQLite 与产物哈希保存于 demo-report.json。历史重新加载仍为 unknown freshness，执行时的报告仅保留当时验收事实。

显式 local-trusted 工具操作实际执行，OS 沙盒验收标 blocked；origin/mode 和 eligible_for_benchmark=false 始终保留。desktop/evaluation 不能加载脚本，默认 provider/strict。不用演示或手算 fixture 生成外部模型成绩。

六个 fixture 提供实际输入、版本化 manifest、明确断言和后续任务。F06 完整运行 fix-python-add；长进程清理、过期证据、受限路径、模型中断/缺 usage、mini bundle 的产品场景仍需对应后续验收。fixture 中故意错误的 unittest 文件不由 pytest 自动收集，演示通过真实 unittest 运行；已有离线回归保持覆盖。

Unit 54、portable 49 pass，0 skip；完整回归结果见任务卡。旧失败索引保留，不作为通过证据。演示覆盖丢失 start 响应后 drain/restart/action.get/重试的真实持久幂等，不重复 turn 或 Journal。
