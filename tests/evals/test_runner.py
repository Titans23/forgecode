'''Tests for the disposable M2 evaluation runner.'''

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import pytest

from evals.runner import EvalCase, PreparedFixture, load_case, run_case
from forge.runtime.state import (
    ModelStreamEvent,
    ModelTextDelta,
    ModelToolCallCompleted,
    ModelUsageUpdate,
    TokenUsage,
    ToolCall,
)


def initialize_project(root: Path) -> str:
    fixture = root / 'fixtures' / 'demo'
    hidden = fixture / 'tests' / 'hidden'
    hidden.mkdir(parents=True)
    (fixture / 'value.txt').write_text('old\n', encoding='utf-8')
    (hidden / 'test_secret.txt').write_text('secret\n', encoding='utf-8')
    subprocess.run(['git', 'init', '--quiet'], cwd=root, check=True)
    subprocess.run(
        ['git', 'config', 'user.email', 'forge@example.test'],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ['git', 'config', 'user.name', 'ForgeCode Tests'],
        cwd=root,
        check=True,
    )
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'fixture baseline'],
        cwd=root,
        check=True,
    )
    return subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_case(commit: str) -> EvalCase:
    public_script = (
        'assert open('
        + repr('value.txt')
        + ').read() == '
        + repr('new\n')
    )
    hidden_script = (
        'assert open('
        + repr('tests/hidden/test_secret.txt')
        + ').read() == '
        + repr('secret\n')
    )
    return EvalCase(
        id='demo-001',
        repo='fixtures/demo',
        base_commit=commit,
        task='Change value.txt from old to new.',
        test_command=subprocess.list2cmdline(
            [sys.executable, '-c', public_script]
        ),
        hidden_test_command=subprocess.list2cmdline(
            [sys.executable, '-c', hidden_script]
        ),
        allowed_paths=('value.txt',),
        forbidden_paths=('tests/hidden/**',),
        max_model_calls=6,
        max_tool_calls=12,
        intent_routing=False,
    )


class FakeModelClient:
    provider = 'fake'
    model = 'scripted-evaluation'

    def __init__(self, *responses: list[ModelStreamEvent]) -> None:
        self.responses = list(responses)
        self.closed = False

    async def aclose(self):
        self.closed = True

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        for event in self.responses.pop(0):
            yield event


class FailingModelClient:
    provider = 'fake'
    model = 'scripted-evaluation'
    closed = False

    async def aclose(self):
        self.closed = True

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        raise RuntimeError('model unavailable')
        yield ModelTextDelta(text='unreachable')


def tool_response(call: ToolCall) -> list[ModelStreamEvent]:
    return [
        ModelUsageUpdate(usage=TokenUsage(10, 0)),
        ModelToolCallCompleted(tool_call=call),
        ModelUsageUpdate(usage=TokenUsage(10, 2)),
    ]


def final_response() -> list[ModelStreamEvent]:
    return tool_response(
        ToolCall(
            index=0,
            id='toolu_finish',
            name='finish_task',
            arguments={
                'task_kind': 'change',
                'status': 'completed',
                'summary': 'Implemented and verified.',
                'blocked_reasons': [],
            },
        )
    )


def test_load_case_validates_real_yaml() -> None:
    path = Path('evals/cases/python-calculator-001.yaml')

    case = load_case(path)

    assert case.id == 'python-calculator-001'
    assert case.allowed_paths == ('src/calculator/**', 'tests/public/**')
    assert case.forbidden_paths == ('tests/hidden/**',)


