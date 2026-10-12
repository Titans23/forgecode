# ForgeCode 当前交接

开发目录已统一到D:\projects\forgecode的main，基线7564021。用户明确授权不备份、不保留旧改动与会话。原独立checkout已释放main，不再用于开发。

T02代码与文档整理已完成：移除796个旧文档及3个停用脚本，修正28条契约失效测试引用。当前源码提交817e826，最终指纹2983fa994337cd9e29a8e212635435cb5c350388b0b3be5b0151d7e5ae570df1。质量、契约及安全检查通过，真实Windows原生6项通过。完整回归初轮1318通过/5失败/7警告；恢复冻结输入后delivery9、documented11项通过，将退役F32改为F00后registry3项通过。校验器登记terminal-native的实际JUnit格式，新增行为测试并补测gates17项通过；不提升原生发布准入。逐项核对最终1324个收集身份、原报告与补测，缺失/额外/重复为0；未宣称再次单进程全量通过。见[回归索引](evidence/T02-regression-20261012.json)，原失败记录保留。

外部清理仍blocked：旧独立checkout已从Git解绑，但磁盘残留因长路径删除失败，直接递归删除被自动审批拒绝；用户目录C:\Users\3dimaging\.forge\projects\forgecode-59ecdad9853ada94仍有15个旧会话文件及检查点，删除也被自动审批拒绝，返回blocked by policy。D目录中的旧改动、.forge、.local历史与桌面产物已清除，没有创建备份。不得将这些外部残留误记为已删除。

核心路径：forge.launch → CLI/benchmark；forge.api → runtime.factory → Conversation/TurnRunner。application/engine仍有实际调用，不作为孤立目录删除。main/renderer是RPC权限角色，当前不交付桌面产品。

Linux22/24原生验收、付费模型、实际训练与生产签名独立未完成，不借清理提升状态。没有新增模型预算或管理员设置授权。
