import asyncio
from dataclasses import replace
import json
from pathlib import Path
import zipfile

import pytest
from typer.testing import CliRunner

from forge.agents import AgentDefinition, PluginSpec
from forge.delivery import export_agent, import_agent, load_definition
from forge.plugin_cli import initialize
from forge.validation import check_agent, receipt_status


def fixture(tmp_path):
    root = tmp_path / 'hello'
    initialize(root, name='hello')
    agent = AgentDefinition(name='hello', base='core', plugins=(PluginSpec(str(root), trusted=True),))
    cases = {'cases': [{'id': 'greeting', 'tool': 'greet', 'arguments': {'name': 'Ada'}, 'expect': {'content': 'Hello, Ada!'}},
                       {'id': 'schema', 'tool': 'greet', 'arguments': {}, 'expect': {'error': 'invalid_arguments'}}]}
    return root, agent, cases


def test_independent_business_check_and_layered_expiration(tmp_path, monkeypatch):
    root, agent, cases = fixture(tmp_path)
    monkeypatch.setenv('COMPANY_SECRET', 'must-not-enter-candidate')
    (root / 'tests/test_plugin.py').write_text('import unittest\nclass Test(unittest.TestCase):\n def test_own(self): self.assertTrue(True)\n')
    path = root / 'plugin.py'
    good = path.read_text()
    path.write_text('import os\nassert "COMPANY_SECRET" not in os.environ\n' + good.replace('Hello, ', 'Wrong, '))
    async def run():
        wrong = await check_agent(agent, cases, work_dir=tmp_path / 'checks')
        assert wrong['self_tests'][0]['status'] == 'passed'
        assert wrong['interface']['status'] == 'passed', wrong
        assert wrong['business']['status'] == 'failed'
        path.write_text(good)
        correct = await check_agent(agent, cases, work_dir=tmp_path / 'checks')
        assert correct['status'] == 'passed', correct
        assert receipt_status(correct, replace(agent, role_models={'main': 'different-model'}), cases) == {'interface': 'passed', 'business': 'passed'}
        other_environment = {**correct, 'environment': {'python': 'different-version', 'platform': 'different-platform'}}
        assert receipt_status(other_environment, agent, cases) == {'interface': 'stale', 'business': 'stale'}
        changed = replace(agent, system_prompt='new business instructions')
        assert receipt_status(correct, changed, cases) == {'interface': 'passed', 'business': 'stale'}
        changed = replace(agent, plugins=(replace(agent.plugins[0], config={'limit': 100}),))
        assert receipt_status(correct, changed, cases) == {'interface': 'passed', 'business': 'stale'}
        path.write_text(good + '\n# changed implementation\n')
        assert receipt_status(correct, agent, cases) == {'interface': 'stale', 'business': 'stale'}
        assert not list((tmp_path / 'checks').iterdir())
    asyncio.run(run())


def test_delivery_does_not_enable_or_trust_old_environment(tmp_path):
    root, agent, cases = fixture(tmp_path)
    async def run():
        receipt = await check_agent(agent, cases, work_dir=tmp_path / 'checks')
        assert receipt['status'] == 'passed', receipt
        archive = tmp_path / 'delivery.zip'
        export_agent(agent, cases, archive, receipt=receipt, models={'internal': {'provider': 'openai_chat', 'model': 'private', 'api_key_env': 'COMPANY_KEY'}})
        imported = import_agent(archive, tmp_path / 'imported')
        assert imported['plugins_enabled'] is False and imported['validation'] == 'unverified'
        definition = load_definition(tmp_path / 'imported/agent.json')
        assert not definition.plugins[0].trusted
        checked = await check_agent(load_definition(tmp_path / 'imported/agent.json', trust=True), cases, work_dir=tmp_path / 'checks')
        assert checked['status'] == 'passed', checked
        with zipfile.ZipFile(archive) as z:
            assert not any('.env' in name or 'session-' in name for name in z.namelist())
        with pytest.raises(ValueError, match='credential'):
            export_agent(replace(agent, plugins=(replace(agent.plugins[0], config={'api_key': 'real-secret'}),)), cases, tmp_path / 'secret.zip')
    asyncio.run(run())


