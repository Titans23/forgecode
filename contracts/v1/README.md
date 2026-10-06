# ForgeCode V4 共享契约

本目录是 JSON Schema 2020-12 的维护源。48 个 Engine 方法的 request/result、6 个独立 Bridge 方法、41 个事件 payload、Policy、RunSpec、配置快照、审批、能力报告、Artifact 和 Bundle 均有 schema。methods.json 保存权限、幂等、提交点、超时查询、错误和测试引用；Bridge 使用独立表。

运行项目 Python 的 `scripts/impl.py contracts` 生成 Python 运行时 schema bundle 和 TypeScript schema/types。`scripts/impl.py contracts --check` 或 `npm run contracts:check` 只检查漂移。生成文件不能单独修改。Python 使用 jsonschema 4.26.0，TypeScript 使用 AJV 8.20.0 / ajv-formats 3.0.1。

`validate_request` / `validateRequest` 校验每个 RPC 请求及受信通道权限，`validate_event` / `validateEvent` 同时校验 envelope 与已登记 payload。UTF-8 JSON 解码拒绝重复字段、非有限数、超出 JS 安全范围的整数、孤立 surrogate、超 1 MiB 帧和超过 32 层容器。哈希使用 forge.canonical.v1：Unicode 标量键序、紧凑 UTF-8 JSON、保留数组顺序，非整数配置必须使用规范十进制字符串。

examples.manifest.json 保留原四种 seed 正反例并新增 envelope/Bundle/error 样本；method-fixtures.json 和 additional-fixtures.json 包含 310 个 Engine、Bridge、事件与快照结构样本。两端运行同一组输入，不运行模型或项目命令。

这些是契约和真实校验器。登记项的 implementation_status=contract_only 不表示业务 handler 可调用。F04/F05/F08/F17/F19 分别接入实际服务、传输、Bridge、事件记录和快照解析；能力列表必须由已实现 handler 生成。origin 字段没有授权效果，资源所属、文件身份、快照解析、审批与预算仍由受信服务验证。示例 scripted_mock 仅用于测试结构，不能形成真实 benchmark 成绩或授权花费。
