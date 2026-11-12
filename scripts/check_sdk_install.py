"""Fresh minimal SDK/CLI install and delivery reconstruction, with no model or Node calls."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('uv is required to build and install the standard wheel')
    def run(command, *, cwd=output, environment=None):
        result = subprocess.run([str(x) for x in command], cwd=cwd, env=environment,
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        with (output / 'commands.log').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps([str(x) for x in command]) + '\n' + result.stdout + result.stderr + '\n')
        if result.returncode:
            raise RuntimeError(f'Command failed ({result.returncode}); see {output / "commands.log"}')
        return result.stdout
    run([uv, 'build', '--wheel', '--out-dir', output / 'wheels',
         '--config-setting=--global-option=build', '--config-setting=--global-option=--build-base=' + str(output / 'build')], cwd=ROOT)
    wheel, = (output / 'wheels').glob('*.whl')
    run([uv, 'venv', '--python', sys.executable, output / 'first'])
    def executable(directory, name):
        return output / directory / ('Scripts' if os.name == 'nt' else 'bin') / (name + '.exe' if os.name == 'nt' else name)
    first = executable('first', 'python')
    run([uv, 'pip', 'install', '--python', first, str(wheel) + '[cli,validation]'])
    environment = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT'}}
    environment.update(HOME=str(output), USERPROFILE=str(output), TEMP=str(output), TMP=str(output),
                       FORGE_DATA_DIR=str(output / 'data'), PYTHONUTF8='1', PYTHONIOENCODING='utf-8', NO_COLOR='1',
                       PATH=str(Path(os.environ.get('SYSTEMROOT', '/')) / 'System32') if os.name == 'nt' else '/nonexistent')
    probe = '''import asyncio, importlib.util, sys
from pathlib import Path
from forge.api import AgentDefinition, HarnessSession, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.state import ModelTextDelta, ModelResponseCompleted
assert all(importlib.util.find_spec(x) is None for x in ('anthropic','openai','httpx','mcp','openpyxl','yaml'))
class Model:
 model = 'offline'
 async def stream(self, **kwargs):
  yield ModelTextDelta('ok')
  yield ModelResponseCompleted('end_turn')
async def main():
 async with await HarnessSession.open(SessionOptions(agent=AgentDefinition.core(), data_root=Path(sys.argv[1]),
  config=ForgeConfig(api_key='unused', model_id='offline')), model_client_factory=lambda *a, **k: Model()) as session:
  assert session.backend is None and (await session.run('hello')).text == 'ok'
 from forge.sessions.trajectory import journal_to_openai
 rows = journal_to_openai(session.journal.path)
 assert rows[0]['messages'][-1]['content'] == 'ok'
 assert not any(x.startswith(('forge.sandbox', 'forge.tasks', 'forge.skills', 'forge.context.repository',
                              'forge.runtime.jobs', 'forge.engine', 'forge.application')) for x in sys.modules)
asyncio.run(main())
'''
    run([first, '-I', '-c', probe, output / 'core-data'], environment=environment)
    plugin = output / 'plugin'
    run([executable('first', 'forge'), 'plugin', 'init', plugin], environment=environment)
    definition = {'name': 'greeting', 'base': 'core', 'plugins': [{'source': str(plugin), 'trusted': False}]}
    (output / 'agent.json').write_text(json.dumps(definition), encoding='utf-8')
    (output / 'acceptance.json').write_text(json.dumps({'cases': [{'id': 'greeting', 'tool': 'greet',
        'arguments': {'name': 'Ada'}, 'expect': {'content': 'Hello, Ada!'}}]}), encoding='utf-8')
    run([executable('first', 'forge'), 'agent', 'check', 'agent.json', '--cases', 'acceptance.json', '--trust', '--output', 'first-validation.json'], environment=environment)
    run([executable('first', 'forge'), 'agent', 'export', 'agent.json', 'delivery.zip', '--cases', 'acceptance.json',
         '--receipt', 'first-validation.json', '--sdk-wheel', wheel], environment=environment)
    run([executable('first', 'forge'), 'agent', 'import', 'delivery.zip', 'imported'], environment=environment)
    run([uv, 'venv', '--python', sys.executable, output / 'second'])
    run([uv, 'pip', 'install', '--python', executable('second', 'python'), '-r', 'requirements.lock'], cwd=output / 'imported')
    run([executable('second', 'forge'), 'agent', 'check', 'agent.json', '--cases', 'acceptance.json', '--trust',
         '--output', str(output / 'second-validation.json')], cwd=output / 'imported', environment=environment)
    first_result, second_result = [json.loads((output / name).read_text()) for name in ('first-validation.json', 'second-validation.json')]
    assert first_result['status'] == second_result['status'] == 'passed'
    assert first_result['bindings'] == second_result['bindings']
    assert json.loads((output / 'imported/agent.json').read_text())['plugins'][0]['trusted'] is False
    result = {'status': 'passed', 'platform': sys.platform, 'same_bindings': True, 'model_api_calls': 0,
              'scope': 'fresh minimal install, core turn with Node unavailable, exported wheel/dependency lock, second environment recheck'}
    (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
