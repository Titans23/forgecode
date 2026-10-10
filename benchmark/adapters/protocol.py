"""Bounded, content-addressed collection and independent reward normalization."""
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import stat

from forge.application.models import ContractError, canonical_hash, strict_loads


NORMALIZATION_VERSION = 'forge.harbor.normalization.v1'
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


def normalize(*, runner_exit, result, agent_status=None, cleanup='unknown', provenance='official-runner'):
    """Shell exit, internal completion and authoritative verifier reward are separate."""
    exception = (result or {}).get('exception_info') or {}
    error_type = exception.get('exception_type', '')
    rewards = ((result or {}).get('verifier_result') or {}).get('rewards') or {}
    reward, grade_result = reward_result(rewards.get('reward'))
    if error_type in {'RewardFileNotFoundError','RewardFileEmptyError','RewardFileInvalidError'}:
        grade_state, grade_result, reward = 'unscored', 'unknown', None
    elif error_type.startswith('Verifier') or error_type in {'GraderError','InvalidRewardError'}:
        grade_state, grade_result, reward = 'grader_error', 'unknown', None
    else:
        grade_state = 'graded' if reward is not None else 'unscored'
    origin = None
    if error_type.startswith(('Environment','AgentSetup')):
        origin = 'environment_setup'
    elif grade_state == 'grader_error':
        origin = 'grader_infrastructure'
    elif runner_exit != 0:
        origin = 'runner_crash'
    agent_outcome = (agent_status or {}).get('status', 'indeterminate')
    if agent_outcome not in {'completed','failed','cancelled','timed_out','indeterminate'}:
        agent_outcome = 'indeterminate'
    return {'normalization_version':NORMALIZATION_VERSION,'execution_state':'error' if origin else 'finished',
        'grading_state':grade_state,'grade_result':grade_result,'raw_reward_decimal':reward,
        'agent_outcome':agent_outcome,'cleanup_state':cleanup,'error_origin':origin,
        'runner_exit_code':runner_exit,'exception_type':error_type or None,
        'reason':'missing_or_invalid_reward' if grade_state=='unscored' else error_type or None,
        'execution_label':'official-environment','provenance':provenance,
        'official_rewards':rewards}


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
