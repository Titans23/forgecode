"""Actual trusted Node/SRT child and dispatcher. Native acceptance remains separate."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

import pytest

from forge.application.models import ContractError
from forge.engine.persistence import new_id
from forge.sandbox.srt_backend import SrtBackend
from forge.sandbox.launcher import verify_runtime, bridge_environment


def policy_input(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    identity = root.stat()
    workspace = {'id': new_id('ws'), 'canonical_path': str(root), 'file_identity': f'{identity.st_dev}:{identity.st_ino}'}
    policy = {'schema_version': 'forge.sandbox.policy.v1', 'policy_id': new_id('policy'), 'workspace_id': workspace['id'],
        'filesystem': {'read_mode': 'backend_default_with_protected_paths', 'read_roots': [str(root)],
            'write_roots': [str(root)], 'protected_paths': [], 'deny_overrides_allow': True, 'reject_unsafe_links': True},
        'network': {'mode': 'deny_direct', 'allowed_domains': [], 'dns_isolation_required': False},
        'limits': {'memory_bytes': None, 'disk_bytes': None, 'pids': None, 'wall_time_seconds': 30,
            'command_output_bytes': 1048576, 'session_artifact_bytes': 104857600},
        'environment_keys': [], 'fallback': 'deny', 'session_mutation': 'replace_session'}
    return root, workspace, policy


def test_real_bridge_probe_has_no_manufactured_native_verification(tmp_path):
    async def scenario():
        _, workspace, policy = policy_input(tmp_path)
        backend = SrtBackend(workspace, {'engine_epoch': new_id('epoch'),
            'sandbox_session_id': new_id('sandbox'), 'execution_id': None}, tmp_path / 'control')
        try:
            report = await backend.probe()
            assert report.value['backend_version'] == '0.0.78'
            assert all(v['status'] != 'verified' for v in report.value['verification'].values())
            with pytest.raises(ContractError) as error:
                await backend.prepare(policy)
            assert error.value.kind in ('SANDBOX_UNAVAILABLE', 'SETUP_REQUIRED', 'CAPABILITY_UNSATISFIED')
            command = {'mode': 'argv', 'argv': [str(verify_runtime().node), '-e',
                "require('node:fs').writeFileSync('fallback-marker','bad')"],
                'cwd': workspace['canonical_path'], 'environment': {},
                'deadline_utc': (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat().replace('+00:00', 'Z'),
                'output_limit_bytes': 4096}
            with pytest.raises(ContractError):
                await backend.execute(new_id('exec'), command)
            assert not (Path(workspace['canonical_path']) / 'fallback-marker').exists()
            first = await backend.close()
            assert await backend.close() == first
            assert first['state'] == 'clean'  # No SRT session/execution was created.
        finally:
            await backend.aclose()
    asyncio.run(scenario())


def test_bridge_namespace_and_task_output_cannot_invoke_engine_methods(tmp_path):
    async def scenario():
        _, workspace, _ = policy_input(tmp_path)
        backend = SrtBackend(workspace, {'engine_epoch': new_id('epoch'),
            'sandbox_session_id': new_id('sandbox'), 'execution_id': None}, tmp_path / 'control')
        try:
            await backend.probe()
            with pytest.raises(ContractError) as error:
                await backend._request('session.start_turn', {})
            assert error.value.kind == 'NOT_FOUND'
            with pytest.raises(ContractError):
                await backend._request('execute', {'execution_id': new_id('exec')})
        finally:
            await backend.aclose()
    asyncio.run(scenario())


def test_actual_launcher_ignores_inherited_node_loader_and_proxy(tmp_path, monkeypatch):
    marker = tmp_path / 'host-loader-ran'
    injected = tmp_path / 'injected.cjs'
    injected.write_text("require('node:fs').writeFileSync(" + json.dumps(str(marker)) + ",'bad')")
    monkeypatch.setenv('NODE_OPTIONS', '--require "' + str(injected) + '"')
    monkeypatch.setenv('HTTP_PROXY', 'http://secret@127.0.0.1:9')
    async def scenario():
        _, workspace, _ = policy_input(tmp_path)
        backend = SrtBackend(workspace, {'engine_epoch': new_id('epoch'),
            'sandbox_session_id': new_id('sandbox'), 'execution_id': None}, tmp_path / 'control')
        try:
            report = await backend.probe()
            assert report.value['backend_version'] == '0.0.78'
            assert not marker.exists()
        finally:
            await backend.aclose()
    asyncio.run(scenario())


def test_real_dispatcher_and_output_transport_without_os_sandbox_claim():
    runtime = verify_runtime()
    result = subprocess.run([str(runtime.node), '--test', '--test-reporter=tap', 'tests/implementation/node/bridge.test.mjs'],
        cwd=runtime.root, env=bridge_environment(runtime.root / '.local'), capture_output=True,
        encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '# pass ' in result.stdout and '# skipped 0' in result.stdout
