"""Scripted-model integration; real tools/pytest, no provider calls or native claim."""

import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

import evals.native_swe as native
import evals.runner as runner
from forge.api import HarnessSession
from forge.runtime.state import ModelToolCallCompleted, ModelUsageUpdate, TokenUsage, ToolCall


class ScriptedClient:
    provider = 'anthropic'
    model = 'scripted-native-task'

    def __init__(self, adapter, calls):
        self.adapter, self.calls = adapter, list(calls)
        self.closed = False

    async def aclose(self):
        self.closed = True

    async def stream(self, messages, tools=None, system=None):
        prompt = json.dumps(messages) + (system or '')
        assert 'EVALUATOR_ONLY_TEST_MARKER' not in prompt
        assert 'GOLD_REFERENCE_MARKER' not in prompt
        assert self.adapter.spec['id'] not in prompt
        assert not (self.adapter.agent_workspace / 'tests/test_hidden.py').exists()
        yield ModelUsageUpdate(TokenUsage(10, 0))
        yield ModelToolCallCompleted(self.calls.pop(0))
        yield ModelUsageUpdate(TokenUsage(10, 2))


class FixtureAdapter(native.NativeTaskAdapter):
    async def _environment(self, prepared, phase):
        # The synthetic fixture has no dependencies beyond the test interpreter.
        # Native real-repository acceptance exercises the actual installer.
        if phase == 'agent-setup':
            self.agent_workspace = prepared.workspace
        return Path(sys.executable)

    async def grade(self, *args):
        assert self.client.closed  # Verifier cannot begin before Agent cleanup.
        self.grader_started = True
        return await super().grade(*args)


