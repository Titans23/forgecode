"""Strict compatibility is refused before model construction or native admission."""
import json
import pytest
from forge.application.models import ContractError
from forge.engine.persistence import new_id
from forge.sandbox.application_backend import NativeBackendFactory
from tests.implementation.integration.test_application import setup


def test_retired_strict_factory_refuses_before_model_or_resource_creation(tmp_path):
    service,store,vault,clients,session_params,params=setup(tmp_path,mode='strict')
    service.backend=None
    service.backend_factory=NativeBackendFactory(store.data_dir,mode='strict')
    policy=json.loads(store.connection.execute('SELECT normalized_json FROM policies WHERE id=?',(params['policy_id'],)).fetchone()[0])
    policy['policy_id']=new_id('policy')
    params['policy_id']=service.put_policy(policy)
    try:
        for _ in range(2):
            params['client_action_id']=new_id('act')
            with pytest.raises(ContractError) as error:
                service.start_turn(params)
            assert error.value.kind == 'SANDBOX_UNAVAILABLE'
            assert clients == []
            assert not (store.data_dir/'native').exists()
        assert store.connection.execute('SELECT count(*) FROM turns').fetchone()[0] == 0
    finally:
        store.close()
