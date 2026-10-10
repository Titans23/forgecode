# ADR023 · 不可变实验向导与实际结果浏览

- 四步向导使用已有完整配置或 Main 原生选择导入的 RunSpec + resolved snapshots。新安装缺少配置时明确提示；不从未知源码、工具链或价格生成虚假身份。
- 每次变更生成实际不可变快照与新 run；预算检查／兼容性 ticket 在创建前执行。更改目标只保存 requested / unverified 环境，不能把原 Linux 或 Docker 测量写成 Windows 原生事实。
- migration 012 保存 profile 配置归属与 immutable template。历史本机 run 回填已证明的归属；导入来源永久保留 imported_unverified，改变 experiment ID 也不授权运行。
- 原 F19 调度器／Harness／官方 Harbor adapter 不替换。修复 Harbor execute(work, scheduler) 签名；兼容性包含来源门禁，领取后的复核仍阻止未验证配置和未授权模型执行。
- 报告采用只读有界分页，保留 planned 分母、first attempt、全部 attempts、独立 execution / grading / cleanup。选定结果仍为 last_authorized_attempt，没有“最好成绩”选择器。
- A/B 对照先返回实际控制条件差异，再按 task ID / revision / repeat 展示两个真实 attempt Trace；查询按 attempt_details 的真实 trace ID 归属。外部结果明确只读，不能用其事件伪造可信本机 Trace。
- 导入／导出路径仅在 Main 原生对话框中产生，窗口／frame／Engine epoch 在等待后重查。结果 ZIP 使用 F21 的 staging、profile token、限额与事务发布；配置 JSON 有 1 MiB 上限、引用 hash 检查和秘密拒绝。
- 文件保存使用实际 private temporary file + fsync + no-replace hard link，不覆盖已有文件。失去窗口授权时只清理仍证明归属的临时文件；拒绝目录链接／junction 和路径身份变化。
- 配置导出保留旧连接 revision，不要求旧连接仍可执行；导出本身不发起 API。产物删失会改变复核提示，不改变原始独立 grade 或指标。
- 正式模型小集验收需要实际 Docker daemon、F30 支出控制／策略对账和用户预算授权；当前 blocked。离线协议测试可用确定性模型驱动真实 Harness、SQLite、Journal 与独立评分进程，单独标记，不称为公开成绩或 OS 沙盒验收。
- Windows 11／Ubuntu／安装版／人工原生对话框选择仍独立 blocked。虚拟列表和 RPC 页面有界；全体指标计算复用 F19 的报告读路径，未宣称大实验性能指标。
