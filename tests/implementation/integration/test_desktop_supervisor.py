"""Main supervisor against actual Python Engine, from a foreign cwd; no MockTransport."""
import json
from pathlib import Path
import subprocess
import pytest

from test_rpc import seed


ROOT = Path(__file__).resolve().parents[3]


def test_actual_main_supervisor_handshake_turn_reload_and_owned_shutdown(tmp_path):
    fixture, params, turn = seed(tmp_path)
    config = tmp_path / 'supervisor-test.json'
    config.write_text(json.dumps({'directory': str(tmp_path), 'fixture': str(fixture), 'params': params, 'turn': turn}), encoding='utf-8')
    result = subprocess.run(['node', str(ROOT / 'tests/implementation/node/desktop-supervisor-case.mjs'), str(config)],
        cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['handshake'] and report['same_engine_after_reload'] and report['real_completed_turn']
    assert report['closed_owned_engine'] and report['no_http_listener']


@pytest.mark.parametrize(('mode', 'check'), [('incompatible', 'incompatible_disables_turn'),
    ('crash', 'lost_without_respawn'), ('active-close', 'cancelled_owned_active_turn')])
def test_actual_main_supervisor_refuses_incompatible_or_lost_engine_and_cancels_owned_turn(tmp_path, mode, check):
    fixture, params, turn = seed(tmp_path, wait=mode == 'active-close')
    config = tmp_path / 'supervisor-test.json'
    config.write_text(json.dumps({'mode': mode, 'directory': str(tmp_path), 'fixture': str(fixture), 'params': params, 'turn': turn}), encoding='utf-8')
    result = subprocess.run(['node', str(ROOT / 'tests/implementation/node/desktop-supervisor-case.mjs'), str(config)],
        cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)[check]
