"""Prompt orchestration only. No UAC or native sandbox installation occurs."""
from pathlib import Path
import subprocess


def test_first_use_prompt_and_retry_state():
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(['node', '--test', 'tests/implementation/node/first-use-setup.test.mjs'],
                            cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
