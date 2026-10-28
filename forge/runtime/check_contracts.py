'''Stable assertion contracts and lossless reuse of recorded check specifications.'''

from __future__ import annotations

from hashlib import sha256
import json
import os

from forge.runtime.state import VerificationEvidence

def resolve_stored_check(arguments, evidence):
    '''Resolve shorthand before validation/authorization; never weaken assertions.'''
    check_id = arguments.get('check_id')
    if not check_id:
        return arguments
    from forge.tools.verify import VerifyInput
    VerifyInput.model_validate(arguments)
    source = next((item for item in reversed(evidence)
                   if check_id in (item.check_id, item.verification_id)), None)
    if source is None or not source.check_spec:
        raise ValueError('Unknown stored check ID in this task')
    from copy import deepcopy
    if arguments.get('revision_reason', '').strip():
        if arguments.get('cwd', source.cwd) != source.cwd:
            raise ValueError('Checker repair must preserve the original cwd.')
        # One reference carries the stored invocation, assertions and scope.
        # The existing inheritance validator still rejects weakened assertions.
        revised = {**deepcopy(source.check_spec), **arguments, 'check_id': None,
                   'cwd': source.cwd, 'inherit_checks_from': list(dict.fromkeys([
                       *arguments.get('inherit_checks_from', []), source.verification_id]))}
        return inherit_check_arguments(revised, evidence)
    for key, value in arguments.items():
        if key not in {'check_id', 'timeout_seconds'} and source.check_spec.get(key) != value:
            raise ValueError('Stored check definitions are immutable; create a revised checker explicitly')
    return {**deepcopy(source.check_spec), 'check_id': source.check_id,
            **({'timeout_seconds': arguments['timeout_seconds']} if 'timeout_seconds' in arguments else {})}

def checker_repair_guidance(item) -> str:
    if item.output_checks and item.check_spec:
        return (f'Use verify.check_id="{item.check_id or item.verification_id}" with the corrected command '
                'and revision_reason. Omit output_checks and cwd: the original assertions, requirements '
                'and working directory are inherited automatically. Changing thresholds is not a checker repair.')
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


def verification_command_key(command: str, cwd: str) -> str:
    '''Return a stable identity for retries of one verification obligation.'''
    # Command values are case/whitespace sensitive; do not merge distinct checks.
    normalized = command.strip()
    normalized_cwd = os.path.normcase(cwd.strip()).replace('\\', '/') or '.'
    return f'{normalized_cwd}\0{normalized}'


def verification_obligation_key(evidence: VerificationEvidence) -> str:
    '''Coverage labels do not make different checks interchangeable.'''
    return '\0'.join((verification_command_key(evidence.command, evidence.cwd),
                      evidence.stdin_sha256, evidence.check_signature))


def unresolved_verification_failures(
    evidence: tuple[VerificationEvidence, ...],
) -> tuple[VerificationEvidence, ...]:
    '''Keep failures until the same command succeeds on the same revision.'''
    unresolved: dict[str, VerificationEvidence] = {}
    # 成功只能消除同一检查或显式保留契约的修订，不能覆盖无关的历史失败。
    for item in evidence:
        key = verification_obligation_key(item)
        if item.success:
            unresolved.pop(key, None)
            for old_key, old in tuple(unresolved.items()):
                if checker_revision_covers(old, item):
                    unresolved.pop(old_key)
        else:
            unresolved[key] = item
    return tuple(unresolved.values())


def checker_revision_covers(old: VerificationEvidence, new: VerificationEvidence) -> bool:
    '''Allow explicit checker repair, never implicit replacement by a weaker check.

    Execution semantics remain a declared limitation: this verifies preservation
    of observable assertion contracts, not equivalence of arbitrary programs.
    '''
    if not (new.success and old.verification_id and old.verification_id in new.supersedes
            and new.revision_reason.strip() and old.cwd == new.cwd):
        return False
    if not old.output_checks:
        # Without serialized assertions we cannot establish equivalence of a
        # rewritten program. Only retrying the original invocation is supported.
        return (verification_command_key(old.command, old.cwd)
                == verification_command_key(new.command, new.cwd)
                and old.stdin_sha256 == new.stdin_sha256
                and set(old.requirement_ids).issubset(new.requirement_ids))
    if not set(old.requirement_ids).issubset(new.asserted_requirement_ids):
        return False
    def contracts(checks):
        return {assertion_contract(c) for c in checks}
    return contracts(old.output_checks).issubset(contracts(new.output_checks))
