"""Real OS lock and durable residual marker; portable tests do not grade native ACLs."""
import json
import subprocess
import sys
from pathlib import Path
import pytest

from forge.application.models import ContractError
from forge.engine.persistence import new_id
from forge.sandbox.windows_worker import WindowsWorkerLease


def owner():
    return {'engine_epoch': new_id('epoch'), 'sandbox_session_id': new_id('sandbox'), 'execution_id': None}


def test_worker_serializes_sessions_and_releases_only_confirmed_clean(tmp_path):
    first = WindowsWorkerLease(tmp_path / 'worker', owner(), 'a' * 64)
    with pytest.raises(ContractError):
        WindowsWorkerLease(tmp_path / 'worker', owner(), 'b' * 64)
    first.close({'owner': first.owner, 'state': 'clean'})
    second = WindowsWorkerLease(tmp_path / 'worker', owner(), 'b' * 64)
    second.close({'owner': second.owner, 'state': 'clean'})


def test_unknown_cleanup_persists_marker_and_blocks_restart(tmp_path):
    first = WindowsWorkerLease(tmp_path / 'worker', owner(), 'a' * 64)
    first.close({'owner': first.owner, 'state': 'unknown'})
    marker = json.loads((tmp_path / 'worker' / 'session.json').read_text())
    assert marker['owner'] == first.owner
    with pytest.raises(ContractError) as error:
        WindowsWorkerLease(tmp_path / 'worker', owner(), 'b' * 64)
    assert error.value.kind == 'CLEANUP_FAILED'


def test_mismatched_owner_cannot_clear_another_session_grants(tmp_path):
    first = WindowsWorkerLease(tmp_path / 'worker', owner(), 'a' * 64)
    first.close({'owner': owner(), 'state': 'clean'})
    assert (tmp_path / 'worker' / 'session.json').exists()
    with pytest.raises(ContractError):
        WindowsWorkerLease(tmp_path / 'worker', owner(), 'b' * 64)


def test_replaced_marker_is_not_deleted(tmp_path):
    first = WindowsWorkerLease(tmp_path / 'worker', owner(), 'a' * 64)
    marker = tmp_path / 'worker' / 'session.json'
    marker.unlink()
    marker.write_text(json.dumps({'owner': owner(), 'policy_hash': 'b' * 64}))
    first.close({'owner': first.owner, 'state': 'clean'})
    assert marker.exists()


def test_different_process_cannot_acquire_active_worker(tmp_path):
    first = WindowsWorkerLease(tmp_path / 'worker', owner(), 'a' * 64)
    code = "import json,sys; from forge.sandbox.windows_worker import WindowsWorkerLease; from forge.application.models import ContractError; v=json.load(sys.stdin)\ntry:\n WindowsWorkerLease(v['directory'],v['owner'],'b'*64)\nexcept ContractError as e:\n print(e.kind);sys.exit(2)\nsys.exit(1)"
    try:
        child = subprocess.run([sys.executable, '-c', code], input=json.dumps({'directory': str(tmp_path / 'worker'), 'owner': owner()}),
            cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=15)
        assert child.returncode == 2 and child.stdout.strip() == 'DATA_DIR_IN_USE'
    finally:
        first.close({'owner': first.owner, 'state': 'clean'})
