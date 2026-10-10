# ADR021 — 安全结果包、只读导入与分块产物

沿用 forge.bundle.v1 / RunSpec / events 契约，新增 requests.jsonl、annotations.jsonl、runs/<run_id>/state.json 和 evidence.json，补足费用、运行状态、人工标注及证据缺失／变化的事实。标准库 ZIP 只支持普通 ZIP32 stored / deflate；固定配额为 expanded 1 GiB / 20000 entries / 100 MiB file / 100:1 ratio。ZIP64、split、extended-link metadata、嵌套 archive、规范路径冲突和额外文件均拒绝。先有界扫描 central directory，再冻结源、私有 staging、逐文件 hash、结构化关系校验，最后单事务发布。

导入永远为 imported_unverified。原始完整包作为 private-bundle 保存，保留原始 grades / events / artifacts；导入 run 获得新的内部 ID，保存在独立 imported_* 表，不写 runs / work_items / model_requests / usage_ledger / connections / policies。不执行代码、不加载插件、不启用包内配置。相同来源复用结果，同 ID 不同事实保留 quarantined-bundle 和冲突 hash，不覆盖旧结果。SQLite 失败只能留下可回收的无引用文件，不留下半个可见 run。

导出 freezes profile-owned evaluation runs，仅支持 run / all-evaluation scope；其他 scope 明确拒绝。metadata_only 只导出结构化事实；redacted_artifacts 额外导出可校验的 metadata JSON 脱敏副本。private-runner / controlled 原始产物不能由 Renderer 获得。evidence.json 保留 original / export hash，标明 missing / omitted / redacted_copy；原始 grader 不被脱敏副本覆盖。

Main 专用 bundle.prepare_import / prepare_export 接收可信原生选择的文件路径，生成短时、epoch / profile / source-identity 或 destination-scope/privacy 绑定的 opaque token。Renderer 不接受文件路径，也不能调用 bundle import/export/preparation。实际原生对话框由 F23 接线。导出保留 durable intent，先冻结 artifact 再 no-replace publication，响应丢失后的重试只接受同一批字节，不重新生成成绩。

migration 010 新增 profile artifact 绑定和只读导入表；沿用 Store 的迁移前真实 SQLite backup。能从旧 attempt/run_details 证明归属的 artifact 自动回填；无法证明的旧产物不猜 profile。artifact.describe / read_chunk 仅返回 profile-owned、可分享分类的实际完整性状态与 <=256 KiB 字节。报告与比较显式绑定 profile，imported run 只读报告标未验证，不能被调度；比较保留 unverified_origin 限制。

python -m benchmark.core.results report --from-bundle 仅读结构化事实，复用版本化 Decimal 指标，不调用模型、不执行代码。每份 RunSpec 至多 10000 planned trials；结构化 JSONL 至多每类 100000 records / 每行 1 MiB、manifest/evidence 16 MiB，作为额外的解析预算。RPC manifest 返回还受 1 MiB frame 上限约束，过大应拆分 scope。人工 annotations 只接受规范类别与真实证据关联，不自动判断根因。

静态 result-bundle.zip 为真实 SQLite/ZIP 路径生成的 synthetic fixture：4 planned / 5 attempts / 2 pass / 1 fail / 1 unscored；known USD 0.125 / estimated 0.05 / unknown 1 request。35 项新增行为测试实际验证文件、数据库事务、RPC 权限和离线脚本不执行；模型／原生平台／安装版验收独立记录，不用 fixture 成绩代替。
