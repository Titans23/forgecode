"""Actual layered quality/contract/security commands with machine-readable reports."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def run(suite, output):
    node=shutil.which('node');npm=shutil.which('npm')
    if not node or not npm:return {'status':'blocked','reason':'Locked build Node/npm are unavailable','checks':[]}
    if suite=='contracts':commands=[[sys.executable,str(ROOT/'scripts/check_contracts.py'),'--check'],[npm,'run','contracts:check']]
    elif suite=='quality':commands=[[sys.executable,str(ROOT/'scripts/quality.py')],[npm,'run','typecheck'],[npm,'run','test:unit'],[node,str(ROOT/'packaging/verify-release.mjs')]]
    else:commands=[[npm,'audit','--json','--registry=https://registry.npmjs.org']]
    checks=[]
    for i,argv in enumerate(commands):
        result=subprocess.run(argv,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=240)
        (output.parent/(suite+f'-{i}.stdout.log')).write_text(result.stdout,encoding='utf-8')
        (output.parent/(suite+f'-{i}.stderr.log')).write_text(result.stderr,encoding='utf-8')
        if suite=='security':
            try:audit=json.loads(result.stdout)
            except ValueError:return {'status':'blocked','reason':'Dependency audit endpoint did not return a valid report','checks':[]}
            if audit.get('error'):return {'status':'blocked','reason':'Dependency audit endpoint unavailable','checks':[]}
            vulnerabilities=audit.get('metadata',{}).get('vulnerabilities')
            if vulnerabilities is None:return {'status':'fail','reason':'npm audit report has no vulnerability totals','checks':[]}
            return {'status':'blocked' if result.returncode or vulnerabilities.get('total') else 'pass',
                **({'reason':'Unresolved locked dependency vulnerabilities'} if result.returncode or vulnerabilities.get('total') else {}),
                'checks':[{'name':'actual npm audit','status':'blocked' if result.returncode else 'pass','command':argv}],
                'audit_ref':(output.parent/(suite+f'-{i}.stdout.log')).relative_to(ROOT).as_posix(),'vulnerabilities':vulnerabilities}
        checks.append({'name':suite+':'+str(i),'status':'pass' if result.returncode==0 else 'fail','command':argv,'exit_code':result.returncode})
        if result.returncode:return {'status':'fail','reason':'Actual '+suite+' command failed','checks':checks}
    return {'status':'pass','scope':'actual '+suite+' commands','checks':checks}


def main():
    p=argparse.ArgumentParser();p.add_argument('--suite',choices=('contracts','quality','security'),required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output=a.output.resolve();a.output.parent.mkdir(parents=True,exist_ok=True)
    try:r=run(a.suite,a.output)
    except (OSError,ValueError,subprocess.TimeoutExpired) as error:r={'status':'fail','reason':str(error),'checks':[]}
    a.output.write_text(json.dumps(r,indent=2)+'\n',encoding='utf-8');print(json.dumps(r));return {'pass':0,'blocked':2,'fail':1}[r['status']]
if __name__=='__main__':raise SystemExit(main())
