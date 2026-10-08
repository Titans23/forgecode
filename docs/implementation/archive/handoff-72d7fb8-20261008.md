# ForgeCode V4 交接 — 2026-10-08

## 当前续作（2026-10-08，Asia/Shanghai）

最新工作流提交12cc51d5d9e22cb7c70d972612186893ca34fd87已正常推送并核对远端。实际run37716477000已完成：Ubuntu22.04、Ubuntu24.04、Windows2025分别contracts2/quality4/unit225/portable400通过，0skip/failure/error，三个freshCIgate与真实浏览器用例均通过。四artifact、四job日志、六JUnit及JSON检查已取回复算，公开索引F29-electron-sandbox-hosted-observations.json。独立安全审计仍为2high/exit2，整体workflow为failure。

人类已明确回复“允许，仅限上述CI”：唯一管理员设置例外是本仓库codex/forgecode-v4分支push触发的GitHub临时Ubuntu24.04 runner，将锁定Electron helper设置为root:root/4755。实际job/API证明该步骤只在获批Ubuntu24执行成功，其他两个job跳过设置；每个测试套件本身均0skip。授权记录human-authorization-ci-20261008.json。本机/原生SRT/系统setup仍未授权，本轮0本机管理员操作、0模型请求。

source5ab31ee4的本机15项CI专项、contracts2、quality4、unit225也已全部通过。工作流之外产品代码保持231532f；上一轮source7d77f873的全量regression1369保留原scope，不转为新源码指纹的fresh全量证据。F28原失败run37714511897保留了真实SUID配置诊断、四artifact/四log/六JUnit；F29已修复该CI问题。最终后续提交只保存证据/文档，使用[skip ci]避免为记录同一结果再启动一轮；不声称该文档提交取得新CI通过。

当前没有未结束的验证/模型工具会话。下一核心任务仍为F28完整初始化SRT资源清理、原生verified promotion和动态敏感路径一致性，以及F31正式环境/网络策略/可信逐请求账本绑定；F28/F31保持in_progress。Windows11/Ubuntu原生证明、Docker daemon、签名/LICENSE、安全依赖与性能目标仍有缺口。现有模型授权/金额无上限保持，不重跑既有成绩；无关未跟踪文件和旧四份证据继续保留。

### 前段续作记录（当前状态以上文为准）

F31续作已提交并正常推送9ac93b0c4053bb9d9137b70bf20365005b1e8e6a，ls-remote核对一致。实际run37654014565已结束：Ubuntu22.04和Windows2025各unit225/portable400通过；Ubuntu24.04 unit225通过、portable399通过/1浏览器report缺失失败，security继续失败。四份实际artifact/JUnit与四job日志已取回，公开索引F31-binding-hosted-observations.json；全workflow仍failure。

当前F28修正terminal/indeterminate execution漏清理与单项失败中断后续cleanup：3项Node before全部失败、after全部通过；真实Bridge重建仅更新自身runtime manifest摘要。原session15060随中断结束，portable20261007T164801Z-44b7ebf2只有部分日志、无JUnit/正式evidence；不能记pass。恢复后已确认无原Python/Electron进程，不再轮询旧session。另补浏览器报告缺失时的退出码和有限stderr，现有成功条件保留；6项Bridge/浏览器真实集成通过。新的五套件session70763正在执行，输出.local/F28-close-final-verification.json/log；保持源码/测试/锁/清单冻结，完成后提交上传。旧3份已通过短套件属于source d00c8024，保留而不当新source报告。

最终验证更新：session70763已结束exit0，source7d77f873a9ef70aa5d448ed99d61da58a59f66ff22edd707210f5cb2ec74f2af的contracts2/quality4/unit225/portable400/regression1369全pass，0skip/failure/error，freshCIgatepass。5当前+3中断前完整报告复算通过，110映射有效、156平台证明仍blocked；原中断portable不计入完整报告。最终ID：20261008T011752Z-765e3b92、011759Z-355b65b0、011817Z-66e79502、011906Z-3fad7eb6、012857Z-c6dfbf8f。下一精确stage本轮5个代码/测试/清单文件及文档/evidence，commit/push，核对远端并取得新Ubuntu24浏览器退出诊断；不重建旧安装包，不运行管理员setup或付费模型。

旧聊天已返回 completed/idle，明确释放工作区；当前聊天接管。3a51097 已正常fast-forward推送并用ls-remote核对。对应GitHub run37642586381首次startup_failure、0 jobs/0 check-runs；官方API单次rerun返回201。attempt2实际Ubuntu22.04/Windows2025 portable通过，Ubuntu24.04一个真实浏览器测试未产出report，security仍2high失败。四个job日志、四份artifact ZIP和JUnit摘要已取回，索引F32-remediation-hosted-observations.json。现有失败栈未保留Electron退出诊断，不能推断具体根因，也不称workflow全绿。

当前F31源码/RunSpec绑定补齐进行中：共享resolved snapshot语义检查；离线freeze生成A/B/read-only计划和清单；真实冻结/暂存包含catalog且核验完整内容；物化前重验；首模型请求前检查model/参数/prompt/tool schema。F31仍in_progress；官方政策、宿主授权/逐请求账本、无上限正式预算表示、Docker/环境证明仍未完成。0新增模型请求/管理员操作。之前实际83请求和A3/3、B2/3历史保持，不重跑挑成绩。

本轮定向初始8项失败全部复现（7项导出校验、1项借用宿主模块），新增freeze/源码核验各2项初始失败及一项测试消息正则失败均保留。最终source92c78581312ad297ca5507e7b739e5ffdf6c2bd72d8ac5340656c203d9aae0db：contracts2、quality4、unit225、portable400、regression1369全pass，0skip/failure/error；五份报告独立复算、freshCIgate通过，session38375已退出0。证据ID依次20261007T154114Z-26ded72b、154120Z-2a46ead5、154135Z-0a1ccbae、154224Z-82b552ed、155239Z-37f6cb63；完整记录F31-frozen-plan-observations.json。build_desktop已刷新development-assets；未重建旧exe。下一精确stage本轮文件，commit/push并检查新hosted结果，再继续F28/F31必需代码。无关未跟踪文件和旧四份10:33证据仍保留，不打包提交。

## 上一聊天移交快照（2026-10-07 15:14 UTC，以下已由当前续作更新）

收到迁移通知后已在安全工具边界停止开发。本段为最新状态；下方旧记录仅作历史，不得把旧的“正在运行”“待测试”或“未授权模型”当作当前状态。没有启动新的源码修改、模型实验、回归或管理员设置，没有创建新聊天或向其他聊天发消息。迁移通知指定新聊天使用 GPT-6 Astra / Max / Fast 关闭；既有人类授权继续有效。

### 本地提交、远端与工作区

- 分支：`codex/forgecode-v4`；工作区：`D:\learn_project\forgecode`；远端：`https://github.com/Titans23/forgecode.git`。
- 最新本地 HEAD：`3a51097d77e012abe297fb05893ec6e9dfba84ab`，提交完整复验文档与证据。已测试的源码提交为其父提交 `a523a7887a238fa7f52bd6be625d71ab4d0625bd`；最新 HEAD 仅增加文档/证据，源码指纹仍为 `9f9a924db65db866dcbe3971bfff2b8c74213456331801df8b4bf5ba7e283d1f`。没有待提交产品源码。
- 15:14 UTC 实际 `git ls-remote` 再次核对：远端仍为 `dd17e3488103008957ed88bfff764cbe27694a94`。本地两个新提交尚未上传，不能把 3a51097 或 a523a78 写成 GitHub 已有版本。
- 三次上传分别于 15:07:55、15:08:36、15:09:21 UTC 被 GitHub 远端 Internal Server Error / HTTP 500 拒绝；第三次明确使用 HTTP/1.1。Request ID 依次为 `9C88:7B98C:A1CF98:D73B06:6AC66049`、`D912:A1665:A0F622:D5F87E:6AC66072`、`8FD6:9E9B8:2FD4E9:3F3453:6AC6609F`。这是上传阻塞，不是测试失败或自动审批拒绝。官方状态页当时未报告全局故障，不推断全局宕机。
- 只读 GitHub API 同样确认 ref 为 dd17e34，查询 3a51097 返回 404。已阅读官方 Git database trees/commits/refs 接口作为潜在备用上传方式；没有执行 API 写入，没有创建上传脚本，没有强推或改写历史。
- 本次交接只修改 `docs/implementation/handoff.md`，留为未提交文档变更供新聊天读取；不为迁移再启动推送。其余既有未跟踪文件保留：`.forge/`、`build/`、`update_implementation_pack/`、`forgecode-architecture-8k.png`、`forgecode-architecture.png`、`forgecode-architecture.svg`、`svg_probe.txt`，以及 evidence 中 `20261007T103345Z-a7c89b8b` / `103355Z-bb8cd159` / `103520Z-669445a7` / `103613Z-0bd17f57` 四份旧记录。它们未进入本次提交；不得 `git add -A`、清理或覆盖。

### 最终本地测试与证据（全部已经结束）

