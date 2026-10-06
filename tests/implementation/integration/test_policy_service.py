"""Trusted service policy validation precedes model construction and acceptance."""
from copy import deepcopy
import json

import pytest

from forge.application.models import ContractError, canonical_hash
from forge.engine.persistence import new_id
from test_application import setup


def test_service_persists_normalized_hash_and_protects_control_directory(tmp_path):
    service, store, _, _, params, _ = setup(tmp_path)
    try:
        policy = store.connection.execute('SELECT * FROM policies WHERE id=?', (params['policy_id'],)).fetchone()
        value = json.loads(policy['normalized_json'])
        assert str(store.data_dir) in value['filesystem']['protected_paths']
        assert str(tmp_path / 'project' / '.git') in value['filesystem']['protected_paths']
        assert policy['hash'] == canonical_hash(value)
    finally:
        store.close()


@pytest.mark.parametrize('strong', ['dns', 'strict_read', 'memory'])
def test_local_trusted_never_silently_satisfies_stronger_policy(tmp_path, strong):
    service, store, _, clients, params, turn = setup(tmp_path)
    try:
        value = json.loads(store.connection.execute('SELECT normalized_json FROM policies WHERE id=?', (params['policy_id'],)).fetchone()[0])
        value['policy_id'] = new_id('policy')
        if strong == 'dns':
            value['network']['dns_isolation_required'] = True
        elif strong == 'strict_read':
            value['filesystem']['read_mode'] = 'strict_allowlist_required'
        else:
            value['limits']['memory_bytes'] = {'value': 1024, 'enforcement': 'hard_required'}
        turn['policy_id'] = service.put_policy(value)
        with pytest.raises(ContractError) as error:
            service.start_turn(turn)
        assert error.value.kind == 'CAPABILITY_UNSATISFIED'
        assert not clients and not store.connection.execute('SELECT 1 FROM turns').fetchone()
    finally:
        store.close()


def test_changed_git_metadata_cannot_reuse_frozen_policy(tmp_path):
    service, store, _, clients, _, turn = setup(tmp_path)
    try:
        metadata = tmp_path / 'external-git'
        metadata.mkdir()
        (tmp_path / 'project' / '.git').write_text('gitdir: ' + str(metadata))
        with pytest.raises(ContractError) as error:
            service.start_turn(turn)
        assert error.value.kind == 'STALE_REVISION'
        assert not clients and not store.connection.execute('SELECT 1 FROM turns').fetchone()
    finally:
        store.close()


def test_unsafe_environment_policy_is_not_persisted(tmp_path):
    service, store, _, _, params, _ = setup(tmp_path)
    try:
        value = json.loads(store.connection.execute('SELECT normalized_json FROM policies WHERE id=?', (params['policy_id'],)).fetchone()[0])
        value['policy_id'] = new_id('policy')
        value['environment_keys'] = ['NODE_OPTIONS']
        with pytest.raises(ContractError):
            service.put_policy(value)
        assert not store.connection.execute('SELECT 1 FROM policies WHERE id=?', (value['policy_id'],)).fetchone()
    finally:
        store.close()
