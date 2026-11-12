# ForgeCode 共享契约 v1

本目录是 JSON Schema 2020-12 的维护源，覆盖旧 Engine RPC、独立 Bridge、事件、执行策略、RunSpec、配置、审批、能力和归档格式。产品 1.0.0 不改变这些格式的独立版本号。

普通 CLI/SDK 不依赖旧 RPC 控制面；内核与 worker 仍复用中立的 `forge.protocol` 校验器。`methods.json` 描述权限、幂等、提交点、超时和测试引用，独立 Bridge 使用自己的方法表。登记为 `contract_only` 的条目不表示已有可调用 handler。

```sh
uv run --no-sync python scripts/check_contracts.py
uv run --no-sync python scripts/check_contracts.py --check
npm run contracts:check
```

生成目标是 Python schema bundle 和 `packages/contracts/src/generated`；不要直接编辑生成文件。Python 使用锁定的 jsonschema，TypeScript 使用 AJV，双方执行同一组结构样本。样本不是模型表现或 benchmark 成绩。

JSON 解码拒绝重复键、非有限数、超出 JS 安全范围的整数、孤立 surrogate、超大帧和过深嵌套。`forge.canonical.v1` 以 Unicode 标量键序、紧凑 UTF-8 JSON 和原数组顺序计算摘要；非整数配置采用规范十进制字符串。

资源归属、文件身份、审批、快照解析和清理由实际受信服务校验；`origin` 字段本身不授予权限。旧预算字段仅为历史归档读取保留，不为当前 Harness 配置累计限制。新接口应先明确调用者与边界，再更新 schema、实现和对应行为测试。
