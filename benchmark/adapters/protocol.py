"""Bounded, content-addressed collection and independent reward normalization."""
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import stat

from forge.application.models import ContractError, canonical_hash, strict_loads


NORMALIZATION_VERSION = 'forge.harbor.normalization.v1'
MODEL_FAILURE_REASONS = {
    'server_error', 'stream_interrupted', 'empty_model_response', 'incomplete_tool_call',
    'stream_termination_missing', 'connection_error', 'timeout', 'rate_limit',
    'authentication_error', 'invalid_model_protocol', 'conflicting_model_content',
    'provider_protocol_error', 'model_protocol_error', 'overloaded', 'provider_error',
    'http_401', 'http_403', 'http_429',
}
CATEGORIES = ('passed', 'acceptance_failed', 'model_failure', 'harness_failure',
              'environment_failure', 'grader_failure', 'budget_exhausted',
              'cancelled_or_incomplete', 'unknown')
MAX_FILES = 20000
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024


def file_bytes(path, *, limit=MAX_FILE_BYTES):
    path = Path(path)
    info = path.lstat()
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400 or info.st_nlink > 1:
        raise ContractError('Collection refuses links and reparse points', kind='POLICY_DENIED', code=-32010)
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ContractError('Collected artifact exceeds its bound', kind='ARTIFACT_LIMIT', code=-32010)
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit or path.stat().st_size != len(value):
        raise ContractError('Artifact changed during collection', kind='STALE_REVISION', code=-32010)
    return value


def capture_tree(root):
    """Freeze bytes before interpreting them; never execute imported result files."""
    root = Path(root)
    if root.is_symlink() or getattr(root.lstat(), 'st_file_attributes', 0) & 0x400:
        raise ContractError('Collection root must be a real directory', kind='POLICY_DENIED', code=-32010)
    captured, total = {}, 0
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ContractError('Collection refuses directory links', kind='POLICY_DENIED', code=-32010)
        if path.is_dir():
            continue
        if len(captured) >= MAX_FILES:
            raise ContractError('Collection exceeds file count', kind='ARTIFACT_LIMIT', code=-32010)
        value = file_bytes(path)
        total += len(value)
        if total > MAX_TOTAL_BYTES:
            raise ContractError('Collection exceeds total bytes', kind='ARTIFACT_LIMIT', code=-32010)
        captured[path.relative_to(root).as_posix()] = value
    # Concurrent writes cannot silently change the frozen input.
    for name, value in captured.items():
        if file_bytes(root / name) != value:
            raise ContractError('Collection changed during capture', kind='STALE_REVISION', code=-32010)
    return captured


def content_hash(captured):
    return canonical_hash({name:sha256(value).hexdigest() for name,value in captured.items()})


def grader_cache_key(*, grader_hash, environment_hash, task_revision, artifact_hash):
    return canonical_hash({'normalization':NORMALIZATION_VERSION,'grader':grader_hash,
        'environment':environment_hash,'task_revision':task_revision,'artifact':artifact_hash})


def reward_result(value):
    if isinstance(value, bool) or value is None:
        return None, 'unknown'
    try:
        reward = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None, 'unknown'
    if not reward.is_finite() or reward < 0 or reward > 1:
        return None, 'unknown'
    text = format(reward, 'f')
    text = text.rstrip('0').rstrip('.') if '.' in text else text
    return text or '0', 'pass' if reward == 1 else 'fail'


