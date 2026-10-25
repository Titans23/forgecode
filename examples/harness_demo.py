"""Scripted model, real native tools: stale evidence, bounded repair, readonly reload.

No provider is contacted. This demonstrates Harness behavior, not model ability
or cross-process Agent continuation. Run from an installed source checkout.
"""
import argparse
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from tempfile import mkdtemp

from forge.api import HarnessSession, RunProfile, SessionOptions, event_dict
from forge.config import ForgeConfig
from forge.runtime.completion import TaskPolicy
from forge.runtime.state import ModelToolCallCompleted, ModelUsageUpdate, TokenUsage, ToolCall
from forge.sandbox.path_policy import inspect_path
from forge.sessions.store import SessionStore


def command(arguments):
    return subprocess.list2cmdline(arguments) if os.name == 'nt' else shlex.join(arguments)


class ScriptedClient:
    model = 'offline-native-demo'

    def __init__(self):
        self.calls, self.closed = 0, False

    async def aclose(self):
        self.closed = True

    async def stream(self, messages, tools=None, system=None):
        self.calls += 1
        verify = {'command': command([sys.executable, '-']),
            'stdin': "from pathlib import Path\nassert Path('value.txt').read_text() == 'new'\nprint('verified actual file')\n"}
        steps = [
            ('replace_text', {'path': 'value.txt', 'old_text': 'old', 'new_text': 'new'}),
            ('verify', verify),
            ('run_command', {'command': command([sys.executable, '-c', "print('inspection after verify')"])}),
            ('finish_task', {'task_kind': 'change', 'status': 'completed', 'summary': 'Updated value.'}),
            ('verify', verify),
            ('finish_task', {'task_kind': 'change', 'status': 'completed', 'summary': 'Freshly verified value.'}),
        ]
        if self.calls == 5:
            assert any('Completion was not accepted' in str(message) for message in messages)
        if self.calls > len(steps):
            raise AssertionError('Unexpected extra scripted request')
        name, arguments = steps[self.calls - 1]
        # Protocol fixture values only, never measured provider token costs.
        yield ModelUsageUpdate(usage=TokenUsage(10, 2))
        yield ModelToolCallCompleted(ToolCall(0, f'demo-{self.calls}', name, arguments))


async def run(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    client = ScriptedClient()
    parent = Path(mkdtemp(prefix='forge-api-demo-')).resolve()
    session = None
    cleanup = {'state': 'unknown'}
    summary = {'scope': 'scripted model / actual native tools; no real-model success claim',
        'provider_api_calls': 0, 'scripted_usage': 'synthetic protocol values, not provider costs',
        'original_request_limit': 8, 'status': 'failed', 'fixture': str(parent),
        'events': str(output / 'events.jsonl')}
    try:
        fixture_anchor = inspect_path(parent, absolute=True)
        if os.name == 'nt':
            from forge.sandbox.windows_worker import grant_controller_access
            grant_controller_access(parent)  # Fresh empty owned fixture only.
        project = parent / 'project'
        project.mkdir()
        if os.name == 'nt':
            grant_controller_access(project)
        (project / 'value.txt').write_text('old', encoding='utf-8')
        options = SessionOptions(project, data_root=output / 'sessions', sandbox='workspace-write', approval='auto',
            config=ForgeConfig(api_key='unused-scripted-demo', model_id=client.model),
            profile=RunProfile(name='api-demo', max_model_calls=8, max_tool_calls=8,
                               wall_seconds=120, intent_routing=False))
        policy = TaskPolicy(require_changes=True, require_verification=True, require_task_verification=True,
                            require_positive_verification=True, max_delivery_repairs=2, allowed_paths=('value.txt',))
        events = []
        async with await HarnessSession.open(options, task_policy=policy,
                model_client_factory=lambda *args, **kwargs: client) as session:
            with (output / 'events.jsonl').open('w', encoding='utf-8') as stream:
                async for event in session.stream('Change value.txt from old to new and verify the actual content.'):
                    value = event_dict(event)
                    events.append(value)
                    stream.write(json.dumps(value, ensure_ascii=False) + '\n')
            assert (project / 'value.txt').read_text() == 'new'
        result = events[-1]['payload']['result']
        assert events[-1]['type'] == 'TurnCompleted' and result['status'] == 'completed'
        assert result['model_calls'] == client.calls == 6 and client.closed and session.cleanup['state'] == 'clean'
        before = sha256(session.journal.path.read_bytes()).hexdigest()
        state = SessionStore(project, data_root=output / 'sessions').load(session.session_id)
        after = sha256(session.journal.path.read_bytes()).hexdigest()
        assert before == after and client.calls == 6 and not state.indeterminate_tools
        summary.update({
            'status': result['status'], 'changed_paths': result['changed_paths'], 'shared_model_calls': result['model_calls'],
            'session_id': session.session_id, 'journal': str(session.journal.path),
            'readonly_reloaded_messages': len(state.messages),
            'journal_sha256_before_reload': before, 'journal_sha256_after_reload': after,
            'original_tools_replayed': 0, 'new_turn_started_for_reload': False})
    except BaseException as error:
        cleanup = getattr(error, 'cleanup_report', cleanup)
        summary['error_type'] = type(error).__name__
        raise
    finally:
        cleanup = (session.cleanup if session is not None else None) or cleanup
        if cleanup['state'] == 'clean':
            # Only the fresh directory created above, after confirmed process cleanup.
            try:
                fixture_anchor.assert_current()
                shutil.rmtree(parent)
            except Exception as error:
                summary['status'] = 'failed'
                summary['fixture_cleanup_error'] = type(error).__name__
                summary['fixture_cleanup_error_detail'] = str(error)
        summary.update(scripted_requests=client.calls, cleanup=cleanup,
                       fixture_retained=parent.exists(), client_closed=client.closed)
        (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False))
        if 'fixture_cleanup_error' in summary and 'error_type' not in summary:
            raise RuntimeError('Owned fixture cleanup failed; see summary.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='A new artifact directory.')
    asyncio.run(run(parser.parse_args().output))