- 固定环境：Windows 10 x64 build 19045；`.venv/Scripts/python.exe` 3.12.13；正式 Node 为 `.local/release-runtime/node-24.21.0-win-x64/node.exe` 24.21.0。系统 PATH 默认 Node 22.17.1；正式复验在当前进程 PATH 前置固定 Node 目录，不改系统设置。
- 五层正式验证全部 PASS，零 skip/failure/error：contracts 2（`20261007T143907Z-f2d2193d`）；quality 4（`20261007T143911Z-01c04c24`）；unit 225（`20261007T143926Z-173a661a`）；portable 382（`20261007T144011Z-c6c40c46`）；regression 1351（`20261007T144844Z-d7aa5443`）。全部绑定 a523a78 和上述 9f9a924 源码指纹。
- 87 项相关专项 PASS；真实 `scripts/ci_bootstrap.py` 已完成锁定 Electron 下载与 contracts / Bridge / Main / Vite 构建。本机 fresh CI gate PASS。没有用 Mock 或静态返回替代执行。
- 公开事实索引：`docs/implementation/evidence/F32-ci-remediation-observations.json`、`F32-first-hosted-ci-observations.json`；私有完整报告：`.local/F32-ci-fix-final.json` / `.log`、`F32-ci-fix-gate.json`、`F32-ci-fix-focused.xml` / `.log`、`F32-ci-bootstrap-fix.log`。31 份实际 F32 报告已复算，另有一份历史无 JUnit 超时 FAIL 独立保留。
- 37 项已有 portable 证明重新绑定当前报告，未提升 native 验收。acceptance `20261007T145332Z-695f9ba9` 仍 BLOCKED：110 引用有效、156 项平台证明缺失。最新 release `20261007T150635Z-a27c64ba` BLOCKED：280 条条件、0 evidence errors；implementation gate 同样 BLOCKED、0 errors，包含真实 F28/F31 必需代码缺口。
- gate 索引中间两次真实 FAIL（`20261007T150406Z-81c7bee6`、`20261007T150523Z-0a7b7434`）均保留：首次缺新增测试索引，第二次派生审查反向输入自己的 gate。只修正文档索引；派生报告放在 `gate_review_evidence_ids`，不作为测试输入证明，未更改产品门禁或掩盖测试失败。
- 所有 verifier/live/build 会话已结束，没有待等待的本任务后台工具；不要轮询旧 session。`.local/F32-ci-fix-finalize.py` 曾按 HEAD=a523a78 完成复算；当前 HEAD=3a51097，不能原样重跑其当前 HEAD 断言后误判源码变动。

### GitHub CI 实际结果与本轮修复

- 最后已上传 dd17e34 的真实 run：`37636301297`，`https://github.com/Titans23/forgecode/actions/runs/37636301297`。四个 job 全部 failure：Ubuntu 22/24 各 unit 223 PASS、portable 367 PASS / 14 FAIL；Windows Server 2025 unit 223 PASS、portable 379 PASS / 2 FAIL；security 实际 2 high。日志、artifact ZIP、JUnit/hash 已取回复算，原始目录 `.local/F32-remote-ci/`。
- a523a78 已修复真实原因并完整本地复验：development Engine 保留 venv Python 启动别名并独立校验目标 hash；明确调用锁定 Electron 44 官方 installer；F25 工具 schema 分别核对原提交 e37e09a 的 Windows/POSIX 描述；catalog JSON 用 ASCII 转义保留 Unicode 值。新增两个 Python unit、一个 integration 和真实 Node venv 启动行为。
- 修正提交尚未上传，因此没有它的新 hosted CI 结果。`.local/F32-ci-remediated-remote/index.json` 空列表不证明通过；不能用本机 portable PASS 或 Windows Server runner 替代 Linux / Windows 11 原生验收。
- 只读 helper：`.local/inspect_remote_ci.py --commit <实际已上传完整SHA> --output .local/F32-ci-remediated-remote`；完成后再加 `--logs --artifacts`。`--output` 接受目录。认证只在内存读取，禁止打印 token/完整私有配置或放入命令参数。原始 artifact 可用 `.local/F32-hosted-finalize.py --directory <目录> --commit <SHA>` 复算。

### 尚未完成的实现和环境阻塞

- F28 必需代码仍缺：严格原生 capability verified promotion、已初始化 SRT/helper/listener 的完整清理、动态敏感路径 Shell/File 策略一致性。strict 继续在模型请求前拒绝，不能用 host fallback 或伪 native 结果解除。
- F31 必需代码仍缺：官方 Harbor 的 RunSpec / source / model / environment / policy / 可信宿主授权及逐请求账本绑定。正式 live 实验仍拒绝，已有 Windows 原始任务实验不等同官方 Harbor 成绩。
- Windows 11 工作站、Ubuntu 22/24 X11/Wayland、原生沙盒、干净系统安装/升级/卸载、OS keystore、suspend 和手动原生对话框等 156 项证明仍缺。当前验收先 Windows 10，不把 GitHub Windows Server 当 Windows 11 客户端验收。
- Docker CLI 29.7.2 存在但无 Linux daemon；管理员系统安装/设置未授权。签名材料与项目 LICENSE 缺失。SRT / node-forge 两个 high dependency entry 属于同一 `GHSA-86w9-cpqp-85rv`；不能为清零盲目降级 SRT 或声称已修补。
- 真实固定负载 100000 events / 10000 spans / 100 MiB：metadata 从 8.0771% 改善到 5.210340492313925%，仍超过 5%。本轮没有重新测量性能；原生图形性能未验收。
- 旧安装包仅属于 `v4-f32-13bc5fd-win-x64`；本轮 CI 修正未重建或系统安装包，不得将旧 Setup 改称 a523a78 / 3a51097 产物。

### 授权、模型历史与恢复顺序

- 人类此前明确授权：已测试开发版本上传 GitHub；使用现有配置真实模型；费用预算“无上限”；本阶段先 Windows 10。安全授权证据 `docs/implementation/evidence/human-authorization-20261007.json`。管理员设置仍未授权。不得重新把付费实验“未授权”列为阻塞。
- 真实模型两批 10 attempts / 83 requests，共 864743 input / 15909 output，cost unknown；首批 source_changed 无效并完整保留。第二批独立 Windows grade A 3/3、B 2/3，B proverb 类型错误真实失败，不能选择性重试后宣传增益。安全索引 `F31-real-model-observations.json`；原始 `.local/F31-live-20261007T1251/`、`1255/`。本轮 F32 未新增模型请求，迁移时未启动新实验。
- 新聊天先读各级 AGENTS、PLANS/progress/backlog、任务卡和本段，再核对本地 HEAD/工作区/远端。已有完整测试和冻结源码未变时，不为上传重复全量回归。优先解除上传阻塞，正常 fast-forward push 两个本地提交并核对远端 SHA。若采用官方 Git database API，只能上传确切已测试 blob/tree/commit，校验身份和父链，并在远端仍为预期父提交时 `force:false` 更新；未核对同一 tree/历史不得改分支。
- 上传成功后检查真实新 Actions，下载/复算三个 portable artifact 与 security 结论。即使 portable 三平台通过，现有 high 仍会使整体 workflow failure；分别记录，不宣称全绿。根据真实失败修复、运行相关行为/回归后再上传，更新 progress、任务卡、evidence、support matrix 和 final report。
- 此后继续补 F28/F31 必需代码，按 backlog 依赖执行；implemented 标签或报告完成不代表全部 V4 功能已实现。`progress.json` 当前任务 F29（hosted 修正跟进），next recommended F28。双平台/正式模型/管理员资源缺失继续记 BLOCKED，同时推进独立代码工作。此聊天按迁移通知停止，不会自行恢复开发。

---

- 15:07UTC追加修正已完成全量：测试父HEADa523a7887a238fa7f52bd6be625d71ab4d0625bd/source9f9a924db65db866dcbe3971bfff2b8c74213456331801df8b4bf5ba7e283d1f，contracts2/quality4/unit225/portable382/regression1351全pass0skip/failure/error，freshCIgatepass。验证会话92229、19466、38971已结束；31份报告复算、87专项通过。37已有portable证明绑定当前report，native不提升，156证明仍blocked。gate派生报告独立保留为gate_review_evidence_ids：首次缺新测试索引、第二次前次派生fail反向输入，原始fail均保留；正确索引后implementation/release blocked0errors，latestrelease20261007T150635Z-a27c64ba/280条件。当前将提交这些文档/证据并推送修正，之后用.local/inspect_remote_ci.py检查真实新run。此前dd17e34/run37636301297已实际失败并完整保存；不能称新矩阵已通过。安装包仍属于此前v4-f32-13bc5fd，未重建或安装；0新增模型/UAC调用。

- CI修正已本机提交为a523a7887a238fa7f52bd6be625d71ab4d0625bd，尚未再次上传；source9f9a924db65db866dcbe3971bfff2b8c74213456331801df8b4bf5ba7e283d1f冻结。87定向测试零failure/error/skip；实际ci_bootstrap全部成功。私有.local/F32-ci-fix-verify.py正在串行contracts/quality/unit/portable/regression，并逐项原子保存.local/F32-ci-fix-final.json；结束后复算五份报告及fresh CIgate，通过才推送。当前新增3个Python行为（unit2、integration1）和真实Nodevenv启动断言。不要在这一轮执行中更改public源码／release-manifest或重建installer，避免source_changed。已有安装包仍属于此前v4-f32-13bc5fd构建，不能将旧二进制改称本候选重建产物。

- F32已经实际推送dd17e3488103008957ed88bfff764cbe27694a94。随后run37636301297四job均failure：Linuxunit223/portable367pass14fail，Windowsunit223/portable379pass2fail，security2high。真实原始artifact/log/JUnit已取回并逐份校验；公开索引F32-first-hosted-ci-observations.json。正在F29/F32追加修复：development Engine保留固定venv启动别名且独立校验目标hash；显式执行锁定Electron44官方installer；原F25平台描述分别对照真实golden；catalog机器JSON ASCII转义保留Unicode值。定向测试正在执行（.local/F32-ci-fix-focused.xml/log），bootstrap已实际完成；尚未冻结完整复验或再次上传。原始F32失败与旧通过scope均保留，不宣称CI绿。没有新增模型调用、管理员setup或生产发布。

- F32开发审查已完成（14:20 UTC，上传前）：测试父HEAD79b43a10c7a6b59a1c1fe47ac19441300ca655d6，source bbd5f9a27959a83f651c7208a61b01cc1f4607483e7b7cd4b63eac3c66e571fb；contracts2／quality4／unit223／portable381／regression1348全pass，零skip/failure/error，fresh本机CIgatepass。21新增行为；22份实际报告复算通过，首轮900秒portable无JUnit的fail另列。初轮reg1347/1Renderer initialize10秒timeout、frozen预算耗尽均保留；相关2专项、最终串行全pass，权限/模型/工具预算未放宽。
  actual build v4-f32-13bc5fd-win-x64，manifest86b8f9a965d26d0b9e82380d612abba6c1a02aa39182c628f7abc184caa36b36，1860files/121components/147license files，196runtime源hash相同；Engine9／packaged5／maker1／hardened13／desktop66pass。Setup231238144bytes/SHA5b67f3a392bda16aa6503ffb2f01157b2178daaee43698e60f2f33f70a4a5643，unsigned developer-preview，未系统安装。fresh源12命令/24日志/3锁/demo原始artifact复算，真实新Node24下载/3固定Git任务/runner preflight一致，0模型请求。
  所有84984/24786/58012/29289/38024等sessions已结束，不再轮询。implementation/release各blocked，0errors，F28native promotion/initialized cleanup/dynamic parity及F31正式RunSpec/policy/可信账本绑定未完成；Win11/Ubuntu/native156证明、metadata5.2103%>5、安全2high、sign/LICENSE/admin仍缺。旧未授权/未构建条件已移历史，不能作为当前模型缺许可。下一核对精确stage、commit/push、远端SHA并检查实际F32矩阵，更新current CI事实；之后在实际资源具备时继续F28/F31。不得重复模型best-of或运行未授权admin。

