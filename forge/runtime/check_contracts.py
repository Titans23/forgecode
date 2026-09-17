'''Stable assertion contracts and lossless reuse of recorded check specifications.'''

from __future__ import annotations

from hashlib import sha256
import json

def resolve_stored_check(arguments, evidence):
    '''Resolve shorthand before validation/authorization; never weaken assertions.'''
    check_id = arguments.get('check_id')
    if not check_id:
        return arguments
    source = next((item for item in reversed(evidence) if item.check_id == check_id), None)
    if source is None or not source.check_spec:
        raise ValueError('Unknown stored check ID in this task')
    for key, value in arguments.items():
        if key not in {'check_id', 'timeout_seconds'} and source.check_spec.get(key) != value:
            raise ValueError('Stored check definitions are immutable; create a revised checker explicitly')
    from copy import deepcopy
    return {**deepcopy(source.check_spec), 'check_id': check_id,
            **({'timeout_seconds': arguments['timeout_seconds']} if 'timeout_seconds' in arguments else {})}

def checker_repair_guidance(item) -> str:
    if item.output_checks:
        return ('Use verify.inherit_checks_from with this verification ID and revision_reason; '
                'omit rewritten old output_checks. Original assertions and cwd must be preserved.')
    return ('This check has no recorded output assertions. Rerun its exact command, stdin and cwd '
            '(or its stored check_id); repair a referenced checker file if needed. '
            'inherit_checks_from supports only that unchanged invocation and optional added assertions. '
            'Changing an inline checker cannot automatically discharge this obligation: '
            'its original behavioral requirements remain unverified.')


def assertion_contract(check: dict) -> str:
    # Legacy records keep their exact source text. New records can separate a
    # stable source reference from explanatory prose without weakening binding.
    return json.dumps({
        **{key: check.get(key) for key in ('key', 'operator', 'expected', 'requirement_id')},
        'source': ('reference', check['source_ref']) if check.get('source_ref') else
                  ('legacy_text', check.get('expected_source')),
        **({'reference_key': check.get('reference_key'), 'tolerance': float(check.get('tolerance', 0))}
           if check.get('operator') == 'delta_eq' else {}),
    }, sort_keys=True)


def inherit_check_arguments(arguments: dict, evidence) -> dict:
    '''Copy exact recorded assertions; conflicting replacements fail before execution.'''
    from forge.tools.verify import VerifyInput

    parsed = VerifyInput.model_validate(arguments)
    if not parsed.inherit_checks_from:
        return arguments
    if not parsed.revision_reason.strip():
        raise ValueError('inherit_checks_from requires revision_reason explaining the checker repair.')
    records = {item.verification_id: item for item in evidence if item.verification_id}
    checks = [check.model_dump() for check in parsed.output_checks]
    supplied_checks = list(checks)
    requirements = set(parsed.requirement_ids)
    for identifier in parsed.inherit_checks_from:
        old = records.get(identifier)
        if old is None:
            raise ValueError(f'Unknown verification: {identifier}.')
        if not old.output_checks:
            # 缺少结构化断言时无法证明两段程序等价，只允许保留原调用重新验证。
            digest = sha256(parsed.stdin.encode()).hexdigest() if parsed.stdin is not None else ''
            if (parsed.command.strip() != old.command.strip() or digest != old.stdin_sha256
                    or parsed.cwd != old.cwd):
                raise ValueError(f'Cannot change an unstructured checker invocation: {identifier}. '
                                 + checker_repair_guidance(old))
        for check in old.output_checks:
            contract = assertion_contract(check)
            if any(c['key'] == check['key'] and assertion_contract(c) != contract for c in supplied_checks):
                raise ValueError(f'Conflicting assertion for {check["key"]!r} from {identifier}; '
                                 'omit the replacement and inherit the original contract unchanged.')
            if not any(assertion_contract(c) == contract for c in checks):
                checks.append(dict(check))
        requirements.update(old.requirement_ids)
    # 修检查器不能丢掉旧需求；继承断言与新增断言一起执行。
    expanded = {**arguments, 'output_checks': checks, 'requirement_ids': sorted(requirements),
                'supersedes': list(dict.fromkeys([*parsed.supersedes, *parsed.inherit_checks_from])),
                'inherit_checks_from': []}
    # Apply the same schema bounds to inherited and handwritten specifications.
    return VerifyInput.model_validate(expanded).model_dump()
