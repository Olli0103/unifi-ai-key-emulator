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
    console = decode_message(await device.handle_message(
        wire("setConsoleInfo", {"controller": {"protectVersion": "7.3.60"}}, "console")))
    assert console.header["errorCode"] == 0
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


async def test_a_busy_camera_cannot_take_the_permits_held_for_a_quiet_one(services, tmp_path, monkeypatch):
    config = continuous_options(services, tmp_path)
    registry = MutableRegistry("camera-fixture", "camera-fixture-two")
    worker = JobProcessor(config, tmp_path, camera_registry=registry)
    release = asyncio.Event()

    async def execute(job):
        await release.wait()
        return {"status": "processed"}

    monkeypatch.setattr(worker, "_execute", execute)
    try:
        for index in range(11):
            assert (await worker.submit(command(f"busy-{index}")))["accepted"] is True
        with pytest.raises(WorkerError, match="held for cameras not yet served"):
            await worker.submit(command("busy-11"))
        quiet = second_camera(command("quiet-0"), "camera-fixture-two")
        assert (await worker.submit(quiet))["accepted"] is True
        with pytest.raises(WorkerError, match="budget is exhausted"):
            await worker.submit(command("busy-12"))
        assert worker.status()["captions"] == {"admitted": 12, "exhausted": 1, "deferred_fair_share": 1}
        assert not services.requests and not services.callbacks
    finally:
        release.set()
        await worker.wait_for_idle()
        await worker.stop()


async def test_registry_explains_why_each_camera_cannot_caption(monkeypatch, tmp_path):
    now = [1000.0]
    rows = [{"id": "cam-a", "model": "Fixture G5", "state": "CONNECTED",
             "processing_class": "smart_event_candidate"},
            {"id": "cam-b", "model": "Fixture G4", "state": "CONNECTED",
             "processing_class": "legacy_ingress_needed"},
            {"id": "cam-c", "model": "Fixture G5", "state": "DISCONNECTED",
             "processing_class": "offline"},
            {"id": "cam-d", "model": "Fixture G6", "state": "CONNECTED",
             "processing_class": "smart_event_candidate"}]

    async def fetch(*args, **kwargs):
        if rows is None:
            raise InventoryError("synthetic failure")
        return {"cameras": deepcopy(rows)}

    monkeypatch.setattr(camera_registry, "fetch_inventory", fetch)
    registry = CameraRegistry("127.0.0.1", policy(tmp_path), clock=lambda: now[0])
    await registry.refresh_once()
    assert registry.eligibility("cam-a") == {"caption": {"eligible": True, "reason": None}}
    reasons = {camera: registry.eligibility(camera)["caption"]["reason"]
               for camera in ("cam-b", "cam-c", "cam-d", "cam-e")}
    assert reasons == {"cam-b": "legacy_ingress_needed", "cam-c": "offline",
                       "cam-d": "model_not_allowed", "cam-e": "not_in_inventory"}
    assert all(registry.eligibility(c)["caption"]["eligible"] is False for c in reasons)
    status = registry.status()
    assert status["eligible_cameras"] == 1 and status["caption_ineligible"] == {
        "legacy_ingress_needed": 1, "model_not_allowed": 1, "offline": 1}
    assert not any(camera in json.dumps(status) for camera in ("cam-a", "cam-b", "cam-c"))
    # A camera coming online is re-classified on the next read without any ID edit.
    rows[2]["state"], rows[2]["processing_class"] = "CONNECTED", "smart_event_candidate"
    await registry.refresh_once()
    assert registry.eligibility("cam-c")["caption"]["eligible"] is True
    # Stale or failed inventory: every camera is ineligible, and no old reasons are reported.
    now[0] += 120
    assert registry.eligibility("cam-a")["caption"]["reason"] == "inventory_stale"
    assert registry.status()["caption_ineligible"] == {}
    rows = None
    await registry.refresh_once()
    assert registry.eligibility("cam-c")["caption"]["reason"] == "inventory_stale"
    assert registry.status() == {"fresh": False, "eligible_cameras": 0, "caption_ineligible": {},
                                 "last_error": "InventoryError"}


