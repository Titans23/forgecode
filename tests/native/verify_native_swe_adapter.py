"""Actual Windows task/environment/boundary verification with a scripted model."""

import asyncio
import ast
import json
from pathlib import Path
import sys
import subprocess

from evals.native_swe import NativeTaskAdapter, run_native_case
from forge.runtime.state import ModelToolCallCompleted, ModelUsageUpdate, TokenUsage, ToolCall


ROOT = Path(__file__).resolve().parents[2]


def test_real_flask_task_uses_base_only_agent_and_independent_native_verifier(tmp_path):
    import pyarrow.parquet as parquet

    assert sys.platform == 'win32', 'This frozen native environment was prepared for Windows only'
    cache = ROOT / '.local/implementation/real-repo-preflight'
    manifest = json.loads((ROOT / 'experiments/native-swe-lite.json').read_text())
    spec = next(case for case in manifest['cases'] if case['id'] == 'pallets__flask-4992')
    row = next(row for row in parquet.read_table(cache / 'test.parquet').to_pylist()
               if row['instance_id'] == spec['id'])
    plan = json.loads((ROOT / 'experiments/native-swe-lite-plan.json').read_text())
    adapter = NativeTaskAdapter(manifest, spec, row, cache, tmp_path / 'results')
    case = adapter.case(1, plan['per_run'])
    fixed = (cache / 'cases/pallets__flask-4992/base/src/flask/config.py').read_text(encoding='utf-8')
    original = subprocess.check_output(['git', 'show', case.base_commit + ':src/flask/config.py'],
        cwd=case.base_commit_repository).decode('utf-8')

    def method(text):
        config = next(node for node in ast.parse(text).body if isinstance(node, ast.ClassDef) and node.name == 'Config')
        node = next(node for node in config.body if isinstance(node, ast.FunctionDef) and node.name == 'from_file')
        return ''.join(text.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])

    class ReferenceClient:
        provider = 'anthropic'
        model = 'scripted-reference-not-cloud'
        closed = False

        def __init__(self):
            self.calls = [
                ToolCall(0, 'status', 'git_status', {}),
                ToolCall(0, 'edit', 'replace_text', {'path': 'src/flask/config.py',
                    'old_text': method(original), 'new_text': method(fixed)}),
                ToolCall(0, 'verify', 'verify', {'command': case.test_command}),
                ToolCall(0, 'finish', 'finish_task', {'task_kind': 'change', 'status': 'completed',
                    'summary': 'Implemented and verified.', 'blocked_reasons': []}),
            ]

        async def aclose(self):
            self.closed = True

        async def stream(self, messages, tools=None, system=None):
            if len(self.calls) == 4:
                prompt = json.dumps(messages) + (system or '')
                assert spec['id'] not in prompt
                assert str(cache) not in prompt
                assert row['test_patch'] not in prompt and row['patch'] not in prompt
                source = adapter.before.root
                assert 'test_config_from_file_toml' not in (source / 'tests/test_config.py').read_text()
                assert 'text: bool = True' not in (source / 'src/flask/config.py').read_text()
            yield ModelUsageUpdate(TokenUsage(10, 0))
            yield ModelToolCallCompleted(self.calls.pop(0))
            yield ModelUsageUpdate(TokenUsage(10, 2), usage_is_final=True)

    client = ReferenceClient()
    outcome = asyncio.run(run_native_case(adapter, case, client=client))
    assert outcome.error is None, outcome.to_dict()
    assert client.closed
    assert outcome.passed, outcome.to_dict()
    assert outcome.cleanup['state'] == outcome.independent_grading['cleanup']['state'] == 'clean'
    assert outcome.independent_grading['assessment']['executed_tests'] == 19
    assert outcome.hidden_tests.execution_boundary == 'workspace-write-file-worker'
    assert outcome.request_evidence['observed_requests'] == 4
