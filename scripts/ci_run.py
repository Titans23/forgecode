"""Run fresh offline CI evidence or explicitly protected native/packaged jobs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.impl import verify, gate
from scripts.evidence_gate import source_fingerprint


def trusted_native_dispatch(environment):
    return environment.get('GITHUB_EVENT_NAME')=='workflow_dispatch' and environment.get('GITHUB_REPOSITORY')=='Titans23/forgecode' and environment.get('GITHUB_REF')=='refs/heads/main'


def main():
    p=argparse.ArgumentParser();p.add_argument('--layer',choices=('portable','native','packaged'),default='portable');a=p.parse_args()
    if a.layer!='portable' and not trusted_native_dispatch(os.environ):
        print(json.dumps({'status':'blocked','reason':'Native/packaged CI requires trusted main manual dispatch; no privileged operations started'}));return 2
    subprocess.run([sys.executable,str(ROOT/'scripts/build_bridge.py')],cwd=ROOT,check=True)
    if a.layer=='portable':suites=['contracts','quality','unit','portable']
    elif a.layer=='native':suites=['terminal-native']
    else:
        subprocess.run([sys.executable,str(ROOT/'scripts/build_bridge.py'),'--bundle-python'],cwd=ROOT,check=True)
        subprocess.run(['uv','build','--wheel','--out-dir',str(ROOT/'.local/terminal/wheels')],cwd=ROOT,check=True)
        wheels=list((ROOT/'.local/terminal/wheels').glob('*.whl'))
        if len(wheels)!=1:raise ValueError('Expected exactly one built terminal wheel')
        subprocess.run([sys.executable,str(ROOT/'scripts/check_wheel.py'),str(wheels[0]),
            '--output',str(ROOT/'.local/ci/wheel.json')],cwd=ROOT,check=True)
        return 0
    records=[];output=ROOT/'.local/ci'/('run-'+a.layer+'.json');output.parent.mkdir(parents=True,exist_ok=True)
    prepared_source_hash = source_fingerprint(ROOT)
    for suite in suites:
        record=verify(suite,'T01',prepared_source_hash=prepared_source_hash);records.append(record)
        output.write_text(json.dumps({'layer':a.layer,'evidence':records},indent=2)+'\n',encoding='utf-8')
        if record['status']!='pass':print(json.dumps({'status':record['status'],'suite':suite,'evidence_id':record['evidence_id']}));return 2 if record['status']=='blocked' else 1
    result=gate('ci',[r['evidence_id'] for r in records]) if a.layer=='portable' else {'status':'pass','scope':'actual '+a.layer+' checks; release gate remains separate'}
    output.write_text(json.dumps({'layer':a.layer,'evidence':records,'gate':result},indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result));return {'pass':0,'blocked':2,'fail':1}[result['status']]
if __name__=='__main__':raise SystemExit(main())