def task(tmp_path):
    cache = tmp_path / 'cache'
    source = cache / 'sources/acme__sample.git'
    (source / 'src').mkdir(parents=True)
    (source / 'src/value.txt').write_text('old\n', encoding='utf-8')
    (source / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
    runner.initialize_workspace_git(source)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    suite = tmp_path / 'suite.json'
    node = 'tests/test_hidden.py::test_fixed'
    suite.write_text(json.dumps({'FAIL_TO_PASS': [node], 'PASS_TO_PASS': [], 'aliases': {},
                                'expected_mapping': {node: [node]}}), encoding='utf-8')
    patch = ('diff --git a/tests/test_hidden.py b/tests/test_hidden.py\n'
             'new file mode 100644\n--- /dev/null\n+++ b/tests/test_hidden.py\n@@ -0,0 +1,4 @@\n'
             '+# EVALUATOR_ONLY_TEST_MARKER\n+from pathlib import Path\n+def test_fixed():\n'
             '+    assert Path("src/value.txt").read_text().strip() == "new"\n')
    spec = {'id': 'acme__sample-101', 'repo': 'acme/sample', 'base_commit': commit,
            'environment': 'fixture', 'suite': str(suite)}
    row = {'problem_statement': 'Change src/value.txt to new.', 'test_patch': patch,
           'patch': 'GOLD_REFERENCE_MARKER', 'version': '1.0'}
    adapter = FixtureAdapter({'environments': {'fixture': {}}}, spec, row, cache, tmp_path / 'out',
                             sandbox='full-access')
    adapter.grader_started = False
    limits = dict(max_model_calls=6, max_tool_calls=12, max_turn_seconds=60,
                  verifier_timeout_seconds=30, intent_routing=False)
    case = adapter.case(1, limits).model_copy(update={'test_command': native.shell_command(
        [sys.executable, '-m', 'compileall', '-q', 'src'])})
    return adapter, case


def test_native_case_preserves_explicit_behavior_requirements(tmp_path):
    adapter, _ = task(tmp_path)
    limits = dict(max_model_calls=18, max_tool_calls=48, max_turn_seconds=900,
                  verifier_timeout_seconds=600, intent_routing=False,
                  require_task_verification=True, require_positive_verification=True)
    case = adapter.case(1, limits)
    assert case.require_task_verification and case.require_positive_verification


def client_for(adapter, case, *, content='new\n', leak=False):
    calls = [ToolCall(0, 'edit', 'replace_text', {'path': 'src/value.txt', 'old_text': 'old\n', 'new_text': content}),
             ToolCall(0, 'verify', 'verify', {'command': case.test_command}),
             ToolCall(0, 'finish', 'finish_task', {'task_kind': 'change', 'status': 'completed',
                 'summary': 'Updated and verified.', 'blocked_reasons': []})]
    if leak:
        (adapter.cache / 'private_gold.txt').write_text('synthetic evaluator secret', encoding='utf-8')
        calls.insert(0, ToolCall(0, 'leak', 'read_file', {'path': str(adapter.cache / 'private_gold.txt')}))
    adapter.client = ScriptedClient(adapter, calls)
    return adapter.client


@pytest.mark.parametrize('scenario', ['correct', 'wrong', 'budget', 'leak'])
def test_shared_runner_independent_grading_and_invalid_attempts(tmp_path, scenario):
    adapter, case = task(tmp_path)
    if scenario == 'budget':
        case = case.model_copy(update={'max_model_calls': 2})
    client = client_for(adapter, case, content='wrong\n' if scenario == 'wrong' else 'new\n',
                        leak=scenario == 'leak')
    outcome = asyncio.run(native.run_native_case(adapter, case, client=client))
    assert client.closed and outcome.cleanup['state'] == 'clean'
    assert outcome.passed == (scenario == 'correct'), outcome.to_dict()
    assert outcome.agent_seconds is not None and outcome.total_seconds >= outcome.agent_seconds
    artifact = json.loads(Path(outcome.independent_grading['candidate']['path']).read_text())
    if scenario != 'leak':
        with runner.PreparedFixture(native.ROOT, case) as replay:
            baseline = native.source_snapshot(replay.workspace, adapter.scope)
            baseline.apply(native.reconstruct_candidate(baseline, artifact, base_commit=case.base_commit))
            assert (replay.workspace / 'src/value.txt').read_text() == ('wrong\n' if scenario == 'wrong' else 'new\n')
    if scenario == 'leak':
        assert not outcome.independent_grading['input_audit']['valid']
        assert not (adapter.logs / 'test.patch').exists()
    else:
        assert adapter.grader_started
        assert outcome.independent_grading['cleanup']['state'] == 'clean'
        assert outcome.hidden_tests.success == (scenario != 'wrong')
        if scenario == 'budget':
            assert outcome.model_calls == 2 and 'budget' in outcome.stop_reason
            assert outcome.task_status != 'completed'
    assert not adapter.agent_workspace.exists()


def test_cancelled_agent_never_starts_independent_grader(tmp_path):
    adapter, case = task(tmp_path)

    async def exercise():
        started = asyncio.Event()
        class Waiting(ScriptedClient):
            async def stream(self, *args, **kwargs):
                if not getattr(self, 'edited', False):
                    self.edited = True
                    yield ModelUsageUpdate(TokenUsage(10, 0))
                    yield ModelToolCallCompleted(ToolCall(0, 'partial-edit', 'replace_text', {
                        'path': 'src/value.txt', 'old_text': 'old\n', 'new_text': 'partial\n'}))
                    yield ModelUsageUpdate(TokenUsage(10, 2), usage_is_final=True)
                    return
                yield ModelUsageUpdate(TokenUsage(7, 0), usage_is_final=False)
                started.set()
                await asyncio.Event().wait()
                yield
        client = adapter.client = Waiting(adapter, [])
        running = asyncio.create_task(native.run_native_case(adapter, case, client=client))
        await asyncio.wait_for(started.wait(), 30)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert client.closed

    asyncio.run(exercise())
    report = json.loads((adapter.output / (case.id + '-latest.json')).read_text())
    assert not report['passed'] and report['task_status'] == 'cancelled'
    assert report['cleanup']['state'] == 'clean' and not adapter.grader_started
    assert report['usage'] is None
    assert report['request_evidence']['observed_requests'] == 2
    assert report['request_evidence']['reported_usage']['input_tokens'] == 17
    assert not report['request_evidence']['usage_complete']
    artifact = json.loads(Path(report['independent_grading']['candidate']['path']).read_text())
    assert artifact['files']['src/value.txt']['after_sha256'] is not None
    with runner.PreparedFixture(native.ROOT, case) as replay:
        baseline = native.source_snapshot(replay.workspace, adapter.scope)
        baseline.apply(native.reconstruct_candidate(baseline, artifact, base_commit=case.base_commit))
        assert (replay.workspace / 'src/value.txt').read_text() == 'partial\n'


def test_candidate_roundtrip_preserves_additions_deletions_and_content_hashes(tmp_path):
    def initial(root):
        (root / 'src').mkdir(parents=True)
        (root / 'src/value.txt').write_bytes(b'old')
        (root / 'src/remove.txt').write_bytes(b'remove')
    source, destination = tmp_path / 'source', tmp_path / 'replay'
    initial(source)
    before = native.source_snapshot(source, 'src')
    (source / 'src/value.txt').write_bytes(b'new')
    (source / 'src/remove.txt').unlink()
    (source / 'src/added').mkdir()
    (source / 'src/added/binary.bin').write_bytes(b'new\0binary')
    artifact = native.candidate_delta(before, native.source_snapshot(source, 'src'),
                                      base_commit='frozen-base', scope='src')
    initial(destination)
    replay = native.source_snapshot(destination, 'src')
    replay.apply(native.reconstruct_candidate(replay, artifact, base_commit='frozen-base'))
    assert (destination / 'src/value.txt').read_bytes() == b'new'
    assert not (destination / 'src/remove.txt').exists()
    assert (destination / 'src/added/binary.bin').read_bytes() == b'new\0binary'
    with pytest.raises(ValueError, match='original-file hash'):
        native.reconstruct_candidate(native.source_snapshot(destination, 'src'), artifact, base_commit='frozen-base')


@pytest.mark.parametrize('provider', ['anthropic', 'openai_responses', 'deepseek'])
def test_shared_factory_zero_retries_makes_one_transport_attempt(monkeypatch, provider):
    import httpx
    from forge.config import ForgeConfig
    from forge.runtime.model_client import ModelCallError
    from forge.runtime.model_budget import request_observer
    from forge.runtime.service_health import profile_health

    attempts = []
    original = httpx.AsyncClient

    def rate_limit(request):
        attempts.append(request)
        return httpx.Response(429, json={'error': {'type': 'rate_limit_error', 'message': 'synthetic limit'}})

    class MockHTTPClient(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(rate_limit), **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', MockHTTPClient)

    async def exercise():
        config = ForgeConfig(api_key='synthetic-key', model_id='scripted-model', provider=provider,
                             base_url='https://' + provider.replace('_', '-') + '.invalid')
        profile_health(config).succeeded()
        client = native.no_retry_client(config)
        observed = []
        token = request_observer.set(lambda: observed.append('started'))
        try:
            with pytest.raises(ModelCallError) as failure:
                async for _ in client.stream([{'role': 'user', 'content': 'synthetic request'}]):
                    pass
            assert failure.value.reason == 'rate_limit'
            assert len(attempts) == len(observed) == 1
        finally:
            request_observer.reset(token)
            await client.aclose()
            profile_health(config).succeeded()

    asyncio.run(exercise())


def test_uncertain_agent_cleanup_blocks_grader_and_retains_owned_fixture(tmp_path, monkeypatch):
    adapter, case = task(tmp_path)
    client = client_for(adapter, case)
    owned = []
    original_close = HarnessSession.close

    class ObservedFixture(runner.PreparedFixture):
        def _prepare(self):
            result = super()._prepare()
            (self.root / 'environment').mkdir()
            (self.root / 'environment/owned-dependency').write_text('preserve until cleanup confirmed')
            owned.append(self)
            return result

    async def uncertain(session):
        await original_close(session)  # Actual process cleanup, then reporting fault.
        session.cleanup = {'state': 'unknown'}
        return session.cleanup

    monkeypatch.setattr(runner, 'PreparedFixture', ObservedFixture)
    monkeypatch.setattr(HarnessSession, 'close', uncertain)
    try:
        outcome = asyncio.run(native.run_native_case(adapter, case, client=client))
        assert not outcome.passed and not adapter.grader_started
        assert outcome.cleanup['state'] == 'unknown' and Path(outcome.retained_workspace).exists()
        assert (owned[0].root / 'environment/owned-dependency').exists()
        assert not (owned[0].workspace / 'environment').exists()
    finally:
        for fixture in owned:
            fixture._temporary.cleanup()
