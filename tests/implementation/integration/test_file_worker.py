"""Actual isolated-interpreter helper IO. These portable tests never claim an OS sandbox."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from forge.application.models import canonical_hash
from forge.engine.persistence import new_id
from forge.sandbox.launcher import bridge_environment


def fixture(tmp_path):
    root = tmp_path / '中文 project'
    root.mkdir()
    control = tmp_path / 'control'
    control.mkdir()
    outside = tmp_path / 'synthetic-private'
    outside.mkdir()
    (outside / 'secret.txt').write_text('synthetic-sensitive')
    info = root.stat()
    workspace = {'id': new_id('ws'), 'canonical_path': str(root), 'file_identity': f'{info.st_dev}:{info.st_ino}'}
    policy = {'schema_version': 'forge.sandbox.policy.v1', 'policy_id': new_id('policy'), 'workspace_id': workspace['id'],
        'filesystem': {'read_mode': 'backend_default_with_protected_paths', 'read_roots': [str(root)],
            'write_roots': [str(root)], 'protected_paths': [str(control), str(outside)],
            'deny_overrides_allow': True, 'reject_unsafe_links': True},
        'network': {'mode': 'deny_direct', 'allowed_domains': [], 'dns_isolation_required': False},
        'limits': {'memory_bytes': None, 'disk_bytes': None, 'pids': None, 'wall_time_seconds': 30,
            'command_output_bytes': 1048576, 'session_artifact_bytes': 104857600},
        'environment_keys': [], 'fallback': 'deny', 'session_mutation': 'replace_session'}
    return root, control, workspace, policy


def invoke(control, workspace, policy, operation='tool', **values):
    request = {'schema_version': 'forge.file-worker.request.v1', 'request_id': new_id('exec'), 'workspace': workspace,
        'policy': policy, 'policy_hash': canonical_hash(policy), 'operation': operation, **values}
    child = subprocess.run([sys.executable, '-I', '-B', '-m', 'forge.engine', 'file-worker'],
        input=json.dumps(request).encode(), cwd=Path(__file__).resolve().parents[3], env=bridge_environment(control),
        capture_output=True, timeout=30)
    assert child.returncode in (0, 2), child.stderr.decode(errors='replace')
    result = json.loads(child.stdout)
    assert result['request_id'] == request['request_id'] and result['policy_hash'] == request['policy_hash']
    return result


def test_early_worker_bootstrap_never_loads_engine_server_model_or_credentials(tmp_path):
    _, control, workspace, policy = fixture(tmp_path)
    (control / 'config.json').write_text('{"api_key":"synthetic-private-key"}')
    result = invoke(control, workspace, policy, 'bootstrap')
    assert result['status'] == 'ok'
    assert not {'forge.engine.rpc', 'forge.application.services', 'forge.config', 'forge.runtime.providers'} & set(result['result']['loaded_modules'])
    assert 'synthetic-private-key' not in json.dumps(result)


def test_worker_real_write_read_search_and_owned_metadata(tmp_path):
    root, control, workspace, policy = fixture(tmp_path)
    write = invoke(control, workspace, policy, name='write_file', arguments={'path': 'a.txt', 'content': 'hello 中文\r\n'})
    assert write['status'] == 'ok' and write['result']['success']
    assert (root / 'a.txt').read_bytes() == 'hello 中文\r\n'.encode()
    read = invoke(control, workspace, policy, name='read_file', arguments={'path': 'a.txt'})
    assert read['result']['metadata']['sha256'] == sha256((root / 'a.txt').read_bytes()).hexdigest()
    search = invoke(control, workspace, policy, name='grep', arguments={'pattern': 'hello', 'path': '.'})
    assert search['result']['success'] and 'hello' in search['result']['content']
    assert write['observations']['a.txt']['sha256'] == read['result']['metadata']['sha256']


def test_stale_patch_does_not_overwrite_external_edit(tmp_path):
    root, control, workspace, policy = fixture(tmp_path)
    target = root / 'public.py'
    target.write_text('old\n')
    expected = sha256(target.read_bytes()).hexdigest()
    target.write_text('user edit\n')
    result = invoke(control, workspace, policy, name='apply_patch', arguments={'patch': '*** Begin Patch\n*** Update File: public.py\n@@\n-old\n+new\n*** End Patch'}, expected_hashes={'public.py': expected})
    assert result['status'] == 'error' and result['error']['code'] == 'STALE_FILE'
    assert target.read_text() == 'user edit\n'


def test_validated_atomic_patch_retains_original_tool_result_schema(tmp_path):
    root, control, workspace, policy = fixture(tmp_path)
    target = root / 'public.py'
    target.write_text('old\n')
    result = invoke(control, workspace, policy, name='apply_patch', arguments={'patch': '*** Begin Patch\n*** Update File: public.py\n@@\n-old\n+new\n*** End Patch'}, expected_hashes={'public.py': sha256(target.read_bytes()).hexdigest()})
    assert result['status'] == 'ok' and result['result']['success']
    assert target.read_text() == 'new\n'
    assert set(result['result']) == {'success', 'summary', 'content', 'error', 'metadata'}
    assert result['observations']['public.py']['sha256'] == sha256(target.read_bytes()).hexdigest()


@pytest.mark.parametrize('path', ['.env', '.git/config', '.forge/state.json', '../synthetic-private/secret.txt', '../control/config.json'])
def test_worker_protected_reads_are_real_denials(tmp_path, path):
    root, control, workspace, policy = fixture(tmp_path)
    (root / '.env').write_text('synthetic-env')
    result = invoke(control, workspace, policy, name='read_file', arguments={'path': path})
    assert result['status'] == 'error' and result['error']['code'] == 'POLICY_DENIED'
    assert 'synthetic-env' not in json.dumps(result) and 'synthetic-sensitive' not in json.dumps(result)


def test_scan_and_checkpoint_snapshot_only_read_authorized_content(tmp_path):
    root, control, workspace, policy = fixture(tmp_path)
    (root / 'a.txt').write_text('public')
    (root / '.env').write_text('synthetic-env')
    secret = root / 'private'
    secret.mkdir()
    (secret / 'secret.txt').write_text('synthetic-sensitive')
    policy['filesystem']['protected_paths'].append(str(secret))
    scan = invoke(control, workspace, policy, 'scan')
    assert scan['result']['complete'] and set(scan['result']['files']) == {'a.txt'}
    snapshot = invoke(control, workspace, policy, 'snapshot', paths=['a.txt', 'missing.txt'])
    assert snapshot['result']['files']['a.txt']['content_base64'] == 'cHVibGlj'
    assert not snapshot['result']['files']['missing.txt']['exists']
    assert 'synthetic-sensitive' not in json.dumps([scan, snapshot])


def test_worker_rejects_hardlinks_and_incomplete_observer_does_not_claim_clean(tmp_path):
    import os
    root, control, workspace, policy = fixture(tmp_path)
    source = root / 'a.txt'
    source.write_text('public')
    os.link(source, root / 'b.txt')
    read = invoke(control, workspace, policy, name='read_file', arguments={'path': 'b.txt'})
    assert read['status'] == 'error' and read['error']['code'] == 'POLICY_DENIED'
    scan = invoke(control, workspace, policy, 'scan')
    assert not scan['result']['complete']


def test_recursive_delete_cannot_erase_protected_descendant(tmp_path):
    root, control, workspace, policy = fixture(tmp_path)
    directory = root / 'folder'
    directory.mkdir()
    (directory / '.env').write_text('synthetic-env')
    (directory / 'a.txt').write_text('public')
    result = invoke(control, workspace, policy, name='remove_directory', arguments={'path': 'folder', 'recursive': True})
    assert result['status'] == 'error' and result['error']['code'] == 'POLICY_DENIED'
    assert (directory / 'a.txt').read_text() == 'public'


def test_real_helper_backend_observer_checkpoint_and_external_edit(tmp_path, monkeypatch):
    import asyncio
    from forge.permissions.policy import PermissionManager
    from forge.runtime.agent_loop import mutation_target_paths
    from forge.runtime.executor import ToolExecutor
    from forge.runtime.state import ToolCall
    from forge.sandbox.file_client import FileWorkerClient
    from forge.sandbox.tool_backend import FileToolBackend
    from forge.sessions.checkpoint import CheckpointError, CheckpointStore
    from forge.sessions.store import SessionStore
    from forge.tools import create_default_registry
    root, control, workspace, policy = fixture(tmp_path)
    (root / 'a.txt').write_text('old\n')
    original = (root / 'a.txt').read_bytes()
    client = FileWorkerClient(workspace, policy, local_control=control)
    backend = FileToolBackend(client)
    backend.check_ready(policy, required_mode='local-trusted')
    with pytest.raises(Exception):
        backend.check_ready(policy, required_mode='strict')
    registry = create_default_registry(root, tool_backend=backend)
    tracker = registry.workspace_tracker
    checkpoints = CheckpointStore(root, control / 'checkpoints')
    journal = SessionStore(root, data_root=control / 'sessions').create(model='scripted', provider='anthropic')
    def forbidden(*args, **kwargs):
        pytest.fail('Control plane attempted task-content IO')
    monkeypatch.setattr('forge.runtime.workspace.fingerprint_path', forbidden)
    monkeypatch.setattr(checkpoints, '_snapshot', forbidden)
    executor = ToolExecutor(registry, PermissionManager(root, mode='auto', load_stored_rules=False),
        workspace_tracker=tracker, checkpoint_store=checkpoints, backend=backend,
        session_journal=journal, path_resolver=lambda call: mutation_target_paths(call, maximum=None))
    async def scenario():
        await tracker.begin_turn()
        assert tracker.available
        read = await executor.execute(ToolCall(0, 'read', 'read_file', {'path': 'a.txt'}))
        assert read.result.success and read.result.metadata['execution_boundary'] == 'local-trusted-file-worker'
        checkpoint = checkpoints.begin()
        patch = {'patch': '*** Begin Patch\n*** Update File: a.txt\n@@\n-old\n+new\n*** End Patch'}
        edit = await executor.execute(ToolCall(0, 'edit', 'apply_patch', patch), checkpoint_id=checkpoint)
        assert edit.result.success and edit.workspace_change.revision == 1
        assert tracker.changed_paths == ('a.txt',)
        metadata = json.loads((checkpoints.directory / (checkpoint + '.json')).read_text())
        assert metadata['files']['a.txt']['before_sha256'] == sha256(original).hexdigest()
        assert (checkpoints.blob_directory / sha256(original).hexdigest()).read_bytes() == original
        with pytest.raises(CheckpointError):
            checkpoints.restore(checkpoint)
        (root / 'a.txt').write_text('user edit\n')
        stale = await executor.execute(ToolCall(0, 'stale', 'apply_patch', {'patch':
            '*** Begin Patch\n*** Update File: a.txt\n@@\n-user edit\n+overwrite\n*** End Patch'}), checkpoint_id=checkpoint)
        assert not stale.result.success and stale.result.error.code == 'STALE_FILE'
        assert (root / 'a.txt').read_text() == 'user edit\n'
        assert tracker.revision >= 2
        assert journal.path.exists() and journal.path.is_relative_to(control)
    asyncio.run(scenario())


def test_recursive_checkpoint_collects_real_authorized_descendant_bytes(tmp_path):
    root, control, workspace, policy = fixture(tmp_path)
    folder = root / 'folder'
    folder.mkdir()
    (folder / 'a.txt').write_text('public')
    result = invoke(control, workspace, policy, 'snapshot', paths=['folder'], recursive=True)
    assert set(result['result']['files']) == {'folder', 'folder/a.txt'}
    assert result['result']['files']['folder/a.txt']['content_base64'] == 'cHVibGlj'


def test_real_harness_preserves_read_answer_completion_through_helper(tmp_path):
    import asyncio
    from forge.config import ForgeConfig
    from forge.engine.test_profile import ScriptedModelClient
    from forge.permissions.policy import PermissionManager
    from forge.runtime.dependencies import RuntimeBindings
    from forge.runtime.factory import create_runtime
    from forge.runtime.state import TurnCompleted
    from forge.sandbox.file_client import FileWorkerClient
    from forge.sandbox.tool_backend import FileToolBackend
    root, control, workspace, policy = fixture(tmp_path)
    (root / 'value.txt').write_text('B')
    config = ForgeConfig(api_key='synthetic-private-model-key', model_id='scripted-test', max_tokens=1024)
    responses = [{'usage': {'input_tokens': 11, 'output_tokens': 3}, 'tool_calls': [
        {'id': 'read', 'name': 'read_file', 'arguments': {'path': 'value.txt'}}]},
        {'usage': {'input_tokens': 12, 'output_tokens': 4}, 'tool_calls': [
        {'id': 'finish', 'name': 'finish_task', 'arguments': {'task_kind': 'answer', 'status': 'completed', 'summary': 'The value is B.'}}]}]
    backend = FileToolBackend(FileWorkerClient(workspace, policy, local_control=control))
    bindings = RuntimeBindings(config=config, data_root=control / 'harness', backend=backend,
        model_client_factory=lambda cfg, **kwargs: ScriptedModelClient(cfg, responses, **kwargs),
        permission_manager=PermissionManager(root, mode='auto', load_stored_rules=False), trusted_extensions=False,
        task_relation='new', max_model_calls=4, max_tool_calls=6, wall_seconds=30)
    async def scenario():
        conversation, journal, _ = create_runtime(root, bindings=bindings)
        try:
            events = [event async for event in conversation.stream('Read value.txt and answer.')]
            terminal = next(event for event in events if isinstance(event, TurnCompleted))
            assert terminal.result.status == 'completed'
            assert conversation.workspace_tracker.observer is backend.observer
            assert journal.path.is_relative_to(control)
        finally:
            await conversation.runtime_close()
    asyncio.run(scenario())


@pytest.mark.parametrize('raw', [b'[]', b'{"a":1,"a":2}', b'{"operation":"bootstrap"}'])
def test_invalid_worker_frames_return_one_bounded_error_without_server(tmp_path, raw):
    _, control, _, _ = fixture(tmp_path)
    child = subprocess.run([sys.executable, '-I', '-B', '-m', 'forge.engine', 'file-worker'],
        input=raw, cwd=Path(__file__).resolve().parents[3], env=bridge_environment(control), capture_output=True, timeout=30)
    assert child.returncode == 2
    result = json.loads(child.stdout)
    assert result['status'] == 'error' and result['error']['code'] == 'INVALID_PARAMS'
