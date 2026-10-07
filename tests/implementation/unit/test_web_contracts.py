"""Browser and Desktop compile the same schemas, including all negative DTO fixtures."""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[3]


def test_browser_standalone_contracts_match_desktop_without_dynamic_code_execution():
    result=subprocess.run(['node',str(ROOT/'tests/implementation/node/browser-contracts-case.mjs')],
        cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads(result.stdout)
    assert report['cases']>400 and report['equivalent'] and report['dynamic_evaluations']==0
