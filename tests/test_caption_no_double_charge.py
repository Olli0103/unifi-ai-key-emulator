"""A failed or uncertain continuous caption is never charged twice (#12). Local services only."""

import json
import time

import pytest

from aikey.worker import JobProcessor, WorkerError, _json
from test_basic_descriptions import PNG, command
from test_continuous_admission import MutableRegistry, continuous_options

pytest_plugins = ["test_worker"]


def reservations(state):
    path = state / "caption-budget.json"
    return len(json.loads(path.read_text())["reservations"]) if path.exists() else 0


async def _first_attempt(services, tmp_path, monkeypatch):
    config = continuous_options(services, tmp_path)
    registry = MutableRegistry("camera-fixture")
    worker = JobProcessor(config, tmp_path, camera_registry=registry)

    async def decode(*args, **kwargs):
        return PNG

    monkeypatch.setattr(worker, "_video_frame", decode)
    item = command("charged-once")
    try:
        with pytest.raises(WorkerError):
            await worker.handle(item)
    finally:
        await worker.stop()
    return config, registry, item


async def _retry(config, registry, tmp_path, item, match):
    restarted = JobProcessor(config, tmp_path, camera_registry=registry)
    try:
        with pytest.raises(WorkerError, match=match):
            await restarted.submit(item)
    finally:
        await restarted.stop()


@pytest.mark.parametrize("failure,expected_state,match", [
    ("provider", "failed", "cannot be replayed"),
    ("callback", "callback_uncertain", "uncertain"),
])
async def test_a_retried_caption_after_a_failure_makes_no_new_request_or_charge(
        services, tmp_path, monkeypatch, failure, expected_state, match):
    if failure == "provider":
        services.model_status = 500
    else:
        services.callback_status = 500
    config, registry, item = await _first_attempt(services, tmp_path, monkeypatch)
    records = [json.loads(p.read_text()) for p in (tmp_path / "worker-jobs").glob("*.json")]
    assert [r["state"] for r in records] == [expected_state]
    requests, callbacks, charged = len(services.requests), len(services.callbacks), reservations(tmp_path)
    assert requests == 1 and charged == 1
    services.model_status = services.callback_status = 200
    await _retry(config, registry, tmp_path, item, match)
    assert (len(services.requests), len(services.callbacks), reservations(tmp_path)) == (
        requests, callbacks, charged)


async def test_a_failed_caption_moved_to_the_archive_is_still_refused(services, tmp_path, monkeypatch):
    services.model_status = 500
    config, registry, item = await _first_attempt(services, tmp_path, monkeypatch)
    (path,) = (tmp_path / "worker-jobs").glob("*.json")
    record = json.loads(path.read_text())
    record["updatedAt"] = time.time() - 25 * 3600                 # past the continuous rollover
    path.write_bytes(_json(record))
    services.model_status = 200
    await _retry(config, registry, tmp_path, item, "cannot be replayed")
    assert not list((tmp_path / "worker-jobs").glob("*.json"))    # rolled over into a tombstone
    (tombstone,) = (tmp_path / "worker-archive").rglob("*.json")
    assert json.loads(tombstone.read_text())["state"] == "failed"
    assert len(services.requests) == 1 and reservations(tmp_path) == 1


async def test_an_uncertain_caption_is_never_archived_and_stays_refused(services, tmp_path, monkeypatch):
    services.callback_status = 500
    config, registry, item = await _first_attempt(services, tmp_path, monkeypatch)
    (path,) = (tmp_path / "worker-jobs").glob("*.json")
    record = json.loads(path.read_text())
    record["updatedAt"] = time.time() - 30 * 24 * 3600
    path.write_bytes(_json(record))
    services.callback_status = 200
    await _retry(config, registry, tmp_path, item, "uncertain")
    assert path.exists() and not list((tmp_path / "worker-archive").rglob("*.json"))
    assert len(services.requests) == 1 and reservations(tmp_path) == 1