def _integration_row(number, model, smart):
    return {"id": f"{number:024x}", "modelKey": "camera", "state": "CONNECTED",
            "name": f"Synthetic {number}", "type": model, "mac": f"02:00:00:00:01:{number:02x}",
            "featureFlags": {"smartDetectTypes": list(smart), "smartDetectAudioTypes": []}}


@pytest.mark.parametrize("paired_g3_smart", [(), ("person", "vehicle", "animal", "package")])
async def test_a_paired_g3_never_enters_caption_scope_without_an_explicit_model_policy(
        monkeypatch, tmp_path, paired_g3_smart):
    """#9: an AI Port-paired G3 Instant reports the same camera fields as a native
    smart camera once Protect lists AI Port-supplied types. The registry cannot tell
    the source apart, so only the explicit camera_models policy may admit it."""
    from aikey.camera_inventory import parse_cameras
    native = _integration_row(1, "UVC G4 Bullet", ("person", "vehicle", "animal", "package"))
    paired_g3 = _integration_row(2, "UVC G3 Instant", paired_g3_smart)
    cameras = [camera.public() for camera in parse_cameras([native, paired_g3])]

    async def fetch(*args, **kwargs):
        return {"cameras": deepcopy(cameras)}

    monkeypatch.setattr(camera_registry, "fetch_inventory", fetch)
    options = dict(policy(tmp_path), camera_models=["UVC G4 Bullet"])
    registry = CameraRegistry("127.0.0.1", options, clock=lambda: 1000.0)
    await registry.refresh_once()
    assert registry.allows(native["id"])                              # native smart, in policy
    assert not registry.allows(paired_g3["id"])                       # paired G3, either inventory
    expected = "model_not_allowed" if paired_g3_smart else "legacy_ingress_needed"
    assert registry.eligibility(paired_g3["id"])["caption"]["reason"] == expected
    # A native model outside the policy is refused the same way; no ID edit admits it.
    options = dict(policy(tmp_path), camera_models=["UVC G3 Instant"])
    narrow = CameraRegistry("127.0.0.1", options, clock=lambda: 1000.0)
    await narrow.refresh_once()
    assert not narrow.allows(native["id"])
    assert narrow.eligibility(native["id"])["caption"]["reason"] == "model_not_allowed"
    assert narrow.allows(paired_g3["id"]) is bool(paired_g3_smart)


def test_unmetered_captions_need_a_local_caption_model(tmp_path):
    config = defaults(tmp_path / "state", "020000000001")
    config["controller"]["protect_version"] = "7.3.68"
    config["worker"].update(callback_mode="enabled", request_mp4_exports=True,
                            continuous=dict(policy(tmp_path), unmetered=True))
    config["inference"].update(provider="ollama", base_url="http://127.0.0.1:11434",
                               model="qwen3-vl:8b-instruct")
    assert validate_config(deepcopy(config))["worker"]["continuous"]["unmetered"] is True
    # OpenVINO Model Server on the NAS backend network is local and free too.
    ovms = deepcopy(config)
    ovms["inference"].update(provider="openai-compatible", base_url="http://172.30.50.13:8000/v3",
                             allow_remote=True, allow_insecure_http=True)
    assert validate_config(ovms)["worker"]["continuous"]["unmetered"] is True
    for mutation in (lambda value: value["inference"].update(provider="openai-compatible",
                                                             base_url="https://api.groq.com/openai/v1"),
                     lambda value: value["inference"].update(provider="openai",
                                                             base_url="https://api.openai.com/v1"),
                     lambda value: value["worker"]["continuous"].update(unmetered=False),
                     lambda value: value["worker"]["continuous"].update(unmetered="yes")):
        bad = deepcopy(config)
        mutation(bad)
        with pytest.raises(ConfigError):
            validate_config(bad)


