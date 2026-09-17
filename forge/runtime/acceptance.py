'''Evidence-backed acceptance diagnostics, without guessing semantic correctness.'''

from forge.runtime.verification import verification_quality


def requirement_observation(criterion, evidence, revision, epoch):
    identifier = criterion.get('id', '')
    linked = [item for item in evidence if identifier and (
        identifier in item.requirement_ids or identifier in item.asserted_requirement_ids)]
    current = [item for item in linked if item.freshness == 'current'
               and item.workspace_revision == revision and item.environment_epoch == epoch]
    asserted = [item for item in current if identifier in item.asserted_requirement_ids
                and item.check_signature]
    if any(item.success and verification_quality(item.command) == 'behavior' for item in asserted):
        state = 'recorded_assertion_passed'
        action = 'Check that the assertion measures the requested behavior against an independent source; execution success is not semantic proof.'
    elif any(item.success for item in asserted):
        state = 'execution_contract_unestablished'
        action = ('Assertions exist, but command exit semantics or check kind do not establish positive behavior. '
                  'Run the substantive checker directly (for example python - with stdin), preserving its assertions.')
    elif asserted:
        state = 'check_failed'
        action = ('Inspect the recorded failure and distinguish a broken checker or environment from a wrong deliverable. '
                  'Repair the cause and rerun check_id. For a checker correction, use inherit_checks_from and revision_reason without rewriting old assertions.')
    elif linked and not current:
        state = 'stale_evidence'
        action = 'Rerun the stored check_id on the current workspace; old results are not current proof.'
    elif current:
        state = 'assertion_binding_missing'
        action = 'A check exists, but no executed output assertion is bound to this requirement with expected_source. Preserve actual measurements and bind the correct requirement ID.'
    else:
        state = 'unverified'
        action = 'Run an independent check of the quoted requirement; use actual values, exact source identity or a behavioral comparison rather than output shape alone.'
    return {
        'requirement_id': identifier, 'source_quote': criterion.get('source_quote', ''),
        'condition': criterion.get('condition', ''), 'intended_check': criterion.get('check', ''),
        'state': state, 'next_action': action,
        'checks': [{
            'verification_id': item.verification_id, 'check_id': item.check_id,
            'command_excerpt': item.command[:800], 'cwd': item.cwd,
            'current': item in current, 'success': item.success,
            'quality': verification_quality(item.command),
            'assertions': [{key: value[:500] if isinstance(value, str) else value
                            for key, value in check.items()}
                           for check in item.output_checks if check.get('requirement_id') == identifier][:8],
            'assertions_omitted': max(0, sum(check.get('requirement_id') == identifier for check in item.output_checks) - 8),
            'limitations': [value[:500] for value in item.limitations[:5]],
            'issues': [value[:500] for value in item.evidence_issues[:5]],
            'diagnostic': item.diagnostic[-1000:],
        } for item in linked[-3:]],
    }


def requirement_reason(observation):
    if observation['state'] == 'recorded_assertion_passed':
        return None
    return (f"Acceptance requirement {observation['requirement_id'] or 'legacy/unidentified'}: "
            f"{observation['state']}. {observation['source_quote']}. "
            + observation['next_action'])
