from dataclasses import replace

import pytest

from forge.tasks.manager import TaskManager
from forge.tasks.state import ActiveTask


def test_user_correction_can_replace_interpretation_without_reusing_evidence_identity(tmp_path):
    manager = TaskManager(tmp_path)
    manager.start('Produce 42.')
    old = manager.register_acceptance([{'source_quote': 'Produce 42.', 'condition': 'value=42', 'check': 'assert'}])
    original = old.acceptance_criteria[0]
    manager.continue_active('Correction: produce 43.')
    updated = manager.revise_acceptance(original['id'], {
        'source_quote': 'produce 43.', 'condition': 'value=43', 'check': 'assert'}, 'User corrected the value')
    assert updated.goal == 'Produce 42.'  # Original source is not rewritten.
    assert updated.acceptance_criteria[0]['id'] != original['id']
    assert updated.acceptance_history[0]['id'] == original['id']
    assert updated.acceptance_history[0]['status'] == 'superseded'
    restored = ActiveTask.from_dict(updated.as_dict())
    assert restored.acceptance_history == updated.acceptance_history
    assert restored.acceptance_criteria == updated.acceptance_criteria


def test_model_cannot_revise_caller_requirement_or_invent_source(tmp_path):
    manager = TaskManager(tmp_path)
    manager.start('Produce 42.')
    task = manager.register_acceptance([{'source_quote': 'Produce 42.', 'condition': '42', 'check': 'assert'}])
    item = task.acceptance_criteria[0]
    with pytest.raises(ValueError, match='quote'):
        manager.revise_acceptance(item['id'], {'source_quote': 'Invented instruction'}, 'Change')
    manager.restore(replace(task, acceptance_criteria=({**item, 'origin': 'caller'},)))
    with pytest.raises(ValueError, match='Caller'):
        manager.revise_acceptance(item['id'], None, 'Drop the test')
    assert manager.active.acceptance_criteria


def test_retracting_model_hypothesis_retains_audit_record(tmp_path):
    manager = TaskManager(tmp_path)
    manager.start('Produce a result.')
    task = manager.register_acceptance([{'source_quote': 'Produce a result.', 'condition': 'guess', 'check': 'guess'}])
    updated = manager.revise_acceptance(task.acceptance_criteria[0]['id'], None, 'Unsupported interpretation')
    assert not updated.acceptance_criteria
    assert updated.acceptance_history[0]['status'] == 'retracted'
