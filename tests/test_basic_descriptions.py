"""One native basic-description command, with synthetic loopback services only."""

from copy import deepcopy
import json
from urllib.parse import parse_qsl, urlencode

import pytest

from aikey.config import defaults, validate_config
from aikey.device import DeviceService
from aikey.protocol import decode_message, encode_message
from aikey.worker import JobProcessor, WorkerError
from test_worker import DESCRIPTION, PNG, configuration, services as services


def options(services):
    config = configuration(services)
    config["worker"].update(request_mp4_exports=True, test_scope={
        "kind": "recognizeKeyFrames", "camera_id": "camera-fixture", "permit_id": "basic-once"})
    return config


def command(event="event-fixture"):
    query = {"camera": "camera-fixture", "event": event, "channel": "0", "start": "1000",
             "end": "11000", "type": "rotating", "mute": "true", "format": "ubv",
             "createEvent": "false"}
    return {"command": "recognizeKeyFrames", "payload": {
        "camera": "camera-fixture", "event": event, "channel": 0, "start": 1000, "end": 11000,
        "type": "rotating", "mute": True, "format": "ubv", "createEvent": False,
        "ramType": "video", "postVLM": True, "keyMoments": [6000, 9000],
        "reqUrl": "/internal/aiprocessors/video/export?" + urlencode(query),
        "resUrl": "/internal/aiprocessors/recognize-anything"}}


def make_worker(services, tmp_path, monkeypatch):
    worker = JobProcessor(options(services), tmp_path)
    timestamps = []
    async def decode(data, headers, url, job, *, timestamp=None):
        timestamps.append(timestamp)
        # The durable permit must exist before media processing starts.
        reservation = worker._read_scope_reservation()
        assert reservation["kind"] == "recognizeKeyFrames"
        return PNG
    monkeypatch.setattr(worker, "_video_frame", decode)
    return worker, timestamps


async def test_real_http_caption_and_full_ram_envelope_once_across_restart(services, tmp_path, monkeypatch):
    worker, timestamps = make_worker(services, tmp_path, monkeypatch)
    try:
        result = await worker.handle(command())
        assert timestamps == [6000, 9000]
        assert len(services.media_requests) == len(services.requests) == len(services.callbacks) == 1
        assert dict(parse_qsl(services.video_queries[0]))["format"] == "mp4"
        callback = services.callbacks[0]
        assert callback["path"] == "/internal/aiprocessors/recognize-anything"
        assert callback["payload"]["name"] == "ram"
        assert callback["payload"]["extra"] is None
        ram = callback["payload"]["payload"]
        assert ram == result["result"]
        assert ram["description"] == DESCRIPTION
        assert ram["cameraId"] == "camera-fixture" and ram["eventId"] == "event-fixture"
        assert ram["status"] == "success" and ram["keyMomentsTags"] == []
        assert set(ram) == {"cameraId", "eventId", "description", "status", "keyMomentsTags",
                            "inferBoxMs", "inferTagMs", "inferTxtMs", "preProcessMs", "timeElapsedMs"}
        assert ram["inferBoxMs"] == ram["inferTagMs"] == 0
        assert all(type(ram[key]) is int and ram[key] >= 0
                   for key in ("inferTxtMs", "preProcessMs", "timeElapsedMs"))
        content = services.requests[0]["messages"][0]["content"]
        assert len(content) == 3  # One prompt and two actual decoded key frames.
        assert (await worker.submit(command()))["duplicate"] is True
        with pytest.raises(WorkerError, match="consumed"):
            await worker.submit(command("second-event"))
    finally:
        await worker.stop()
    restarted, restarted_timestamps = make_worker(services, tmp_path, monkeypatch)
    try:
        assert await restarted.handle(command()) == result
        assert restarted_timestamps == []
        with pytest.raises(WorkerError, match="consumed"):
            await restarted.submit(command("second-event"))
        assert len(services.requests) == len(services.callbacks) == 1
    finally:
        await restarted.stop()


