"""Real HTTP delivery and process ownership; portable evidence is not SRT acceptance."""
import asyncio
import os
import sys
from urllib.request import urlopen

from forge.engine.lifecycle import PhaseLifecycle
from forge.sandbox.process_owner import LocalProcessOwner


async def server(root):
    program = "from http.server import HTTPServer,SimpleHTTPRequestHandler; s=HTTPServer(('127.0.0.1',0),SimpleHTTPRequestHandler); print(s.server_port,flush=True); s.serve_forever()"
    owner = await LocalProcessOwner.start([sys.executable, '-I', '-c', program], cwd=root,
        environment=dict(os.environ), output=True)
    try:
        port = int(await asyncio.wait_for(owner.process.stdout.readline(), 5))
        return owner, f'http://127.0.0.1:{port}/public.txt'
    except BaseException:
        await owner.close()
        raise


async def read(url):
    def request():
        with urlopen(url, timeout=1) as response:
            return response.read().decode()
    return await asyncio.to_thread(request)


def test_delivery_service_retained_for_actual_http_grade_then_owned_cleanup(tmp_path):
    (tmp_path / 'public.txt').write_text('delivery-v1')
    async def scenario():
        owner, url = await server(tmp_path)
        peer, peer_url = await server(tmp_path)
        phases = PhaseLifecycle(agent_seconds=0.1, environment_seconds=5)
        phases.register('delivery', owner.close, retain_for_grader=True)
        try:
            await phases.run_agent(lambda: asyncio.sleep(0.02))
            await asyncio.sleep(0.15)  # Agent budget expired; grader may still use delivery.
            assert phases.agent.remaining() == 0
            result = await phases.grade(lambda: read(url), seconds=2)
            assert result == 'delivery-v1' and phases.grade_state == 'graded'
            assert phases.cleanup['state'] == 'clean' and owner.process.returncode is not None
            assert await read(peer_url) == 'delivery-v1'  # No peer was killed.
        finally:
            await phases.close()
            await peer.close()
    asyncio.run(scenario())


def test_phase_deadlines_and_actual_service_cleanup_are_persisted_separately(tmp_path):
    from test_application import setup
    service, store, _, _, _, turn = setup(tmp_path)
    accepted = service.start_turn(turn)
    (tmp_path / 'project/public.txt').write_text('delivery-v1')
    async def scenario():
        owner, url = await server(tmp_path / 'project')
        phases = PhaseLifecycle(agent_seconds=0.1, environment_seconds=5, store=store, turn_id=accepted['turn_id'])
        phases.register('delivery', owner.close, retain_for_grader=True)
        try:
            await phases.finish_agent()
            assert await phases.grade(lambda: read(url), seconds=2) == 'delivery-v1'
            row = store.connection.execute('SELECT * FROM turn_lifecycle WHERE turn_id=?', (accepted['turn_id'],)).fetchone()
            assert row['agent_deadline_utc'] < row['grader_deadline_utc'] <= row['environment_deadline_utc']
            assert row['cleanup_state'] == 'clean' and row['cancel_state'] == 'none' and row['finished_at_utc']
        finally:
            await phases.close()
    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_final_environment_deadline_reclaims_ungraded_service(tmp_path):
    (tmp_path / 'public.txt').write_text('delivery-v1')
    async def scenario():
        owner, url = await server(tmp_path)
        # Establish real liveness before starting the deliberately short lifetime.
        # A valid expiry may close an HTTP connection started inside that lifetime.
        try:
            assert await read(url) == 'delivery-v1'
        except BaseException:
            await owner.close()
            raise
        phases = PhaseLifecycle(agent_seconds=0.1, environment_seconds=0.25)
        phases.register('delivery', owner.close, retain_for_grader=True)
        try:
            await phases.finish_agent()
            assert owner.process.returncode is None
            async with asyncio.timeout(3):
                while phases.cleanup is None:
                    await asyncio.sleep(0.01)
            assert owner.process.returncode is not None
            assert phases.cleanup['state'] == 'clean' and phases.grade_state == 'unscored'
        finally:
            await phases.close()
    asyncio.run(scenario())


def test_grade_result_survives_real_cleanup_callback_error(tmp_path):
    (tmp_path / 'public.txt').write_text('delivery-v1')
    async def scenario():
        owner, url = await server(tmp_path)
        phases = PhaseLifecycle(agent_seconds=1, environment_seconds=5)
        async def faulty_cleanup():
            await owner.close()
            raise OSError('Injected observation failure after real owned cleanup')
        phases.register('delivery', faulty_cleanup, retain_for_grader=True)
        await phases.finish_agent()
        result = await phases.grade(lambda: read(url), seconds=2)
        assert result == 'delivery-v1' and phases.grade_result == result and phases.grade_state == 'graded'
        assert phases.cleanup['state'] == 'unknown'
        assert (await phases.close()) == phases.cleanup
    asyncio.run(scenario())


def test_agent_deadline_cancels_agent_without_extending_for_retained_service(tmp_path):
    async def scenario():
        phases = PhaseLifecycle(agent_seconds=0.08, environment_seconds=1)
        started = asyncio.Event()
        async def agent():
            started.set()
            await asyncio.sleep(30)
        try:
            try:
                await phases.run_agent(agent)
                assert False, 'Expired Agent ran to completion'
            except asyncio.CancelledError:
                pass
            assert started.is_set() and phases.agent_state == 'deadline_exceeded'
            assert phases.environment.remaining() > 0
        finally:
            await phases.close()
    asyncio.run(scenario())
