"""Offline syntax, dependency, secret and workflow checks; never print secret bytes."""
import argparse
import ast
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import tomllib
import yaml

ROOT=Path(__file__).resolve().parents[1]


def check_dependency_graph(tasks):
    graph={t['id']:t['depends_on'] for t in tasks}
    if len(graph)!=len(tasks):raise ValueError('Duplicate task identity')
    active=set();done=set()
    def visit(name):
        if name not in graph:raise ValueError('Unknown dependency: '+name)
        if name in active:raise ValueError('Task dependency cycle: '+name)
        if name in done:return
        active.add(name)
        for dependency in graph[name]:visit(dependency)
        active.remove(name);done.add(name)
    for name in graph:visit(name)


def check_workflow(path):
    raw=path.read_text(encoding='utf-8');workflow=yaml.load(raw,Loader=yaml.BaseLoader)
    triggers=workflow['on'];events=set(triggers) if isinstance(triggers,dict) else {triggers}
    if events & {'pull_request_target','workflow_run'}:raise ValueError('Untrusted privileged workflow trigger: '+path.name)
    if workflow.get('permissions')!={'contents':'read'}:raise ValueError('Workflow token must be contents-read only')
    for job in workflow['jobs'].values():
        body=json.dumps(job)
        privileged='self-hosted' in body or 'secrets.' in body
        if privileged:
            condition=job.get('if','')
            if events!={'workflow_dispatch'} or not job.get('environment') or any(s not in condition for s in (
                "github.event_name == 'workflow_dispatch'","github.repository == 'Titans23/forgecode'","github.ref == 'refs/heads/main'")):
                raise ValueError('Native/secret job must require trusted manual dispatch and protected environment')
        if 'write' in json.dumps(job.get('permissions',{})):raise ValueError('Unexpected job write permission')
        for step in job.get('steps',[]):
            if 'uses' in step and not re.fullmatch(r'[\w.-]+/[\w./-]+@[0-9a-f]{40}',step['uses']):raise ValueError('Action must use a full verified commit SHA')
            if step.get('uses','').startswith('actions/checkout@') and step.get('with',{}).get('persist-credentials')!='false':raise ValueError('Checkout credentials must not persist')


SECRET_PATTERNS=[('private-key',re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\\n]+[A-Za-z0-9+/]{40,}')),
    ('provider-token',re.compile(r'\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{40,}')),
    ('github-token',re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{70,})'))]


def scan_secrets(path):
    try:text=path.read_text(encoding='utf-8')
    except UnicodeError:return []
    return [{'path':path.as_posix(),'line':text[:m.start()].count('\n')+1,'kind':kind}
        for kind,pattern in SECRET_PATTERNS for m in pattern.finditer(text)]


def check_project(root):
    docs=root/'docs/implementation'
    tasks=json.loads((docs/'backlog.json').read_bytes())['tasks']
    progress=json.loads((docs/'progress.json').read_bytes())['tasks']
    check_dependency_graph(tasks)
    if set(progress)!={t['id'] for t in tasks}:raise ValueError('Progress/backlog task identities differ')
    spec=(docs/'forgecode-v4.md').read_text(encoding='utf-8')
    for task in tasks:
        card=docs/'tasks'/(''+task['id']+'.md')
        if not card.is_file():raise ValueError('Task card missing: '+task['id'])
        for chapter in task.get('chapters',[]):
            if not re.search(r'^## '+str(chapter)+r'\.',spec,re.M):raise ValueError('Task references missing specification chapter')
    for case in json.loads((docs/'acceptance-registry.json').read_bytes())['cases']:
        if case['owner_task'] not in progress:raise ValueError('Unknown acceptance owner')
        if progress[case['owner_task']]['implementation_status']=='implemented' and not case['implementation_test_refs']:raise ValueError('Implemented case lacks real test refs: '+case['id'])
        for ref in case['implementation_test_refs']:
            parts=ref.split('::')
            p=(root/parts[0]).resolve()
            if not p.is_relative_to(root.resolve()) or not p.exists():raise ValueError('Acceptance test ref missing/outside repository: '+case['id'])
            if len(parts)>1 and p.suffix=='.py':
                symbols={n.name for n in ast.walk(ast.parse(p.read_text(encoding='utf-8-sig'))) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
                if any(name.split('[')[0] not in symbols for name in parts[1:]):raise ValueError('Acceptance test symbol missing: '+case['id'])
    package=json.loads((root/'package.json').read_bytes());lock=json.loads((root/'package-lock.json').read_bytes())
    for field in ('dependencies','devDependencies'):
        if package[field]!=lock['packages'][''][field]:raise ValueError('npm manifest differs from lock: '+field)
        for name,version in package[field].items():
            if lock['packages']['node_modules/'+name]['version']!=version:raise ValueError('npm dependency is not exactly locked: '+name)
    release=json.loads((root/'release-lock.json').read_bytes())
    for name,digest in release['lockfile_hashes'].items():
        if sha256((root/name).read_bytes()).hexdigest()!=digest:raise ValueError('Release lock hash differs: '+name)
    tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8'));tomllib.loads((root/'uv.lock').read_text(encoding='utf-8'))
    names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=root,text=True,encoding='utf-8').splitlines()
    findings=[];syntax=0
    for name in sorted(set(names)):
        p=root/name
        if not p.is_file(): continue
        if Path(name).parts[0] in {'.forge','build','update_implementation_pack'} or p.suffix.lower() not in {'.py','.spec','.ts','.mts','.tsx','.js','.mjs','.cjs','.json','.toml','.yaml','.yml','.md','.env','.txt'}:continue
        findings.extend(scan_secrets(p))
        if p.suffix in ('.py','.spec'):ast.parse(p.read_text(encoding='utf-8-sig'),filename=name);syntax+=1
    if findings:raise ValueError('Potential secret locations: '+json.dumps(findings))
    workflows=list((root/'.github/workflows').glob('*.yml'))
    if not workflows:raise ValueError('Real CI workflows are missing')
    for path in workflows:check_workflow(path)
    return {'python_syntax_files':syntax,'workflows':len(workflows),'task_cards':len(tasks),'secret_findings':0}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.parse_args()
    try:print(json.dumps({'status':'pass','scope':'offline syntax/locks/graph/refs/workflows/secret patterns',**check_project(ROOT)}));return 0
    except (OSError,ValueError,KeyError,SyntaxError,yaml.YAMLError) as error:print(json.dumps({'status':'fail','reason':str(error)}));return 1
if __name__=='__main__':raise SystemExit(main())
