"""Recompute final release conditions from repository-owned actual evidence."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.evidence_gate import evaluate_gate

def review():
    gate=evaluate_gate(ROOT,'release')
    reasons=gate['errors']+['Incomplete task: '+task for task in gate['incomplete_tasks']]+gate['blocked_dependencies']
    return {'status':gate['status'],'reason':'; '.join(reasons) if reasons else None,
        'scope':'actual full-source evidence, mandatory implementation gaps, native/security/signing/license conditions',
        'eligible_for_native_pass':False,'public_model_calls':0,'gate':gate,
        'checks':[{'id':'recomputed-release-conditions','status':gate['status']}]}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:report=review()
    except (OSError,ValueError,KeyError,TypeError) as error:report={'status':'fail','reason':str(error),'checks':[]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return {'pass':0,'blocked':2,'fail':1}[report['status']]
if __name__=='__main__':raise SystemExit(main())
