"""Natural-language development in the existing coding Harness and original budget."""
from dataclasses import asdict
import json
from pathlib import Path

from forge.agents import AgentDefinition, PluginSpec
from forge.api import HarnessSession, SessionOptions, RunProfile
from forge.validation import check_agent, validate_acceptance


async def develop_plugin(request, directory, acceptance, *, execute=False, max_model_calls=40,
                         config=None, model_client_factory=None, sandbox='workspace-write', data_root=None):
    directory = Path(directory).resolve()
    acceptance = validate_acceptance(acceptance)  # Controller's private copy, outside candidate scope.
    if not acceptance['cases']:
        raise ValueError('Development requires frozen independent acceptance cases')
    if not directory.exists():
        from forge.plugin_cli import initialize
        initialize(directory, name=directory.name)
    candidate = AgentDefinition(name='candidate', base='core', plugins=(PluginSpec(str(directory), trusted=True),))
    if not execute:
        return {'status': 'planned', 'directory': str(directory), 'model_api_calls': 0,
                'case_ids': [c['id'] for c in acceptance['cases']], 'next': 'Repeat with --execute to use the configured model budget'}
    latest = None
    async def independent_check():
        nonlocal latest
        latest = await check_agent(candidate, acceptance)
        if latest['status'] == 'passed':
            return ()
        failures = [c['id'] for c in latest.get('business', {}).get('cases', []) if c['status'] != 'passed']
        return ('Independent acceptance failed: ' + ', '.join(failures) + '; ' + json.dumps(latest, ensure_ascii=False)[-9000:],)
    options = SessionOptions(directory, data_root=data_root, config=config, sandbox=sandbox, approval='auto',
        agent=AgentDefinition(name='plugin-developer', completion='strict'),
        profile=RunProfile(name='plugin-development', max_model_calls=max_model_calls, max_tool_calls=max_model_calls * 3,
                           wall_seconds=1800, intent_routing=False))
    prompt = (f'Develop a ForgeCode Python plugin in this directory. Requirement: {request}\n'
              'Use forge.plugins.PluginContext public methods. Keep distributable code and tests listed in forge-plugin.json files. '
              'Run unittest tests with verify before finishing. Independent business acceptance is controlled externally; '
              'do not weaken requirements. Do not enable the plugin. Input/output acceptance contract:\n' + json.dumps(acceptance, ensure_ascii=False))
    async with await HarnessSession.open(options, model_client_factory=model_client_factory) as session:
        session.add_completion_check('independent-business', independent_check)
        result = await session.run(prompt)
    return {'status': 'passed' if result.status == 'completed' and latest and latest['status'] == 'passed' and
            (session.cleanup or {}).get('state') == 'clean' else 'failed',
            'turn': asdict(result), 'validation': latest, 'cleanup': session.cleanup,
            'plugin_enabled': False, 'scope': 'bounded development; no new turn or replacement budget for repair'}
