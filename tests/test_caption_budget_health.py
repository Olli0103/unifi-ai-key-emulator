"""Real journal snapshots and bounded health; no provider/controller requests."""

import asyncio
from copy import deepcopy
import json
import os
import threading
from types import SimpleNamespace

import pytest

from aikey.admin_runtime import _normalize
from aikey.caption_budget import CaptionBudget, CaptionBudgetError, HOUR_NS, LIMIT
from aikey.caption_budget_health import CaptionBudgetHealth
from aikey.runtime import Application
from aikey.worker import JobProcessor
from test_caption_budget import digest
from test_continuous_admission import continuous_options, MutableRegistry
from test_basic_descriptions import options

pytest_plugins = ["test_worker"]


def test_snapshot_missing_journal_never_creates_a_lock_or_file(tmp_path):
    budget = CaptionBudget(tmp_path, clock_ns=lambda: 10 * HOUR_NS)
    before = list(tmp_path.iterdir())
    assert budget.snapshot() == {"observation": "available", "sampled_at_ns": 10 * HOUR_NS,
        "window_seconds": 3600, "limit": LIMIT, "used_reservations": 0, "remaining_reservations": LIMIT}
    assert list(tmp_path.iterdir()) == before


def test_restart_rolling_expiry_and_snapshot_do_not_reserve_or_refund(tmp_path):
    now = [10 * HOUR_NS]
    budget = CaptionBudget(tmp_path, clock_ns=lambda: now[0])
    for i in range(LIMIT):
        budget.reserve(digest(str(i)), digest("input" + str(i)), "PRIVATE-camera")
    paths = {p: p.read_bytes() for p in tmp_path.iterdir()}
    restarted = CaptionBudget(tmp_path, clock_ns=lambda: now[0])
    assert restarted.snapshot()["remaining_reservations"] == 0
    now[0] += HOUR_NS - 1
    assert restarted.snapshot()["used_reservations"] == LIMIT
    now[0] += 1
    view = restarted.snapshot()
    assert view["used_reservations"] == 0 and view["remaining_reservations"] == LIMIT
    assert "PRIVATE" not in json.dumps(view) and digest("0") not in json.dumps(view)
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == paths


@pytest.mark.parametrize("invalid", ["clock", "rollback", "symlink", "fifo", "directory", "mode",
    "duplicate", "schema_bool", "overflow_count", "bad_json", "directory_link", "directory_missing"])
def test_unavailable_snapshot_is_not_zero_or_repaired(tmp_path, invalid):
    now = [10 * HOUR_NS]
    root = tmp_path / "private"
    budget = CaptionBudget(root, clock_ns=lambda: now[0])
    budget.reserve(digest("job"), digest("input"), "PRIVATE-camera")
    if invalid == "clock":
        now[0] = True
    elif invalid == "rollback":
        now[0] -= 1
    elif invalid in {"symlink", "fifo", "directory"}:
        budget.path.unlink()
        if invalid == "symlink":
            budget.path.symlink_to(tmp_path / "absent")
        elif invalid == "fifo":
            os.mkfifo(budget.path, 0o600)
        else:
            budget.path.mkdir(mode=0o700)
    elif invalid == "mode":
        budget.path.chmod(0o644)
    elif invalid == "directory_link":
        moved = tmp_path / "moved"
        root.rename(moved)
        root.symlink_to(moved, target_is_directory=True)
    elif invalid == "directory_missing":
        root.rename(tmp_path / "moved")
    else:
        state = json.loads(budget.path.read_bytes())
        if invalid == "duplicate":
            budget.path.write_text('{"schema":1,"schema":1,"high_water_ns":0,"reservations":[]}')
        elif invalid == "schema_bool":
            state["schema"] = True
            budget.path.write_text(json.dumps(state))
        elif invalid == "overflow_count":
            state["reservations"] = [dict(state["reservations"][0], job_id=digest(str(i))) for i in range(13)]
            budget.path.write_text(json.dumps(state))
        else:
            budget.path.write_text("{")
    before = {str(p): p.lstat().st_mode for p in tmp_path.rglob("*")}
    with pytest.raises(CaptionBudgetError, match="observation unavailable"):
        budget.snapshot()
    assert {str(p): p.lstat().st_mode for p in tmp_path.rglob("*")} == before
    if invalid == "directory_missing":
        assert not root.is_dir()


async def test_single_flight_slow_read_and_cancelled_waiter_no_unbounded_threads():
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def snapshot():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {"observation": "available", "remaining_reservations": 4}

    view = CaptionBudgetHealth(SimpleNamespace(snapshot=snapshot))
    view.WAIT_SECONDS = .01
    try:
        pending = asyncio.create_task(view.refresh())
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.001)
        assert entered.is_set()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await asyncio.gather(*(view.refresh() for _ in range(32)))
        assert calls == [1] and view.status() == {"observation": "unavailable"}
        task = view.task
        release.set()
        await task
        await asyncio.sleep(0)
        assert view.status()["remaining_reservations"] == 4
        view.status()["remaining_reservations"] = 100
        assert view.status()["remaining_reservations"] == 4
        await view.refresh()
        assert calls == [1]
    finally:
        release.set()
        if view.task is not None:
            await view.task


async def test_failed_read_is_sanitized_cached_then_recovered(tmp_path):
    calls = []

    def bad():
        calls.append(1)
        raise OSError("PRIVATE-path-and-record")

    view = CaptionBudgetHealth(SimpleNamespace(snapshot=bad))
    await view.refresh()
    assert view.status() == {"observation": "unavailable"}
    await view.refresh()
    assert calls == [1]
    view.expires = 0
    view.budget = CaptionBudget(tmp_path)
    await view.refresh()
    assert view.status()["observation"] == "available"