- F32恢复状态（13:53 UTC）：全部旧sessions已结束。初次regression实际1347pass/1fail（Renderer权限测试在Engine initialize的10秒host冷启动处超时，尚未执行权限断言），初次portable900秒超时/noJUnit（不计pass）。先前已定向锁/approval4pass，frozen独立复验9pass；actualmaker1/hardened13/desktop66/packaged5pass。将Renderer仅initialize等待改60秒，后续权限请求仍10秒；Bridge外层90秒涵盖子脚本60/65秒有限预算；portable外层1800秒与regression相同，所有case assertions未改。相关2行为9.75秒pass。
  新本地候选79b43a10c7a6b59a1c1fe47ac19441300ca655d6尚未上传，source bbd5f9a27959a83f651c7208a61b01cc1f4607483e7b7cd4b63eac3c66e571fb冻结。shorts84984，串行portable/regression新session见工具返回；.local/F32-short-rechecked-final.json与F32-portable-regression-rechecked-final.json。禁止改source；等待完整结果、复算并更新所有文档，再push/核对实际CI。初次失败及新12命令fresh副本、当前真实包安全索引已保留。模型无新增请求，admin未授权；必需native/official代码缺口与生产安全继续blocked。

- F32 当前恢复状态（2026-10-07 13:30 UTC）：候选本地提交3d4454fb4f67aba92410d49dc2398db564ede68e尚未上传；source bdafc692ead990ea3a6036714a413f79348962575627a68c0b5574afdcacf461冻结，禁止修改source或tracked清单。public定向Python50／Node24 21pass；真实物化三pinnedGit任务hash/checksum相同0model，当前frozen Engine/Bridge/Desktop/assembly成功，build v4-f32-13bc5fd-win-x64，manifest86b8f9a965d26d0b9e82380d612abba6c1a02aa39182c628f7abc184caa36b36，1860files/121components unsigned developer-preview。
  unit1288／portable29289／regression58012／contracts-quality34126正在验证，.local/F32-<suite>-final.json；packages与fresh-source新sessions见工具返回，.local/F32-packages-final.json／F32-clean-checkout-result.json。先等完整报告定位真实失败、复算、更新progress/card/evidence/ADR/finalreport，再上传及核对实际GitHub。F31实际remote run37627185380四failure与F28相同原因：Linux214pass/1纯文件Electron冷导入fail、Windows Node18pass/3冷启动/aliasfail，security2high；raw .local/F31-remote-ci已取回。不要声称远端已绿。没有新模型请求；F28/F31必需代码缺口已由remaining_implementation门禁拒绝。

- F31 开发阶段结束（2026-10-07 13:15 UTC，提交前）：测试父HEAD368b60535397a22f84cfa77a8eb19a4315939560；final sourceecb3ee345d41a725e79c1fae9945f4171c111c23315d7ceb6e7131239c8727ec。unit215／portable368／regression1327，contracts2／quality4 pass，零skip/failure/error，本机fresh CI gate pass。最终7份正式报告独立复算，12份F31历史metareports保留，3份准备失败无JUnit不计pass。
  实际第二批6attempt／50requests：A3/3、B2/3独立grade；Bproverb字符串／列表错位8fail而内部completed，Atransposegradepass但验收引用不足partial。三task-cluster B−A−1/3、exploratory95%[-1,0]，离线完全复算；cost unknown／formal trace缺／0official Harbor／0native证明。第一批4attempt／33requests因tracked development-assets刷新source_changed不可比，全保留，不best-of合并；两批83请求完整usage864743/15909、cache0。原始.local/F31-live-20261007T1251与1255，安全索引F31-real-model-observations.json。
  全部F31 verifier/live sessions已结束，4051/86810/97020等不再轮询。先精确stage本任务、commit/push/核对远端，随后F32。progress接口标implemented不代表正式必需功能完整，remaining_implementation已明确官方binding/policy/RunSpec代码缺口；F32候选gate将阻止凭标签自称代码全部完成，2候选行为已pass。
  F28真实GitHubrun37622858984四job failure：Ubuntu22/24各213pass/1Electron冷导入输出fail，Windows Node18pass/3fail（PowerShell10秒timeout、2短路径alias比较），security2high。安全索引F31-prior-hosted-ci-observations.json。F32 private候选pure file-publication提取与规范路径／有限冷启动65秒修复已实测15Node+1真实FS行为pass；尚未public／hosted验证，不称CI已绿。
  F32 private.local/F32-prepared最新materialize_release使用atomic link无覆盖，ci_bootstrap复用；新materialize_experiment已真实重新物化3官方pinnedGit任务，hash/checksum一致，0model/0grade，.local/F32-private-fresh-task-download.json。候选Node测试的private imports是2层，仅public导入要恢复3层；native file测试public目标改apps/desktop/src/main/file-publication.ts，privateROOT常量应换__file__.parents[1]。fresh_source还需加入实际任务物化命令并执行；public完整Engine/Bridge/Desktop/assembly/installer重建后冻结，再5套件和clean checkout验证。源码可开始下一任务改动。

- 当前恢复状态（2026-10-07 12:55 UTC）：F31首次三pytest suite准备失败，真实development-assets.json因新benchmark源码变动已过期，0测试／无JUnit，记录fail，不计pass。实际build_desktop已刷新受约束的tracked清单，source从198d7...变为ecb3ee345d41a725e79c1fae9945f4171c111c23315d7ceb6e7131239c8727ec；首批live因此真实blocked/source_changed，已完成4attempt/33requests/4独立grades，全部保留.private.local/F31-live-20261007T1251，禁止从中挑选best-of。
  单独第二批完整6attempt已启动，private.local/F31-live-20261007T1255；不得改源码/清单。3pytest重验正在运行：unit46944/portable86810/regression4051（输出.local/F31-<suite>-rechecked.json）；新的contracts/quality/live-eval输出.local/F31-layers-rechecked.json。老86862/20968/25144已结束，不能重复轮询。第二批完成后公开原始失败与全部实际请求的安全索引，先复算、再commit/push、继续F32。

- 当前恢复状态（2026-10-07 12:51 UTC）：F28 已推送并核对 368b60535397a22f84cfa77a8eb19a4315939560。GitHub实际run37622858984四job failure；Ubuntu22/24各unit213 pass/1 fail（Electron自动下载提示混入JSON），Windows完整日志已下载待核对，security真实2high继续拒绝。不得称远端CI通过。
  F31 public实现已加入，40项专项pass；source198d7bc009b4eb13d59477689dab52c9c1138243c75972dec1396ce92d79f71f冻结。实际六次Harness实验session82647，private.local/F31-live-20261007T1251（frozen-plan已写、首题实际执行）；真实provider anthropic/gpt-6-luna localhost，用户授权无金额上限，每attempt40模型/100工具/900秒有限预算。独立Windows unittest使用官方public tests bytes，0正式Harbor成绩，不证明native。
  正式三层contracts/quality/unit session86862，portable20968，regression25144；输出.local/F31-{short|portable|regression}-verification.json，必须等待完整报告，源码不得中途改。live-eval20261007T124524Z-f790f549真实blocked：官方policy/授权账本绑定/RunSpec未完成、LinuxDocker unavailable；不再把用户未授权作为理由。下一步完成六attempt/复算/全部回归/证据并上传F31，然后F32修CI/实际重建/鲜拷贝文档命令验证。private.local/F32-prepared是准备文件，不直接把探测算正式验收。

