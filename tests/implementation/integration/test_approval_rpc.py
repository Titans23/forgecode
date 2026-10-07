"""Pending Harness approvals leave the actual private Engine reader responsive."""
import asyncio
import json

import pytest

from forge.engine.persistence import new_id
from test_rpc import seed, launch, initialize, send, receive, stop


@pytest.mark.parametrize('decision', ['approve', 'cancel', 'restart'])
def test_real_engine_wait_is_queryable_and_only_exact_main_grant_runs_tool(tmp_path, decision):
    fixture, _, turn = seed(tmp_path)
    value = json.loads(fixture.read_text())
    value['allowed_tools'] = ['apply_patch', 'finish_task']
    value['responses'][0]['tool_calls'] = [{'id': 'delete', 'name': 'apply_patch',
        'arguments': {'patch': '*** Begin Patch\n*** Delete File: value.txt\n*** End Patch'}}]
    fixture.write_text(json.dumps(value), encoding='utf-8')
    async def run():
        process = await launch(tmp_path, fixture, interactive_approvals=True)
        try:
            assert 'approval.decide' in (await initialize(process))['result']['capabilities']['supported_methods']
            await send(process, 'session.start_turn', turn, request_id='start')
            accepted = (await receive(process, wanted_id='start'))['result']
            approval = None
            async with asyncio.timeout(10):
                while approval is None:
                    await send(process, 'approval.list', {'scope': {'kind': 'turn', 'id': accepted['turn_id']}}, request_id='list')
                    rows = (await receive(process, wanted_id='list'))['result']['items']
                    approval = next((row for row in rows if row['state'] == 'pending'), None)
                    await asyncio.sleep(.02)
            assert (tmp_path / 'project/value.txt').exists()
            await send(process, 'system.health', {}, request_id='health')
            assert (await receive(process, wanted_id='health'))['result']['active_work_items'] == 1
            if decision == 'restart':
                binding = {'approval_id': approval['approval_id'], 'binding_hash': approval['binding_hash']}
                await send(process, 'approval.prepare_decision', binding, request_id='prepare')
                prepared = await receive(process, wanted_id='prepare')
                assert 'result' in prepared, prepared
                token = prepared['result']['confirmation_token']
                await stop(process)
                process = await launch(tmp_path, fixture, interactive_approvals=True)
                assert (await initialize(process))['result']['readiness']['status'] == 'blocked'
                await send(process, 'approval.get', {'approval_id': approval['approval_id']}, request_id='get')
                assert (await receive(process, wanted_id='get'))['result']['state'] == 'expired'
                await send(process, 'approval.decide', {**binding, 'confirmation_token': token, 'decision': 'approve', 'client_action_id': new_id('act')}, request_id='old')
                assert (await receive(process, wanted_id='old'))['error']['data']['kind'] == 'UNAUTHORIZED'
                assert (tmp_path / 'project/value.txt').read_text() == 'B'
                return
            if decision == 'approve':
                binding = {'approval_id': approval['approval_id'], 'binding_hash': approval['binding_hash']}
                await send(process, 'approval.prepare_decision', binding, request_id='prepare')
                prepared = await receive(process, wanted_id='prepare')
                assert 'result' in prepared, prepared
                token = prepared['result']['confirmation_token']
                params = {**binding, 'client_action_id': new_id('act'), 'confirmation_token': token, 'decision': 'approve'}
                await send(process, 'approval.decide', {**params, 'confirmation_token': 'forged' + 'a' * 40}, request_id='forged')
                assert (await receive(process, wanted_id='forged'))['error']['data']['kind'] == 'UNAUTHORIZED'
                assert (tmp_path / 'project/value.txt').exists()
                await send(process, 'approval.decide', params, request_id='approve')
                assert (await receive(process, wanted_id='approve'))['result']['state'] == 'approved'
            else:
                await send(process, 'session.cancel_turn', {'client_action_id': new_id('act'), 'turn_id': accepted['turn_id'], 'reason': 'Cancel approval wait'}, request_id='cancel')
                assert (await receive(process, wanted_id='cancel'))['result']['accepted']
            async with asyncio.timeout(10):
                while True:
                    await send(process, 'session.get', {'session_id': turn['session_id']}, request_id='session')
                    snapshot = (await receive(process, wanted_id='session'))['result']
                    if snapshot['turns'][0]['state'] == 'finished': break
                    await asyncio.sleep(.02)
            assert (tmp_path / 'project/value.txt').exists() is (decision == 'cancel')
            await send(process, 'system.shutdown', {'client_action_id': new_id('act'), 'mode': 'drain'}, request_id='shutdown')
            assert (await receive(process, wanted_id='shutdown'))['result']['state'] == 'draining'
            await asyncio.wait_for(process.wait(), 10)
            assert process.returncode == 0
        finally:
            await stop(process)
    asyncio.run(run())