def test_import_rejects_traversal_before_creating_destination(tmp_path):
    archive = tmp_path / 'invalid.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../outside.py', 'raise RuntimeError()')
    with pytest.raises(ValueError):
        import_agent(archive, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_timeout_and_unload_failure_do_not_pass(tmp_path):
    root, agent, cases = fixture(tmp_path)
    (root / 'tests/test_plugin.py').unlink()
    manifest = json.loads((root / 'forge-plugin.json').read_text())
    manifest['files'].remove('tests/test_plugin.py')
    (root / 'forge-plugin.json').write_text(json.dumps(manifest))
    (root / 'plugin.py').write_text('def activate(ctx, config):\n def close(): raise OSError("cleanup")\n ctx.effect(close)\n')
    async def run():
        result = await check_agent(agent, {'cases': []}, work_dir=tmp_path / 'checks')
        assert result['status'] == 'failed' and result['interface']['status'] == 'failed'
        (root / 'plugin.py').write_text('import time\ndef activate(ctx, config): time.sleep(100)\n')
        timed = await check_agent(agent, cases, timeout=2, work_dir=tmp_path / 'checks')
        assert timed['status'] == 'failed' and timed['failure'] == 'TimeoutError'
    asyncio.run(run())


@pytest.mark.parametrize('color', [False, True])
def test_cli_requires_explicit_trust(tmp_path, color):
    from click import unstyle
    from forge.cli import app
    root, agent, cases = fixture(tmp_path)
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps(cases))
    result = CliRunner().invoke(app, ['plugin', 'check', str(root), '--cases', str(path)], color=color)
    assert result.exit_code != 0 and '--trust' in unstyle(result.output)
    untrusted = replace(agent, plugins=(replace(agent.plugins[0], trusted=False),))
    with pytest.raises(ValueError, match='explicit trust'):
        asyncio.run(check_agent(untrusted, cases, work_dir=tmp_path / 'must-not-create'))
    assert not (tmp_path / 'must-not-create').exists()


def test_candidate_cannot_rewrite_checked_copy_and_keep_receipt(tmp_path):
    root, agent, cases = fixture(tmp_path)
    (root / 'tests/test_plugin.py').write_text('''from pathlib import Path
import unittest
class Test(unittest.TestCase):
 def test_changes_candidate(self):
  path = Path(__file__).resolve().parents[1] / 'plugin.py'
  path.write_text(path.read_text() + '\\n# mutated by own test\\n')
''')
    result = asyncio.run(check_agent(agent, cases, work_dir=tmp_path / 'checks'))
    assert result['status'] == 'failed' and result['source_unchanged'] is False


def test_cancel_case_and_plugin_completion_extension(tmp_path):
    root, agent, cases = fixture(tmp_path)
    (root / 'tests/test_plugin.py').write_text('import unittest\n')
    (root / 'plugin.py').write_text('''import asyncio
def activate(ctx, config):
 async def wait(args):
  await asyncio.Event().wait()
 ctx.register_tool('wait', 'wait', {'type':'object'}, wait)
 ctx.register_completion_check('rule', lambda: ['business rule is unmet'] if config.get('reject') else [])
''')
    async def run():
        checked = await check_agent(agent, {'cases': [{'id': 'cancel', 'tool': 'wait', 'arguments': {},
            'cancel_after_ms': 100, 'expect': {'cancelled': True}}]}, work_dir=tmp_path / 'checks')
        assert checked['status'] == 'passed', checked
        rejected = replace(agent, plugins=(replace(agent.plugins[0], config={'reject': True}),))
        checked = await check_agent(rejected, {'cases': []}, work_dir=tmp_path / 'checks')
        assert checked['status'] == 'failed'
    asyncio.run(run())


def test_development_repairs_real_plugin_in_original_budget(tmp_path):
    import os
    import shlex
    import subprocess
    import sys
    from forge.config import ForgeConfig
    from forge.development import develop_plugin
    from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted, ToolCall
    root, agent, cases = fixture(tmp_path)
    (root / 'tests/test_plugin.py').write_text('import unittest\nclass Test(unittest.TestCase):\n def test_own(self): self.assertTrue(True)\n')
    command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests']
    verify = {'command': subprocess.list2cmdline(command) if os.name == 'nt' else shlex.join(command)}
    steps = [('replace_text', {'path': 'plugin.py', 'old_text': 'Hello, ', 'new_text': 'Wrong, '}),
             ('verify', verify), ('finish_task', {'status': 'completed', 'task_kind': 'change', 'summary': 'Developed plugin'}),
             ('replace_text', {'path': 'plugin.py', 'old_text': 'Wrong, ', 'new_text': 'Hello, '}),
             ('verify', verify), ('finish_task', {'status': 'completed', 'task_kind': 'change', 'summary': 'Repaired and verified plugin'})]
    class Model:
        model = 'scripted-development'
        calls = 0
        async def stream(self, messages, **kwargs):
            if self.calls == 3:
                assert 'Independent acceptance failed' in str(messages)
            name, args = steps[self.calls]
            self.calls += 1
            if name == 'replace_text':
                yield ModelToolCallCompleted(ToolCall(0, str(self.calls) + '-read', 'read_file', {'path': args['path']}))
            yield ModelToolCallCompleted(ToolCall(1 if name == 'replace_text' else 0, str(self.calls), name, args))
            yield ModelResponseCompleted('tool_use')
        async def aclose(self):
            pass
    model = Model()
    result = asyncio.run(develop_plugin('Greet Ada with Hello', root, cases, execute=True,
        config=ForgeConfig(api_key='unused', model_id=model.model), model_client_factory=lambda *a, **k: model,
        sandbox='full-access', data_root=tmp_path / 'data'))
    assert result['status'] == 'passed', result
    assert model.calls == result['turn']['model_calls'] == 6
    assert result['plugin_enabled'] is False and result['validation']['business']['status'] == 'passed'