def test_snapshot_directory_rename_cannot_retarget_opened_inode(tmp_path, monkeypatch):
    root = tmp_path / "source"
    budget = CaptionBudget(root, clock_ns=lambda: 10 * HOUR_NS)
    budget.reserve(digest("job"), digest("input"), "PRIVATE-camera")
    other = tmp_path / "other"
    CaptionBudget(other)
    original = os.open

    def open_and_rename(path, flags, *args, **kwargs):
        fd = original(path, flags, *args, **kwargs)
        if path == root:
            root.rename(tmp_path / "original-inode")
            root.symlink_to(other, target_is_directory=True)
        return fd

    monkeypatch.setattr(os, "open", open_and_rename)
    assert budget.snapshot()["used_reservations"] == 1
    assert not list(other.iterdir())


async def test_slow_completed_snapshot_is_not_relabelled_as_fresh():
    from time import monotonic
    release = threading.Event()

    def snapshot():
        assert release.wait(5)
        return {"observation": "available", "remaining_reservations": 12}

    view = CaptionBudgetHealth(SimpleNamespace(snapshot=snapshot))
    view.WAIT_SECONDS = .001
    try:
        await view.refresh()
        view.started = monotonic() - view.CACHE_SECONDS - 1
        task = view.task
        release.set()
        await task
        await asyncio.sleep(0)
        assert view.status() == {"observation": "unavailable"}
    finally:
        release.set()
        if view.task is not None:
            await view.task


@pytest.mark.parametrize("mode", ["metered", "unmetered", "one_use", "disabled"])
async def test_worker_reports_loaded_policy_not_later_saved_config(services, tmp_path, mode):
    config = (continuous_options(services, tmp_path) if mode in {"metered", "unmetered"}
              else options(services))
    if mode == "unmetered":
        config["worker"]["continuous"]["unmetered"] = True
    if mode == "disabled":
        del config["worker"]["test_scope"]
    worker = JobProcessor(config, tmp_path, camera_registry=MutableRegistry("fixture-camera"))
    try:
        config["worker"]["continuous"] = {"unmetered": mode != "unmetered"}
        before = sorted(str(p) for p in tmp_path.rglob("*"))
        await worker.refresh_caption_budget_status()
        policy = worker.status()["caption_policy"]
        assert policy["mode"] == mode
        if mode == "metered":
            assert policy["budget"]["remaining_reservations"] == 12
        else:
            assert policy["budget"] is None
        assert sorted(str(p) for p in tmp_path.rglob("*")) == before
        assert not (tmp_path / "caption-budget.json").exists()
        assert not services.requests and not services.media_requests and not services.callbacks
    finally:
        await worker.stop()


def policy(mode="metered"):
    return {"mode": mode, "scope": "continuous_captions_not_speech_port_or_all_paid_calls",
        "budget": {"observation": "available", "sampled_at_ns": 10 * HOUR_NS,
            "window_seconds": 3600, "limit": 12, "used_reservations": 5, "remaining_reservations": 7,
            "PRIVATE": "PRIVATE-journal"} if mode == "metered" else None,
        "PRIVATE": "PRIVATE-route"}


def test_normalization_allowlists_runtime_policy_and_older_worker_unknown():
    raw = {"service": "local-aikey", "device": {"adopted": True}, "worker": {}}
    assert _normalize("aikey", raw)["caption_policy"] is None
    for mode in ("metered", "unmetered", "one_use", "disabled"):
        raw["worker"]["caption_policy"] = policy(mode)
        result = _normalize("aikey", raw)["caption_policy"]
        assert result["mode"] == mode and "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(mode="unknown"), lambda p: p.update(scope="all_paid_calls"),
    lambda p: p.update(mode=[]), lambda p: p["budget"].update(observation=[]),
    lambda p: p.update(mode="unmetered"), lambda p: p.update(budget=None),
    lambda p: p["budget"].update(observation="unknown"),
    lambda p: p["budget"].update(limit=13), lambda p: p["budget"].update(limit=True),
    lambda p: p["budget"].update(window_seconds=3601),
    lambda p: p["budget"].update(used_reservations=6),
    lambda p: p["budget"].update(remaining_reservations=-1),
    lambda p: p["budget"].update(sampled_at_ns=False),
    lambda p: p["budget"].update(sampled_at_ns=2**63),
])
def test_invalid_runtime_budget_refused_not_reinterpreted(mutation):
    value = deepcopy(policy())
    mutation(value)
    raw = {"service": "local-aikey", "device": {"adopted": True}, "worker": {"caption_policy": value}}
    with pytest.raises(ValueError):
        _normalize("aikey", raw)


async def test_actual_health_handler_awaits_budget_before_returning_status():
    events = []

    async def refresh():
        events.append("sampled")

    def status():
        assert events == ["sampled"]
        return {"caption_policy": policy("unmetered")}

    app = SimpleNamespace(_startup_configuration={}, worker=SimpleNamespace(
        refresh_caption_budget_status=refresh, status=status), device=SimpleNamespace(status={}),
        camera_registry=None, search=SimpleNamespace(status={}), discovery=SimpleNamespace(status={}), rerank=None)
    result = await Application._health(app, None)
    assert json.loads(result.body)["worker"]["caption_policy"]["mode"] == "unmetered"