def classify_result(result, agent_status=None, *, verifier_fault=None, cancelled=False):
    """Classify terminal evidence; recovered errors never replace the final outcome."""
    result, agent_status = result or {}, agent_status or {}
    exception = result.get('exception_info') or {}
    error = exception.get('exception_type', '')
    reason = agent_status.get('stop_reason') or ''
    status = agent_status.get('status', 'indeterminate')
    rewards = (result.get('verifier_result') or {}).get('rewards') or {}
    reward, grade = reward_result(rewards.get('reward'))
    grader_failure = bool(verifier_fault or error.startswith(('Verifier', 'RewardFile'))
                          or error in {'GraderError', 'InvalidRewardError'})
    grading = 'grader_error' if grader_failure else 'graded' if reward is not None else 'unscored'
    # Raw reward remains evidence even when the grader itself failed.
    verdict = grade if grading == 'graded' else 'unknown'
    terminal_faults = [value for value in (reason, error, verifier_fault) if value]
    if reason.endswith('_budget_exhausted') or error == 'AgentTimeoutError' or status in {'timed_out', 'budget_exhausted'}:
        category, execution, termination = 'budget_exhausted', 'finished', reason or 'agent_deadline'
    elif cancelled or status == 'cancelled' or reason == 'cancelled':
        category, execution, termination = 'cancelled_or_incomplete', 'cancelled', 'cancelled'
    elif grader_failure:
        category, execution, termination = 'grader_failure', 'finished', verifier_fault or error
    elif reason in MODEL_FAILURE_REASONS or error.startswith(('Api', 'API', 'Anthropic', 'Provider', 'ModelCall', 'ModelProtocol', 'RateLimit', 'Authentication')):
        category, execution, termination = 'model_failure', 'error', reason or error
    elif error.startswith(('Environment', 'AgentSetup', 'Docker', 'ImageBuild')):
        category, execution, termination = 'environment_failure', 'error', error
    elif error in {'ToolExecutionError', 'HarnessError', 'AgentExecutionError'} or reason in {'tool_execution_failed', 'harness_error'}:
        category, execution, termination = 'harness_failure', 'error', reason or error
    elif error:
        category, execution, termination = 'unknown', 'error', error
    elif reward is not None:
        category, execution, termination = ('passed' if grade == 'pass' else 'acceptance_failed'), 'finished', reason or 'completed'
    elif not result.get('finished_at') and status == 'indeterminate':
        category, execution, termination = 'cancelled_or_incomplete', 'unfinished', 'incomplete'
    else:
        category, execution, termination = 'unknown', 'finished', reason or 'missing_or_invalid_reward'
    return {'category': category, 'execution_state': execution, 'grading_state': grading,
            'grade_result': verdict, 'termination_reason': termination, 'agent_outcome': status,
            'raw_reward_decimal': reward, 'official_rewards': rewards, 'observed_faults': terminal_faults}


def normalize(*, runner_exit, result, agent_status=None, cleanup='unknown', provenance='official-runner'):
    """Shell exit, internal completion and authoritative verifier reward are separate."""
    classified = classify_result(result, agent_status)
    exception = (result or {}).get('exception_info') or {}
    error_type = exception.get('exception_type', '')
    rewards = classified['official_rewards']
    grade_state, grade_result = classified['grading_state'], classified['grade_result']
    reward = classified['raw_reward_decimal'] if grade_state == 'graded' else None
    origin = None
    if error_type.startswith(('Environment','AgentSetup')):
        origin = 'environment_setup'
    elif grade_state == 'grader_error':
        origin = 'grader_infrastructure'
    elif runner_exit != 0:
        origin = 'runner_crash'
    agent_outcome = (agent_status or {}).get('status', 'indeterminate')
    if classified['category'] == 'budget_exhausted':
        agent_outcome = 'budget_exhausted'
    if agent_outcome not in {'completed','failed','cancelled','timed_out','indeterminate', 'budget_exhausted'}:
        agent_outcome = 'indeterminate'
    return {'normalization_version':NORMALIZATION_VERSION,'execution_state':'error' if origin else 'finished',
        'grading_state':grade_state,'grade_result':grade_result,'raw_reward_decimal':reward,
        'agent_outcome':agent_outcome,'cleanup_state':cleanup,'error_origin':origin,
        'runner_exit_code':runner_exit,'exception_type':error_type or None,
        'reason':'missing_or_invalid_reward' if grade_state=='unscored' else error_type or None,
        'execution_label':'official-environment','provenance':provenance,
        'official_rewards':rewards, 'category': classified['category'],
        'termination_reason': classified['termination_reason'], 'observed_faults': classified['observed_faults']}


def collect_harbor(root, *, task_name, task_checksum, runner_exit):
    captured = capture_tree(root)
    # A job has one attempt by construction. Ambiguous/mismatched results are refused.
    candidates = [name for name in captured if name.count('/')==1 and name.endswith('/result.json')]
    if len(candidates) > 1:
        raise ContractError('Multiple Harbor trials for a single attempt', kind='EVENT_CONFLICT', code=-32010)
    result, agent_status = None, None
    if candidates:
        name = candidates[0]
        from harbor.models.trial.result import TrialResult
        raw = strict_loads(captured[name])
        TrialResult.model_validate(raw)
        if raw['task_name'] != task_name or raw['task_checksum'] != task_checksum:
            raise ContractError('Official result task identity changed', kind='EVENT_CONFLICT', code=-32010)
        result = raw
        status_file = name.rsplit('/',1)[0] + '/agent/forgecode-result.json'
        if status_file in captured:
            agent_status = strict_loads(captured[status_file])
    artifact_bytes = {k.split('/',1)[1]:v for k,v in captured.items() if '/artifacts/' in k or k.endswith('/agent/forgecode-pregrade.patch')}
    return {'files':captured,'result':result,'agent_status':agent_status,'runner_exit':runner_exit,
        'collection_hash':content_hash(captured),'artifact_hash':content_hash(artifact_bytes),
        'artifact_present':bool(artifact_bytes)}
