'''Conservative execution diagnostics and explicit output assertions.

Assertions are caller/model-provided expectations, not independently discovered
task requirements or permission grants. Actual values come from process stdout.
'''

import ast
import json
import math
from pathlib import Path
import re
import shlex
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OutputCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=1, description='Top-level key in the final JSON object printed to stdout.')
    operator: Literal['eq', 'ge', 'le'] = 'eq'
    expected: bool | int | float | str
    requirement: str = Field(min_length=1, description='User requirement this assertion checks; not an authority grant.')


def evaluate_output_checks(stdout: str, checks: list[OutputCheck]) -> list[str]:
    if not checks:
        return []
    try:
        actual = json.loads(stdout.strip().splitlines()[-1])
        if not isinstance(actual, dict):
            raise ValueError('not an object')
    except (ValueError, IndexError):
        return ['Output assertions require a JSON object on the final stdout line.']
    failures = []
    for check in checks:
        if check.key not in actual:
            failures.append(f'Missing output key {check.key!r}: {check.requirement}')
            continue
        value = actual[check.key]
        expected = check.expected
        numeric = all(type(item) is int or (type(item) is float and math.isfinite(item))
                      for item in (value, expected))
        if check.operator == 'eq':
            passed = (numeric or type(value) is type(expected)) and value == expected
            if isinstance(value, float) and not math.isfinite(value):
                passed = False
        else:
            passed = numeric and (value >= expected if check.operator == 'ge' else value <= expected)
        if not passed:
            failures.append(f'{check.key}: observed {value!r}, required {check.operator} {expected!r}; {check.requirement}')
    return failures


def dormant_python_tests(command: str, cwd: Path, root: Path) -> str | None:
    '''Recognize a plain script launch that defines tests without invoking them.

No arbitrary AST execution, shell rewriting, automatic pytest invocation, or
file access outside the authorized workspace. Unknown wrappers are left alone.
'''
    try:
        words = shlex.split(command)
        if len(words) != 2 or not re.fullmatch(r'python(?:\d+(?:\.\d+)?)?(?:\.exe)?', Path(words[0]).name, re.I):
            return None
        path = (cwd / words[1]).resolve()
        if path.suffix != '.py' or not path.is_relative_to(root.resolve()) or path.stat().st_size > 200_000:
            return None
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, SyntaxError, UnicodeError):
        return None
    tests = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name.startswith('test_')]
    if not tests or any(node.decorator_list for node in tests):
        return None
    # Conservatively allow only imports, definitions and the common sys.path
    # setup statement; a main guard, assertion or other executable statement
    # makes this uncertain, so do not classify it as dormant.
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value.func
            if ast.unparse(call) in {'sys.path.insert', 'sys.path.append'}:
                continue
        return None
    return (f'This Python script defines {len(tests)} test function(s), but has no visible test invocation. '
            'Exit 0 does not establish that those tests ran. Use the appropriate test runner '
            '(for example python -m pytest <path>) or an explicit invocation and inspect actual test results.')