- F28 已完成本阶段开发验收（2026-10-07 12:41 UTC，待上传）：最终source6078be1646f91cf2238bf911134726b1b6b5b6a168a442c96878d742844bebd3，父HEAD f3c194ae7eebca392fb1db8511903013e554c933；unit214／portable355／regression1313、contracts2／quality4 pass，零skip/failure/error，fresh CI gate pass。所有45份正式证据已独立复算，current6份（含acceptance blocked157平台证明）和实际包9／5／1／13见F28 observations。
  严格原生promotion/full cleanup/dynamic sensitive path、5.2103%metadata目标、安全2high、签名/LICENSE/admin、后续Windows11/Ubuntu和官方Docker实验仍受阻。Windows10为用户当前验收阶段；真实模型授权金额无上限已记录，连接1请求335/10 tokens pass，cost unknown／0official grades。
  当前没有运行中的F28 verifier，91120/11742/53069/39517均结束，不再轮询。接下来精确stage本任务文件、commit/push/核对远端SHA，实际检查GitHub矩阵，然后正式开始F31。F31/F32最新候选只在.local/*-prepared，不覆盖为旧store版本；F31 live候选需要接入实际results raw requests、impl suite和source fingerprint experiments，再跑13项新增行为／五层回归与6次真实Windows Harness任务。F32继续运行文档、完整重建、fresh-source验证及最终审查。

- 当前恢复状态（2026-10-07 12:31 UTC）：F28 第一次最终 regression 20261007T120124Z-c1577d00 实际1312 pass／1 fail，已保留；0.25秒服务寿命按期关闭HTTP，修正测试先确认liveness再启用原deadline，5项实际HTTP/owned cleanup专项pass。
  最新 source 6078be1646f91cf2238bf911134726b1b6b5b6a168a442c96878d742844bebd3 冻结。unit214（20261007T122040Z-d187f818）、contracts2（20261007T122050Z-66356479）、quality4（20261007T122057Z-9dbacf36）pass。正在运行 regression91120（20261007T122019Z-9e16aa46）与 portable11742（20261007T122029Z-b3dc3704），输出.local/F28-{regression|portable}-rechecked-final.json；等待完整报告后完成F28/上传/查真实远端CI，不轮询88174等历史完成sessions。
  人类授权见evidence/human-authorization-20261007.json：真实模型、金额无上限、当前Windows10验收；admin仍未授权。配置实际anthropic/gpt-6-luna localhost已发1请求pass，335input/10output，最终usage完整，cost unknown；公开安全索引F28-real-model-connectivity.json，原始private.local/authorized-model-probe/report.json。不可把连接结果当任务成绩。
  F31新候选model_regression.py及4专项测试在.local/F31-prepared，只做private准备；计划真实三道公开Python任务×repair0/2、独立Windows unittest，不冒称正式Harbor/Linux。实际候选grader已对原始stubs8/12/16测试判fail（正确），零skip／零模型，private.local/F31-prepared-grader-probe/report.json。F32私有文档已同步最新授权；尚未正式加入。F28有效历史证据42份独立复算通过.local/F28-evidence-validation.json。

- 当前授权与恢复状态（2026-10-07 12:18 UTC）：用户明确授权接入配置中的真实模型，金额预算“无上限”，本阶段先在 Windows 10 验收；管理员系统设置授权仍未提供。真实模型请求保留有限请求数、时间和工具预算，费用无来源时记 unknown，不再把未授权付费作为当前阻塞。凭据只在进程内读取，不进入报告或 Git。
  F28 最终 source 0f2bc42808ee2122017d8357c69f9f4df0c3923b3d9236bcf6eb404944d335e3 冻结。unit214 / portable355 / contracts2 / quality4 / development desktop66 pass，零 skip；实际 frozen9 / packaged5 / installer-maker1 / hardened13 已通过。全仓 regression session88174 仍运行，stdout 已出现一项失败，必须等完整报告定位，修复后对最终源码重跑，不可称为通过。旧 session 列表属于历史，不再轮询。
  全固定性能负载两轮8.0771%→5.2103%，仍超过5%；strict verified capability、完整初始化清理和动态敏感路径尚有代码缺口，security2high、签名/LICENSE、Docker daemon及后续 Windows11/Ubuntu 验收仍受阻。F31/F32仅 private准备，F28结束后继续依赖执行。
  真实连接检查发现配置可用；只公开 provider anthropic / requested model gpt-6-luna / endpoint host localhost，不公开 URL路径端口或 key。尚未发模型请求；先做1请求、120秒、1024输出token连接与usage探测。恢复时查 .local/authorized-model-probe/ 的真实报告后再决定是否重试。

- F28 最新状态（11:55 UTC）：两次完整固定100000 events／10000 spans／100MiB已经完成，保留原 blocked 报告。metadata8.0771%→5.2103%，仍未达5%目标；第二次20261007T111308Z-47ed56ff，cold median3.2679285s／hot0.76535ms／projection55.6678706s，React10000items仅14rows。source b742dbc9d1d13554e8da1b51e9eb105c8d4d273d4baa119a0776daef8be42b4d，之后仅加锁组件及验收映射／审计测试修正。真实host run_process100MiB／完整归档hash8checks pass不证明native。事实 evidence/F28-acceptance-observations.json。
  已实际重建 frozen Engine v4-f28-f3c194a-win-x64、Bridge、Desktop及assembly。每turn actual owned Bridge/FileWorker模型前组合、fuse/utility credential helper/独立smoke、uv Python/TAP/悬空资源CI修复已写入。verified native capability promotion、initialized full cleanup及动态敏感路径仍有代码缺口，strict拒绝；平台/admin/签名/LICENSE/高危/paid model依赖继续blocked。
  初次unit214 collected出现验收映射绝对路径漏洞，已补仓库相对路径／..拒绝，3专项pass；第二次并发遇audit比较共享Git状态被其他verifier新增证据干扰，改真实独立clone覆盖staged/unstaged/untracked状态和原始bytes，10专项pass。失败及中断日志保留，不能算最终pass。
  当前最终源码冻结：unit session67165已经214pass；portable43673；regression88730；packages39916（engine-packaged、desktop-packaged、installer-windows、hardened、desktop）；layers90641（contracts/quality/installer-linux/sandbox-windows/sandbox-linux/security）。输出.local/F28-<unit|portable|regression|packages|layers>-reviewed-final.json。等待余下结束，核验报告、运行acceptance、更新card/progress/backlog/ADR/handoff，再push并检查实际GitHub，随后F31/F32。不要重复轮询已完成session。
  F30远端run37606254120实际日志/Ubuntu JUnit已下载.local/F30-remote-ci；Windows Python获取与Ubuntu4unit失败仍同F29。真实GitHub只读helper.local/inspect_remote_ci.py已验证snapshot/log/artifacts；认证仅内存，redirect不带Authorization。F28上传后检查Ubuntu selected-file stdout根因，不把WinNode24结果替代Linux。
  F31候选仅.local/F31-prepared，未开始正式任务：strict frozen配置对比/6run预注册/lastauthorized/taskcluster/real SQLite+ZIP复算，最新候选补raw attempts/requests保留，需接入results.py。F32候选仅.local/F32-prepared，文档、安全、真实命令demo、runtime materializer及6测试、复用CI bootstrap正在准备，尚未执行。不得恢复旧functions.store候选覆盖最新private文件。


- F30 已推送 f3c194ae7eebca392fb1db8511903013e554c933，远端SHA已核对；测试父 HEAD e5d5b88671fc84ab2483966397aa49047ac93852。source f35d374c5c4fdb1fd1f05dd66548613aaaebcccc3ed39ee62c62536b0209034f / dirty 8e7f67151172cec234f738a8b5d8fd98300a3eb218fb5bf0c14f2f9caa87d610。
  contracts2 / quality4 / unit190 / portable353 / regression1287 pass，0 skip/failure/error；本机 fresh CI gate pass，5份正式报告已独立复算：20261007T100100Z-61029d00 / 20261007T100104Z-966ab87c / 20261007T100117Z-2b3a1290 / 20261007T100154Z-0aaf1497 / 20261007T100111Z-456566b1。
  实际 pending cancel TCP1→0、current failure不被stale success解除、repair cap0..2/terminal剩余一致、Adapter真实策略能力、ignored workspace外部编辑hash失效已修；原父子usage与长输出压缩行为保持。12项Harness+1项workspace新增行为；before/after/focused/深验与原夹具错误均保留。事实 evidence/F30-harness-observations.json。
  F29 推送 e5d5b88671fc84ab2483966397aa49047ac93852 后，GitHub run37603953403实际四job failure：Windows setup-python找不到3.12.13，Ubuntu22/24各4个unit failure，security按预期exit2。已下载实际日志/JUnit/hash；本机Node24复现两个reporter断言fail。F28先修CI可移植性，再fuses/真实性能/原生矩阵与strict组合。
  下一F28，依赖均implemented。未授权admin/paid模型；Windows11/Ubuntu原生设备、Docker daemon、signing/LICENSE、GHSA安全仍blocked，不虚构验收或成功率。


- F29 已推送 e5d5b88671fc84ab2483966397aa49047ac93852，本地／远端 SHA 已核对。F30 当前实施中，依赖 F12/F17/F18/F25 全 implemented，任务卡和规范9/14/17已读。
  测试父 HEAD 779d74aa19aad7d083191b69e367e42f77eedd7a；source 722e3acb06fe54791898705fb7c5872eeba318657fc440c83ce7e241d9dc08aa / dirty 1aee3b2a0141bae42b96d1e566a68cc95531dced4d43ca9aa540649dd4e35225。
  contracts2 / quality4 / unit189 / portable341 / regression1274 pass，0 skip/failure/error；本机 fresh CI gate pass。最终6份 evidence 20261007T094141Z-120a6785 / 20261007T094145Z-f0087cfb / 20261007T094158Z-06aa2a42 / 20261007T094233Z-03fcb104 / 20261007T095022Z-ac0916fa / 20261007T094141Z-77705e45，全部18份历史保留并核对真实报告。
  已修复 gate 对零测试/skip/unknown verdict 的 false pass、当前 HEAD/平台/源码证据绑定、实际锁文件/type/AST/secret/workflow检查；真实 Git 子工作区误读父仓库导致审批 STALE_REVISION 的2项 before fail / 36项 after pass，未放宽审批限制。
  security actual exit2 blocked（2 high）；GitHub Actions后续实际run37603953403失败，最新诊断见上述F30，protected environment/reviewers/runner外部配置未核验；native/signing/LICENSE/admin/paid实验不宣称通过。事实索引 evidence/F29-ci-observations.json。
  F30 的11项候选已加入 tests/implementation/integration/test_harness_regressions.py，在当前分支重现 before7 fail/4 pass；.local/F30-before-current-branch.xml 保留。取消请求边界、当前证据展示、实际repair cap和Adapter capability已写入，专项/全仓回归已完成，结果见上述F30。原候选报告和 hashes .local/F30-prepared-index.json；付费模型和原生验收仍 blocked。


- F27 已推送 779d74aa19aad7d083191b69e367e42f77eedd7a，本地／远端 SHA 已核对。F29 当前实施中：已读任务卡／规范21/22/24，先复现 gate 零测试／skip 伪通过，再补 CI、实际证据复算与安全门禁。F27 最终测试父 HEAD 08ec4ccbaf55f3a8db6e561e24b1df4ae3303724。
  final dirty/source 3a9cf621a750d9a0bd7d9523c72431f9d76a762d6b7d9c65218f568b11936a32。unit160 / portable341 / regression1245 pass，0 skip/failure/error；frozen9 / development66 / installed readonly preview4 / actual installer maker1 pass。
  build v4-f27-08ec4cc-win-x64；manifest SHA 7a89b7187824fd73999ccfdc8b1d42c445d2e61eab3aff62c58eb7a47d0f356f；1860 files / 87 frozen Python distributions / 119 components / 147 license files。
  最终10份 evidence：20261007T045603Z-77e5a97d / 20261007T083904Z-e08a09a3 / 20261007T084739Z-f2a68093 / 20261007T084926Z-6b3ab489 / 20261007T085013Z-844215b0 / 20261007T085101Z-86488a82 / 20261007T085304Z-5c4b0e50 / 20261007T085305Z-9f595b5e / 20261007T085317Z-1601a869 / 20261007T083904Z-d1139af0；此前10份正式历史（含 desktop38 fail）保留。中断的 04:56 portable/regression 无完成报告，不算 pass；恢复后核对进程并补跑。
  修正实际 worker 丢失 PYTHONDONTWRITEBYTECODE=1，真实 import 回归与 desktop 原断言重验通过；冻结 Job 强制退出实测清理2个后代，真实 CLI/doctor/Git/Python/Node/Bridge 已实测。
  Windows Setup.exe 231220224 bytes / SHA 71e849efb4a755cf364b1bf8feb783095636fd32c1c2ead04afd61f9a11f8179，unsigned developer-preview。事实／失败历史见 evidence/F27-release-observations.json；实际产物在 .local/desktop-resources 与 .local/desktop-packages/make。
  Linux installer / native Windows / native Linux blocked 0 checks；Windows11/Ubuntu clean install、签名、项目 LICENSE、安全、admin setup、paid model 和 F28 verified strict composition 继续 blocked。
- F26 已推送 08ec4ccbaf55f3a8db6e561e24b1df4ae3303724，远端 SHA 已核对；F27 当前实施中，依赖全部满足。
  测试父 HEAD e37e09a514f3bfdf7888e45ea25a3dcc10d961b3；最终 dirty/source hash 959df5e670e6a2011cf58449f0b52479d2d526d4943ad8d2a48fb4bddce6b0f1。
  unit 149 / portable 334 / 全仓 regression 1227 pass，0 skip / failure / error；development Electron 66 checks pass；npm Node 21 pass、全工作区 typecheck pass。
  最终7份 evidence：20261007T033133Z-af055100 / 20261007T033159Z-d6931948 / 20261007T033910Z-29796944 / 20261007T033951Z-0aa732a8 / 20261007T033951Z-8bd53d99 / 20261007T034003Z-4dd217c8 / 20261007T034015Z-cc9b3ce8。
  legacy readonly scan / exact-byte verified backup / hash-confirmed immutable imported mapping、未知配置保留与诊断、原 CLI golden / delegates、实际 authenticated loopback HTTP/SSE 与同一 React HttpTransport、CSP-safe shared AJV validators 均已实现。
  真实自然过期 ack 关闭旧订阅、读 actual snapshot、标 gap 并续流；丢失受理响应仅 action.get 对账，不重发副作用。SQLite/Chromium 真实观测索引 evidence/F26-compatibility-observations.json；scripted 模型，0 公共模型成绩。
  首轮 registry、配置授权丢失与过期游标的实际失败/修正见 F26 卡；20份历史和最终 evidence 全保留，只有最后7份计最终验证。
  packed / native Linux / native Windows 各 blocked 0；Windows 11 / Ubuntu / admin / signing / paid model / installed strict composition 缺失继续 blocked。Web 默认 strict 无 native backend 不降级；CLI process memory credential 不称 OS keystore。
  F27 要构建真实 frozen console Engine / file-worker / grouped resource closure / installer与手动升级备份；读取 tasks/F27.md 和规范20。现有 packaging smoke 不是产品 Engine，不沿用假验收。
- F25 已推送 e37e09a514f3bfdf7888e45ea25a3dcc10d961b3，远端 SHA 已核对。
  migration 014 / committed Journal startup projection / recovery.inspect / readonly recovery UI / unknown cleanup barrier / bounded Main late/action reconciliation / live parent observation 已实现。
  unit148 / portable292 / regression1184 / development66 pass，0 skip；原生、packaged、actual suspend/resume、paid model blocked。实际恢复事实见 evidence/F25-recovery-observations.json；细节和失败历史见 F25 卡。
- F24 已实现 migration 013 / 人工标注版本与 CAS / 有限建议 / 脱敏候选 / 实际新执行复现核验 / 失败页面与 Main 原生导出。
  专项 14 项、含既有 ZIP 回归 49 项 pass；initial desktop 64 checks 的旧 zoom 失败保留，等待实际布局后重验 64 checks pass。
  正式 unit 147 / portable 268 / regression 1159 pass，全部 0 skip；development desktop 64 checks pass。
  最终 evidence 20261007T010959Z-5c2cc1a6 / 20261007T011016Z-ef2bdeb2 / 20261007T011517Z-8b0f33be / 20261007T011549Z-d546abf4 / 20261007T011550Z-aed347b6 / 20261007T011555Z-727165ad / 20261007T011559Z-8a7b78d1；final dirty 4596b013bb59f887f18caa42d6d5bbbd88423666d7e23a5fef44db2ccda839d2。
  packed / native Linux / native Windows 各 blocked 0 checks；不把离线 fixture 视为公开模型／原生 sandbox 结果。下一 F25：修复 RPC 超时迟到响应并做真实故障对账。
- 提交前只移除 annotations.py 一个空行的尾随空格（逐文本比较确认无其他源码变化），刷新 asset inventory 后补跑 unit 147 / desktop 64 pass：20261007T012542Z-218e0608 / 20261007T012606Z-e13d7eac；dirty 8ba311c8fcf12233a3b4afffc8c986d458d245246f168d3d800ec934ea64d50c。
- F23 已推送 4082833ff7a81f6555e96111239c87ea32b5a854，远端 SHA 已核对；这是 F24 的测试父 HEAD。
- F23 已实现四步不可变实验配置、profile 快照归属、真实分页报告、同题 A/B Trace 与不可比告警、Main 原生文件／隐私确认接线。
  unit 145 / portable 256 / regression 1145，全部 0 skip；Windows 10 development Electron 59 checks pass。
  最终 evidence 20261007T004628Z-9d6e9f79 / 20261007T004659Z-01d191f0 / 20261007T004716Z-11c7a1ea / 20261007T005211Z-485cb317 / 20261007T005211Z-82b55461 / 20261007T005216Z-cdbc3407 / 20261007T005220Z-8a72deb8；final dirty 850c79a0e7244712b36cabfb1c3d9f1f3252d5d84a880dbba2456f653902e030。
  初次 TypeScript preparation 与目录加载时序的失败证据保留；被中断的未完成套件不计通过。
  public model / Docker / F30 spend-policy、native Windows 11 / Ubuntu / installed client / 人工文件对话框仍 blocked。F24 将接真实失败标注、不可变历史与证据绑定复现候选。
- F21 已推送 2a96fc8593b10b2969482e408f42d4bd67151445，远端 SHA 已核对；F22 测试父 HEAD 即此 SHA，提交后用 git HEAD 核对。
- F21：安全 ZIP / private staging / 原始包与冲突隔离 / imported_* 只读表 / profile artifact / Main-only file token / offline report 已实现。
  unit 142 / portable 231 / regression 1117，0 skip；Windows 10 development Electron 44 checks pass。
  最终 evidence 20261006T231423Z-a85f0301 / 20261006T231438Z-2fe8c473 / 20261006T231905Z-de0f6db7 / 20261006T231934Z-9be0bef8 / 20261006T231934Z-6330ec21 / 20261006T231939Z-2971b33e / 20261006T231943Z-2e15c738。
  final dirty 528cd116b097af4ccb7ca6215d4c9a76f2e485e69e4df3c0f2f72afba81d6440，测试父 HEAD f9574492088892b88da3362b37a44b6c81687e37。
  35 项新增行为使用真实文件 / SQLite / RPC；静态手算 synthetic ZIP 为 4 planned / 5 attempts，不是真实模型成绩。
  schema 010 自动备份与旧产物归属回填；原始包 exact bytes 保留，Renderer 不可读原始 private bundle。
  首次 portable 230 pass / 1 fail：旧 F18 fixture 伪降 schema 7 遗留 artifact_profiles；改真实旧 schema 构造后完整重验通过。失败 evidence 保留；已中断旧回归不计 pass。
  packed / native Linux / native Windows 各 blocked 0 checks；原生 file dialog 接线留 F23，无 paid model / Docker / native sandbox 调用。
- F22 已实现真实 execution ID 工具导航、Trace 树／瀑布、stdout/stderr 安全懒加载、上下文版本、证据扫描版本差异、请求账本质量与客户端／Harness 分离时延。
  migration 011 / profile 归属 / filter-bound cursor；运行中 250 ms Journal 投影，首次文本 chunk metadata 及时记录，失败 reconciling 不重跑。
  正式 unit 143 / portable 242 / regression 1129 pass，0 skip；Windows 10 development Electron 52 checks pass。
  最终 evidence 20261006T235241Z-462bb220 / 20261006T235257Z-7e30a489 / 20261006T235747Z-6c832b03 / 20261006T235820Z-ebc91bad / 20261006T235820Z-02ca11f4 / 20261006T235825Z-e0e8fd6e / 20261006T235829Z-17b9f81e。
  final dirty f42654ff778895a1ac8232dd0ce363c11a91cc4a32bb641a78b4e57f70638932；packed / native Linux / native Windows 各 blocked 0 checks。
  focused fixture 修复详见任务卡，下一 F23；客户端实际小集 paid model / Docker 仍 blocked，不能假造公开成绩。
- F20 测试父 HEAD 12da1f9ab8c021d89f99ae35f796fcde4352eed4；提交／推送 SHA 以实际 git HEAD 核对。
  unit 142 / portable 196 / regression 1082，0 skip；Windows 10 development Electron 44 checks pass。
  最终证据 20261006T222943Z-40ff0fc5 / 20261006T222959Z-e4a89567 / 20261006T223421Z-c303888f / 20261006T223450Z-81b1e3a6 / 20261006T223450Z-f696f399 / 20261006T223455Z-d454cccc / 20261006T223459Z-232a8d30 / 20261006T223508Z-0c033e57。
  final dirty df16b9a3e48f2236455775548e9f38b79c4d55719019d0dcecd0cc6424ee4f56。
  真实官方 Harbor adapter / owned process / immutable grade / patch cache key；冻结 Harness 无额外 repair 或 Harbor retry。
  actual Aider Polyglot 1.0 三题来源 commit f30b14415dd733c83627204bad0af69a89ceb46f，官方 --print-config 3 checks pass；整体 probe blocked，0 model calls / 0 grades。
  Harbor 0.18.0 / Docker client 29.7.2，Linux daemon 缺失；不自动启动管理员服务。Paid approval / F30 spend / native policy reconciliation 仍 blocked。
  packed / native Linux / native Windows 各 blocked 0 checks；生产 executor 不降级、不调用未授权模型；远端 raw journals 不投影可信费用账本。
  F21 实施安全 ZIP quotas / staging / provenance / 原始与脱敏证据 / 原生 profile artifact 归属；导入命名空间只读，离线报告不执行脚本。
- F19 已推送 12da1f9ab8c021d89f99ae35f796fcde4352eed4，远端 HEAD 已核对；F20 当前实施中。
- F19 测试父 HEAD fdbcf50047d290d6c765aaa8ac7b5da77b26b95c；提交／推送 SHA 以实际 git HEAD 核对。
  unit 142 / portable 184 / regression 1070，0 skip；Windows 10 development Electron 44 checks pass。
  最终证据 20261006T215137Z-955dfd02 / 20261006T215153Z-20c452df / 20261006T215603Z-bc7852d1 / 20261006T215634Z-245af063 / 20261006T215634Z-fca4984d / 20261006T215639Z-7dfab335 / 20261006T215643Z-b64762b3。
  final dirty a1117ffe4bc74b91738fe6b07c69d8e3eeee2ec217664f680442140e828616f4。
  原子生成全部 planned trials；同 action 幂等；turn/attempt 单 worker、epoch、真实 heartbeat／deadline／owned process cancel。
  独立 execution／grading／cleanup 三轴；显式基础设施重试、末次授权选择；不可变 grade 和恢复 trace link，无自动副作用回放。
  真实 Harness journal 投影／请求费用去重，手算分母／未知费用／N/A／配置可比性通过；无官方／真实模型成绩。
  首次 portable 曾因旧 RPC capability 断言失败，修正动态目录校验后重验通过；旧 evidence 保留，已中断回归不计通过。
  修正交接 progress 的 F17 缺少对象键；JSON 解析复核，未改变代码测试结论。
  packed / native Linux / native Windows 各 blocked 0 checks；paid API、管理员设置、签名及 G7 仍 blocked。
  F20 实际环境：Harbor 0.18.0 已安装；Docker CLI 29.7.2，但 desktop-linux daemon named pipe 不存在。
  不启动管理员 Docker／付费模型；接既有官方协议、记录独立 grader／环境标签、源码与 patch hash。
- F18 已推送 fdbcf50047d290d6c765aaa8ac7b5da77b26b95c，远端 HEAD 已核对。
- F18 测试父 HEAD 939d6fa0dcd0d7583571521fdef3f08f176a926e；提交 SHA 以实际 git HEAD 核对。
  unit 139 / portable 170 / regression 1053，0 skip；Windows 10 development Electron 44 checks pass。
  最终证据 20261006T211118Z-7289c010 / 20261006T211134Z-d4dc513b / 20261006T211536Z-3bcf5fe6 / 20261006T211605Z-c37e3033 / 20261006T211606Z-ff84a680 / 20261006T211610Z-70274cd5 / 20261006T211615Z-e379a908。
  final dirty babd694a41cbccf99d4abcd0d9ce478ef3f27ca8ef0fd257018189bf124e1abc。
  实际 request 唯一 ledger／冻结价格／Decimal／unknown、角色重试归因、上下文指纹、真实证据失效、显式 metadata OTLP。
  官方 SDK 仅调用受控 loopback provider；不称为真实模型实验。部分 Anthropic usage 未结束时费用 unknown。
  controlled_debug 保存有界脱敏本地模型／工具／上下文副本，导出只含 metadata；出口和磁盘失败不阻断 Agent。
  migration 008 实际回填旧请求，不猜旧价格；原 CLI 与 create_runtime 签名保持。
  packed / native Linux / native Windows 各 blocked 0 checks；G7、管理员设置、签名与 paid API 继续 blocked。
  下一 F19 固定 RunSpec／计划分母／owner epoch／三轴终态；F20 接既有官方 Runner。
- F17 已推送 939d6fa0dcd0d7583571521fdef3f08f176a926e，远端 HEAD 已核对。
- F17：真实 Journal observation / fsync、受理根 trace、模型→上下文→工具→验证父子关系、Explore／摘要／重试归因。
  来源 ID/sequence/hash 冲突隔离；外部 import: 命名空间；重复回放不执行、不增 ledger。
  migration 007；恢复创建新 trace link 旧 trace，unknown 副作用仍不自动重跑。
  已实现事件目录留 trial/attempt/client 未接入项 contract_only；grader 是真实回调 fixture，F19—F21 接续正式协议。
  unit 127 / portable 156 / regression 1026，0 skip；Windows 10 development Electron 44 checks pass。
  证据 20261006T195728Z-806a10fc / 20261006T195743Z-1fa9ce8a / 20261006T195925Z-29d74dae / 20261006T200005Z-56bfc334。
  packaged blocked / 0：20261006T195955Z-97a1025e；native Linux / Windows blocked / 0：
  20261006T195956Z-d6a34afe / 20261006T200000Z-05e1019e。native Bridge child execution tracing 未验收；不制造 verified caps。
  F17 测试父 HEAD bc27f9780b179097d0e2bb9f732e93748ce3709c；final dirty 0fbdf33ae33b98b252644e79b82f76c3f0aa7156280eff4a71221c4ddc2f75b0。
  F18 继续 raw / normalized usage、冻结价格与 unknown 成本、上下文版本／配对、证据失效、有界异步 OTLP。
- F16 已推送 bc27f9780b179097d0e2bb9f732e93748ce3709c，远端 HEAD 已核对；F17 测试父 HEAD 即此提交。
- F16：真实项目文件、会话提交／流式消息／取消、immutable dirty 基线、Diff 和 guarded reverse patch 预览。
  migration 006；session+action / turn+action 同事务；snapshot 签名事件边界、sequence 去重、10000 有界列表。
  valid Hook/MCP/.env 不执行；Git 禁 fsmonitor / hooks 并限制父仓库查找。原 CLI / create_runtime 签名不变。
  unit 127 / portable 139 / regression 1009，0 skip；Windows 10 development Electron 44 checks pass。
  证据 20261006T191227Z-fdf08b04 / 20261006T191242Z-960d04de / 20261006T191601Z-c1e273e9 / 20261006T191654Z-94a8d24d。
  packaged blocked / 0：20261006T191629Z-b8802a4c；native Linux / Windows blocked / 0：
  20261006T191645Z-824876b1 / 20261006T191650Z-e24e7d15。人工原生 IME/对话框、安装版和 paid API 仍 blocked。
  早期桌面选择检查失败保留，最终实际展开面板后全部通过。final dirty dca27cb8ed3cc9567d26c41fbee24f7dcad2a8109cd5c5deeff18afbaff1f5b3。
  F16 测试父 HEAD 7d40c3e8f26c17c590463380defb9cf72e855913；提交 SHA 以实际 git HEAD 核对。
- F15 已推送 7d40c3e8f26c17c590463380defb9cf72e855913；F16 测试父 HEAD 即此提交。
- F15 测试父 HEAD e612f360be14dc2a198d65f936b88a7ffd336941；本次提交 SHA 以实际 git HEAD 核对。
  Main fixed Electron safeStorage helper / DPAPI、Linux 保护分类和内存模式、私有 Engine 注入、确认连接与显式联网测试。
  Renderer 无密钥读回；设置页清空输入，绑定 canonical endpoint / revision，锁定／删除／变更立即撤销并取消。
  锁定 Electron Win bootstrap stdin 是 EOF；辅助进程实际读取继承 fd 0。无 Node exe fallback / 自造 crypto。
  migration 005 独立测试结果，immutable accepted actions 不 UPDATE；action.get 查询实际观察。
  慢网络测试最多 16 个 owned async requests，真实 RPC 撤销／EOF 及时取消，batch 保持成员顺序。
  unit 126 / portable 128 / regression 997，0 skip；Windows 10 development Electron 29 checks pass。
  20261006T181011Z-29e85a98 / 20261006T181026Z-879caa5b / 20261006T181340Z-690e66f5 / 20261006T181449Z-45ec1b68。
  packaged blocked / 0：20261006T181358Z-6ef85083；native Linux / Windows blocked / 0：
  20261006T181437Z-202e38e3 / 20261006T181443Z-9ce797db。final dirty 14ab3eda6e9f1e99bc47402b15f58ea88219b5e748a914cbd27a3ac86a40e4ef。
  actual Linux keyring、原生人工确认、F28 installed / F24 bundle / F27 diagnostics export 和 paid API 未验收。
  F16 接入当前契约-only workspace.files / read_file / changes，内容 revision 与授权 revision 分开；
  session / message / Diff 复用真实 Harness / Journal / 写入锁，10000 项有界、IME、reload 不重开 turn。
- F14 已推送 e612f360be14dc2a198d65f936b88a7ffd336941；F15 测试父 HEAD 即此提交。
- F14 测试父 HEAD c094171f9ce175d8914a777110a3975346474a2e，提交 SHA 以实际 git HEAD 核对。
- F14：命名 IPC、可信窗口／frame／origin／schema；原生目录选择与高风险拒绝／仅此次。
  Engine 持久 pending、最终参数／policy／实际 workspace / environment 绑定、短期 nonce / CAS 一次消费。
  真实删除、文件／参数变化拒绝、伪造／过期／重试、取消与 Engine 重启测试；等待不挂起 RPC reader。
  migration 004 增加不可变审批详情与 nonce hash；两个 prepare 方法扩展 catalog 至 50。
  重复定义会被 generator 拒绝；workspace.authorize 旧 contract-only payload 已升级。
  unit 126 / portable 109 / regression 976，0 skip；Windows 10 Electron development 21 checks pass。
  20261006T171100Z-46d571f9 / 20261006T171115Z-dc7c34c1 / 20261006T171412Z-ce51f277 / 20261006T171509Z-18a7199e。
  desktop-packaged blocked / 0：20261006T171429Z-f16c25e3；native Linux / Windows blocked / 0：
  20261006T171457Z-102cd590 / 20261006T171503Z-7e896f69。手工原生批准／拒绝选择与安装版未验收。
  F15 继续 Main 私有凭证与连接；F16 设置接线既有 fixed setup broker，不自动管理员安装。
- F13 已推送 c094171f9ce175d8914a777110a3975346474a2e；F14 测试父 HEAD 即此提交。
- F12 已推送 8875bfbaf9c14cce9834cb1e93f596a1055403b8；F13 本次提交的测试父 HEAD 即此提交。
- F13：真实安全 Electron 窗口、固定 Preload、动态 DesktopTransport、固定 Engine 资产／私有 stdio Supervisor。
  实际修复 demo、reload / Renderer crash 保持 Engine/turn、第二实例、obsolete 参数、OS 无 TCP listener、退出 cleanup confirmed。
  unit 125 / portable 97 / regression 962，0 skip；desktop-development 10 checks pass。
  20261006T162646Z-4fe55ae3 / 20261006T162701Z-1b15650e / 20261006T163202Z-24e933d4 / 20261006T163212Z-4433d05b。
  frozen assembly 尚缺，desktop-packaged blocked / 0：20261006T162951Z-e58dd5f3；实际 Forge prePackage 拒绝缺资源。
  Native Linux / Windows blocked / 0：20261006T163223Z-4a291216 / 20261006T163229Z-e28151f4。
  Windows 10 GUI 是 development scope；Windows 11/Ubuntu/X11/Wayland、native close dialog choices 与发布仍未验收。
  正常 start 使用 provider/strict；离线 demo 仅可信开发脚本 explicit scripted/local-trusted，不产真实模型成绩。
  build_desktop.py --check 不修复 inventory；npm start 无开发 HTTP server；包加载绝不退回源码 Python。
  Main-only credential/setup/approval 接线由 F14/F15继续，完整 frozen/hardened package 由 F28完成。
- F12：EOF / 输出背压停止调度并取消，readonly 实际取消不再调用模型；未知写入不回放。
  预算／批次跳过与 execution_cancelled 区分，保留原 Harness 失败和预算行为。
  migration 003 保存 Agent / grader / environment deadline 和独立 cancel / cleanup 状态。
  Job accounting / Linux subreaper 只处理 live owned handle；严格原生 cleanup unknown 保持 indeterminate。
  固定 helper 执行 run_command / verify；PhaseLifecycle 保留服务到评分结束或 final deadline，评分结果独立。
- F12 最终 unit 124 / portable 92 / regression 956 pass，0 skip；证据
  20261006T153532Z-e789956e / 20261006T153544Z-ed76e3cf / 20261006T154553Z-92330490。
  Linux / Windows native blocked 0：20261006T153751Z-30d364a9 / 20261006T153755Z-48e0813d。
  contracts --check pass。旧 EOF 演示先显式 drain；新 bare EOF 测试必须取消队列和在途。
  旧单测同名收集冲突、四项取消误判已修复，失败证据保留，最终 956 项全过。
  直接在原测试进程重开刚强杀的 WAL 库出现过 disk I/O error；真实新 Engine 恢复测试通过。
  F25 继续 I/O 诊断与对账覆盖；F20/F21 接入 PhaseLifecycle grader，native / 付费实验继续 blocked。
- F00 已推送：3355ee07fd05978cd19284f0460694d10d4d9086，分支 codex/forgecode-v4。
- 实际基线 HEAD：d5c08a3158c764d4e797b2a1e2907e391f6414a8。
  接续时运行 git status / git rev-parse HEAD，以实际提交为准。
- F11 新增早期 file-worker、固定 stdin payload、真实 read/write/patch/search、授权观察、
  expected hash / STALE_FILE、逐文件原子 patch、受控 checkpoint blob 和实际路径身份。
  现有 CLI 默认、公开工具 schema / ToolResult 保留。工具包延迟导入；helper 不读凭证或启 RPC。
  严格模式不执行未迁移 host Hook/MCP；Explore 继承 backend。受限 checkpoint 不走宿主 restore。
- F11 新增 18 项 helper/Harness 行为，unit 117 / portable 79 / regression 936 pass，0 skip。
  证据 20261006T142311Z-40883bdb / 20261006T142329Z-a1f5d82a / 20261006T142628Z-56aaf5ff。
  Linux / Windows native blocked / 0 checks：20261006T142532Z-0939a885 / 20261006T142538Z-16bcd303。
  Shell/helper 一致性、OS TOCTOU 和安装版 Engine 可读授权未验收；strict 仍拒绝未验证能力。
  command/session 监督由 F12 接续，受控 restore 由 F16 接续。未执行付费模型或产生真实成绩。
- 实施包原件在 update_implementation_pack/forgecode-implementation-v4，
  工作规范在 docs/implementation；原件未修改。
- 用户原有 untracked：.forge/、build/、三张 architecture 图、svg_probe.txt、
  update_implementation_pack/；保持原样，不加入本次提交。
- 已实现 scripts/impl.py audit [--write]、doctor --scope development
  [--check-network]、status、verify --task F00 / --suite audit / --suite regression。
  异目录定位、AST 签名核验、缺工具／网络 blocked、JUnit 判定、真实 evidence 索引。
- 基线 powershell -NoProfile -File scripts/test.ps1 -q --tb=short：exit 0，740 passed。
- 新行为 verify --task F00：exit 0，7 passed，evidence 20261006T091942Z-d39f0b28。
- 回归 verify --suite regression：exit 0，747 passed、0 skipped，
  evidence 20261006T092001Z-c7f13bd9；日志和 JUnit 留在 .local/implementation。
- 两次旧 evidence 采集因扫描缓存太慢被取消；已修正并重跑，不计通过。
- 工具：项目 Python 3.12.13，Node 22.17.1、npm 10.9.2、uv 0.12.5，
  Git 2.52.0.windows.1。gh / pip / py 不可用；uv pip 可用。
- 本机 Windows 10 build 19045。Windows 11、原生 Ubuntu、GUI 安装版、签名、
  SRT 管理员 setup 和付费模型预算均未验收／未授权，不冒充 pass。
- 用户授权每个开发版本测试后上传 GitHub；未授权付费模型实验、管理员 setup。
- F01 完成 npm workspaces、package-lock / uv.lock / resolved release-lock、资产
  loader、固定 Node 和 Python 运行时、PyInstaller onedir / Forge Vite 真构建探针。
  uv sync --group desktop-build --link-mode=copy 和官方 npm ci 成功。构建与安装串行。
- F01 unit：9 passed（内含 4 个真实 Node 行为断言），20261006T094338Z-9ee68b20。
  回归：748 passed，20261006T093957Z-8e28ebc0；新增固定 Python 拒绝行为另有 unit 证据。
  开发 smoke 6 checks pass，20261006T094341Z-a9e7179e；实际 packaged 验收 exit 2
  / blocked，因为当前 Windows 10 不属于声明平台。npm run typecheck 通过。
- 一次误并行 npm ci 占用 native 模块导致失败 20261006T093844Z-5d88174b，已重装重验。
- GHSA-86w9-cpqp-85rv：node-forge 1.4.0 尚无已发布补丁，SRT 0.0.78 依赖它。
  security=blocked；node packaging/verify-release.mjs --release 拒绝生产发布。
  不运行 npm audit fix 的旧 SRT 降级；不自造密码学补丁。详见 ADR 001。
- F01 已推送 629b8d612a0b0587144429522561a0dd0f26bcbb。F02 提交父 HEAD 即此提交。
- F02 共享契约已实现：48 个 Engine / 6 个 Bridge / 41 个事件 payload，配置快照、
  Policy / RunSpec / Bundle；严格 UTF-8 JSON、canonical hash、权限、生成漂移检查。
  contract_only 不作为可调用能力；后续服务/Bridge/观测任务接入真实 handler。
- F02 verify --task：unit 32 pass，portable 1 pass；同判定 310 个结构样本与
  原始 seed/边界、99 个通道请求。evidence 20261006T100851Z-a7ee0375 / 20261006T100856Z-8425af60。
  最终回归 773 pass / 0 skip，20261006T100851Z-a837e8f2；types / contracts check pass。
- F02 新命令 contracts [--check]、gate --name implementation/release、suite/case 注册。
  尚未实现的原生/desktop/live verifier 返回失败，required skip/零测试不能 pass。
- F02 已推送 6c2d10f4d68cae884993b92525459271a938780e。F03 提交父 HEAD 即此提交。
- F03 完成真实 SQLite migration、OS owner lock、WAL/FULL、FK/唯一/不可变约束、
  事务受理、snapshot、CAS/epoch/对账、Journal metadata 投影/冲突隔离、Backup API、
  原子 artifact 和单项/attempt/diagnostic 配额。未迁移用户 .forge。
- 修复原 SessionJournal：append/fsync 成功后再加入 started/completed 去重集合。
  否则首次写入失败后同 ID 重试可不记录 intent/result；真实命令测试已复现并验证修复。
- F03 unit 32 pass / portable 17 pass（16 个存储行为），证据
  20261006T102610Z-94aab17c / 20261006T102615Z-0f141cab；最终回归 789 pass / 0 skip，
  20261006T102610Z-698a0127；contracts/typecheck pass。N02/N06 portable pass，
  D30 Windows 11/Ubuntu 原生验收仍 blocked，不能用 Windows 10 代替。
- 旧 owner 项重启后 reconciling，不能领取新任务；mark_indeterminate 只保留未知结果，
  完整清理/继续策略留给 F12/F25。Journal 回放仅元数据，不重跑工具、不编造费用。
- F03 后续修正迁移 CRLF/LF 校验差异，锁文件和生成契约固定 LF；新增真实迁移重开测试。
  最新 unit 32 / portable 18 / regression 790 pass，0 skip；证据
  20261006T103414Z-2a9980f6 / 20261006T103419Z-f3e3cf9a / 20261006T103452Z-166d145d。
- F04 复用 create_runtime/Conversation/TurnRunner，增加 backend/recorder/approval 注入、
  服务化 create/start/cancel/snapshot、真实权限/连接前置校验；保留CLI入口和原始终态映射。
  strict sandbox 尚未就绪时明确阻断，测试可使用显式 scripted profile，不产生成绩。
- G0 pass；G7 blocked；其他门禁未运行。保留 Python Harness、CLI、MCP、
  Hook、Explore、Feishu 和已有 Harbor runner，不复制或重写主循环。
- F03 换行符修复已推送 4e851013f2dfc3021787f9dce35d0bc7346cb7de。
- F04 实现 ApplicationServices / HarnessAdapter / session views / RuntimeBindings；
  真实 create/start/cancel/snapshot、冻结连接/预算、凭证内存注入、backend/recorder/审批回调。
  migration 002 保留原数据，服务不读项目 .env、Hook/MCP 或权限配置。CLI 默认兼容。
  工作区文件身份 OS 锁由 CLI/服务共用，Explore 继承 backend/recorder/父预算。
  原 Journal/完成契约不变；真实进程取消未知结果标 indeterminate，停止后续模型调用。
  16 新行为测试，最新 unit 32 / portable 34 pass，0 skip：
  20261006T105304Z-1b46b663 / 20261006T105310Z-27975238。
  全量回归 806 pass、0 skip，20261006T105339Z-c4dd0804。
  strict 未就绪明确拒绝。原生 Windows 11/Ubuntu 和真实模型实验仍未验收。
- F04 已推送 980e77be6ac093aef25baebcde316b468a5f07b0。
- F05 实现 python -m forge.engine、17 个真实 RPC handler、Main ACL、严格 JSONL、
  32 项 batch、stderr 隔离、控制/数据有界队列、真实工作调度；握手前不派发任务。
  契约 manifest hash 与发布资产 manifest 分开；scripted fixture 仅 test profile。
  持久 HMAC 游标绑定 store/profile/scope，snapshot 高水位、ACK 后补读、保留期缺口，
  turn.started / cancellation.requested / turn.finished 在真实事务中写入。
  slow-reader 测试实际填满管道后仍受理 start/cancel，不声称大帧可抢占或已完成 UI 验收。
- F05 最新 unit 32 / portable 44 / regression 816 pass，0 skip；证据
  20261006T111751Z-a64edfea / 20261006T111756Z-f5819b93 / 20261006T111902Z-ff8046cd。
  contracts --check pass。早期测试误用已注册 F05 作为失败样例，递归启动验收；
  已停止本任务进程，改用隔离空 registry 的单元断言并重跑。中断运行不计通过。
- F06 已完成版本化/限步 ScriptedModel、六个公开 fixture 基础结构、真实读改测
  RPC demo；只能显式 local-trusted，OS 隔离验收仍 blocked，不能导出真实模型成绩。
- F05 已推送 cb724ac5cbd2b874f9d4fdd2f7501905d44c53d5。
- F06 已实现 forge.testing.demo / RpcClient、版本化限步脚本、精确 test 审批、六个实际
  fixture 基础结构。verify --task F06 unit 54 / portable 49 pass、0 skip；证据
  20261006T113646Z-1cb5c7d9 / 20261006T113651Z-324b93bb。完整回归 843 pass、0 skip，
  20261006T113811Z-54d4f9dd；contracts --check pass。验收后提交/push F06。
  早期报告查询/视图和旧脚本兼容失败已修复，失败索引保留。
- 演示 python -m forge.testing.demo [--output-dir 新目录]；真实文件/进程、修复前后
  unittest 结果及 native Journal/SQLite 哈希。scripted/local-trusted，不能作为真实榜单。
  断开响应后 drain，再开启 Engine 用 action.get 和同 ID 重试，确认只有一个 turn/Journal。
- F07 已阅读任务卡、第 10—12 章和锁定 SRT 的 config/schema 类型。allowRead 是 deny
  例外，不能伪装严格读取白名单；需要路径身份/链接/特殊路径拒绝及独立能力要求检查。
- F06 已推送 b5d2b4f5a79d9691e0a94ce430c38375911b4adc。
- F07 已实现 forge/sandbox policy/capabilities/path_policy，规范化/hash/服务前置复核、
  强要求拒绝、真实身份/硬链接/junction/特殊路径、外部 gitdir/commondir 保护。
  capability verification schema 及 3 个生成文件已更新；未探测无 verified 能力。
  unit 96 / portable 56 pass，0 skip，证据 20261006T115350Z-bfd58d91 /
  20261006T115355Z-edb1d80e；contracts --check pass。完整回归 892 pass、0 skip，
  20261006T115517Z-a30f1c2e。Native OS 隔离与动态 shell/file-worker 验收 blocked。
- F07 已推送 0a3c03f6f5e88e50595b1c8c78fca311f4c6c247。
- F08 实现独立 SrtBackend、固定 Node/资产/1413 文件运行库存、六方法 Bridge、受限
  dispatcher、单独 payload stdin、raw byte/增量 UTF-8/输出与 session 配额、有界队列、
  execution hash 幂等与 owner、重复 close。新 command argv 保留空参数；生成契约已同步。
  真实 NODE_OPTIONS 注入测试未执行宿主 loader，输出伪造 RPC 只成为数据 envelope。
  unit 100 / portable 60 / regression 900 pass，0 skip；证据
  20261006T122617Z-467c0ff0 / 20261006T122623Z-252a9f8b / 20261006T122834Z-df2df90c。
  contracts --check / npm run typecheck pass。F08 验收后提交并推送。
- Bridge probe 仅系统/依赖前提，不制造 verified 能力；严格 prepare 在原生边界/清理证据
  未齐时拒绝。SRT reset 为 best effort，native 初始化后 cleanup unknown；不能据
  remaining_processes=0 推断无残留。仅从未初始化/启动的 Bridge close 可确认 clean。
  F09/F10 的受控验证器可调用 initializeNative 底层入口；它不注册 RPC，不接收模型能力。
- Node 路径由 release-lock 固定；安装根/compiled modules 需与 trusted control 根分开，
  不允许工作区覆盖安装目录。prepare 的 control-root 保护会进入冻结 policy hash，
  F11 接入存储/工具时需在创建 policy 前固定这些根。现有 Harness/CLI 入口保持。
- 构建命令 python scripts/build_bridge.py；校验 --check，inventory 不能在测试中自修复。
  packages/contracts 和 sandbox_bridge 编译强制 LF，相关资产清单固定 LF。
  Windows 11/Ubuntu 原生、动态 .env、权限/后代清理和真实模型实验仍 blocked；GHSA 保持。
- F08 已推送 0cabc1873caa83bdb096f4532a4c5707196aa736。
- F09 新增只读系统/项目 Doctor、固定 bwrap 真实前提探针、受控 Linux native fixture、
  namespace/start tick/父子链绑定的保守取消；不按名称或孤立 PID 杀进程。
  真实 canary 包括动态 .env、外部合成文件、代理变量删除、直连、Unix socket、嵌套进程。
  动态敏感路径若可读必须 fail；SRT 初始化后 session cleanup 仍 unknown，不制造 verified。
- F09 unit 110 / portable 60 / regression 910 pass，0 skip：
  20261006T125123Z-88d982fc / 20261006T125133Z-3780e12c / 20261006T125856Z-b7b52c5d。
  sandbox-linux 真实入口 blocked / 0 checks，20261006T125256Z-c6d9de11；Windows 10
  不满足 Ubuntu 22.04/24.04，doctor exit 2、task 聚合 exit 1。不是空集合 pass。
  早一轮回归误把非 test_ 的 native verifier 路径纳入 case_ids；已修复并重跑，旧证据保留。
- Ubuntu 内核/隔离、C10 显式受控公共 HTTP endpoint、F12/F25 完整 session cleanup 尚缺。
  当前 runtime inventory 1415 文件；构建和 --check 均成功。F09 验收后提交/push。
- F10 已审计实际 srt-win --help 与上游 install/status/ACL API：诊断只读，install 真 UAC；
  重复 install 可能更新共用用户凭据，不能把 repair 当无影响动作；不得自动 uninstall /
  force / acl recover。现无只读共享 ACL holder 列表，未知共享活动需保守阻断。
  此审计没有执行管理员 setup，没有 Windows 11 原生通过证据；F10 尚未写代码。
- F09 已推送 cc2df6c99012bc7ee8cd21147fcccd4408479d8a。
- F10 正在实施：固定 Main setup broker / 三 action Bridge launch、实际上游 UAC API、
  保守共享 repair 阻断、OS Known Folder worker + OS lock + unresolved marker、真实 Windows
  Doctor / NTFS / helper 状态、双平台共同 native canary。没有执行管理员 setup。
  首轮 unit 116 / portable 61 / native Windows blocked 0；证据
  20261006T132815Z-f8ebdcb8 / 20261006T132833Z-f342b184 / 20261006T133034Z-632dd5ea。
  实际 raw srt-win user 有额外 wrapper / snake_case，Python Doctor 映射已修正并新增单测；
  上述旧报告的账户字段遗漏不能作为最终诊断证据。需重跑最终 task + regression 后推送。
  此前回归 20261006T133122Z-68006520 为 917 pass，是映射修正前启动；不作为最终 source 验收。
  最新局部 doctor 测试 12 pass；setup blocked 退出码 0 的问题已修复为 2，stdout 完整写出。
  接续先核对 git status；F10 改动未提交，用户原有 untracked 仍保留。F11 卡 / 第 9、11 章
  已读并盘点现有工具 IO、Tracker / Checkpoint；F11 尚未写代码。
- F10 最终代码验收 unit 117 / portable 61 / regression 918 pass，0 skip：
  20261006T134127Z-76e53bec / 20261006T134145Z-5e720e59 / 20261006T134442Z-98283708。
  Windows native 20261006T134352Z-ad842262、Linux native 20261006T134433Z-be4553ee
  均 blocked / 0 checks / 底层 exit 2。实际 raw status 映射已修复：SRT 账户 / credential
  缺失；BFE cannot-read、pwsh7 缺失、NTFS。没有自动 setup / force / recover / CA 安装。
  首次 setup 需账号/group/credential/marker 全缺；cannot-read 不单独阻止明确确认的 UAC。
  所有共享安装 repair 均保守 blocked；正常 task 不提权。Main 仅编译，F13/F14 接入 GUI。
  Worker 锁/未解决 grants marker 真实测试；全 Job/ACL 清理 F12/F25 仍需原生证明。
  inventory 1418 文件已实际构建/--check。F10 验收后提交/push，再开始 F11。
- F10 已推送 d17c361e147e07109c3f74761cd7df7954fad5b9；F11 进入开发。
