"""Synthetic real process and independent grader; never a public benchmark score."""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import TrialConfig
from harbor.models.trial.result import AgentInfo, ExceptionInfo, TrialResult
from harbor.models.verifier.result import VerifierResult


root,mode,checksum=Path(sys.argv[1]),sys.argv[2],sys.argv[3]
trial=root/'fixture-trial'
(trial/'agent').mkdir(parents=True)
(trial/'artifacts').mkdir()
answer=trial/'artifacts/answer.txt'
answer.write_text('41' if mode=='fail' else '42')
(trial/'agent/forgecode-result.json').write_text(json.dumps({'status':'completed'}))
reward=None
exception=None
if mode not in ('missing','grader-error'):
    verifier=subprocess.run([sys.executable,'-c','import pathlib,sys;assert pathlib.Path(sys.argv[1]).read_text()=="42"',str(answer)],capture_output=True)
    reward=1 if verifier.returncode==0 else 0
elif mode=='grader-error':
    verifier=subprocess.run([sys.executable,'-c','raise RuntimeError("synthetic independent grader failure")'],capture_output=True)
    if verifier.returncode:
        exception=ExceptionInfo(exception_type='VerifierRuntimeError',exception_message='Synthetic grader failed',
            exception_traceback=verifier.stderr.decode(),occurred_at=datetime.now())
result=TrialResult(task_name='synthetic-process-fixture',trial_name='fixture-trial',trial_uri=trial.as_uri(),
    task_id=LocalTaskId(path=root),task_checksum=checksum,
    config=TrialConfig(task={'path':str(root)},trial_name='fixture-trial',trials_dir=root),
    agent_info=AgentInfo(name='synthetic fixture process',version='1'),
    verifier_result=VerifierResult(rewards={'reward':reward}) if reward is not None else None,
    exception_info=exception,source='explicit synthetic process fixture')
(trial/'result.json').write_text(result.model_dump_json())
print('Actual synthetic runner and independent verifier finished')
raise SystemExit(17 if mode=='nonzero' else 0)
