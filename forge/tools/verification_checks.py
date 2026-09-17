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

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer


def finite_number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


class OutputCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=1, description='Top-level key in the final JSON object printed to stdout.')
    operator: Literal['eq', 'ge', 'le', 'delta_eq'] = 'eq'
    expected: bool | int | float | str
    reference_key: str = Field(default='', description='For delta_eq, another measured JSON key. Assert key minus reference_key equals expected within tolerance; use to test input transformations.')
    tolerance: float = Field(default=0.0, ge=0, allow_inf_nan=False,
                             description='Absolute tolerance for delta_eq, derived from the requirement or independent reference.')
    requirement: str = Field(min_length=1, description='User requirement this assertion checks; not an authority grant.')
    requirement_id: str = Field(default='', description='Stable req ID from the current acceptance criteria, if this is an acceptance check.')
    expected_source: str = Field(default='', description='Original task quote or independent reference artifact establishing the expectation; never derive it only from implementation choices.')
    source_ref: str = Field(default='', description='Stable identity of the expectation source, such as a requirement ID or reference artifact with hash. Keep unchanged across checker revisions; expected_source explains this reference.')

    @model_validator(mode='after')
    def validate_relation(self):
        if self.operator == 'delta_eq':
            if not self.reference_key or self.reference_key == self.key:
                raise ValueError('delta_eq requires a distinct measured reference_key')
            if not finite_number(self.expected):
                raise ValueError('delta_eq requires a finite numeric expected difference')
        elif self.reference_key or self.tolerance:
            raise ValueError('reference_key and tolerance are only supported by delta_eq')
        return self

    @model_serializer(mode='wrap')
    def stable_legacy_record(self, handler):
        record = handler(self)
        if self.operator != 'delta_eq':
            # New optional fields must not change old check identities on replay.
            record.pop('reference_key', None)
            record.pop('tolerance', None)
        return record


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
        if check.operator == 'delta_eq':
            reference = actual.get(check.reference_key)
            if not all(finite_number(item) for item in (value, reference)):
                failures.append(f'{check.key} and {check.reference_key}: delta_eq requires two finite numeric observations')
                continue
            try:
                difference = value - reference
                passed = finite_number(difference) and abs(difference - expected) <= check.tolerance
            except OverflowError:
                failures.append(f'{check.key} - {check.reference_key}: numeric range exceeded')
                continue
            if not passed:
                failures.append(f'{check.key} - {check.reference_key}: observed {difference!r}, required {expected!r} +/- {check.tolerance}; {check.requirement}')
            continue
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
