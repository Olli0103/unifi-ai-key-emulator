"""Terminal job rollover keeps paid-call deduplication after a worker restart."""

import json
import time

import pytest

from aikey.worker import JobProcessor, WorkerError
from test_basic_descriptions import PNG, command
from test_continuous_admission import MutableRegistry, continuous_options

pytest_plugins = ["test_worker"]


def age_record(worker, job_id):
    record = worker._history[job_id]
    record["updatedAt"] = time.time() - 25 * 3600
    path = worker.state_dir / f"{job_id}.json"
    path.write_text(json.dumps(record))


async def test_old_terminal_result_moves_to_private_archive_and_replays_after_restart(
    services, tmp_path, monkeypatch
):
    config = continuous_options(services, tmp_path)
    config["worker"]["max_ledger_entries"] = 2
    registry = MutableRegistry("camera-fixture")
    worker = JobProcessor(config, tmp_path, camera_registry=registry)

    async def decode(*args, **kwargs):
        return PNG

    monkeypatch.setattr(worker, "_video_frame", decode)
    first, second, third = (command(value) for value in ("archive-one", "archive-two", "archive-three"))
    try:
        first_result = await worker.handle(first)
        await worker.handle(second)
        first_id = worker._normalize(first)[0]
        age_record(worker, first_id)
        assert (await worker.handle(third))["status"] == "processed"
        assert worker._archive_path(first_id).is_file()
        assert not (worker.state_dir / f"{first_id}.json").exists()
        assert (await worker.handle(first))["status"] == "archived"
        archived = json.loads(worker._archive_path(first_id).read_text())
        assert set(archived) == {"schema", "jobId", "fingerprint", "state", "updatedAt"}
        assert first_result["result"]["description"] not in json.dumps(archived)
        assert len(services.requests) == len(services.callbacks) == 3
    finally:
        await worker.stop()

    restarted = JobProcessor(config, tmp_path, camera_registry=registry)
    try:
        assert (await restarted.handle(first))["status"] == "archived"
        assert len(services.requests) == len(services.callbacks) == 3
    finally:
        await restarted.stop()


async def test_archive_failure_blocks_new_budget_charge_and_media(services, tmp_path,
                                                                   monkeypatch):
    config = continuous_options(services, tmp_path)
    config["worker"]["max_ledger_entries"] = 1
    registry = MutableRegistry("camera-fixture")
    worker = JobProcessor(config, tmp_path, camera_registry=registry)

    async def decode(*args, **kwargs):
        return PNG

    monkeypatch.setattr(worker, "_video_frame", decode)
    try:
        first = command("archive-failure-one")
        await worker.handle(first)
        age_record(worker, worker._normalize(first)[0])
        monkeypatch.setattr("aikey.worker.os.link", lambda *args: (_ for _ in ()).throw(OSError()))
        with pytest.raises(WorkerError, match="archive outcome is uncertain"):
            await worker.submit(command("archive-failure-two"))
        assert len(services.requests) == len(services.callbacks) == 1
        assert len(json.loads((tmp_path / "caption-budget.json").read_text())["reservations"]) == 1
    finally:
        await worker.stop()


async def test_corrupt_archive_blocks_matching_duplicate_without_new_paid_call(
    services, tmp_path, monkeypatch
):
    config = continuous_options(services, tmp_path)
    registry = MutableRegistry("camera-fixture")
    worker = JobProcessor(config, tmp_path, camera_registry=registry)

    async def decode(*args, **kwargs):
        return PNG

    monkeypatch.setattr(worker, "_video_frame", decode)
    item = command("archived-corrupt")
    try:
        await worker.handle(item)
        job_id = worker._normalize(item)[0]
        age_record(worker, job_id)
        worker._rollover_history()
        worker._archive_path(job_id).write_text("{}")
        with pytest.raises(WorkerError, match="Invalid archived worker result"):
            await worker.submit(item)
        assert len(services.requests) == len(services.callbacks) == 1
        assert len(json.loads((tmp_path / "caption-budget.json").read_text())["reservations"]) == 1
    finally:
        await worker.stop()


