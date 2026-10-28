import asyncio
import shlex
import subprocess
import sys

import pytest

from benchmark.harbor.run_forge import BENCHMARK_TASK_POLICY
from forge.runtime.completion import CompletionGate
from forge.runtime.verification import verification_from_result
from forge.runtime.workspace import WorkspaceTracker
from forge.tools.verify import VerifyTool


@pytest.mark.parametrize('correct', [False, True])
def test_public_task_accepts_executed_behavior_without_model_authored_ids(tmp_path, correct):
    (tmp_path / 'solution.py').write_text(
        'def add(a, b): return a ' + ('+' if correct else '-') + ' b\n')
    join = subprocess.list2cmdline if sys.platform == 'win32' else shlex.join
    command = join([sys.executable, '-'])

    async def run():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        result = await VerifyTool(tmp_path, tracker).run({
            'command': command, 'stdin': 'from solution import add\nassert add(2, 3) == 5\n',
            'covers': ['Integer addition for the supplied operands'],
        })
        evidence = verification_from_result(result)
        assert evidence is not None
        decision = await CompletionGate(tmp_path, BENCHMARK_TASK_POLICY).evaluate(
            tracker, evidence, verification_history=(evidence,), mutation_attempted=False)
        assert decision.allowed == correct
        assert evidence.requirement_ids == ()
        assert result.success == correct
    asyncio.run(run())
