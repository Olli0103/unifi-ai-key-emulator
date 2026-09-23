"""Two independent one-use camera trials with synthetic media only."""

from copy import deepcopy
import json
from urllib.parse import parse_qsl, urlencode

import pytest

from aikey.config import ConfigError, defaults, validate_config
from aikey.device import DeviceService
from aikey.protocol import decode_message
from aikey.worker import JobProcessor, WorkerError
from test_basic_descriptions import command, device_config, options, wire, PNG

pytest_plugins = ["test_worker"]


def second_camera(item, camera_id):
    item = deepcopy(item)
    body = item["payload"]
    body["camera"] = camera_id
    path, raw_query = body["reqUrl"].split("?", 1)
    query = dict(parse_qsl(raw_query))
    query["camera"] = camera_id
    body["reqUrl"] = path + "?" + urlencode(query)
    return item


def two_scopes(worker):
    first = worker.pop("test_scope")
    worker["test_scopes"] = [
        first,
        {
            "kind": "recognizeKeyFrames",
            "camera_id": "camera-fixture-two",
            "permit_id": "second-once",
        },
    ]
    return worker


@pytest.mark.parametrize(
    "invalid",
    [
        [],
        [{"kind": "recognizeKeyFrames", "camera_id": "one", "permit_id": "one"}] * 3,
        [{"camera_id": "same", "permit_id": "one"}, {"camera_id": "same", "permit_id": "two"}],
        [{"camera_id": "one", "permit_id": "same"}, {"camera_id": "two", "permit_id": "same"}],
    ],
)
def test_invalid_multi_scope_never_becomes_runtime_authority(tmp_path, invalid):
    config = defaults(tmp_path / "state", "020000000001")
    config["worker"]["test_scopes"] = invalid
    with pytest.raises(ConfigError, match="scope"):
        validate_config(config)


def test_old_and_new_scope_fields_cannot_be_combined(tmp_path):
    config = defaults(tmp_path / "state", "020000000001")
    config["worker"]["test_scope"] = {"camera_id": "one", "permit_id": "one"}
    config["worker"]["test_scopes"] = [{"camera_id": "two", "permit_id": "two"}]
    with pytest.raises(ConfigError, match="mutually exclusive"):
        validate_config(config)


async def test_each_camera_spends_only_its_own_permit_and_replays_after_restart(
    services, tmp_path, monkeypatch
):
    config = options(services)
    two_scopes(config["worker"])
    first = command("first-event")
    second = second_camera(command("second-event"), "camera-fixture-two")
    outside = second_camera(command("outside-event"), "camera-fixture-three")

    async def decode(*args, **kwargs):
        return PNG

    worker = JobProcessor(config, tmp_path)
    monkeypatch.setattr(worker, "_video_frame", decode)
    try:
        with pytest.raises(WorkerError, match="target-camera"):
            await worker.submit(outside)
        assert not list((tmp_path / "worker-test-scopes").glob("*.json"))
        first_result = await worker.handle(first)
        assert worker._read_scope_reservation(config["worker"]["test_scopes"][0]) is not None
        assert worker._read_scope_reservation(config["worker"]["test_scopes"][1]) is None
        second_result = await worker.handle(second)
        assert worker._read_scope_reservation(config["worker"]["test_scopes"][1]) is not None
        assert len(services.requests) == len(services.callbacks) == 2
        with pytest.raises(WorkerError, match="consumed"):
            await worker.submit(second_camera(command("third-event"), "camera-fixture-two"))
        with pytest.raises(WorkerError, match="consumed"):
            await worker.submit(command("fourth-event"))
        assert len(services.requests) == 2
    finally:
        await worker.stop()

    restarted = JobProcessor(config, tmp_path)
    monkeypatch.setattr(restarted, "_video_frame", decode)
    try:
        assert await restarted.handle(first) == first_result
        assert await restarted.handle(second) == second_result
        assert len(services.requests) == len(services.callbacks) == 2
    finally:
        await restarted.stop()


async def test_device_gate_accepts_both_cameras_and_rejects_third(tmp_path):
    config = device_config()
    two_scopes(config["worker"])
    calls = []

    async def admit(item):
        calls.append(item["payload"]["camera"])
        return {"accepted": True}

    device = DeviceService(config, tmp_path, admit)
    for index, item in enumerate(
        (command("one"), second_camera(command("two"), "camera-fixture-two"))
    ):
        reply = decode_message(
            await device.handle_message(
                wire("recognizeKeyFrames", item["payload"], f"request-{index}")
            )
        )
        assert reply.header["errorCode"] == 0
    outside = second_camera(command("three"), "camera-fixture-three")
    reply = decode_message(
        await device.handle_message(
            wire("recognizeKeyFrames", outside["payload"], "request-outside")
        )
    )
    assert reply.header["errorCode"] == 95
    assert calls == ["camera-fixture", "camera-fixture-two"]
    assert device.status["recognize_key_frames"]["camera_match_counts"]["matches"] == 2
    assert device.status["recognize_key_frames"]["camera_match_counts"]["different"] == 1
    for camera_id in calls:
        assert camera_id not in json.dumps(device.status)
