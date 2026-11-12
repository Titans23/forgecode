"""Modern execution imports no legacy RPC or SQLite services."""
import subprocess
import sys
import textwrap


def test_host_runtime_and_benchmark_are_independent_of_legacy_control_plane(tmp_path):
    code = textwrap.dedent('''
        import asyncio, importlib.abc, sys
        from pathlib import Path
        class Reject(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, *args):
                if fullname.startswith(('forge.engine', 'forge.application')):
                    raise ImportError('Legacy control plane loaded: ' + fullname)
        sys.meta_path.insert(0, Reject())
        from forge.api import HarnessSession, SessionOptions
        from forge.config import ForgeConfig
        from forge.runtime.state import ModelTextDelta, ModelResponseCompleted
        from forge.tools.shell import run_process
        from benchmark.harbor.platform import make_plan
        from benchmark.core.bundle import write_bundle
        class Model:
            model = 'fixture'
            async def stream(self, **kwargs):
                yield ModelTextDelta('ok')
                yield ModelResponseCompleted('end_turn')
        async def main():
            root = Path(sys.argv[1]) / 'project'
            root.mkdir()
            opts = SessionOptions(root, data_root=root.parent / 'data',
                config=ForgeConfig(api_key='fixture', model_id='fixture'))
            async with await HarnessSession.open(opts, model_client_factory=lambda *a, **k: Model()) as session:
                assert (await session.run('hello')).text == 'ok'
                reply = await session.backend.observer.request('tool', name='write_file',
                    arguments={'path': 'test.txt', 'content': 'written'})
                assert reply['result']['success']
                result = await run_process([sys.executable, '-c', 'print(42)'], cwd=root, timeout_seconds=15)
                assert result.exit_code == 0 and '42' in result.stdout
            assert session.cleanup['state'] == 'clean'
        asyncio.run(main())
    ''')
    result = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', code, str(tmp_path)],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
