# ADR 019：不可变评测计划与独立终态

F19 在既有 Harness、Engine SQLite 和工作队列上实现评测，不复制 Agent 主循环。创建 action、冻结配置和全部 task × repeat trials 原子受理，未领取项保留在 planned denominator；validation ticket 绑定实际 snapshot hash 和 profile。

一个 Engine worker 串行领取 turn／attempt。领取时保存 owner epoch、deadline、monotonic start、heartbeat 和 trace；旧 owner 回调拒绝。取消实际 executor，未得到可信清理证据保留 reconciling。重启生成恢复 trace 并 link 原 trace，不重跑未知副作用。

execution、grading、cleanup 分开。评分仅来自持久 authoritative grade，Agent completed／stdout 不等于成功。基础设施重试须显式列出类别、仍有固定预算并已确认清理；末次授权 attempt 为 final，first attempt 独立保留。任务逻辑失败、未确认清理和未知副作用不自动重试。

报告从 SQLite 和唯一 request ledger 复算。计划成功率、覆盖率、已评分成功率、first attempt、false positive 和 trace 完整率都有分子分母；Decimal 费用区分 known／estimated／unknown。零成功为 N/A，缺 usage／价格或混合币种不能假定零费用。Harness／source 可声明处理变量，其余配置差异使对比不可比；不自动输出提升或统计显著性。

attempt journal 复用原 fsync／完整性／metadata projector，恢复和回放不重复费用。桌面资源 inventory 增加实际 Engine 依赖 benchmark/core；现有 CLI 和公开 create_runtime 签名保持。migration 009 使用既有迁移备份流程。

验证：unit 142、portable 184、regression 1070，零失败／零跳过；Windows 10 development Electron 44 checks。实际 Harness、真实 owned Python 进程／heartbeat／取消和受控独立评分 fixture 通过；不称为公开 benchmark／真实模型实验。证据编号见 F19 任务卡；父 HEAD fdbcf50047d290d6c765aaa8ac7b5da77b26b95c，dirty a1117ffe4bc74b91738fe6b07c69d8e3eeee2ec217664f680442140e828616f4。

Windows 11／Ubuntu 原生验收、installed frozen Engine、管理员 setup、签名、付费 API 和 G7 继续 blocked。默认 executor 尚未绑定官方 Runner，兼容性检查明确 runner_unavailable；F20 接入真实 Harbor。当前实际 Harbor 0.18.0 可用，Docker daemon unavailable，不启动外部环境或模型。
