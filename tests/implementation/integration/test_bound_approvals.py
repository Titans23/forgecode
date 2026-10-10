"""Actual Harness permission waits, SQLite, one-use nonces and side effects."""
import asyncio

import pytest

from forge.application.approvals import ApprovalService
from forge.application.models import ContractError
from forge.engine.methods import EngineMethods
from forge.engine.persistence import new_id
from forge.runtime.state import ModelToolCallCompleted, ModelUsageUpdate, TokenUsage, ToolCall
from test_application import setup, ScriptedClient


def deletion_script():
    return [[ModelUsageUpdate(TokenUsage(10, 2)), ModelToolCallCompleted(ToolCall(0, 'delete', 'apply_patch', {'patch': '*** Begin Patch\n*** Delete File: value.txt\n*** End Patch'}))],
        [ModelUsageUpdate(TokenUsage(10, 2)), ModelToolCallCompleted(ToolCall(0, 'finish', 'finish_task', {'task_kind': 'answer', 'status': 'completed', 'summary': 'The exact deletion finished.'}))]]


async def pending(service, store, turn):
    task = asyncio.create_task(service.execute_turn(service.start_turn(turn)['turn_id']))
    async with asyncio.timeout(10):
        while True:
            row = store.connection.execute('SELECT id FROM approvals ORDER BY rowid DESC LIMIT 1').fetchone()
            if row:
                return task, service.approvals.get({'approval_id': row[0]})
            if task.done():
                await task
                pytest.fail(str([dict(row) for row in store.connection.execute('SELECT * FROM turn_results')]))
            await asyncio.sleep(.01)


@pytest.mark.parametrize('change', [False, True])
def test_real_approval_wait_binds_exact_deletion_and_rejects_changed_workspace(tmp_path, change):
    client = ScriptedClient(deletion_script())
    service, store, _, _, _, turn = setup(tmp_path, factory=lambda *args, **kwargs: client)
    methods = EngineMethods(service, profile='desktop')
    service.approvals = ApprovalService(service)
    try:
        async def run():
            task, approval = await pending(service, store, turn)
            path = tmp_path / 'project/value.txt'
            try:
                assert path.exists() and approval['state'] == 'pending'
                assert store.connection.execute('SELECT state FROM turns WHERE id=?', (approval['turn_id'],)).fetchone()[0] == 'awaiting_approval'
                challenge = await service.approvals.prepare({'approval_id': approval['approval_id'], 'binding_hash': approval['binding_hash']})
                if change:
                    path.write_text('User changed the file during approval', encoding='utf-8')
                params = {'client_action_id': new_id('act'), 'approval_id': approval['approval_id'],
                    'binding_hash': approval['binding_hash'], 'decision': 'approve', 'confirmation_token': challenge['confirmation_token']}
                if change:
                    with pytest.raises(ContractError):
                        await service.approvals.decide(params)
                    task.cancel()
                else:
                    result = await service.approvals.decide(params)
                    assert result['state'] == 'approved'
                    assert (await service.approvals.decide(params))['reused_existing_action'] is True
                await task
                if change:
                    assert path.read_text() == 'User changed the file during approval'
                else:
                    assert not path.exists()
                    assert service.approvals.get({'approval_id': approval['approval_id']})['state'] == 'consumed'
            finally:
                if not task.done(): task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        asyncio.run(run())
    finally:
        store.close()


def test_forged_binding_and_expired_nonce_never_authorize_real_write(tmp_path):
    client = ScriptedClient(deletion_script())
    service, store, _, _, _, turn = setup(tmp_path, factory=lambda *args, **kwargs: client)
    service.approvals = ApprovalService(service, nonce_ttl=.02)
    try:
        async def run():
            task, approval = await pending(service, store, turn)
            try:
                challenge = await service.approvals.prepare({'approval_id': approval['approval_id'], 'binding_hash': approval['binding_hash']})
                for binding, token in [('0' * 64, challenge['confirmation_token']), (approval['binding_hash'], 'forged-' + 'a' * 40)]:
                    with pytest.raises(ContractError):
                        await service.approvals.decide({'client_action_id': new_id('act'), 'approval_id': approval['approval_id'],
                            'binding_hash': binding, 'decision': 'approve', 'confirmation_token': token})
                await asyncio.sleep(.03)
                with pytest.raises(ContractError):
                    await service.approvals.decide({'client_action_id': new_id('act'), 'approval_id': approval['approval_id'],
                        'binding_hash': approval['binding_hash'], 'decision': 'approve', 'confirmation_token': challenge['confirmation_token']})
                assert (tmp_path / 'project/value.txt').read_text() == 'B'
            finally:
                task.cancel(); await asyncio.gather(task, return_exceptions=True)
        asyncio.run(run())
    finally:
        store.close()


@pytest.mark.parametrize('operation', ['deny', 'cancel', 'arguments_change'])
def test_denial_cancellation_and_changed_final_arguments_do_not_delete(tmp_path, operation):
    client = ScriptedClient(deletion_script())
    service, store, _, _, _, turn = setup(tmp_path, factory=lambda *args, **kwargs: client)
    EngineMethods(service, profile='desktop')
    try:
        async def run():
            task, approval = await pending(service, store, turn)
            try:
                challenge = await service.approvals.prepare({'approval_id': approval['approval_id'], 'binding_hash': approval['binding_hash']})
                params = {'client_action_id': new_id('act'), 'approval_id': approval['approval_id'], 'binding_hash': approval['binding_hash'],
                    'confirmation_token': challenge['confirmation_token'], 'decision': 'deny'}
                if operation == 'arguments_change':
                    service.approvals.pending[approval['approval_id']][3].call.arguments['patch'] += '\nchanged'
                    params['decision'] = 'approve'
                    with pytest.raises(ContractError): await service.approvals.decide(params)
                elif operation == 'deny':
                    assert (await service.approvals.decide(params))['state'] == 'denied'
                if operation != 'deny':
                    service.cancel_turn({'client_action_id': new_id('act'), 'turn_id': approval['turn_id'], 'reason': 'User cancelled pending approval'})
                await task
                assert (tmp_path / 'project/value.txt').read_text() == 'B'
                assert service.approvals.get({'approval_id': approval['approval_id']})['state'] in ('denied', 'expired')
            finally:
                if not task.done(): task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        asyncio.run(run())
    finally:
        store.close()
