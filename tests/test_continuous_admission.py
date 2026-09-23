"""Offline checks for fresh all-camera admission and the paid-caption limit."""

import asyncio
from copy import deepcopy
import json

import pytest

from aikey import camera_registry
from aikey.camera_inventory import InventoryError
from aikey.camera_registry import CameraRegistry
from aikey.config import ConfigError, defaults, validate_config
from aikey.device import DeviceService
from aikey.protocol import decode_message
from aikey.worker import JobProcessor, WorkerError
from test_basic_descriptions import PNG, command, device_config, options, wire
from test_two_camera_scope import second_camera

pytest_plugins = ["test_worker"]


def policy(tmp_path):
    return {"enabled": True, "api_key_file": str(tmp_path / "key"),
            "web_trust_file": str(tmp_path / "trust"),
            "web_cert_file": str(tmp_path / "cert"), "refresh_seconds": 60,
            "camera_models": ["Fixture G5", "Fixture G4"]}


def continuous_options(services, tmp_path):
    config = options(services)
    del config["worker"]["test_scope"]
    config["worker"]["continuous"] = policy(tmp_path)
    config["worker"]["max_queue"] = 20
    return config


class MutableRegistry:
    def __init__(self, *cameras):
        self.allowed_ids = frozenset(cameras)

    def allows(self, camera_id):
        return camera_id in self.allowed_ids


def test_continuous_config_is_explicit_and_excludes_one_use_scopes(tmp_path):
    config = defaults(tmp_path / "state", "020000000001")
    config["controller"]["protect_version"] = "7.3.60"
    config["worker"].update(callback_mode="enabled", request_mp4_exports=True,
                            continuous=policy(tmp_path))
    assert validate_config(config)["worker"]["continuous"]["refresh_seconds"] == 60
    for mutation in (
        lambda value: value["worker"].update(test_scope={"camera_id": "fixture", "permit_id": "once"}),
        lambda value: value["worker"]["continuous"].update(enabled=False),
        lambda value: value["worker"]["continuous"].update(refresh_seconds=1),
        lambda value: value["worker"]["continuous"].update(api_key_file="relative"),
        lambda value: value["worker"]["continuous"].update(camera_models=[]),
        lambda value: value["controller"].update(protect_version="7.3.56"),
        lambda value: value["worker"].update(request_mp4_exports=False),
    ):
        bad = deepcopy(config)
        mutation(bad)
        with pytest.raises(ConfigError):
            validate_config(bad)


async def test_registry_add_remove_offline_failure_and_expiry(monkeypatch, tmp_path):
    now = [1000.0]
    rows = [{"id": "fixture-one", "model": "Fixture G5", "state": "CONNECTED",
             "processing_class": "smart_event_candidate"},
            {"id": "legacy", "model": "Fixture G4", "state": "CONNECTED",
             "processing_class": "legacy_ingress_needed"}]

    async def fetch(*args, **kwargs):
        if rows is None:
            raise InventoryError("synthetic failure")
        return {"cameras": deepcopy(rows)}

    monkeypatch.setattr(camera_registry, "fetch_inventory", fetch)
    registry = CameraRegistry("127.0.0.1", policy(tmp_path), clock=lambda: now[0])
    await registry.refresh_once()
    assert registry.allows("fixture-one")
    assert not registry.allows("legacy")
    rows[:] = [{"id": "fixture-two", "model": "Fixture G4", "state": "CONNECTED",
                "processing_class": "smart_event_candidate"},
               {"id": "fixture-one", "model": "Fixture G5", "state": "DISCONNECTED",
                "processing_class": "offline"},
               {"id": "unverified", "model": "Fixture G6", "state": "CONNECTED",
                "processing_class": "smart_event_candidate"}]
    await registry.refresh_once()
    assert registry.allows("fixture-two") and not registry.allows("fixture-one")
    assert not registry.allows("unverified")
    now[0] += 120
    assert not registry.allows("fixture-two")
    rows = None
    await registry.refresh_once()
    assert not registry.allowed_ids
    assert registry.status()["last_error"] == "InventoryError"


