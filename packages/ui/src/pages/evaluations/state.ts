import type { EvaluationChoices, EvaluationSnapshotResult } from '@forgecode/contracts';
export type Choices=EvaluationChoices;
export type Snapshot=EvaluationSnapshotResult;
export type Trial=Snapshot['trials'][number];
export type Attempt=Snapshot['attempts'][number];
export function attemptViews(trial:Pick<Trial,'id'|'selected_attempt_id'>,attempts:Attempt[]) {
  const all=attempts.filter(a=>a.trial_id===trial.id).slice().sort((a,b)=>a.attempt_no-b.attempt_no);
  return {all,first:all[0]??null,selected:all.find(a=>a.id===trial.selected_attempt_id)??null};
}
export function resultLabel(attempt:Pick<Attempt,'grade_result'|'grade_state'>|null) {
  return !attempt?'未评分':attempt.grade_state==='grader_error'?'评分器错误':attempt.grade_state!=='graded'?'未评分':attempt.grade_result==='pass'?'通过':attempt.grade_result==='fail'?'失败':'未评分';
}
export function missingLabel(count:number) { return count?`${count} 项产物缺失或无法复核`:'未检测到已索引产物缺失（不证明评分来源）'; }
export function budgetProblem(c:Choices):string|null {
  if (!c.task_ids.length || c.task_ids.length*c.repeats>10000) return '计划题数应为 1—10000。';
  for(const value of [c.repeats,c.max_infrastructure_attempts,c.attempt_wall_seconds,c.trial_wall_seconds,c.max_model_requests_per_attempt,c.max_tool_calls_per_attempt,c.max_context_tokens,c.max_output_tokens])
    if(!Number.isSafeInteger(value)||value<1) return '预算必须使用正整数。';
  if(c.trial_wall_seconds<c.attempt_wall_seconds) return 'trial 时间不能小于一次 attempt 的时间。';
  if(c.max_context_tokens<=c.max_output_tokens) return '上下文上限必须大于模型输出上限。';
  if(c.spend_policy==='human_unbounded') {
    if(c.spend_ceiling!==null) return '无上限政策不能同时指定金额上限。';
  } else if(c.spend_ceiling===null||c.spend_ceiling.length>32||!/^(0|[1-9][0-9]*)(\.[0-9]*[1-9])?$/.test(c.spend_ceiling)) return '支出上限需要非负十进制字符串。';
  if(c.network_mode==='deny_direct'&&c.allowed_domains.length) return '禁止直连网络时，域名列表应为空。';
  return null;
}
