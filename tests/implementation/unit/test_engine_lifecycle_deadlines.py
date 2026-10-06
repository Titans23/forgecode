from datetime import datetime, timedelta, timezone

import pytest

from forge.engine.lifecycle import Deadline, PhaseLifecycle


def test_wall_clock_rollback_and_sleep_never_add_time():
    utc = datetime(2026, 10, 6, tzinfo=timezone.utc)
    deadline = Deadline.start(30, utc=utc, clock=100)
    assert deadline.remaining(utc=utc - timedelta(hours=1), clock=110) == 20
    assert deadline.remaining(utc=utc + timedelta(seconds=40), clock=101) == 0
    assert deadline.remaining(utc=utc - timedelta(seconds=2), clock=99) == 30
    assert deadline.utc_text == '2026-10-06T00:00:30Z'


@pytest.mark.parametrize('seconds', [True, 0, -1, float('inf'), float('nan')])
def test_invalid_deadline_is_not_an_unlimited_budget(seconds):
    with pytest.raises(ValueError):
        Deadline.start(seconds)


def test_invalid_environment_budget_is_refused():
    with pytest.raises(ValueError):
        PhaseLifecycle(agent_seconds=20, environment_seconds=10)