def test_prepared_fixture_uses_commit_and_hides_tests(tmp_path: Path) -> None:
    commit = initialize_project(tmp_path)
    case = make_case(commit)

    with PreparedFixture(tmp_path, case) as prepared:
        workspace = prepared.workspace
        assert (workspace / 'value.txt').read_text() == 'old\n'
        assert not (workspace / 'tests' / 'hidden').exists()
        status = subprocess.run(
            ['git', 'status', '--short'],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        assert status.stdout == ''
        exclude = (workspace / '.git' / 'info' / 'exclude').read_text(
            encoding='utf-8'
        )
        assert '__pycache__/' in exclude
        assert '*.py[cod]' in exclude

        prepared.restore_hidden_tests()
        hidden = workspace / 'tests' / 'hidden' / 'test_secret.txt'
        assert hidden.read_text() == 'secret\n'


def test_run_case_passes_with_fake_agent_and_independent_hidden_test(
    tmp_path: Path,
) -> None:
    commit = initialize_project(tmp_path)
    case = make_case(commit)
    edit = ToolCall(
        index=0,
        id='toolu_edit',
        name='replace_text',
        arguments={
            'path': 'value.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        index=0,
        id='toolu_verify',
        name='verify',
        arguments={'command': case.test_command},
    )
    client = FakeModelClient(
        tool_response(edit),
        tool_response(verify),
        final_response(),
    )
    output_dir = tmp_path / 'out'

    outcome = asyncio.run(
        run_case(
            case,
            project_root=tmp_path,
            output_dir=output_dir,
            client=client,
            sandbox='full-access',  # Explicit trusted fixture mode; not native evidence.
        )
    )

    assert outcome.passed is True
    assert outcome.task_status == 'completed'
    assert outcome.changed_paths == ('value.txt',)
    assert outcome.public_tests is not None
    assert outcome.public_tests.success is True
    assert outcome.hidden_tests is not None
    assert outcome.hidden_tests.success is True
    assert outcome.trajectory is not None
    assert Path(outcome.trajectory).is_file()
    report = json.loads(
        (output_dir / 'demo-001-latest.json').read_text(encoding='utf-8')
    )
    assert report['passed'] is True
    assert client.closed and outcome.cleanup['state'] == 'clean'
    assert Path(outcome.journal).is_file()


def successful_client(case, *, path='value.txt', content='new\n', verification=None):
    return FakeModelClient(
        tool_response(ToolCall(0, 'edit', 'replace_text', {'path': path, 'old_text': 'old\n', 'new_text': content})),
        tool_response(ToolCall(0, 'verify', 'verify', {'command': verification or case.test_command})),
        final_response(),
    )


def test_public_prompt_distinguishes_syntax_and_forbidden_test_edits():
    from evals.runner import build_agent_prompt
    case = make_case('0' * 40).model_copy(update={
        'test_command': '"C:\\task environment\\python.exe" -m compileall -q src',
        'forbidden_paths': ('tests/**',)})
    prompt = build_agent_prompt(case)
    assert 'structural check (not functional tests)' in prompt
    assert 'read and run existing public tests without changing them' in prompt
    assert case.hidden_test_command not in prompt


@pytest.mark.parametrize('behavior', [False, True])
def test_case_opt_in_behavior_policy_reaches_real_shared_completion_gate(tmp_path, behavior):
    case = make_case(initialize_project(tmp_path)).model_copy(update={
        'require_task_verification': True, 'require_positive_verification': True, 'max_model_calls': 4})
    syntax = subprocess.list2cmdline([sys.executable, '-m', 'compileall', '-q', '.'])
    client = successful_client(case, verification=case.test_command if behavior else syntax)
    result = asyncio.run(run_case(case, project_root=tmp_path, output_dir=tmp_path / 'out',
                                 client=client, sandbox='full-access'))
    assert result.passed == behavior
    assert result.public_tests.success and result.hidden_tests.success
    assert result.run_configuration['task_policy']['require_task_verification']
    assert result.run_configuration['task_policy']['require_positive_verification']
    assert client.closed and result.cleanup['state'] == 'clean'
    if not behavior:
        assert result.task_status != 'completed' and result.stop_reason == 'acceptance_unmet'
        assert 'structural' in Path(result.journal).read_text(encoding='utf-8')


@pytest.mark.parametrize('invalid', [-1, 3, True])
def test_eval_repair_is_explicit_bounded_integer_with_zero_default(invalid):
    from pydantic import ValidationError
    data=make_case('0'*40).model_dump()
    assert data['max_delivery_repairs']==0
    data['max_delivery_repairs']=invalid
    with pytest.raises(ValidationError):
        EvalCase.model_validate(data)


@pytest.mark.parametrize('scenario,budget,expected_calls', [('repair',6,6),('repeat-gap',8,5),('exhausted',4,4)])
def test_opt_in_repair_reverifies_in_one_turn_without_new_budget(tmp_path, scenario, budget, expected_calls):
    """Real shared runner/tools and independent graders; scripted client, no native/cloud claim."""
    from forge.sessions.store import SessionStore
    case=make_case(initialize_project(tmp_path)).model_copy(update={
        'max_model_calls':budget,'max_delivery_repairs':2,'require_task_verification':True,
        'require_positive_verification':True})
    inspect=subprocess.list2cmdline([sys.executable,'-c','print("inspection after verification")'])
    responses=[
        tool_response(ToolCall(0,'edit','replace_text',{'path':'value.txt','old_text':'old\n','new_text':'new\n'})),
        tool_response(ToolCall(0,'verify-first','verify',{'command':case.test_command})),
        tool_response(ToolCall(0,'inspect-after-verify','run_command',{'command':inspect})),
        final_response()]
    if scenario=='repair':
        responses.extend([tool_response(ToolCall(0,'verify-current','verify',{'command':case.test_command})),
            tool_response(ToolCall(0,'finish-current','finish_task',{'task_kind':'change','status':'completed','summary':'Verified current revision'}))])
    elif scenario=='repeat-gap':
        responses.append(tool_response(ToolCall(0,'finish-again','finish_task',
            {'task_kind':'change','status':'completed','summary':'No new evidence'})))
    class Client(FakeModelClient):
        calls=0
        async def stream(self,messages,tools=None,system=None):
            self.calls+=1
            if self.calls==5:
                assert any('Completion was not accepted' in str(message) for message in messages)
                assert any('original budget still applies' in str(message) for message in messages)
            async for event in super().stream(messages,tools,system):
                yield event
    client=Client(*responses)
    result=asyncio.run(run_case(case,project_root=tmp_path,output_dir=tmp_path/'out',client=client,sandbox='full-access'))
    assert client.calls==result.model_calls==result.statistics['model_requests']==expected_calls
    assert result.run_configuration['profile']['max_model_calls']==budget
    assert result.run_configuration['task_policy']['max_delivery_repairs']==2
    assert result.passed==(scenario=='repair')
    assert result.public_tests.success and result.hidden_tests.success
    assert client.closed and result.cleanup['state']=='clean'
    records=SessionStore._read_records(Path(result.journal))
    assert sum(record['type']=='turn_started' for record in records)==1
    if scenario!='repair':
        assert result.task_status=='partial' and result.stop_reason=='acceptance_unmet'


def test_budget_exhaustion_keeps_failed_attempt_even_when_public_grader_passes(tmp_path):
    case = make_case(initialize_project(tmp_path)).model_copy(update={'max_model_calls': 2})
    client = successful_client(case)
    output = tmp_path / 'out'
    result = asyncio.run(run_case(case, project_root=tmp_path, output_dir=output,
        client=client, sandbox='full-access'))
    assert not result.passed and result.task_status == 'failed'
    assert 'budget' in result.stop_reason and result.model_calls == 2
    assert result.public_tests.success and result.hidden_tests.success
    assert client.closed and result.cleanup['state'] == 'clean'
    records = list(output.glob('demo-001-*.json'))
    assert len(records) == 2  # Immutable attempt plus convenience latest view.
    assert all(not json.loads(path.read_text())['passed'] for path in records)


def test_independent_grader_rejects_incorrect_edit_despite_claimed_completion(tmp_path):
    case = make_case(initialize_project(tmp_path))
    client = successful_client(case, content='wrong\n', verification='python -c "pass"')
    result = asyncio.run(run_case(case, project_root=tmp_path, output_dir=tmp_path / 'out',
        client=client, sandbox='full-access'))
    assert not result.passed and not result.public_tests.success
    assert 'Public tests failed.' in result.reasons
    assert client.closed


def test_provider_configuration_uses_shared_resolver_and_factory(tmp_path, monkeypatch):
    import evals.runner as runner
    import forge.runtime.factory as factory
    case = make_case(initialize_project(tmp_path))
    client = successful_client(case)
    seen = []
    monkeypatch.setattr(runner, 'model_environment', lambda path: {
        'FORGE_PROVIDER': 'deepseek', 'FORGE_MODEL': 'configured-model',
        'FORGE_API_KEY': 'offline-fixture-key', 'FORGE_BASE_URL': 'https://example.test/v1'})
    def configured(config, **kwargs):
        seen.append(config)
        return client
    monkeypatch.setattr(factory, 'create_model_client', configured)
    result = asyncio.run(run_case(case, project_root=tmp_path, output_dir=tmp_path / 'out',
        sandbox='full-access'))
    assert result.passed
    assert len(seen) == 1
    assert (seen[0].provider, seen[0].model_id, seen[0].base_url) == (
        'deepseek', 'configured-model', 'https://example.test/v1')
    assert result.run_configuration['provider'] == 'deepseek' and client.closed


def test_explicit_client_keeps_its_provider_in_session_metadata(tmp_path):
    case = make_case(initialize_project(tmp_path))
    client = successful_client(case)
    client.provider = 'deepseek'
    result = asyncio.run(run_case(case, project_root=tmp_path, output_dir=tmp_path / 'out',
        client=client, sandbox='full-access'))
    assert result.passed and client.closed
    assert result.run_configuration['provider'] == 'deepseek'
    records = [json.loads(line) for line in Path(result.journal).read_text(encoding='utf-8').splitlines()]
    assert records[0]['payload']['provider'] == 'deepseek'


def test_default_native_failure_never_falls_back_or_requests_model(tmp_path, monkeypatch):
    from forge.sandbox.application_backend import NativeBackendFactory
    from forge.protocol import ContractError
    from forge.config import ForgeConfig
    case = make_case(initialize_project(tmp_path))
    async def unavailable(*args, **kwargs):
        raise ContractError('native unavailable', kind='SANDBOX_UNAVAILABLE')
    monkeypatch.setattr(NativeBackendFactory, 'open', unavailable)
    called = []
    result = asyncio.run(run_case(case, project_root=tmp_path, output_dir=tmp_path / 'out',
        config=ForgeConfig(api_key='offline', model_id='fixture'),
        model_client_factory=lambda *a, **k: called.append(True)))
    assert not result.passed and 'native unavailable' in result.error
    assert not called and result.session_id is None


def test_cancelled_model_closes_client_and_persists_failed_attempt(tmp_path):
    case = make_case(initialize_project(tmp_path))
    output = tmp_path / 'out'
    class WaitingClient(FakeModelClient):
        async def stream(self, *args, **kwargs):
            started.set()
            await asyncio.Event().wait()
            yield ModelTextDelta('unreachable')
    async def run():
        nonlocal started
        started = asyncio.Event()
        client = WaitingClient()
        task = asyncio.create_task(run_case(case, project_root=tmp_path, output_dir=output,
            client=client, sandbox='full-access'))
        await asyncio.wait_for(started.wait(), 30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.closed
    started = None
    asyncio.run(run())
    result = json.loads((output / 'demo-001-latest.json').read_text())
    assert result['task_status'] == 'cancelled' and not result['passed']
    assert result['cleanup']['state'] == 'clean'
    assert Path(result['journal']).is_file()


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'a/../b', 'C:/escape'])
def test_fixture_scope_cannot_escape_owned_directory(path):
    with pytest.raises(ValueError):
        EvalCase.model_validate({**make_case('HEAD').model_dump(), 'repo': path})


def test_router_initialization_failure_closes_already_created_model(tmp_path):
    from forge.config import ForgeConfig
    case = make_case(initialize_project(tmp_path)).model_copy(update={'intent_routing': True})
    client = FakeModelClient()
    def factory(config, **kwargs):
        if kwargs:
            raise RuntimeError('router initialization failed')
        return client
    result = asyncio.run(run_case(case, project_root=tmp_path, output_dir=tmp_path / 'out',
        config=ForgeConfig(api_key='offline', model_id='configured-model'),
        model_client_factory=factory, sandbox='full-access'))
    assert not result.passed and 'router initialization failed' in result.error
    assert client.closed and result.cleanup['state'] == 'clean'


def test_task_policy_rejects_forbidden_edit_before_side_effect(tmp_path):
    case = make_case(initialize_project(tmp_path)).model_copy(update={
        'forbidden_paths': ('value.txt',), 'allowed_paths': ('elsewhere/**',)})
    client = successful_client(case)
    client.responses.extend(final_response() for _ in range(4))
    result = asyncio.run(run_case(case, project_root=tmp_path, output_dir=tmp_path / 'out',
        client=client, sandbox='full-access'))
    assert not result.passed
    assert not result.changed_paths and not result.public_tests.success
    events = [json.loads(line) for line in Path(result.trajectory).read_text(encoding='utf-8').splitlines()]
    failures = [event for event in events if 'outside_task_scope' in json.dumps(event)]
    assert failures and 'Forbidden paths were modified: value.txt' in json.dumps(failures)
    assert 'Paths outside the allowed scope were modified: value.txt' in json.dumps(failures)
    assert client.closed and result.cleanup['state'] == 'clean'


def test_tool_failure_preserves_result_and_closes_runtime(tmp_path):
    case = make_case(initialize_project(tmp_path))
    client = FakeModelClient(
        tool_response(ToolCall(0, 'bad-read', 'read_file', {'path': 'missing.txt'})),
        tool_response(ToolCall(0, 'failed', 'finish_task', {'task_kind': 'change', 'status': 'failed',
            'summary': 'Required source is missing.', 'blocked_reasons': ['missing source']})))
    result = asyncio.run(run_case(case, project_root=tmp_path, output_dir=tmp_path / 'out',
        client=client, sandbox='full-access'))
    assert not result.passed and result.task_status == 'failed'
    assert client.closed and result.cleanup['state'] == 'clean'
    assert 'missing.txt' in Path(result.trajectory).read_text(encoding='utf-8')


def test_hidden_restore_rejects_agent_created_hardlink(tmp_path):
    import os
    case = make_case(initialize_project(tmp_path))
    with PreparedFixture(tmp_path, case) as fixture:
        outside = fixture.root / 'outside.txt'  # Same filesystem, outside the agent workspace.
        outside.write_bytes(b'untouched')
        hidden = fixture.workspace / 'tests/hidden/test_secret.txt'
        hidden.parent.mkdir(parents=True)
        os.link(outside, hidden)
        with pytest.raises(ValueError, match='hardlink'):
            fixture.restore_hidden_tests()
        assert outside.read_bytes() == b'untouched'


def test_unconfirmed_cleanup_cannot_be_success_and_preserves_workspace(tmp_path, monkeypatch):
    import evals.runner as runner
    from forge.api import HarnessSession
    case = make_case(initialize_project(tmp_path))
    client = successful_client(case)
    owned = []
    class ObservedFixture(PreparedFixture):
        def _prepare(self):
            result = super()._prepare()
            owned.append(self)
            return result
    monkeypatch.setattr(runner, 'PreparedFixture', ObservedFixture)
    close = HarnessSession.close
    async def uncertain(session):
        await close(session)  # Actual helpers are gone; inject reporting failure only.
        session.cleanup = {'state': 'unknown'}
        return session.cleanup
    monkeypatch.setattr(HarnessSession, 'close', uncertain)
    try:
        result = asyncio.run(run_case(case, project_root=tmp_path, output_dir=tmp_path / 'out',
            client=client, sandbox='full-access'))
        assert not result.passed and result.public_tests.success and result.hidden_tests.success
        assert result.cleanup['state'] == 'unknown'
        assert Path(result.retained_workspace).exists() and client.closed
    finally:
        for fixture in owned:
            fixture._temporary.cleanup()


def test_run_case_preserves_trajectory_when_model_fails(
    tmp_path: Path,
) -> None:
    commit = initialize_project(tmp_path)
    case = make_case(commit)
    output_dir = tmp_path / 'out'

    client = FailingModelClient()
    outcome = asyncio.run(
        run_case(
            case,
            project_root=tmp_path,
            output_dir=output_dir,
            client=client,
            sandbox='full-access',
        )
    )

    assert outcome.passed is False
    assert outcome.error is not None
    assert 'model unavailable' in outcome.error
    assert outcome.trajectory is not None
    assert Path(outcome.trajectory).is_file()
    assert client.closed and outcome.cleanup['state'] == 'clean'
