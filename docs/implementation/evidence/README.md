# 冻结评测输入

这里仅保留 `experiments/delivery-repair.json` 仍引用并校验内容的两个固定输入：

- `F20-20261006T223459Z-harbor-report.json`：冻结任务集的来源。
- `human-authorization-20261007.json`：该协议的原始授权输入。

不要修改它们的原始字节，也不要据此复用历史模型预算。
新的验证报告由脚本写入已忽略的 `.local/implementation/evidence`；不再写入此目录。