@pytest.mark.parametrize("variant", [
    "camera", "event_query", "camera_query", "start_query", "extra_query", "duplicate_query",
    "callback", "foreign_media", "image_path", "too_long", "bool_timestamp", "outside_timestamp",
    "too_many_frames", "duplicate_frame", "recognition", "images", "no_summary", "audio",
    "channel", "format", "unknown_body", "face_metadata", "empty_frames",
])
async def test_rejects_unrelated_work_before_any_media_or_inference(services, tmp_path, variant):
    item = command()
    body = item["payload"]
    changes = {
        "camera": {"camera": "other-camera"}, "callback": {"resUrl": "/internal/aiprocessors/descriptions/task"},
        "foreign_media": {"reqUrl": "https://unapproved.invalid/internal/aiprocessors/video/export"},
        "image_path": {"reqUrl": "/internal/aiprocessors/image/fixture"},
        "too_long": {"end": 11001}, "bool_timestamp": {"keyMoments": [True]},
        "outside_timestamp": {"keyMoments": [11000]}, "too_many_frames": {"keyMoments": [2000, 3000, 4000, 5000, 6000]},
        "duplicate_frame": {"keyMoments": [6000, 6000]}, "recognition": {"ramType": "videoWithRecognition"},
        "images": {"ramType": "multipleImages"}, "no_summary": {"postVLM": False},
        "audio": {"mute": False}, "channel": {"channel": 1}, "format": {"format": "jpeg"},
        "unknown_body": {"unknown": True}, "face_metadata": {"faceMeta": []}, "empty_frames": {"keyMoments": []},
    }
    if variant in changes:
        body.update(changes[variant])
    else:
        path, query = body["reqUrl"].split("?", 1)
        pairs = parse_qsl(query)
        if variant == "duplicate_query":
            pairs.append(("camera", "camera-fixture"))
        else:
            changed = dict(pairs)
            key, value = {"event_query": ("event", "other-event"), "camera_query": ("camera", "other-camera"),
                          "start_query": ("start", "0"), "extra_query": ("token", "not-permitted")}[variant]
            changed[key] = value
            pairs = list(changed.items())
        body["reqUrl"] = path + "?" + urlencode(pairs)
    worker = JobProcessor(options(services), tmp_path)
    try:
        with pytest.raises(WorkerError):
            await worker.submit(item)
        assert services.media_requests == services.requests == services.callbacks == []
        assert worker._read_scope_reservation() is None
    finally:
        await worker.stop()


async def test_scope_kind_is_explicit_and_cannot_be_swapped_after_use(services, tmp_path, monkeypatch):
    unscoped = options(services)
    unscoped["worker"].pop("test_scope")
    worker = JobProcessor(unscoped, tmp_path / "unscoped")
    with pytest.raises(WorkerError, match="explicit"):
        worker._normalize(command())
    await worker.stop()
    worker, _ = make_worker(services, tmp_path, monkeypatch)
    try:
        with pytest.raises(WorkerError, match="only on-demand"):
            worker._normalize({"targetUri": ":7968/on_demand_inference",
                "resUrl": "/internal/camera-upload/test", "payload": {
                    "cameraId": "camera-fixture", "eventId": "event-fixture", "timestamp": 6000,
                    "videoUrl": command()["payload"]["reqUrl"]}})
        await worker.handle(command())
    finally:
        await worker.stop()
    changed = options(services)
    changed["worker"]["test_scope"]["kind"] = "on_demand"
    with pytest.raises(WorkerError, match="reservation"):
        JobProcessor(changed, tmp_path)
    stored = defaults(tmp_path / "config", "020000000001")
    stored["worker"]["test_scope"] = options(services)["worker"]["test_scope"]
    validate_config(stored)


