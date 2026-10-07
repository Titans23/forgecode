"""Audit every fixed acceptance mapping; preserve missing native proof as blocked."""
import argparse
import ast
import json
from pathlib import Path,PurePosixPath
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.evidence_gate import load_evidence,check_evidence

def audit_mapping(root,cases):
    expected={prefix+f'{i:02d}' for prefix,count in (('C',24),('W',12),('D',40),('O',10),('N',24)) for i in range(1,count+1)}
    if {c['id'] for c in cases}!=expected or len(cases)!=110:raise ValueError('The fixed 86+24 acceptance set changed')
    result=[]
    proofs={}
    for case in cases:
        refs=case.get('implementation_test_refs',[])
        if not refs:raise ValueError('Acceptance mapping missing: '+case['id'])
        for ref in refs:
            parts=ref.split('::');relative=parts[0]
            if not relative or '\\' in relative or ':' in relative or PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts:
                raise ValueError('Acceptance ref escapes repository-relative mapping: '+case['id'])
            path=(root/relative).resolve(strict=True)
            if not path.is_relative_to(root.resolve()) or not path.is_file():raise ValueError('Acceptance ref escapes repository: '+case['id'])
            if len(parts)>1:
                if path.suffix!='.py':raise ValueError('Test symbols require a Python source')
                symbols={n.name for n in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
                if any(s.split('[')[0] not in symbols for s in parts[1:]):raise ValueError('Acceptance test symbol missing: '+case['id'])
        platforms={}
        for platform in case['required_platforms']:
            declared=case['platform_verification'].get(platform,{})
            valid=False;reason=declared.get('reason') or 'Actual required-platform evidence unavailable'
            ids=declared.get('evidence_ids',[])
            if declared.get('status')=='pass' and ids:
                try:
                    for identity in ids:
                        if identity not in proofs:
                            record=load_evidence(root,identity);proofs[identity]=(record,check_evidence(root,record))
                        record,report=proofs[identity]
                        if record['status']!='pass' or case['id'] not in record['case_ids']:raise ValueError('Case lacks actual passing verifier evidence')
                        if platform!='portable' and (report.get('eligible_for_native_pass') is not True or
                            platform=='windows' and (record['platform']!='win32' or 'Windows-11-' not in record['os_build']) or
                            platform=='linux' and record['platform']!='linux'):raise ValueError('Development evidence cannot prove supported native acceptance')
                    valid=True;reason=None
                except (OSError,ValueError,KeyError,TypeError) as error:reason=str(error)
            platforms[platform]={'status':'pass' if valid else 'blocked','reason':reason,'evidence_ids':ids}
        result.append({'id':case['id'],'test_refs':refs,'platforms':platforms})
    return result

def verify(root=ROOT):
    cases=audit_mapping(root,json.loads((root/'docs/implementation/acceptance-registry.json').read_bytes())['cases'])
    blocked=sum(p['status']!='pass' for c in cases for p in c['platforms'].values())
    return {'status':'blocked' if blocked else 'pass','reason':str(blocked)+' required platform proofs unavailable' if blocked else None,
        'eligible_for_native_pass':False,'scope':'all 86 legacy plus 24 new case mappings and recomputed existing proofs',
        'checks':[{'id':'all-110-real-test-mappings','status':'pass'},{'id':'required-platform-proofs','status':'blocked' if blocked else 'pass'}],
        'case_count':len(cases),'blocked_platform_proofs':blocked,'cases':cases}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:report=verify()
    except (OSError,ValueError,KeyError,SyntaxError) as error:report={'status':'fail','reason':str(error),'checks':[]}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
    return 0 if report['status']=='pass' else 2 if report['status']=='blocked' else 1
if __name__=='__main__':raise SystemExit(main())
