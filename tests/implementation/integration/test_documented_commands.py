"""Execute documented source entry points and the real offline Harness demonstration."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('arguments',[
    ['-m','forge','--help'],['-m','forge','--version'],['-m','forge.engine','--help'],
    ['scripts/package_engine.py','--help'],['scripts/assemble_release.py','--help'],
    ['scripts/make_installer.py','--help'],['scripts/materialize_release.py','--help'],['scripts/materialize_experiment.py','--help'],['scripts/release_review.py','--help'],['scripts/delivery_experiment.py','--help'],
    ['-m','benchmark.core.results','--help']])
def test_documented_source_entry_points_are_real(arguments):
    result=subprocess.run([sys.executable,*arguments],capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()


def test_documented_demo_executes_actual_tools_and_durable_evidence(tmp_path):
    target=tmp_path/'new-demo'
    result=subprocess.run([sys.executable,'-m','forge.testing.demo','--output-dir',str(target)],
        capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode==0,result.stderr
    report=json.loads((target/'demo-report.json').read_text(encoding='utf-8'))
    assert report['status']=='pass' and all(report['checks'].values())
    assert report['origin']=='scripted' and report['mode']=='local-trusted'
    assert report['eligible_for_benchmark'] is False and report['sandbox_acceptance']=='blocked'
    assert report['durable_event_count']>0 and len(report['artifacts'])>=4
    # A repeat must refuse the existing directory rather than erase its previous evidence.
    before=(target/'demo-report.json').read_bytes()
    again=subprocess.run([sys.executable,'-m','forge.testing.demo','--output-dir',str(target)],
        capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert again.returncode!=0 and (target/'demo-report.json').read_bytes()==before

def test_task_materialization_refuses_existing_or_external_storage_before_download(tmp_path):
    existing=tmp_path/'user-tasks';existing.mkdir();sentinel=existing/'user.txt';sentinel.write_bytes(b'owned user data')
    result=subprocess.run([sys.executable,'scripts/materialize_experiment.py','--output-dir',str(existing)],
        capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert result.returncode==1
    report=json.loads(result.stdout)
    assert report['public_model_calls']==0 and report['grades']==0 and report['status']=='fail'
    assert sentinel.read_bytes()==b'owned user data' and list(existing.iterdir())==[sentinel]
