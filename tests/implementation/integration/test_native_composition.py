"""Actual Bridge refusal and owned cleanup; this is not native isolation acceptance."""
import asyncio
import json
from pathlib import Path
from forge.engine.persistence import new_id
from forge.sandbox.application_backend import NativeBackendFactory
from tests.implementation.integration.test_application import setup


def test_native_factory_starts_real_bridge_before_model_and_keeps_turn_owners_separate(tmp_path):
    service,store,vault,clients,session_params,params=setup(tmp_path,mode='strict')
    service.backend=None
    service.backend_factory=NativeBackendFactory(store.data_dir)
    policy=json.loads(store.connection.execute('SELECT normalized_json FROM policies WHERE id=?',(params['policy_id'],)).fetchone()[0])
    policy['policy_id']=new_id('policy')
    params['policy_id']=service.put_policy(policy)
    async def run():
        owners=[]
        for _ in range(2):
            params['client_action_id']=new_id('act')
            turn=service.start_turn(params)
            assert await service.execute_turn(turn['turn_id']) is None
            row=store.connection.execute('SELECT t.outcome,l.cleanup_json FROM turns t JOIN turn_lifecycle l ON l.turn_id=t.id WHERE t.id=?',(turn['turn_id'],)).fetchone()
            assert row['outcome']=='blocked' and clients==[]
            cleanup=json.loads(row['cleanup_json'])
            assert cleanup['state']=='clean' and cleanup['scope']=='native-session'
            owner=cleanup['reports'][0]['owner']
            assert owner['engine_epoch']==store.epoch and owner['execution_id'] is None
            assert (store.data_dir/'native'/turn['turn_id']).is_dir()
            owners.append(owner['sandbox_session_id'])
        assert len(set(owners))==2 and service.backend is None
    try:asyncio.run(run())
    finally:store.close()
