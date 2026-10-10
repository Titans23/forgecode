# F21 手算结果包

result-bundle.zip 是真实 SQLite 受理、排队、grade fixture、重试和 ZIP 导出路径生成的静态测试包。RunSpec 的 model_mode 为 scripted_mock；没有调用模型、Docker 或原生沙盒，不是公开基准成绩。

4 道计划题：pass、fail、unscored、先基础设施失败后 pass；共 5 次 attempt。planned success 2/4，grading coverage 3/4，first attempt success 1/4；3 次 internally completed 中有 1 次 grader fail。

三个独立 synthetic ledger request：actual USD 0.125、estimated USD 0.05、unknown。未知请求使 cost per success 为 unknown；不能把 0.125/2 当成完整成本。重复导入不增加本机 ledger，重新复算必须与 bundle-expected.json 一致。

包中 trusted_engine 是原始采集方的声明；离线读入和导入始终显示 imported_unverified。校验和证明字节一致，不能证明评分或采集来源可信。
