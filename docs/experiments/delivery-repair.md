# 交付修复 A/B 协议与实际评测

用户已授权本次升级接入现有配置中的真实模型，金额预算无上限，本阶段先在 Windows 10 验收。授权记录为 `docs/implementation/evidence/human-authorization-20261007.json`。连接探测已经实际发出一次请求并获得完整 usage；这不算任务成绩。正式 Harbor/Linux 实验仍因 Docker daemon、执行策略证明和完整冻结 RunSpec 缺口受阻。

`experiments/delivery-repair.json` 固定三道此前检查过的官方 Harbor 数据集 Python 任务及实际内容摘要，保留3题×2组×1次的六次预实验。A最多0次内部交付修复，B最多2次；两组模型调用、工具、wall、上下文与输出总预算相同，修复共享父预算。单工作者交错A/B并交替先后，seed20261007，无外部feedback，cold cache，Harbor retry0。

正式规模为12×2×2=48或20×2×3=120。正式题目必须在策略冻结前确定，排除已检查的预实验和回归题；当前正式集合与规模未选择，不能把上述三道题称独立测试集。

## 预注册与正式环境检查

```text
uv run --no-sync python scripts/delivery_experiment.py verify --output .local/delivery-readiness.json
uv run --no-sync python scripts/delivery_experiment.py run --output .local/delivery-official-blocked.json
```

两条命令验证预注册、真实官方任务证据及本机 Harbor/Docker，不请求模型。当前正式 `run` 记录未完成的环境与代码条件，不用其他执行方式伪造 Harbor 结果。修改JSON中的授权布尔值不能自行创建人类授权；明确的人类无上限政策也不会被改成零额度。未解决的正式 source/model/environment/pricing 快照仍保留null。

## 当前 Windows 10 真实 Harness 回归

固定任务目录必须来自已验证的官方任务 materialization，内容与预注册完全相同。使用新私有目录显式运行：

```text
uv run --no-sync python scripts/model_regression.py --authorize-real-model --task-root .local/f20/aider-smallset --output-dir .local/windows-live-NEW_DIRECTORY
```

这个入口实际读取配置并调用现有 Python Harness，保留 source/connection/task/policy/budget 摘要、完整 Session Journal、请求输入与wire摘要、每次尝试和最终 usage。独立 grader 与 oracle 文件未放入模型初始输入或工作目录；local-trusted 不建立宿主文件隔离证明。每个初始 stub 先由真实测试确认失败，模型交付后在独立目录使用原始 Python unittest 判分。截止时间、请求数和工具数仍有有限上限；API或工具失败、未知usage与缺grade都保留，不自动增加外部重试或选择最好结果。

这建立 Windows 10 上的受控公开任务回归，不是正式 Harbor/Linux grader，也不证明 SRT 隔离。默认自动测试和CLI未显式opt-in时不发请求。raw证据留在私有 `.local`，公开报告按隐私范围保留安全摘要。模型名字是配置网关的请求/返回观测，最终上游身份与费用未得到独立账单证明；费用为unknown，不能虚构零费用或节省金额。

## 从实际结果包复算

```text
uv run --no-sync python scripts/delivery_experiment.py report --from-bundle RESULTS.zip --plan-a A.json --plan-b B.json --run-a RUN_A --run-b RUN_B --output .local/delivery-paired-report.json
```

plan来自实际配置导出，含RunSpec、spec_hash和全部resolved_snapshots。报告校验schema、snapshot哈希、bundle中的run hash及完整计划分母；只有repair cap允许不同。模型、数据、预算、环境、协议或其他Harness配置不同会返回incomparable，CLI非零退出。

按题／repeat配对，选最后一次获准attempt，拒绝best-of旧结果替换。报告保留trial、attempt、request元数据及调用／费用汇总、全部未评分项和缺失证据。未知费用保持unknown，零成功cost-per-success为N/A。同一bundle与plan重复复算保持一致，不写数据库或执行任务。

全部有独立grader证据且至少两个task cluster时，以固定seed、2000次task-cluster percentile bootstrap计算95%区间；缺grade区间为null，少量题目标为exploratory。导入包即使校验通过仍是imported_unverified，不建立模型或grader来源信任。

合成grade测试只验证统计和来源规则。它们经过真实SQLite、ZIP导出和离线报告器，不作为公开基准成绩或原生沙盒验收。

## 本次实际结果（2026-10-07）

完整冻结批次实际6attempt／50模型请求，A组独立测试3/3通过，B组2/3通过；B的proverb返回字符串而原始grader要求列表，8项测试全部失败，Harness却判completed。A的transpose独立测试通过，但验收原文引用不足导致partial。上述区别保留在报告，不以内部完成判定替代独立grade。

按三个task cluster复算B−A为−1/3，固定seed的95%探索区间为[−1,0]；少量且先前检查过的任务不能证明总体效果或修复收益。全部费用unknown，原始请求／尝试／grader日志在私有目录保留，安全索引见[实际模型证据](../implementation/evidence/F31-real-model-observations.json)。

首批因执行期间刷新受源码指纹约束的客户端development-assets清单而被判不可比，保留4attempt／33请求／4评分，不进入上述成对统计。两批共10attempt／83请求，已观测完整usage合计864743 input／15909 output token，缓存token0；另有F28连接探测1请求335/10 token。第二批离线逐项复算一致，不合并两批选best-of；正式Harbor评分仍为0。
