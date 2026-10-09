"""Recompute repository-owned evidence; declarations alone cannot establish pass."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


def windows_acceptance_evidence(record):
    match = re.fullmatch(r'Windows-10-10\.0\.(\d+)(?:-[A-Za-z0-9._-]+)?', record.get('os_build', ''))
    return record.get('platform') == 'win32' and match is not None and 19045 <= int(match[1]) < 22000


def source_fingerprint(root):
    names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=root,text=True,encoding='utf-8').splitlines()
    files={}
    for name in sorted(set(names)):
        path=Path(name)
        source=path.parts[0] in {'forge','benchmark','scripts','tests','packaging','apps','packages','sandbox_bridge','contracts','.github','experiments'}
        manifest=name in {'release-manifest.json','.gitattributes','.python-version','pyproject.toml','package.json','package-lock.json','release-lock.json','uv.lock'}
        if manifest or source and path.suffix in {'.py','.ts','.mts','.tsx','.css','.html','.js','.mjs','.cjs','.json','.toml','.spec','.sql','.yaml','.yml'} or name.startswith(('tests/implementation/fixtures/','packaging/linux/')):
            files[name]=sha256((root/path).read_bytes()).hexdigest()
    return sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()


def owned_file(root, relative):
    if not isinstance(relative,str) or not relative or '\\' in relative or ':' in relative or PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts:
        raise ValueError('Evidence path is outside the repository')
    path=(root/relative).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or not path.is_file():raise ValueError('Evidence path escapes repository ownership')
    return path


def check_evidence(root, record):
    if record['status'] not in ('pass','blocked','fail'):raise ValueError('Unknown evidence verdict')
    report=owned_file(root,record['report_ref'])
    if sha256(report.read_bytes()).hexdigest()!=record['report_hash']:raise ValueError('Evidence report hash changed')
    junit_suites={'audit','unit','portable','regression'}
    if (record['suite'] in junit_suites)!=(report.suffix=='.xml'):raise ValueError('Report format does not match registered verifier suite')
    if report.suffix=='.xml':
        cases=list(ET.parse(report).iter('testcase'))
        counts={'collected':len(cases),'skipped':sum(c.find('skipped') is not None for c in cases),
            'failures':sum(c.find('failure') is not None for c in cases),'errors':sum(c.find('error') is not None for c in cases)}
        actual='fail' if not cases or record['exit_code'] or counts['failures'] or counts['errors'] else 'blocked' if counts['skipped'] else 'pass'
        if actual=='pass' and any(record.get('tests',{}).get(k)!=v for k,v in counts.items()):raise ValueError('Declared testcase counts differ from JUnit')
        data={'status':actual,'counts':counts}
    else:
        data=json.loads(report.read_bytes())
        actual=data['status']
        checks=data.get('checks',[])
        if actual=='pass' and (record['exit_code']!=0 or not checks or any(c.get('status')!='pass' for c in checks)):actual='fail'
        if record['suite'].startswith('sandbox-') and actual=='pass' and data.get('eligible_for_native_pass') is not True:actual='fail'
        if actual=='blocked' and not data.get('reason'):raise ValueError('Blocked evidence needs an actual reason')
    if actual!=record['status']:raise ValueError('Declared evidence status differs from actual report')
    return data


def load_evidence(root, evidence_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',evidence_id):raise ValueError('Invalid evidence identity')
    path=owned_file(root,'docs/implementation/evidence/'+evidence_id+'.json')
    value=json.loads(path.read_bytes())
    if value.get('evidence_id')!=evidence_id:raise ValueError('Evidence identity mismatch')
    datetime.fromisoformat(value['start'])
    return value


def latest_evidence(records):
    selected={}
    for record in records:
        key=(record.get('task_id'),record['suite'],record['platform'])
        previous=selected.get(key)
        if previous is None or datetime.fromisoformat(record['start'])>datetime.fromisoformat(previous['start']):selected[key]=record
    return list(selected.values())


def evaluate_gate(root, name, evidence_ids=None):
    root=Path(root);docs=root/'docs/implementation'
    errors=[];blocked=[];records=[];incomplete=[];invalid_evidence=[];implementation_gaps={}
    if name=='ci':
        if not evidence_ids:return {'status':'fail','gate':name,'errors':['Fresh CI evidence IDs are required']}
        ids=list(evidence_ids)
    else:
        progress=json.loads((docs/'progress.json').read_bytes())
        incomplete=[t for t,s in progress['tasks'].items() if s['implementation_status']!='implemented']
        ids=[]
        for task,state in progress['tasks'].items():
            gaps=state.get('remaining_implementation',[])
            if not isinstance(gaps,list) or any(not isinstance(gap,str) or not gap.strip() for gap in gaps):
                errors.append(task+': invalid mandatory implementation gap declaration')
            elif gaps:
                implementation_gaps[task]=gaps
                blocked.extend(task+': mandatory implementation gap: '+gap for gap in gaps)
            if state['implementation_status']!='implemented':continue
            if not state['evidence_ids']:errors.append(task+': no evidence')
            ids.extend(state['evidence_ids'])
            if name=='release':blocked.extend(task+': '+r for r in state.get('blocked_reasons',[]))
    for identity in dict.fromkeys(ids):
        try:records.append(load_evidence(root,identity))
        except (OSError,ValueError,KeyError,TypeError) as error:
            errors.append(identity+': '+str(error));invalid_evidence.append(identity)
    selected=records if name=='ci' else latest_evidence(records)
    reports={}
    for record in selected:
        try:
            reports[record['evidence_id']]=check_evidence(root,record)
            if record['status']=='fail':errors.append(record['evidence_id']+': actual verification failed')
            elif record['status']=='blocked' and name in ('release','ci'):blocked.append(record['evidence_id']+': required verification blocked')
        except (OSError,ValueError,KeyError,TypeError,ET.ParseError) as error:
            errors.append(record['evidence_id']+': '+str(error));invalid_evidence.append(record['evidence_id'])
    if name=='ci':
        missing={'quality','contracts','unit','portable'}-{r['suite'] for r in selected}
        errors.extend('Missing CI suite: '+s for s in sorted(missing))
        fingerprint=source_fingerprint(root)
        head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
        for r in selected:
            if r.get('source_inventory_hash')!=fingerprint or r['git_commit']!=head or r['platform']!=sys.platform:errors.append(r['evidence_id']+': evidence belongs to another source/platform')
    elif progress['tasks'].get('F29',{}).get('implementation_status')=='implemented':
        fingerprint=source_fingerprint(root)
        for suite in ('contracts','quality','unit','portable','regression'):
            current=[r for r in selected if r['suite']==suite and r['platform']==sys.platform]
            latest=max(current,key=lambda r:datetime.fromisoformat(r['start'])) if current else None
            if latest is None or latest.get('source_inventory_hash')!=fingerprint or latest['status']!='pass':
                errors.append('Current source lacks passing '+suite+' evidence')
    if name=='release':
        registry=json.loads((docs/'acceptance-registry.json').read_bytes())
        if not registry['cases']:errors.append('Acceptance registry is empty')
        for case in registry['cases']:
            if not case.get('release_required'):continue
            if not case.get('implementation_test_refs'):blocked.append(case['id']+': no implementation test mapping')
            for platform in case['required_platforms']:
                proof=case['platform_verification'].get(platform,{})
                if proof.get('status')!='pass' or not proof.get('evidence_ids'):
                    blocked.append(case['id']+'/'+platform+': required platform evidence unavailable');continue
                for identity in proof['evidence_ids']:
                    try:
                        r=load_evidence(root,identity);report=check_evidence(root,r)
                        if r['status']!='pass' or case['id'] not in r['case_ids']:raise ValueError('Case is not established by cited passing evidence')
                        if platform!='portable':
                            if report.get('eligible_for_native_pass') is not True:raise ValueError('Development evidence cannot establish native platform acceptance')
                            if platform=='windows' and not windows_acceptance_evidence(r):raise ValueError('Windows 10 x64 build 19045 or later workstation evidence is missing')
                            if platform=='linux' and r['platform']!='linux':raise ValueError('Native Linux evidence is missing')
                    except (OSError,ValueError,KeyError,TypeError,ET.ParseError) as error:blocked.append(case['id']+'/'+platform+': '+str(error))
        lock=json.loads((root/'release-lock.json').read_bytes())
        manifest=json.loads((root/'release-manifest.json').read_bytes()) if (root/'release-manifest.json').is_file() else {}
        if lock.get('resolution_status')!='resolved' or lock.get('security',{}).get('status')!='pass':blocked.append('Dependency resolution/security remains blocked')
        if not any(r['suite']=='security' and r['status']=='pass' for r in selected):blocked.append('Actual dependency security evidence is missing')
        if manifest.get('channel')!='production' or manifest.get('signature',{}).get('status')!='verified' or not manifest.get('signing_verification'):blocked.append('Independent production signing evidence is missing')
        if manifest.get('project_license_status')!='present' or not (root/'LICENSE').is_file():blocked.append('Owner-approved project LICENSE is missing')
    return {'status':'fail' if errors or incomplete else 'blocked' if blocked else 'pass','gate':name,
        'incomplete_tasks':incomplete,'implementation_gaps':implementation_gaps,'errors':errors,'missing_or_changed_evidence':invalid_evidence,'blocked_dependencies':blocked,
        'selected_evidence_ids':[r['evidence_id'] for r in selected]}
