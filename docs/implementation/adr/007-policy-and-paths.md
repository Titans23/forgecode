# ADR 007 — 要求、实际能力和路径身份分开

策略先由可信服务规范化并冻结 JSON/hash，绑定真实工作区；根数组和域名是集合，规范化去重/排序。拒绝 URL/端口/通配符/IP 形式的域名和控制组件/凭证环境变量。写根只允许工作区或可信临时根，control data、.git/.forge 与声明的保护路径 deny 优先。服务受理/派发重编译核对 hash，旧策略规范化或 gitdir 绑定变化要求新 policy，不自动修改既有 action、turn 或政策记录。

锁定 SRT 的读取是 deny then allow-back，写入是 allow-only。allowRead=[] 不能表示严格读取白名单；要求全盘严格白名单时本 adapter 拒绝。SRT config 不提供本实现的硬内存/磁盘/PID controller，要求 hard_required 时拒绝；best_effort 的实际支持状态保留在 capability report。local-trusted 仅为显式测试/CLI 模式，也拒绝更强的 DNS/读取/硬资源要求。

能力报告兼容旧载荷，但没有 verification 的字段按 unsupported 处理。verified 必须有证据引用且与实际支持字段一致；partial 不满足强要求。生产使用的未探测报告所有隔离/清理能力都 unsupported，不把模块导入或静态编译当作原生验证。单元测试的声明矩阵仅测试编译逻辑，不能作为 Native 验收。

路径拒绝 UNC、设备、ADS、保留名称、歧义尾点/空格、特殊对象、symlink/reparse 和现有多硬链接文件。工作区预检、路径父链身份及缺失节点记录用于拒绝替换/外部创建；gitdir/commondir 解析并保护真实外部元数据，marker 的 fd 身份与大小在读取前检查。

这些检查不替代 worker 的安全 OS 打开/原生隔离。动态 .env/敏感名称由路径规则在每次操作时拒绝；SRT glob 的实际动态效果必须由 F09/F10/F11 原生 shell/file-worker 案例证明，N08 平台状态仍 blocked。现有 CLI 默认运行语义保留。

验证：unit 96 / portable 56 pass、0 skip，包括真实 Windows 10 硬链接/junction/父目录替换、6 项服务前置拒绝和锁定 SRT 实际 schema 校验；完整回归结果见任务卡。Windows 11/Ubuntu、OS 资源强制和安全文件 worker 尚未验收。