async def test_uncertain_record_is_never_archived_for_capacity(services, tmp_path):
    config = continuous_options(services, tmp_path)
    config["worker"]["max_ledger_entries"] = 1
    registry = MutableRegistry("camera-fixture")
    worker = JobProcessor(config, tmp_path, camera_registry=registry)
    first = command("uncertain-old")
    job_id, fingerprint, *_ = worker._normalize(first)
    record = {"jobId": job_id, "fingerprint": fingerprint,
              "state": "callback_uncertain", "updatedAt": time.time() - 25 * 3600}
    (worker.state_dir / f"{job_id}.json").write_text(json.dumps(record))
    worker._history[job_id] = record
    try:
        with pytest.raises(WorkerError, match="journal is full"):
            await worker.submit(command("uncertain-next"))
        assert not list(worker.archive_dir.rglob("*.json"))
        assert not (tmp_path / "caption-budget.json").exists()
    finally:
        await worker.stop()


# --- #12 the ledger rolls over outside continuous mode too -----------------

from test_basic_descriptions import options  # noqa: E402

DAY = 24 * 3600


def seed(worker, name, state, age_s):
    job_id, fingerprint, *_ = worker._normalize(command(name))
    record = {"jobId": job_id, "fingerprint": fingerprint, "state": state,
              "updatedAt": time.time() - age_s, "operation": "describe"}
    (worker.state_dir / f"{job_id}.json").write_text(json.dumps(record))
    worker._history[job_id] = record
    return job_id


async def test_old_records_leave_the_ledger_outside_continuous_mode(services, tmp_path):
    worker = JobProcessor(options(services), tmp_path)
    assert not worker.continuous
    ids = {
        "old_done": seed(worker, "old-done", "completed", 2 * DAY),
        "new_done": seed(worker, "new-done", "completed", 3600),
        "recent_fail": seed(worker, "recent-fail", "failed", 2 * DAY),
        "old_fail": seed(worker, "old-fail", "failed", 8 * DAY),
        "old_uncertain": seed(worker, "old-uncertain", "callback_uncertain", 30 * DAY),
    }
    try:
        worker._rollover_history()
        archived = {name for name, job_id in ids.items() if worker._archive_path(job_id).is_file()}
        assert archived == {"old_done", "old_fail"}
        # A recent failure stays for Protect's retry; an uncertain callback stays for review.
        assert {"new_done", "recent_fail", "old_uncertain"} <= {
            name for name, job_id in ids.items() if job_id in worker._history}
        # Replays of archived work are answered, never re-run or re-charged.
        assert (await worker.submit(command("old-done")))["duplicate"] is True
        with pytest.raises(WorkerError, match="cannot be replayed"):
            await worker.submit(command("old-fail"))
        with pytest.raises(WorkerError, match="Callback outcome is uncertain"):
            await worker.submit(command("old-uncertain"))
        assert services.requests == [] and services.callbacks == []
    finally:
        await worker.stop()


async def test_a_full_ledger_of_old_completed_work_no_longer_blocks_admission(services, tmp_path):
    config = options(services)
    config["worker"]["max_ledger_entries"] = 2
    worker = JobProcessor(config, tmp_path)
    seed(worker, "done-1", "completed", 2 * DAY)
    seed(worker, "done-2", "completed", 2 * DAY)
    try:
        # Before #12 these stayed forever outside continuous mode and this was refused
        # with "journal is full"; now the day-old records roll over first.
        worker._rollover_history()
        assert len(worker._history) == 0
        assert len(list(worker.archive_dir.rglob("*.json"))) == 2
    finally:
        await worker.stop()


async def test_uncertain_records_still_fill_the_ledger_and_say_so(services, tmp_path):
    config = options(services)
    config["worker"]["max_ledger_entries"] = 1
    worker = JobProcessor(config, tmp_path)
    seed(worker, "stuck", "callback_uncertain", 30 * DAY)
    try:
        with pytest.raises(WorkerError, match="journal is full"):
            await worker.submit(command("next-one"))
        assert worker.status()["ledger"] == 1
    finally:
        await worker.stop()
