"""A live AI Port event cap must survive restarts and fail closed."""

import json
from concurrent.futures import ProcessPoolExecutor

import pytest

from aikey.aiport_event_budget import EventBudget, EventBudgetError, _HOUR_NS


FIRST = "2A1122334455"
SECOND = "2A1122334456"


def _claim_in_process(args):
    directory, now = args
    return EventBudget(directory, limit=3, clock_ns=lambda: now).claim(FIRST)


def test_per_camera_hour_survives_restart_and_rolls_forward(tmp_path):
    now = [10 * _HOUR_NS]
    budget = EventBudget(tmp_path, limit=2, clock_ns=lambda: now[0])
    assert budget.claim(FIRST)
    assert budget.claim(FIRST)
    assert not budget.claim(FIRST)
    assert budget.claim(SECOND)
    restarted = EventBudget(tmp_path, limit=2, clock_ns=lambda: now[0])
    assert restarted.remaining(FIRST) == 0
    assert restarted.remaining(SECOND) == 1
    assert not restarted.claim(FIRST)
    now[0] += _HOUR_NS - 1
    assert not restarted.claim(FIRST)
    now[0] += 1
    assert restarted.claim(FIRST)
    assert restarted.remaining(FIRST) == 1
    state = json.loads((tmp_path / "aiport-event-budget.json").read_text())
    assert state["events"][FIRST] == [now[0]]


def test_vision_request_cap_is_separate_from_native_event_cap(tmp_path):
    now = 10 * _HOUR_NS
    events = EventBudget(tmp_path, limit=1, clock_ns=lambda: now)
    requests = EventBudget(tmp_path, limit=2, clock_ns=lambda: now,
                           namespace="vision-request")
    assert requests.claim(FIRST) and requests.claim(FIRST)
    assert not requests.claim(FIRST)
    assert events.claim(FIRST)
    assert not events.claim(FIRST)


def test_corrupt_state_clock_rollback_and_failed_write_deny(tmp_path, monkeypatch):
    now = [5 * _HOUR_NS]
    budget = EventBudget(tmp_path, limit=1, clock_ns=lambda: now[0])
    assert budget.claim(FIRST)
    now[0] -= 1
    with pytest.raises(EventBudgetError, match="rollback"):
        budget.claim(SECOND)
    now[0] += 1
    import aikey.aiport_event_budget as module

    def broken_write(*args, **kwargs):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(module, "atomic_private", broken_write)
    with pytest.raises(EventBudgetError, match="uncertain"):
        budget.claim(SECOND)
    monkeypatch.undo()
    assert budget.remaining(SECOND) == 1
    (tmp_path / "aiport-event-budget.json").write_text("{}")
    with pytest.raises(EventBudgetError, match="corrupt"):
        budget.claim(SECOND)


def test_symlinked_state_file_and_directory_deny(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(EventBudgetError, match="unsafe"):
        EventBudget(linked, limit=1)
    budget = EventBudget(real, limit=1)
    (real / "aiport-event-budget.json").symlink_to(tmp_path / "missing")
    with pytest.raises(EventBudgetError, match="unavailable"):
        budget.claim(FIRST)


def test_parallel_processes_cannot_exceed_one_camera_limit(tmp_path):
    with ProcessPoolExecutor(max_workers=8) as pool:
        admitted = list(pool.map(_claim_in_process, [(tmp_path, 7 * _HOUR_NS)] * 16))
    assert admitted.count(True) == 3
    assert admitted.count(False) == 13