async def test_model_failure_never_claims_tagging_success_or_restores_permit(services, tmp_path, monkeypatch):
    services.model_status = 503
    worker, _ = make_worker(services, tmp_path, monkeypatch)
    try:
        with pytest.raises(WorkerError, match="Inference"):
            await worker.handle(command())
        assert services.callbacks == []
        with pytest.raises(WorkerError, match="consumed"):
            await worker.submit(command())
    finally:
        await worker.stop()


def device_config():
    return {"runtime": {"mode": "lab", "bind": "127.0.0.1"},
            "controller": {"host": "127.0.0.1"},
            "device": {"mac": "020000000001", "ip": "127.0.0.1",
                       "management_username": "fixture", "management_password": "fixture-only"},
            "worker": {"test_scope": {"kind": "recognizeKeyFrames", "permit_id": "once",
                                       "camera_id": "camera-fixture"}}}


def wire(action, payload, identifier="fixture-request"):
    return encode_message({"id": identifier, "type": "request", "action": action}, payload)


async def test_device_admits_explicit_wrapper_and_sanitizes_bounded_diagnostics(tmp_path):
    calls = []
    async def admit(payload):
        calls.append(deepcopy(payload))
        return {"accepted": True}
    service = DeviceService(device_config(), tmp_path, admit)
    item = command()
    request = wire(item["command"], item["payload"])
    reply = decode_message(await service.handle_message(request))
    assert reply.header["errorCode"] == 0 and calls == [item]
    assert reply.body == item["payload"]
    await service.handle_message(request)
    assert len(calls) == 1
    diagnostics = service.status["control_commands"]
    assert diagnostics["recognizeKeyFrames"] == {"count": 1, "last_result_code": 0}
    secret = "secret-must-not-be-in-health"
    unknown = decode_message(await service.handle_message(wire(secret, {"token": secret}, "unknown")))
    assert unknown.header["errorCode"] == 95
    assert service.status["control_commands"]["unknown"] == {"count": 1, "last_result_code": 95}
    assert secret not in json.dumps(service.status)
    diagnostics["recognizeKeyFrames"]["count"] = 500
    assert service.status["control_commands"]["recognizeKeyFrames"]["count"] == 1


async def test_device_rejects_wrong_scope_and_does_not_echo_admission_errors(tmp_path):
    calls = []
    async def reject(payload):
        calls.append(payload)
        raise WorkerError("secret-provider-detail")
    service = DeviceService(device_config(), tmp_path, reject)
    payload = command()["payload"]
    reply = decode_message(await service.handle_message(wire("recognizeKeyFrames", {**payload, "camera": "other"})))
    assert reply.header["errorCode"] == 95 and calls == []
    reply = decode_message(await service.handle_message(wire("recognizeKeyFrames", payload, "second")))
    assert reply.header["errorCode"] != 0 and reply.body == {}
    assert "secret" not in str(reply.header)
    assert service.status["control_commands"]["recognizeKeyFrames"]["last_result_code"] == 5


def test_summary_advertisement_requires_explicit_opt_in_and_caption_configuration(tmp_path):
    config = device_config()
    decoder = tmp_path / "nonexecuted-decoder-fixture"
    decoder.write_text("synthetic fixture, never executed")
    config["worker"]["ffmpeg_path"] = str(decoder)
    config["inference"] = {"model": "synthetic-test-only"}
    async def admit(payload):
        return {"accepted": True}
    def flags():
        return DeviceService(config, tmp_path, admit).get_info()["featureFlags"]
    assert flags()["supportAiSummary"]["enabled"] is False
    config["device"]["feature_flags"] = {"supportAiSummary": {"enabled": True, "version": "v1"}}
    assert flags()["supportAiSummary"]["enabled"] is True
    assert flags()["supportDeepMode"] is False
    assert flags()["supportRecognizeAnything"]["enabled"] is False
    config["worker"]["callback_mode"] = "disabled"
    assert flags()["supportAiSummary"]["enabled"] is False
    config["worker"]["callback_mode"] = "enabled"
    decoder.unlink()
    assert flags()["supportAiSummary"]["enabled"] is False
