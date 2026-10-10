# F06 可运行离线演示

在仓库的 Python 环境执行：

```powershell
.\.venv\Scripts\python.exe -m forge.testing.demo --output-dir .local\offline-demo
```

Linux 使用 `.venv/bin/python -m forge.testing.demo`；此命令实现可移植，Linux 实机验收仍待对应 runner。省略 output-dir 时创建新的系统临时目录；指定目录必须不存在，避免覆盖已有文件。

程序复制公开 fix-python-add 到私有工作目录，由真实 Engine RPC 创建 session、受理 turn、订阅并 ACK 事件。ScriptedModel 经现有 HarnessAdapter/Conversation/Runner 调用真实 read_file、verify、apply_patch、verify、finish_task。原始 unittest 失败，修复后同一组测试通过。再次提交同一 action 返回原 turn，模型不会再调用。

结果保存在 demo-report.json，含原生完成报告、验证历史、Journal/文件/数据库哈希和真实行为检查。报告保留加载后 history freshness=unknown；执行时通过的当前版本证据来自原生完成报告，不因重新加载而升级为新证据。

模型 origin=scripted，执行 mode=local-trusted；文件和进程实际运行，但没有 OS 沙盒隔离，sandbox_acceptance=blocked、eligible_for_benchmark=false。原生沙盒、桌面窗口及真实模型实验分别由后续任务验收。

六个公开 fixture 位于 tests/implementation/fixtures。F06 执行 fix-python-add；其他五个已有实际输入、断言和依赖任务，完整场景未完成前不记 pass。
