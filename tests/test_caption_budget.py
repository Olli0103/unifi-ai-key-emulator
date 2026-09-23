"""Synthetic, offline checks for the installation-wide paid caption gate."""

from concurrent.futures import ProcessPoolExecutor
import hashlib
import json

import pytest

from aikey.caption_budget import (
    CaptionBudget,
    CaptionBudgetError,
    CaptionBudgetExhausted,
    HOUR_NS,
    LIMIT,
)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def reserve_in_process(args):
    directory, index, now = args
    return (
        CaptionBudget(directory, clock_ns=lambda: now)
        .reserve(digest(f"job-{index}"), digest(f"input-{index}"), f"fixture-camera-{index % 3}")
        .new
    )


def test_twelve_across_cameras_and_rolling_reopen_after_restart(tmp_path):
    now = [10 * HOUR_NS]
    budget = CaptionBudget(tmp_path, clock_ns=lambda: now[0])
    for index in range(LIMIT):
        receipt = budget.reserve(
            digest(f"job-{index}"), digest(f"input-{index}"), f"fixture-camera-{index % 3}"
        )
        assert receipt.new and receipt.remaining == LIMIT - index - 1
    with pytest.raises(CaptionBudgetExhausted) as denied:
        budget.reserve(digest("extra"), digest("extra-input"), "fixture-camera-2")
    assert denied.value.next_at_ns == now[0] + HOUR_NS
    assert len(json.loads((tmp_path / "caption-budget.json").read_text())["reservations"]) == LIMIT

    # A restart neither refunds an attempt nor charges the same work again.
    restarted = CaptionBudget(tmp_path, clock_ns=lambda: now[0])
    assert not restarted.reserve(digest("job-0"), digest("input-0"), "fixture-camera-0").new
    with pytest.raises(CaptionBudgetError, match="different input"):
        restarted.reserve(digest("job-0"), digest("changed"), "fixture-camera-0")
    now[0] += HOUR_NS - 1
    with pytest.raises(CaptionBudgetExhausted):
        restarted.reserve(digest("extra"), digest("extra-input"), "fixture-camera-2")
    now[0] += 1
    assert restarted.reserve(digest("extra"), digest("extra-input"), "fixture-camera-2").new


def test_concurrent_processes_share_one_global_limit(tmp_path):
    now = 7 * HOUR_NS
    attempts = [(tmp_path, index, now) for index in range(32)]
    # The target must be top-level for spawn-based multiprocessing.
    with ProcessPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(_process_attempt, attempts))
    assert outcomes.count(True) == LIMIT
    assert outcomes.count(False) == len(attempts) - LIMIT
    assert len(json.loads((tmp_path / "caption-budget.json").read_text())["reservations"]) == LIMIT


def _process_attempt(args):
    try:
        return reserve_in_process(args)
    except CaptionBudgetExhausted:
        return False


def test_rollback_corruption_and_write_failure_never_grant_new_work(tmp_path, monkeypatch):
    now = [5 * HOUR_NS]
    budget = CaptionBudget(tmp_path, clock_ns=lambda: now[0])
    budget.reserve(digest("first"), digest("first-input"), "fixture-camera")
    now[0] -= 1
    with pytest.raises(CaptionBudgetError, match="backwards"):
        budget.reserve(digest("second"), digest("second-input"), "fixture-camera")
    now[0] += 1
    import aikey.caption_budget as module

    def broken_write(*args, **kwargs):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(module, "atomic_private", broken_write)
    with pytest.raises(CaptionBudgetError, match="uncertain"):
        budget.reserve(digest("second"), digest("second-input"), "fixture-camera")
    monkeypatch.undo()
    assert json.loads((tmp_path / "caption-budget.json").read_text())["reservations"][-1][
        "job_id"
    ] == digest("first")
    (tmp_path / "caption-budget.json").write_text("{}")
    with pytest.raises(CaptionBudgetError, match="corrupt"):
        budget.reserve(digest("second"), digest("second-input"), "fixture-camera")


def test_symlinked_state_or_budget_fails_closed(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(CaptionBudgetError, match="unsafe"):
        CaptionBudget(linked)
    budget = CaptionBudget(real)
    (real / "caption-budget.json").symlink_to(tmp_path / "missing")
    with pytest.raises(CaptionBudgetError, match="Cannot read"):
        budget.reserve(digest("job"), digest("input"), "fixture-camera")
