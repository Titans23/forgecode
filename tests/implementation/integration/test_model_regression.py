"""Actual independent grader processes and an opt-in boundary; no live calls in CI."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

from scripts.model_regression import frozen_task,grade

def test_independent_grader_rejects_stub_and_accepts_delivered_behavior(tmp_path):
    tests={'answer_test.py':b'import unittest\nfrom answer import answer\nclass Tests(unittest.TestCase):\n def test_negative(self): self.assertEqual(answer(-4), -3)\n def test_zero(self): self.assertEqual(answer(0), 1)\n'}
    async def scenario():
        before=await grade(tmp_path/'before',{'answer.py':b'def answer(value): return value\n'},tests)
        after=await grade(tmp_path/'after',{'answer.py':b'def answer(value): return value+1\n'},tests)
        return before,after
    before,after=asyncio.run(scenario())
    assert before['state']==after['state']=='graded' and before['result']=='fail' and after['result']=='pass'
    assert before['observation']['collected']==after['observation']['collected']==2
    assert before['test_source_sha256']==after['test_source_sha256']
    assert before['delivered_source_sha256']!=after['delivered_source_sha256']

def test_skipped_or_empty_independent_test_collection_never_passes(tmp_path):
    async def scenario():
        skip=await grade(tmp_path/'skipped',{'answer.py':b'value=1\n'},
            {'answer_test.py':b'import unittest\nclass Tests(unittest.TestCase):\n @unittest.skip("fixture")\n def test_skipped(self): pass\n'})
        empty=await grade(tmp_path/'empty',{'answer.py':b'value=1\n'}, {})
        return skip,empty
    skip,empty=asyncio.run(scenario())
    assert skip['result']=='fail' and skip['observation']['skipped']==1
    assert empty['state']=='grader_error' and empty['result'] is None

def test_live_cli_requires_explicit_opt_in_before_reading_task_or_model(tmp_path):
    output=tmp_path/'unused'
    process=subprocess.run([sys.executable,'scripts/model_regression.py','--task-root',str(tmp_path/'missing'),
        '--output-dir',str(output)],capture_output=True,text=True,encoding='utf-8',timeout=30)
    report=json.loads(process.stdout)
    assert process.returncode==2 and report['status']=='blocked'
    assert report['actual_model_calls']==report['grades']==0 and not output.exists()

def test_oracle_and_independent_test_bytes_are_never_in_public_agent_input(tmp_path):
    workspace=tmp_path/'environment/workspace';workspace.mkdir(parents=True)
    (workspace/'answer.py').write_text('pass')
    oracle=workspace/'.oracle';oracle.mkdir();(oracle/'secret.py').write_text('ORACLE_SENTINEL')
    tests=tmp_path/'tests';tests.mkdir();(tests/'answer_test.py').write_text('HIDDEN_SENTINEL')
    (tmp_path/'instruction.md').write_text('Implement the public interface.')
    public,hidden,prompt=frozen_task(tmp_path)
    assert public=={'answer.py':b'pass'} and hidden=={'answer_test.py':b'HIDDEN_SENTINEL'}
    assert 'ORACLE_SENTINEL' not in prompt and 'HIDDEN_SENTINEL' not in prompt
