"""Prevent runtime and service implementation dependencies from pointing upward."""
import pytest

from scripts.check_architecture import violations


@pytest.mark.parametrize(('path', 'source', 'forbidden'), [
    ('runtime/runner.py', 'from forge.runtime.agent_loop import helper', True),
    ('runtime/runner.py', 'def run():\n    import forge.runtime.agent_loop', True),
    ('runtime/runner.py', 'from typing import TYPE_CHECKING\n'
     'if TYPE_CHECKING:\n    from forge.runtime.agent_loop import Conversation', False),
    ('runtime/runner.py', 'from typing import TYPE_CHECKING\n'
     'if TYPE_CHECKING:\n    pass\nelse:\n    import forge.runtime.agent_loop', True),
    ('runtime/runner.py', 'from forge.runtime.state import build_assistant_message\n'
     'from forge.runtime.verification import verification_from_result', False),
    ('application/evaluations.py', 'from forge.application.evaluation_client import configuration_issues', True),
    ('application/evaluation_client.py', 'from forge.application.evaluations import EvaluationService', False),
    ('application/sessions.py', 'from forge.engine.event_stream import accepted_view', True),
    ('engine/event_stream.py', 'from forge.application.sessions import accepted_view', False),
    ('messages.py', 'from forge.runtime.state import ToolCall', True),
    ('sandbox/backend.py', 'from forge.runtime.agent_loop import Conversation', True),
])
def test_dependency_direction(tmp_path, path, source, forbidden):
    target = tmp_path / 'forge' / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source + '\n', encoding='utf-8')
    errors = violations(tmp_path)
    assert bool(errors) is forbidden
    if forbidden:
        assert len(errors) == 1
        assert 'forbidden dependency' in errors[0]


def test_repository_dependencies_respect_boundaries():
    assert violations() == []
