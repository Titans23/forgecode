"""Real selected directories and single-use Main grants against the SQLite writer."""
import pytest

from forge.application.models import ContractError, validate_request
from forge.engine.methods import EngineMethods
from forge.engine.persistence import new_id
from test_application import setup


def test_native_selection_nonce_is_consumed_and_registration_is_inspection_only(tmp_path):
    service, store, _, _, _, _ = setup(tmp_path)
    methods = EngineMethods(service, profile='desktop')
    selected = tmp_path / 'selected'
    selected.mkdir()
    params = {'client_action_id': new_id('act'), 'path': str(selected), 'selection_nonce': 'a' * 64}
    try:
        result = methods.register_workspace(params)
        assert result['trust'] == 'inspect_only' and result['revision'] == 0
        assert methods.register_workspace(params) == result
        with pytest.raises(ContractError):
            methods.register_workspace({**params, 'client_action_id': new_id('act')})
        assert store.connection.execute('SELECT COUNT(*) FROM consumed_nonces').fetchone()[0] == 1
    finally:
        store.close()


def test_workspace_grant_rejects_forgery_changed_revision_and_nonce_replay(tmp_path):
    service, store, _, _, params, _ = setup(tmp_path)
    methods = EngineMethods(service, profile='desktop')
    workspace_id = params['workspace_id']
    prepared = methods.prepare_workspace_authorization({'workspace_id': workspace_id, 'expected_revision': 1})
    request = {'client_action_id': new_id('act'), 'workspace_id': workspace_id, 'expected_revision': 1,
        'allow': False, 'binding_hash': prepared['binding_hash'], 'confirmation_token': 'forged' + 'a' * 40}
    try:
        with pytest.raises(ContractError): methods.authorize_workspace(request)
        assert service._workspace(workspace_id)['trust'] == 'execution_allowed'
        request['confirmation_token'] = prepared['confirmation_token']
        result = methods.authorize_workspace(request)
        assert result['trust'] == 'inspect_only' and result['revision'] == 2
        assert methods.authorize_workspace(request) == result
        with pytest.raises(ContractError): methods.authorize_workspace({**request, 'client_action_id': new_id('act')})
        fresh = methods.prepare_workspace_authorization({'workspace_id': workspace_id, 'expected_revision': 2})
        service.authorize_workspace(workspace_id, expected_revision=2, allow=False)
        with pytest.raises(ContractError): methods.authorize_workspace({**request, **fresh, 'client_action_id': new_id('act'), 'expected_revision': 2})
        assert service._workspace(workspace_id)['trust'] == 'inspect_only'
    finally:
        store.close()


def test_renderer_cannot_issue_or_consume_internal_grants(tmp_path):
    service, store, _, _, params, _ = setup(tmp_path)
    methods = EngineMethods(service, profile='desktop')
    try:
        challenge = methods.prepare_workspace_authorization({'workspace_id': params['workspace_id'], 'expected_revision': 1})
        request = {'client_action_id': new_id('act'), 'workspace_id': params['workspace_id'], 'expected_revision': 1,
            'allow': True, **challenge}
        request.pop('expires_at_utc')
        for method, payload in [('workspace.prepare_authorization', {'workspace_id': params['workspace_id'], 'expected_revision': 1}),
                                ('workspace.authorize', request), ('approval.prepare_decision', {'approval_id': new_id('approval'), 'binding_hash': 'a' * 64})]:
            with pytest.raises(ContractError) as caught:
                validate_request({'jsonrpc': '2.0', 'id': 'r', 'method': method, 'params': payload}, 'renderer')
            assert caught.value.kind == 'UNAUTHORIZED'
    finally:
        store.close()
