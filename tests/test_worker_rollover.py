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
