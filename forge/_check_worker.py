"""Private worker for validation; receives inputs only, never expected answers."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys

# -I excludes candidate directories and environment PYTHONPATH. Use this SDK.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge.agents import AgentDefinition
from forge.api import HarnessSession, RunProfile, SessionOptions
from forge.config import ForgeConfig
from forge.permissions.policy import ApprovalResponse
from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted, ModelTextDelta, ToolCall, ToolExecutionCompleted, TurnCompleted


class ProbeModel:
    provider = 'anthropic'
    model = 'deterministic-check'

    def __init__(self, cases):
        self.cases = iter(cases)
        self.closed = False

    async def stream(self, **kwargs):
        case = next(self.cases, None)
        if case:
            yield ModelToolCallCompleted(ToolCall(0, case['id'], case['tool'], case['arguments']))
            yield ModelResponseCompleted('tool_use')
        else:
            yield ModelTextDelta('Deterministic probe finished')
            yield ModelResponseCompleted('end_turn')

    async def aclose(self):
        self.closed = True


async def run(payload):
    directory = Path(payload['directory'])
    workspace = directory / 'workspace'
    workspace.mkdir()
    from forge.validation import relative_file
    for name, content in payload['files'].items():
        path = workspace.joinpath(*relative_file(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    definition = AgentDefinition.from_dict(payload['agent'])
    cases = json.loads(json.dumps(payload['cases']).replace('${WORKSPACE}', str(workspace).replace('\\', '\\\\')))
    regular = [c for c in cases if 'cancel_after_ms' not in c]
    cancellations = [c for c in cases if 'cancel_after_ms' in c]
    results = {}
    checks = []
    async def approve(request):
        return ApprovalResponse('allow_once' if request.tool_name in {c['tool'] for c in cases} else 'deny',
                                reason='Explicit deterministic acceptance input in an owned test directory')
    for index, group in enumerate([regular, *[[c] for c in cancellations]]):
        model = ProbeModel(group)
        options = SessionOptions(root=workspace if definition.base == 'coding' else None, agent=definition,
            data_root=directory / f'session-{index}', approval='auto',
            config=ForgeConfig(api_key='unused-probe', model_id=model.model),
            profile=RunProfile(intent_routing=False))
        async with await HarnessSession.open(options, approval_handler=approve, model_client_factory=lambda *a, **k: model) as session:
            terminal = None
            if index:
                task = asyncio.create_task(session.run('Execute cancellation probe'))
                await asyncio.sleep(group[0]['cancel_after_ms'] / 1000)
                requested = session.cancel()
                try:
                    terminal = (await task).status
                    cancelled = False
                except asyncio.CancelledError:
                    cancelled = True
                results[group[0]['id']] = {'cancelled': requested and cancelled}
            else:
                async for event in session.stream('Execute the supplied deterministic tool probes'):
                    if isinstance(event, TurnCompleted):
                        terminal = event.result.status
                    if isinstance(event, ToolExecutionCompleted):
                        if event.tool_call.id in results:
                            raise ValueError('Duplicate probe result')
                        results[event.tool_call.id] = asdict(event.result)
        host = session.conversation.plugin_host if definition.plugins else None
        checks.append({'cleanup': session.cleanup, 'client_closed': model.closed,
                       'terminal': terminal, 'expected_cancel': bool(index),
                       'registrations_removed': host is None or not (host.tools or host.services or host.providers or host.checks)})
    good = all((c['cleanup'] or {}).get('state', 'clean') == 'clean' and c['client_closed'] and c['registrations_removed']
               and (c['expected_cancel'] or c['terminal'] == 'completed') for c in checks)
    return {'results': results, 'framework': {'status': 'passed' if good else 'failed', 'sessions': checks,
        'scope': 'schema/load, supplied tool/error/cancel cases, unload registrations and owned cleanup; not a safety certification'}}


if __name__ == '__main__':
    try:
        result = asyncio.run(run(json.load(sys.stdin)))
    except Exception as error:
        result = {'error': type(error).__name__ + ': ' + str(error), 'framework': {'status': 'failed'}}
    print(json.dumps(result, ensure_ascii=True))
