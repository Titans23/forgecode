"""Installation failures retain output without changing execution or retry policy."""
import asyncio
import json
import sys

import pytest
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import ExecResult

from benchmark.harbor.forgecode_agent import ForgeCodeHarborAgent


@pytest.mark.parametrize('failed_step', [1, 2])
def test_install_preserves_real_process_error_tail_and_redacts_secrets(tmp_path, failed_step):
    class LocalEnvironment:
        default_user = 'worker'

        def __init__(self):
            self.calls = []

        async def exec(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) != failed_step:
                return ExecResult(return_code=0)
            process = await asyncio.create_subprocess_exec(sys.executable, '-c',
                "import sys;print('progress\\n'*3000);print('API_KEY=fixture-private-key');"
                "print('FINAL INSTALL ERROR');"
                "print('Authorization: Bearer fixture-private-bearer',file=sys.stderr);"
                "print('LAST STDERR DETAIL',file=sys.stderr);sys.exit(100)",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await process.communicate()
            return ExecResult(return_code=process.returncode, stdout=stdout.decode(), stderr=stderr.decode())

    environment = LocalEnvironment()
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path, package='fixture-package', frozen_configuration='{}')
    with pytest.raises(NonZeroAgentExitCodeError) as raised:
        asyncio.run(agent.install(environment))
    assert len(environment.calls) == failed_step  # No hidden retry or later install step.
    assert environment.calls[0]['user'] == 'root'
    if failed_step == 2:
        assert environment.calls[1]['user'] is None
    path, = (tmp_path / 'setup').glob('install-error-*.json')
    raw = path.read_text(encoding='utf-8')
    result = json.loads(raw)
    assert result['return_code'] == 100 and result['output_available']
    assert result['stdout']['text'].splitlines()[-1] == 'FINAL INSTALL ERROR'
    assert result['stderr']['text'].splitlines()[-1] == 'LAST STDERR DETAIL'
    assert not result['stdout']['truncated'] and result['stdout']['text'].count('progress') == 3000
    assert 'fixture-private-key' not in raw and 'fixture-private-bearer' not in raw
    assert str(path) in raised.value.__notes__[0]


@pytest.mark.parametrize('failure', [TimeoutError('installation timed out'), asyncio.CancelledError()])
def test_install_without_a_result_records_unknown_output_and_preserves_exception(tmp_path, failure):
    class Environment:
        async def exec(self, **kwargs):
            raise failure

    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)
    with pytest.raises(type(failure)) as raised:
        asyncio.run(agent._exec_install(Environment(), 'install fixture'))
    assert raised.value is failure
    path, = (tmp_path / 'setup').glob('install-error-*.json')
    result = json.loads(path.read_text())
    assert result['return_code'] is None and result['output_available'] is False
    assert result['stdout'] is result['stderr'] is None
    assert result['exception'][0]['type'] == type(failure).__name__


def test_install_log_limit_keeps_error_tail_without_touching_original_result(tmp_path, monkeypatch):
    monkeypatch.setattr('benchmark.harbor.forgecode_agent._INSTALL_LOG_CHAR_LIMIT', 128)
    original = 'START\n' + 'x' * 2000 + '\nFINAL ERROR'
    result = ExecResult(return_code=100, stdout=original)

    class Environment:
        async def exec(self, **kwargs):
            return result

    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)
    with pytest.raises(NonZeroAgentExitCodeError):
        asyncio.run(agent._exec_install(Environment(), 'install fixture'))
    path, = (tmp_path / 'setup').glob('install-error-*.json')
    stream = json.loads(path.read_text())['stdout']
    assert stream['truncated'] and stream['characters'] == len(original)
    assert stream['text'].startswith('START') and stream['text'].endswith('FINAL ERROR')
    assert len(stream['text']) < 160 and result.stdout == original


def test_install_log_write_failure_does_not_replace_installation_error(tmp_path, caplog):
    (tmp_path / 'setup').write_text('a conflicting file')

    class Environment:
        async def exec(self, **kwargs):
            return ExecResult(return_code=100, stderr='original installation error')

    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)
    with pytest.raises(NonZeroAgentExitCodeError, match='original installation error'):
        asyncio.run(agent._exec_install(Environment(), 'install fixture'))
    assert 'could not be saved' in caplog.text


def test_successful_install_command_preserves_result_and_has_no_error_log(tmp_path):
    result = ExecResult(return_code=0, stdout='installed')

    class Environment:
        async def exec(self, **kwargs):
            return result

    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)
    assert asyncio.run(agent._exec_install(Environment(), 'install fixture')) is result
    assert not (tmp_path / 'setup').exists()
