"""Natural-language development in the existing coding Harness."""
from dataclasses import asdict, replace
import json
from pathlib import Path

from forge.agents import AgentDefinition, PluginSpec
from forge.api import HarnessSession, SessionOptions, RunProfile
from forge.validation import check_agent, validate_acceptance
from forge.delivery import load_definition


async def develop_plugin(request, directory, acceptance, *, execute=False,
                         config=None, model_client_factory=None, sandbox='workspace-write', data_root=None):
    directory = Path(directory).resolve()
    acceptance = validate_acceptance(acceptance)  # Controller's private copy, outside candidate scope.
    if not acceptance['cases']:
        raise ValueError('Development requires frozen independent acceptance cases')
    if not directory.exists():
        from forge.plugin_cli import initialize
        initialize(directory, name=directory.name)
    candidate = AgentDefinition(name='candidate', base='core', plugins=(PluginSpec(str(directory), trusted=True),))
    definition_path = directory / 'agent.json'
    if not definition_path.exists():
        definition_path.write_text(json.dumps({'name': directory.name, 'base': 'core',
            'plugins': [{'source': '.', 'trusted': False}]}, indent=2), encoding='utf-8')
    if not execute:
        return {'status': 'planned', 'directory': str(directory), 'model_api_calls': 0,
                'case_ids': [c['id'] for c in acceptance['cases']], 'next': 'Repeat with --execute to use the configured model'}
    latest = None
    async def independent_check():
        nonlocal latest, candidate
        candidate = load_definition(definition_path, trust=True)
        if candidate.base != 'core' or len(candidate.plugins) != 1 or Path(candidate.plugins[0].source).resolve() != directory:
            return ('Development acceptance permits only the candidate plugin in this directory and a core composition.',)
        latest = await check_agent(candidate, acceptance)
        if latest['status'] == 'passed':
            return ()
        failures = [c['id'] for c in latest.get('business', {}).get('cases', []) if c['status'] != 'passed']
        return ('Independent acceptance failed: ' + ', '.join(failures) + '; ' + json.dumps(latest, ensure_ascii=False)[-9000:],)
    options = SessionOptions(directory, data_root=data_root, config=config, sandbox=sandbox, approval='auto',
        agent=AgentDefinition(name='plugin-developer', completion='strict'),
        profile=RunProfile(name='plugin-development', intent_routing=False))
    prompt = (f'Develop a ForgeCode Python plugin in this directory. Requirement: {request}\n'
              'Use forge.plugins.PluginContext public methods. Keep distributable code and tests listed in forge-plugin.json files. '
              'Keep agent.json as the deliverable Agent configuration; use its plugin config for business parameters. '
              'Run unittest tests with verify before finishing. Independent business acceptance is controlled externally; '
              'do not weaken requirements. Do not enable the plugin. Input/output acceptance contract:\n' + json.dumps(acceptance, ensure_ascii=False))
    async with await HarnessSession.open(options, model_client_factory=model_client_factory) as session:
        session.add_completion_check('independent-business', independent_check)
        result = await session.run(prompt)
    return {'status': 'passed' if result.status == 'completed' and latest and latest['status'] == 'passed' and
            (session.cleanup or {}).get('state') == 'clean' else 'failed',
            'turn': asdict(result), 'validation': latest, 'cleanup': session.cleanup,
            'definition': replace(candidate, plugins=tuple(replace(p, trusted=False) for p in candidate.plugins)).as_dict(),
            'plugin_enabled': False, 'scope': 'development with independent acceptance; repair stays in the same turn'}