async def test_unmetered_captions_are_not_limited_to_twelve_an_hour(services, tmp_path, monkeypatch):
    config = continuous_options(services, tmp_path)
    config["worker"]["continuous"]["unmetered"] = True
    registry = MutableRegistry("camera-fixture", "camera-fixture-two")
    worker = JobProcessor(config, tmp_path, camera_registry=registry)
    release = asyncio.Event()

    async def execute(job):
        await release.wait()
        return {"status": "processed"}

    monkeypatch.setattr(worker, "_execute", execute)
    try:
        for index in range(15):
            assert (await worker.submit(command(f"local-{index}")))["accepted"] is True
        assert worker.caption_budget is None
        assert not (tmp_path / "caption-budget.json").exists()
    finally:
        release.set()
        await worker.wait_for_idle()
        await worker.stop()


def on_demand(camera="camera-fixture", **query_changes):
    from urllib.parse import urlencode
    query = {"camera": camera, "channel": "0", "type": "rotating", "mute": "true",
             "format": "ubv", "createEvent": "false", "event": "event-fixture",
             "start": "1000", "end": "11000"}
    query.update(query_changes)
    return {"targetUri": ":7968/on_demand_inference", "timeoutMs": 30000,
            "resUrl": "/internal/camera-upload/summary-token",
            "payload": {"cameraId": camera, "eventId": "event-fixture", "timestamp": 6000,
                        "videoUrl": "/internal/aiprocessors/video/export?" + urlencode(query)}}


async def test_the_player_summary_button_works_in_continuous_mode(services, tmp_path, monkeypatch):
    config = continuous_options(services, tmp_path)
    config["worker"]["continuous"]["unmetered"] = True
    worker = JobProcessor(config, tmp_path, camera_registry=MutableRegistry("camera-fixture"))
    try:
        normalized = worker._normalize(on_demand())
        assert normalized[2] == "on_demand" and normalized[5] == "on_demand"
        # 30 Sep: a manual summary of a longer event was refused by the old
        # 10 s export rule; continuous mode allows a caption-length export.
        assert worker._normalize(on_demand(end="99000"))[2] == "on_demand"
        assert worker._normalize(on_demand(start="1000", end="6000"))[2] == "on_demand"   # moment at the end
        for bad in (on_demand(camera="other-camera"),            # not in caption scope
                    on_demand(end="130000"),                     # past max_video_duration_ms
                    on_demand(start="7000", end="9000"),         # moment outside the export
                    on_demand(createEvent="true"),
                    dict(on_demand(), targetUri=":7968/describe")):
            with pytest.raises(WorkerError):
                worker._normalize(bad)
    finally:
        await worker.stop()


async def test_a_metered_summary_reserves_the_paid_budget(services, tmp_path, monkeypatch):
    config = continuous_options(services, tmp_path)
    worker = JobProcessor(config, tmp_path, camera_registry=MutableRegistry("camera-fixture"))
    release = asyncio.Event()

    async def execute(job):
        await release.wait()
        return {"status": "processed"}

    monkeypatch.setattr(worker, "_execute", execute)
    try:
        assert (await worker.submit(on_demand()))["accepted"] is True
        reservations = json.loads((tmp_path / "caption-budget.json").read_text())["reservations"]
        assert len(reservations) == 1
    finally:
        release.set()
        await worker.wait_for_idle()
        await worker.stop()


async def test_caption_timeout_is_configurable_for_slow_local_models(services, tmp_path):
    config = continuous_options(services, tmp_path)
    worker = JobProcessor(config, tmp_path, camera_registry=MutableRegistry("camera-fixture"))
    assert worker.caption_timeout_s == 30
    await worker.stop()
    config["worker"].update(caption_timeout_s=180, timeout_s=300)
    worker = JobProcessor(config, tmp_path, camera_registry=MutableRegistry("camera-fixture"))
    assert worker.caption_timeout_s == 180
    await worker.stop()
    config["worker"]["caption_timeout_s"] = 0
    with pytest.raises(WorkerError):
        JobProcessor(config, tmp_path, camera_registry=MutableRegistry("camera-fixture"))