async def test_device_gate_tracks_fresh_camera_registry(tmp_path):
    config = device_config()
    del config["worker"]["test_scope"]
    config["worker"]["continuous"] = policy(tmp_path)
    registry = MutableRegistry("camera-fixture")
    admitted = []

    async def submit(item):
        admitted.append(item["payload"]["camera"])
        return {"accepted": True}

    device = DeviceService(config, tmp_path, submit, camera_registry=registry)
    assert "recognizeKeyFrames" in device.status["supported_commands"]
    first = decode_message(await device.handle_message(
        wire("recognizeKeyFrames", command("one")["payload"], "request-one")))
    assert first.header["errorCode"] == 0
    registry.allowed_ids = frozenset({"camera-fixture-two"})
    removed = decode_message(await device.handle_message(
        wire("recognizeKeyFrames", command("two")["payload"], "request-two")))
    assert removed.header["errorCode"] == 95
    second = second_camera(command("three"), "camera-fixture-two")
    added = decode_message(await device.handle_message(
        wire("recognizeKeyFrames", second["payload"], "request-three")))
    assert added.header["errorCode"] == 0
    assert admitted == ["camera-fixture", "camera-fixture-two"]
    assert "camera-fixture-two" not in json.dumps(device.status)


async def test_global_budget_charges_twelve_before_media_across_cameras_and_restart(
    services, tmp_path, monkeypatch
):
    config = continuous_options(services, tmp_path)
    registry = MutableRegistry("camera-fixture", "camera-fixture-two")
    worker = JobProcessor(config, tmp_path, camera_registry=registry)
    release = asyncio.Event()
    contacted = []

    async def execute(job):
        contacted.append(job.job_id)
        await release.wait()
        return {"status": "processed"}

    monkeypatch.setattr(worker, "_execute", execute)
    try:
        for index in range(12):
            item = command(f"budget-{index}")
            if index % 2:
                item = second_camera(item, "camera-fixture-two")
            assert (await worker.submit(item))["accepted"] is True
        with pytest.raises(WorkerError, match="budget is exhausted"):
            await worker.submit(command("budget-overflow"))
        assert not services.requests and not services.callbacks
        assert len(json.loads((tmp_path / "caption-budget.json").read_text())["reservations"]) == 12
    finally:
        release.set()
        await worker.wait_for_idle()
        await worker.stop()
    restarted = JobProcessor(config, tmp_path, camera_registry=registry)
    try:
        with pytest.raises(WorkerError, match="budget is exhausted"):
            await restarted.submit(command("after-restart"))
    finally:
        await restarted.stop()


async def test_continuous_real_loopback_callback_and_duplicate_no_extra_charge(
    services, tmp_path, monkeypatch
):
    config = continuous_options(services, tmp_path)
    registry = MutableRegistry("camera-fixture", "camera-fixture-two")
    worker = JobProcessor(config, tmp_path, camera_registry=registry)

    async def decode(*args, **kwargs):
        return PNG

    monkeypatch.setattr(worker, "_video_frame", decode)
    first = command("continuous-first")
    second = second_camera(command("continuous-second"), "camera-fixture-two")
    try:
        assert (await worker.handle(first))["status"] == "processed"
        assert (await worker.handle(second))["status"] == "processed"
        assert (await worker.submit(first))["duplicate"] is True
        assert len(services.media_requests) == len(services.requests) == len(services.callbacks) == 2
        assert len(json.loads((tmp_path / "caption-budget.json").read_text())["reservations"]) == 2
    finally:
        await worker.stop()


async def test_orphaned_reservation_cannot_repeat_a_paid_call(services, tmp_path):
    config = continuous_options(services, tmp_path)
    registry = MutableRegistry("camera-fixture")
    worker = JobProcessor(config, tmp_path, camera_registry=registry)
    item = command("crashed-before-journal")
    job_id, fingerprint, *_ = worker._normalize(item)
    worker.caption_budget.reserve(job_id, fingerprint, "camera-fixture")
    try:
        with pytest.raises(WorkerError, match="reservation exists"):
            await worker.submit(item)
        assert not services.requests and not services.callbacks
    finally:
        await worker.stop()


async def test_inventory_change_during_start_never_reserves_or_fetches(services, tmp_path,
                                                                       monkeypatch):
    config = continuous_options(services, tmp_path)
    registry = MutableRegistry("camera-fixture")
    worker = JobProcessor(config, tmp_path, camera_registry=registry)
    original_start = worker.start

    async def changed_start():
        await original_start()
        registry.allowed_ids = frozenset()

    monkeypatch.setattr(worker, "start", changed_start)
    try:
        with pytest.raises(WorkerError, match="Camera inventory changed"):
            await worker.submit(command("lost-during-start"))
        assert not (tmp_path / "caption-budget.json").exists()
        assert not services.requests and not services.callbacks
    finally:
        await worker.stop()
