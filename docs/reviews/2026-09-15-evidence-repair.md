# 验证反馈与实测关系断言修复

本轮针对 c2/c6 对比中可由框架直接处理的问题：已有断言被统称“没有断言”，提交前反馈缺少关联检查与具体失败，输入变换测试只断言格式或顺序。没有更改官方测试、历史产物、reward、模型配置或评测并发，没有启动新的模型请求。

## 改动

1. **共享验收诊断。** `forge/runtime/acceptance.py` 根据原始证据区分 unverified、assertion_binding_missing、stale_evidence、check_failed、execution_contract_unestablished、recorded_assertion_passed。CompletionGate 和 review_delivery 共用此逻辑。原有独立的历史失败、路径和调用方约束继续生效，记录中的通过断言不等于最终验收允许。
2. **立即解释验证结果。** verify 对 unknown、structural、negative 给出说明，指出现有断言是否因执行语义不足而不能构成正向行为证据。保留实际退出码与断言记录，不把 unknown 自动放宽成通过。
3. **可操作的提交前报告。** 按需求列出原文、预期检查、状态、检查 ID、最近的断言、失败诊断、局限与下一步操作。优先显示尚未建立证据的需求，最多 12 项、每项最近 3 次运行、每次 8 条断言，详情约 24K 字符；完整定义仍可通过 task_get 获取。长诊断使用截取内容，不将截断命令冒充可直接重跑的完整命令。
4. **实测关系断言。** OutputCheck 新增 delta_eq，直接验证两个输出键的差是否等于期望差值，支持有来源依据的绝对容差。缺失、布尔、非数值和非有限观测均不能通过。结果不符时，即便程序退出 0，也标记 evidence_valid=false。
5. **保持检查修订约束。** reference_key、expected、tolerance 均属于不可暗中放宽的断言合同。supersedes 和 inherit_checks_from 不能通过改参考字段或扩大容差消除旧失败。旧 eq/ge/le 序列化不增加默认字段，保留历史断言身份。

## 使用示例

检查程序实际对原始输入和变换后的输入运行被测实现，末行输出：

```json
{"original":20,"transformed":32}
```

断言示例：

```json
{
  "key":"transformed",
  "operator":"delta_eq",
  "reference_key":"original",
  "expected":12,
  "tolerance":0,
  "requirement":"输入平移后，响应位置应平移同样的距离",
  "requirement_id":"实际登记的需求ID",
  "expected_source":"来自任务定义或独立参考的变换关系"
}
```

如果 transformed 为 25，两数虽然有序，该断言仍失败。示例不包含任何评测隐藏答案。变换关系本身须有独立依据；只有自洽性检查仍不足以证明所有语义正确。

对于源包或组件精确身份，继续使用 eq 比较实际摘要与独立参考摘要。提示明确指出版本文字和文件存在不能替代身份检查。框架没有内置 benchmark 的官方哈希，也不会凭名称决定应下载哪一个归档。

## 验证与剩余边界

测试覆盖状态分类、CompletionGate 保持拒绝、零退出码但实测关系错误、容差边界、继承时拒绝放宽、历史断言序列化、需求关联诊断及不终止/不修改任务。

最终 Windows 全量回归 **707 passed，2 warnings，209.86 秒**。此前针对性回归 69 项通过；新增测试单独 28 项通过，输出中确认存在 pytest 缓存目录写权限警告，不影响测试断言。`compileall`、`git diff --check` 通过。测试使用工作区内隔离状态及临时目录。

- [最终全量日志](D:/projects/forgecode/docs/reviews/2026-09-15-second-repair-final.log)
- [针对性测试日志](D:/projects/forgecode/docs/reviews/2026-09-15-second-repair-targeted.log)

这轮没有自动修改视频、电路、MIPS 或 POV-Ray 的历史解答；这些是代理生成方案的正确性问题，新增反馈与检查能力有待后续受控评测验证。服务端 server_error 的根因尚无服务端证据，本轮没有通过增加重试或删除失败样本来掩盖它。也没有把内部 unmet 标签改成 met 来人为提高表面成绩。
