"""Continuous-caption admission pins (#12): per-camera allowlist and the reported controller version."""

from copy import deepcopy

import pytest

from aikey import camera_registry
from aikey.camera_registry import CameraRegistry
from aikey.config import ConfigError, defaults, validate_config
from aikey.device import DeviceService
from aikey.protocol import CONTINUOUS_CAPTION_VERSIONS, decode_message
from test_basic_descriptions import command, device_config, wire
from test_continuous_admission import MutableRegistry, policy

LISTED, SIBLING, OTHER = "a" * 24, "b" * 24, "c" * 24


def _row(camera_id, model, state="CONNECTED"):
    return {"id": camera_id, "model": model, "state": state,
            "processing_class": "smart_event_candidate" if state == "CONNECTED" else "offline"}


async def _registry(monkeypatch, tmp_path, rows, **changes):
    async def fetch(*args, **kwargs):
        return {"cameras": deepcopy(rows)}
    monkeypatch.setattr(camera_registry, "fetch_inventory", fetch)
    registry = CameraRegistry("127.0.0.1", dict(policy(tmp_path), **changes), clock=lambda: 1000.0)
    await registry.refresh_once()
    return registry


async def test_a_listed_camera_is_admitted_and_a_same_model_sibling_is_not(monkeypatch, tmp_path):
    rows = [_row(LISTED, "Fixture G4"), _row(SIBLING, "Fixture G4"), _row(OTHER, "Fixture G5")]
    pinned = await _registry(monkeypatch, tmp_path, rows, camera_ids=[LISTED, OTHER])
    assert pinned.allowed_ids == {LISTED, OTHER}
    assert pinned.eligibility(SIBLING)["caption"]["reason"] == "camera_not_listed"
    assert pinned.status()["caption_ineligible"] == {"camera_not_listed": 1}
    # Without the pin, the model policy alone admits the sibling: the gap being closed.
    assert SIBLING in (await _registry(monkeypatch, tmp_path, rows)).allowed_ids


async def test_a_reconnecting_same_model_camera_does_not_widen_the_scope(monkeypatch, tmp_path):
    rows = [_row(LISTED, "Fixture G4"), _row(SIBLING, "Fixture G4", state="DISCONNECTED")]
    registry = await _registry(monkeypatch, tmp_path, rows, camera_ids=[LISTED])
    assert registry.allowed_ids == {LISTED}
    rows[1] = _row(SIBLING, "Fixture G4")                             # the offline sibling returns
    await registry.refresh_once()
    assert registry.allowed_ids == {LISTED}


async def test_a_listed_camera_still_needs_its_model_and_a_connection(monkeypatch, tmp_path):
    rows = [_row(LISTED, "Fixture G6"), _row(OTHER, "Fixture G5", state="DISCONNECTED")]
    registry = await _registry(monkeypatch, tmp_path, rows, camera_ids=[LISTED, OTHER])
    assert registry.allowed_ids == frozenset()
    assert registry.eligibility(LISTED)["caption"]["reason"] == "model_not_allowed"
    assert registry.eligibility(OTHER)["caption"]["reason"] == "offline"


def _config(tmp_path, **policy_changes):
    config = defaults(tmp_path / "state", "020000000001")
    config["controller"]["protect_version"] = "7.3.68"
    config["worker"].update(callback_mode="enabled", request_mp4_exports=True,
                            continuous=dict(policy(tmp_path), **policy_changes))
    return config


def test_the_config_accepts_a_camera_pin_and_the_true_controller_label(tmp_path):
    assert validate_config(_config(tmp_path, camera_ids=[LISTED, OTHER]))["worker"]["continuous"][
        "camera_ids"] == [LISTED, OTHER]
    assert CONTINUOUS_CAPTION_VERSIONS == {"7.3.60", "7.3.68"}


@pytest.mark.parametrize("ids", [[], [LISTED, LISTED], [LISTED, 7], ["two words"], [""],
                                 ["x" * 65], [f"{i:024x}" for i in range(17)], LISTED])
def test_a_malformed_camera_pin_is_refused(tmp_path, ids):
    with pytest.raises(ConfigError, match="camera_ids"):
        validate_config(_config(tmp_path, camera_ids=ids))


def test_a_version_without_caption_evidence_is_refused_at_config(tmp_path):
    config = _config(tmp_path)
    config["controller"]["protect_version"] = "7.3.56"
    with pytest.raises(ConfigError, match="caption evidence"):
        validate_config(config)


async def _device(tmp_path, reported):
    config = device_config()
    del config["worker"]["test_scope"]
    config["worker"]["continuous"] = policy(tmp_path)
    admitted = []

    async def submit(item):
        admitted.append(item["payload"]["camera"])
        return {"accepted": True}

    device = DeviceService(config, tmp_path, submit, camera_registry=MutableRegistry("camera-fixture"))
    if reported is not None:
        await device.handle_message(wire("setConsoleInfo", {"controller": {"protectVersion": reported}},
                                         "console"))
    reply = decode_message(await device.handle_message(
        wire("recognizeKeyFrames", command("one")["payload"], "request-one")))
    return reply.header["errorCode"], admitted, device


@pytest.mark.parametrize("reported", sorted(CONTINUOUS_CAPTION_VERSIONS))
async def test_a_controller_reporting_an_evidenced_version_is_admitted(tmp_path, reported):
    code, admitted, _ = await _device(tmp_path, reported)
    assert code == 0 and admitted == ["camera-fixture"]


@pytest.mark.parametrize("reported", [None, "7.3.56", "7.4.1", "not-a-version"])
async def test_an_unreported_or_unevidenced_controller_version_blocks_admission(tmp_path, reported):
    code, admitted, device = await _device(tmp_path, reported)
    assert code == 95 and admitted == []                             # no worker, no budget, no media
    phases = device.status["recognize_key_frames"]["phase_counts"]
    assert phases["controller_version_unverified"] == 1
