import asyncio

from forge.runtime.agent_loop import Conversation
from forge.runtime.state import ModelResponseCompleted, ModelTextDelta
from forge.sessions.store import SessionStore


def test_steering_is_admitted_at_request_boundary_and_followup_waits(tmp_path):
    store = SessionStore(tmp_path, data_root=tmp_path.parent / (tmp_path.name + '-sessions'))
    journal = store.create(model='offline')

    class Model:
        calls = 0
        async def stream(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                conversation.queue_input('Use Python')
                conversation.queue_input('Then explain the result', follow_up=True)
                assert messages[-1]['content'] == 'Implement the task'
            else:
                assert messages[-1]['content'] == 'Use Python'
                contract = kwargs['system'].split('[Current turn contract]\n', 1)[1].split('[Runtime request tools]', 1)[0]
                assert 'Implement the task' in contract
                assert 'Use Python' in contract
            yield ModelTextDelta('done')
            yield ModelResponseCompleted('end_turn')

    model = Model()
    conversation = Conversation(client=model, context_root=tmp_path, session_journal=journal)
    async def run():
        events = [event async for event in conversation.stream('Implement the task')]
        assert events[-1].result.model_calls == 2
    asyncio.run(run())
    assert len(conversation.inbox.pending) == 1
    restored = store.load(journal.session_id)
    assert len(restored.pending_inputs) == 1
    assert restored.pending_inputs[0]['content'] == 'Then explain the result'
    assert sum(message.get('content') == 'Use Python' for message in restored.messages) == 1


def test_queued_input_survives_cancel_and_is_consumed_once(tmp_path):
    store = SessionStore(tmp_path, data_root=tmp_path.parent / (tmp_path.name + '-sessions'))
    journal = store.create(model='offline')
    class Model:
        async def stream(self, **kwargs):
            yield ModelTextDelta('next')
            yield ModelResponseCompleted('end_turn')
    conversation = Conversation(client=Model(), context_root=tmp_path, session_journal=journal)
    identity = conversation.queue_input('a later task', follow_up=True)
    journal.record_turn_cancelled('offline fixture')
    restored = store.load(journal.session_id)
    assert restored.pending_inputs[0]['id'] == identity
    conversation.inbox.restore(restored.pending_inputs)
    async def run():
        async for _ in conversation.stream('a later task', input_id=identity):
            pass
    asyncio.run(run())
    assert not store.load(journal.session_id).pending_inputs


def test_terminal_input_routes_cancel_and_followup_without_starting_another_turn():
    from forge.terminal_input import TurnInput
    class Terminal:
        values = iter(['change direction', '/follow next task', '/cancel'])
        async def read_prompt_async(self):
            return next(self.values)
        def show_notice(self, *args):
            pass
    class Session:
        calls = []
        def queue_input(self, text, *, follow_up):
            self.calls.append((text, follow_up))
            return 'pending-id'
    async def run():
        session = Session()
        controller = TurnInput(Terminal(), session, lambda text: text)
        controller.turn = asyncio.create_task(asyncio.Event().wait())
        controller.resume()
        await controller.reader
        await asyncio.gather(controller.turn, return_exceptions=True)
        assert controller.turn.cancelled()
        assert session.calls == [('change direction', False), ('next task', True)]
    asyncio.run(run())


def test_active_turn_refuses_history_mutation(tmp_path):
    import pytest
    conversation = Conversation(client=object(), context_root=tmp_path)
    conversation._running = True
    with pytest.raises(RuntimeError, match='Cancel the active turn'):
        conversation.session_clear()
    with pytest.raises(RuntimeError, match='Cancel the active turn'):
        conversation.checkpoint_rewind()
    with pytest.raises(RuntimeError, match='Cancel the active turn'):
        asyncio.run(conversation.compact())
