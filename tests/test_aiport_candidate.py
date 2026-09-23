"""The isolated AI Port candidate keeps camera access behind an expiring permit."""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl
import sys
import time

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey.aiport_candidate import CandidateError, CandidateService, load_config
from aikey.aiport_detection import ObjectObservation, RFDetrNanoDetector
from aikey.aiport_smart_settings import SmartPolicy
from aikey.aiport_tracking import TrackChange
from aikey.tls import ensure_identity_certificate


def private_file(path: Path, content: bytes):
    path.write_bytes(content)
    path.chmod(0o600)


def fixture_state(tmp_path):
    cert, _ = ensure_identity_certificate(tmp_path, "2A1100F0A55E")
    private_file(tmp_path / "controller-ca.pem", cert.read_bytes())
    config = {"controller_ip": "192.168.10.1", "device_ip": "192.168.10.20",
              "mac": "2A1100F0A55E", "controller_pin": hashlib.sha256(ssl.PEM_cert_to_DER_cert(
                  cert.read_text())).hexdigest(), "firmware_version": "5.1.12"}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    return config


def test_candidate_requires_private_stable_identity(tmp_path):
    config = fixture_state(tmp_path)
    assert load_config(tmp_path / "config.json") == config
    (tmp_path / "config.json").chmod(0o644)
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


@pytest.mark.parametrize("field,value", [
    ("mac", "001122334455"),
    ("device_ip", "127.0.0.1"),
    ("controller_ip", "8.8.8.8"),
    ("controller_pin", "invalid"),
    ("firmware_version", "unverified build"),
])
def test_candidate_rejects_invalid_identity_and_destination(tmp_path, field, value):
    config = fixture_state(tmp_path)
    config[field] = value
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


def test_diagnostic_hello_requires_short_lived_private_config(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["diagnostic_hello_until"] == config[
        "diagnostic_hello_until"]
    config["diagnostic_hello_until"] = int(time.time()) + 601
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


def test_multi_camera_stream_policy_is_expiring_distinct_and_stream_only(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    streams = [{"camera_mac": mac, "source_ip": "192.168.10.1",
                "ffmpeg_path": sys.executable}
               for mac in ("2A1122334455", "2A1122334456")]
    config["diagnostic_streams"] = streams
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert len(load_config(tmp_path / "config.json")["diagnostic_streams"]) == 2
    config["diagnostic_detector"] = {"checkpoint_path": "/tmp/model.pth"}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="streams only"):
        load_config(tmp_path / "config.json")
    del config["diagnostic_detector"]
    config["diagnostic_streams"] = [streams[0], streams[0]]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="Duplicate multi-camera identity"):
        load_config(tmp_path / "config.json")


def test_multi_camera_event_policy_requires_same_expiry_and_bounded_local_model(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_streams"] = [
        {"camera_mac": mac, "source_ip": "192.168.10.1",
         "ffmpeg_path": sys.executable}
        for mac in ("2A1122334455", "2A1122334456")]
    config["diagnostic_pool_event_until"] = config["diagnostic_hello_until"]
    config["diagnostic_pool_detector"] = {
        "checkpoint_path": "/tmp/nano.pth", "checkpoint_sha256": "a" * 64,
        "threshold": 0.5, "max_frames_per_camera": 3}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["diagnostic_pool_event_until"] == (
        config["diagnostic_hello_until"])
    config["diagnostic_pool_event_until"] -= 1
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="Pool event diagnostic"):
        load_config(tmp_path / "config.json")
    config["diagnostic_pool_event_until"] += 1
    config["diagnostic_pool_detector"]["max_frames_per_camera"] = 121
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="bounded pool detector"):
        load_config(tmp_path / "config.json")


@pytest.mark.parametrize("until", [True, -1, "beyond_window"])
def test_function_fingerprint_probe_rejects_unbounded_config(tmp_path, until):
    config = fixture_state(tmp_path)
    config["diagnostic_function_fingerprints_until"] = (
        int(time.time()) + 3600 if until == "beyond_window" else until)
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


def test_stream_diagnostic_requires_expiring_exact_private_policy(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["diagnostic_stream"] == config[
        "diagnostic_stream"]
    del config["diagnostic_hello_until"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


def test_smart_probe_requires_same_camera_stream_and_expiry(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 90
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    config["diagnostic_smart_probe_until"] = config["diagnostic_hello_until"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["diagnostic_smart_probe_until"] == (
        config["diagnostic_smart_probe_until"])
    config["diagnostic_smart_probe_until"] = config["diagnostic_hello_until"] - 1
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="Smart settings probe"):
        load_config(tmp_path / "config.json")
    config["diagnostic_smart_probe_until"] = True
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="Smart settings probe"):
        load_config(tmp_path / "config.json")
    config["diagnostic_smart_probe_until"] = int(time.time()) + 60
    del config["diagnostic_stream"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="Smart settings probe"):
        load_config(tmp_path / "config.json")


def test_event_probe_requires_model_same_deadline_and_bounded_frames(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 90
    config["diagnostic_smart_probe_until"] = config["diagnostic_hello_until"]
    config["diagnostic_event_until"] = config["diagnostic_hello_until"]
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    config["diagnostic_detector"] = {"checkpoint_path": "/tmp/model.pth",
                                     "checkpoint_sha256": "0" * 64,
                                     "threshold": 0.5, "max_frames": 120}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["diagnostic_event_until"] == (
        config["diagnostic_event_until"])
    config["diagnostic_detector"]["max_frames"] = 121
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")
    config["diagnostic_detector"]["max_frames"] = 2
    config["diagnostic_event_until"] -= 1
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="Smart event probe"):
        load_config(tmp_path / "config.json")


def native_probe_config(tmp_path):
    config = fixture_state(tmp_path)
    until = int(time.time()) + 90
    config.update({
        "diagnostic_hello_until": until,
        "diagnostic_smart_probe_until": until,
        "diagnostic_event_until": until,
        "diagnostic_stream": {"camera_mac": "2A1122334455",
                              "source_ip": "192.168.10.1", "ffmpeg_path": sys.executable},
        "diagnostic_native_event_probe": {
            "camera_mac": "2A1122334455", "nonce": "a" * 32,
            "box": [0.2, 0.2, 0.5, 0.8]},
    })
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    return config


@pytest.mark.parametrize("mutation", ["wrong_camera", "bad_nonce", "bad_box",
                                      "with_detector", "other_class", "no_deadline"])
def test_native_event_probe_rejects_unsafe_configuration(tmp_path, mutation):
    config = native_probe_config(tmp_path)
    assert load_config(tmp_path / "config.json")["diagnostic_native_event_probe"][
        "camera_mac"] == "2A1122334455"
    if mutation == "wrong_camera":
        config["diagnostic_native_event_probe"]["camera_mac"] = "2A1122334456"
    elif mutation == "bad_nonce":
        config["diagnostic_native_event_probe"]["nonce"] = "short"
    elif mutation == "bad_box":
        config["diagnostic_native_event_probe"]["box"] = [0.9, 0.2, 0.5, 0.8]
    elif mutation == "with_detector":
        config["diagnostic_detector"] = {"checkpoint_path": "/tmp/model.pth",
                                         "checkpoint_sha256": "0" * 64,
                                         "threshold": 0.5, "max_frames": 2}
    elif mutation == "other_class":
        config["diagnostic_smart_type"] = "vehicle"
    else:
        del config["diagnostic_event_until"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


@pytest.mark.asyncio
async def test_native_event_probe_sends_one_test_pair_and_cannot_replay_after_restart(
        tmp_path, monkeypatch):
    native_probe_config(tmp_path)
    config = load_config(tmp_path / "config.json")
    policy = {"deviceID": "2A1122334455", "algoVersion": "beta",
              "enableSmartDetect": ["person"], "eventStartMSec": 1000,
              "eventStopMSec": 3000, "zones": {}, "lines": {},
              "reVerificationPolicy": {
                  kind: {"enable": False}
                  for kind in ("person", "vehicle", "animal")}}

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    real_sleep = asyncio.sleep

    async def no_sleep(_seconds):
        await real_sleep(0)

    monkeypatch.setattr("aikey.aiport_candidate.asyncio.sleep", no_sleep)
    command = {"functionName": "ChangeSmartDetectSettings", "messageId": 16,
               "responseExpected": True, "payload": policy}
    for attempt in (1, 2):
        service = CandidateService(config, tmp_path)
        service._params_agreed = True
        service.ingress.list_streams = lambda: [{"active": True}]
        sink = Sink()
        service._current_ws = sink
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        if attempt == 1:
            assert [msg["functionName"] for msg in sink.messages] == [
                "ChangeSmartDetectSettings", "EventSmartDetect", "EventSmartDetect"]
            assert [msg["payload"]["edgeType"] for msg in sink.messages[1:]] == [
                "enter", "leave"]
            assert service.synthetic_probe_claimed == 1
            assert service.smart_events_entered == service.smart_events_left == 1
        else:
            assert [msg["functionName"] for msg in sink.messages] == [
                "ChangeSmartDetectSettings"]
            assert service.synthetic_probe_claimed == 0
        await service.stop()
    marker = tmp_path / (".native-event-probe-" + "a" * 32)
    assert marker.read_text() == "claimed\n"
    assert marker.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_native_event_probe_needs_active_stream_and_supported_policy(tmp_path):
    native_probe_config(tmp_path)
    service = CandidateService(load_config(tmp_path / "config.json"), tmp_path)
    service._params_agreed = True

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    policy = {"deviceID": "2A1122334455", "algoVersion": "beta",
              "enableSmartDetect": ["person"], "eventStartMSec": 1000,
              "eventStopMSec": 3000, "zones": {}, "lines": {},
              "reVerificationPolicy": {
                  kind: {"enable": False}
                  for kind in ("person", "vehicle", "animal")}}
    command = {"functionName": "ChangeSmartDetectSettings", "messageId": 16,
               "responseExpected": True, "payload": policy}
    service.ingress.list_streams = lambda: []
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert service.synthetic_probe_claimed == 0
    assert not list(tmp_path.glob(".native-event-probe-*"))
    service.ingress.list_streams = lambda: [{"active": True}]
    command["messageId"] = 17
    command["payload"]["reVerificationPolicy"]["person"] = {
        "enable": True, "mode": "custom", "minPresenceProbability": 99,
        "maxPresenceProbability": 100}
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert service.synthetic_probe_claimed == 0
    assert not list(tmp_path.glob(".native-event-probe-*"))
    assert not any(msg["functionName"] == "EventSmartDetect" for msg in sink.messages)
    await service.stop()


def recorded_probe_config(tmp_path):
    config = native_probe_config(tmp_path)
    del config["diagnostic_native_event_probe"]
    frame_dir = tmp_path / "recorded-probe"
    frame_dir.mkdir(mode=0o700)
    captured = int(time.time() * 1000) - 3_600_000
    frames = []
    for index, timestamp in enumerate((captured, captured + 2000)):
        frame = frame_dir / f"frame-{index}.jpg"
        private_file(frame, f"recorded-frame-{index}".encode())
        frames.append({"path": str(frame),
                       "sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
                       "captured_ms": timestamp})
    config["diagnostic_recorded_event_probe"] = {
        "camera_mac": "2A1122334455", "nonce": "b" * 32,
        "frames": frames, "checkpoint_path": "/models/nano.pth",
        "checkpoint_sha256": "c" * 64, "threshold": 0.5}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    return config


@pytest.mark.asyncio
async def test_recorded_event_probe_sends_original_time_once(tmp_path, monkeypatch):
    config = recorded_probe_config(tmp_path)
    loaded = load_config(tmp_path / "config.json")
    model_calls = []

    def infer(_probe):
        model_calls.append(True)
        from aikey.aiport_recorded_probe import RecordedPersonTrack
        return RecordedPersonTrack(
            TrackChange("enter", 1, "person", "person", 0.94,
                        (0.2, 0.2, 0.5, 0.8)),
            TrackChange("moving", 1, "person", "person", 0.95,
                        (0.21, 0.2, 0.51, 0.8)))

    monkeypatch.setattr("aikey.aiport_candidate.infer_recorded_person", infer)
    policy = {"deviceID": "2A1122334455", "algoVersion": "beta",
              "enableSmartDetect": ["person"], "eventStartMSec": 1000,
              "eventStopMSec": 3000, "zones": {}, "lines": {},
              "reVerificationPolicy": {
                  kind: {"enable": False}
                  for kind in ("person", "vehicle", "animal")}}

    class Sink:
        def __init__(self):
            self.messages = []
            self.native_event_times = []

        async def send_bytes(self, raw):
            message = json.loads(raw)
            self.messages.append(message)
            if message["functionName"] == "EventSmartDetect":
                self.native_event_times.append(time.monotonic())

    command = {"functionName": "ChangeSmartDetectSettings", "messageId": 16,
               "responseExpected": True, "payload": policy}
    for attempt in (1, 2):
        service = CandidateService(loaded, tmp_path)
        service._params_agreed = True
        service.ingress.list_streams = lambda: [{"active": True}]
        sink = Sink()
        service._current_ws = sink
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        assert service._recorded_probe_task is not None
        await service._recorded_probe_task
        events = [message for message in sink.messages
                  if message["functionName"] == "EventSmartDetect"]
        if attempt == 1:
            assert [event["payload"]["edgeType"] for event in events] == [
                "enter", "moving", "leave"]
            assert [event["payload"]["clockWall"] for event in events] == [
                config["diagnostic_recorded_event_probe"]["frames"][0]["captured_ms"],
                config["diagnostic_recorded_event_probe"]["frames"][1]["captured_ms"],
                config["diagnostic_recorded_event_probe"]["frames"][1]["captured_ms"]
                + 2000]
            assert sink.native_event_times[1] - sink.native_event_times[0] >= 1.8
            assert sink.native_event_times[2] - sink.native_event_times[1] >= 1.8
            assert service.recorded_probe_claimed == 1
            assert service.smart_events_entered == service.smart_events_left == 1
            assert service.smart_events_moved == 1
        else:
            assert events == []
            assert service.recorded_probe_claimed == 0
        await service.stop()
    marker = tmp_path / (".native-event-probe-" + "b" * 32)
    assert marker.stat().st_mode & 0o777 == 0o600
    assert len(model_calls) == 1


@pytest.mark.parametrize("mutation", ["other_camera", "with_synthetic", "no_deadline",
                                      "other_class"])
def test_recorded_event_probe_rejects_unsafe_configuration(tmp_path, mutation):
    config = recorded_probe_config(tmp_path)
    assert load_config(tmp_path / "config.json")["diagnostic_recorded_event_probe"]
    if mutation == "other_camera":
        config["diagnostic_recorded_event_probe"]["camera_mac"] = "2A1122334456"
    elif mutation == "with_synthetic":
        config["diagnostic_native_event_probe"] = {
            "camera_mac": "2A1122334455", "nonce": "a" * 32,
            "box": [0.2, 0.2, 0.5, 0.8]}
    elif mutation == "no_deadline":
        del config["diagnostic_event_until"]
    else:
        config["diagnostic_smart_type"] = "vehicle"
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


def test_expired_stream_diagnostic_restarts_passively(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) - 1
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    loaded = load_config(tmp_path / "config.json")
    service = CandidateService(loaded, tmp_path)
    assert service.ingress is None


def test_detector_policy_requires_bounded_stream_and_pinned_local_weights(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    config["diagnostic_detector"] = {"checkpoint_path": str(tmp_path / "nano.pth"),
                                     "checkpoint_sha256": "a" * 64,
                                     "threshold": 0.5, "max_frames": 2}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["diagnostic_detector"] == config[
        "diagnostic_detector"]
    config["diagnostic_detector"]["max_frames"] = 100
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="bounded detector policy"):
        load_config(tmp_path / "config.json")
    config["diagnostic_detector"]["max_frames"] = 2
    del config["diagnostic_stream"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="bounded detector policy"):
        load_config(tmp_path / "config.json")


@pytest.mark.asyncio
async def test_detector_probe_discards_objects_and_caps_model_calls(tmp_path, monkeypatch):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    config["diagnostic_detector"] = {"checkpoint_path": str(tmp_path / "nano.pth"),
                                     "checkpoint_sha256": "a" * 64,
                                     "threshold": 0.5, "max_frames": 1}
    calls = []

    class Model:
        def detect(self, frame):
            calls.append(frame)
            return (ObjectObservation("person", "person", 0.9,
                                      (0.1, 0.1, 0.4, 0.8)),
                    ObjectObservation("animal", "dog", 0.8,
                                      (0.6, 0.2, 0.9, 0.7)))

    monkeypatch.setattr(RFDetrNanoDetector, "from_checkpoint", lambda *a, **k: Model())
    service = CandidateService(config, tmp_path)
    await service._observe_frame(b"synthetic-frame")
    await service._observe_frame(b"another-frame")
    assert calls == [b"synthetic-frame"]
    health = await service._health(None)
    assert "synthetic-frame" not in health.text
    assert json.loads(health.text)["detector_frames_succeeded"] == 1
    assert json.loads(health.text)["detector_objects_seen"] == 2
    assert json.loads(health.text)["detector_tracks_entered"] == 0
    await service.stop()


@pytest.mark.asyncio
async def test_detector_probe_counts_tracks_without_exposing_objects(tmp_path, monkeypatch):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    config["diagnostic_detector"] = {"checkpoint_path": str(tmp_path / "nano.pth"),
                                     "checkpoint_sha256": "a" * 64,
                                     "threshold": 0.5, "max_frames": 3}

    class Model:
        def detect(self, frame):
            return (ObjectObservation("person", "person", 0.9,
                                      (0.1, 0.1, 0.4, 0.8)),)

    monkeypatch.setattr(RFDetrNanoDetector, "from_checkpoint", lambda *a, **k: Model())
    service = CandidateService(config, tmp_path)
    await service._observe_frame(b"private-frame-1")
    await service._observe_frame(b"private-frame-2")
    await service._observe_frame(b"private-frame-3")
    health = (await service._health(None)).text
    assert json.loads(health)["detector_frames_succeeded"] == 3
    assert json.loads(health)["detector_tracks_entered"] == 1
    assert "private-frame" not in health
    assert "person" not in health
    assert "0.1" not in health
    await service.stop()


@pytest.mark.asyncio
async def test_https_manage_rejects_adoption_and_keeps_only_field_shape(tmp_path):
    config = fixture_state(tmp_path)
    service = CandidateService(config, tmp_path)
    server = TestServer(service.app())
    await server.start_server(ssl=service._server_context())
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as client:
            response = await client.post(str(server.make_url("/api/1.2/manage")), json={
                "username": "sensitive-user", "password": "sensitive-password",
                "mgmt": {"token": "sensitive-token", "hosts": ["controller"]},
            })
            assert response.status == 503
            assert service.manage_requests == 1
            shape = service.last_manage_shape
            assert shape["recognized_fields"] == ["mgmt", "password", "username"]
            assert shape["mgmt_recognized_fields"] == ["hosts", "token"]
            health = await client.get(str(server.make_url("/healthz")))
            public = await health.text()
            assert "sensitive-" not in public
            assert (await response.json())["error"] == "Adoption requires rotated credentials"
    finally:
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("server_protocols", [[], ["secure_transfer"]])
async def test_candidate_uses_pinned_secure_transfer_websocket(tmp_path, server_protocols):
    config = fixture_state(tmp_path)
    config["controller_ip"] = "127.0.0.1"
    connected = asyncio.Event()

    async def websocket(request):
        assert request.headers["Camera-Model"] == "0xa5f1"
        assert request.headers["Camera-MAC"] == config["mac"]
        assert request.headers["Adopted"] == "false"
        ws = web.WebSocketResponse(protocols=server_protocols)
        await ws.prepare(request)
        connected.set()
        await asyncio.sleep(10)
        return ws

    app = web.Application()
    app.router.add_get("/camera/1.0/ws", websocket)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server = TestServer(app)
    await server.start_server(ssl=server_context)
    try:
        service = CandidateService(config, tmp_path, control_port=server.port)
        task = asyncio.create_task(service._connect_loop())
        try:
            await asyncio.wait_for(connected.wait(), timeout=3)
            for _ in range(50):
                if service.upgrades:
                    break
                await asyncio.sleep(0.01)
            assert service.upgrades == 1
            assert service.connected is True
            assert service.last_result == "websocket_101"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_candidate_counts_control_frames_without_exposing_payload(tmp_path):
    config = fixture_state(tmp_path)
    config["controller_ip"] = "127.0.0.1"
    secret = b"synthetic-sensitive-stream-url"
    sent = asyncio.Event()

    async def websocket(request):
        ws = web.WebSocketResponse(protocols=["secure_transfer"])
        await ws.prepare(request)
        await ws.send_bytes(secret)
        await ws.send_str("synthetic-sensitive-token")
        sent.set()
        await asyncio.sleep(1)
        return ws

    app = web.Application()
    app.router.add_get("/camera/1.0/ws", websocket)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server = TestServer(app)
    await server.start_server(ssl=server_context)
    try:
        service = CandidateService(config, tmp_path, control_port=server.port)
        task = asyncio.create_task(service._connect_loop())
        try:
            await asyncio.wait_for(sent.wait(), timeout=3)
            for _ in range(50):
                if service.ws_binary_frames == 1 and service.ws_text_frames == 1:
                    break
                await asyncio.sleep(0.01)
            assert service.ws_binary_frames == 1
            assert service.ws_text_frames == 1
            assert service.ws_last_frame_bytes == len("synthetic-sensitive-token")
            public = await service._health(None)
            assert secret.decode() not in public.text
            assert "synthetic-sensitive-token" not in public.text
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_candidate_records_websocket_close_code_without_reason(tmp_path):
    config = fixture_state(tmp_path)
    config["controller_ip"] = "127.0.0.1"
    close_reason = "synthetic-private-camera-token"

    async def websocket(request):
        ws = web.WebSocketResponse(protocols=["secure_transfer"])
        await ws.prepare(request)
        await ws.close(code=4001, message=close_reason.encode())
        return ws

    app = web.Application()
    app.router.add_get("/camera/1.0/ws", websocket)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server = TestServer(app)
    await server.start_server(ssl=server_context)
    try:
        service = CandidateService(config, tmp_path, control_port=server.port)
        task = asyncio.create_task(service._connect_loop())
        try:
            for _ in range(100):
                if service.websocket_close_codes:
                    break
                await asyncio.sleep(0.01)
            response = await service._health(None)
            health = json.loads(response.text)
            assert health["websocket_close_codes"] == {"4001": 1}
            assert health["last_disconnect_origin"] == "peer_or_transport"
            assert close_reason not in response.text
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_diagnostic_stream_survives_short_control_reconnect(tmp_path):
    config = fixture_state(tmp_path)
    config["controller_ip"] = "127.0.0.1"
    config["diagnostic_hello_until"] = int(time.time()) + 60
    stream_restarted = asyncio.Event()
    stream_lists = []
    connections = 0

    class FakeIngress:
        camera_mac = "2A1122334455"

        def __init__(self):
            self.active = False
            self.closes = 0

        async def control(self, payload):
            self.active = payload["streaming"]
            return {"status": "started" if self.active else "stopped",
                    "usedPoints": 2 if self.active else 0}

        def list_streams(self):
            return [{"deviceID": self.camera_mac, "points": 2}] if self.active else []

        async def close(self):
            self.active = False
            self.closes += 1

    async def websocket(request):
        nonlocal connections
        connections += 1
        number = connections
        restart_ack = False
        ws = web.WebSocketResponse(protocols=["secure_transfer"])
        await ws.prepare(request)
        async for frame in ws:
            if frame.type != aiohttp.WSMsgType.BINARY:
                continue
            message = json.loads(frame.data)
            function = message["functionName"]
            if function == "ubnt_avclient_hello":
                await ws.send_bytes(json.dumps({"functionName": function,
                    "inResponseTo": message["messageId"]}).encode())
                await ws.send_bytes(json.dumps({"functionName":
                    "ubnt_avclient_paramAgreement", "messageId": 20}).encode())
            elif function == "ubnt_avclient_paramAgreement":
                if number == 1:
                    await ws.send_bytes(json.dumps({"functionName": "UiStreamControl",
                        "messageId": 21, "payload": {"streaming": True}}).encode())
                else:
                    await ws.send_bytes(json.dumps({"functionName": "GetStreamList",
                        "messageId": 22, "payload": {}}).encode())
            elif message.get("inResponseTo") == 21:
                assert message["statusCode"] == 0
            elif function == "EventAIPortStatus" and number == 1:
                await ws.close(code=1000)
            elif function == "EventAIPortStatus" and number == 2 and restart_ack:
                stream_restarted.set()
                await ws.close(code=1000)
            elif message.get("inResponseTo") == 22:
                stream_lists.append(message["payload"]["list"])
                await ws.send_bytes(json.dumps({"functionName": "ResetAIPortStreams",
                    "messageId": 23, "payload": {}}).encode())
            elif message.get("inResponseTo") == 23:
                assert message["statusCode"] == 0
                await ws.send_bytes(json.dumps({"functionName": "UiStreamControl",
                    "messageId": 24, "payload": {"streaming": True}}).encode())
            elif message.get("inResponseTo") == 24:
                assert message["statusCode"] == 0
                restart_ack = True
        return ws

    app = web.Application()
    app.router.add_get("/camera/1.0/ws", websocket)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server = TestServer(app)
    await server.start_server(ssl=server_context)
    service = CandidateService(config, tmp_path, control_port=server.port)
    ingress = FakeIngress()
    service.ingress = ingress
    task = asyncio.create_task(service._connect_loop())
    try:
        await asyncio.wait_for(stream_restarted.wait(), timeout=8)
        assert connections >= 2
        assert stream_lists == [[{"deviceID": ingress.camera_mac, "points": 2}]]
        assert ingress.closes == 1
        assert service.stream_reconnects_preserved == 1
        assert service.stream_resets_answered == 1
        assert service.stream_controls_started == 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await service.stop()
        await server.close()


@pytest.mark.asyncio
async def test_stream_reset_rejects_unexpected_payload_without_stopping(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    service = CandidateService(config, tmp_path)
    service._params_agreed = True

    class FakeIngress:
        frame_count = 0
        streams_with_decoded_frames = 0
        total_frames_decoded = 0
        frames_observed = 0
        frames_skipped = 0
        observer_failed = False
        last_decoder_exit_code = None
        last_decoder_stderr_seen = False
        last_decoder_error_markers = ()
        last_decoder_error_terms = ()

        def __init__(self):
            self.closed = False

        def list_streams(self):
            return []

        async def close(self):
            self.closed = True

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    ingress = FakeIngress()
    service.ingress = ingress
    sink = Sink()
    private_value = "synthetic-private-stream-alias"
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ResetAIPortStreams", "messageId": 23,
        "payload": {"unexpected": private_value}}).encode())
    assert not ingress.closed
    assert sink.messages[-1]["statusCode"] == 5
    assert sink.messages[-1]["payload"] == {"description": "invalid_reset_command"}
    assert service.stream_resets_rejected == 1
    assert private_value not in (await service._health(None)).text


@pytest.mark.asyncio
async def test_diagnostic_stream_closes_if_control_does_not_reconnect(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    service = CandidateService(config, tmp_path, disconnect_grace_seconds=0.02)

    class FakeIngress:
        def __init__(self):
            self.active = True
            self.closed = asyncio.Event()

        def list_streams(self):
            return [{"deviceID": "2A1122334455", "points": 2}] if self.active else []

        async def close(self):
            self.active = False
            self.closed.set()

    ingress = FakeIngress()
    service.ingress = ingress
    try:
        await service._schedule_ingress_close()
        assert ingress.active
        await asyncio.wait_for(ingress.closed.wait(), timeout=1)
        assert not ingress.active
        assert service.stream_grace_closures == 1
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_diagnostic_observes_only_fixed_function_names(tmp_path):
    config = fixture_state(tmp_path)
    service = CandidateService(config, tmp_path)
    secret = "synthetic-private-camera-stream-alias"
    await service._handle_diagnostic_frame(None, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "payload": {"secret": secret}
    }).encode())
    await service._handle_diagnostic_frame(None, json.dumps({
        "functionName": "ChangeVideoSettings", "messageId": 10,
        "responseExpected": True, "payload": {"secret": secret}
    }).encode())
    await service._handle_diagnostic_frame(None, json.dumps({
        "functionName": "ChangeIspSettings", "messageId": 11,
        "responseExpected": True, "payload": {"secret": secret}
    }).encode())
    await service._handle_diagnostic_frame(None, json.dumps({
        "functionName": secret, "responseExpected": True,
        "payload": {"secret": secret}
    }).encode())
    await service._handle_diagnostic_frame(None, json.dumps({
        "functionName": secret, "inResponseTo": 11, "payload": {"secret": secret}
    }).encode())
    await service._handle_diagnostic_frame(None, json.dumps({
        "functionName": secret, "payload": {"secret": secret}
    }).encode())
    await service._handle_diagnostic_frame(None, b"not-json-private-token")
    response = await service._health(None)
    health = json.loads(response.text)
    assert health["observed_function_counts"] == {
        "ChangeSmartDetectSettings": 1, "ChangeVideoSettings": 1,
        "ChangeIspSettings": 1}
    assert health["unlisted_function_frames"] == 3
    assert health["unlisted_envelope_counts"] == {
        "request": 1, "response": 1, "other": 1}
    assert health["unlisted_function_fingerprints"] == {}
    assert health["unparsed_binary_frames"] == 1
    assert secret not in response.text
    assert "not-json-private-token" not in response.text


@pytest.mark.asyncio
async def test_function_fingerprint_probe_is_private_bounded_and_expires(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_function_fingerprints_until"] = int(time.time()) + 60
    service = CandidateService(config, tmp_path)
    private_name = "synthetic-private-unknown-command"
    for _ in range(2):
        await service._handle_diagnostic_frame(None, json.dumps({
            "functionName": private_name, "responseExpected": True,
            "payload": {"secret": "synthetic-private-stream-url"},
        }).encode())
    health = json.loads((await service._health(None)).text)
    fingerprint = hashlib.sha256(private_name.encode()).hexdigest()[:16]
    assert health["unlisted_function_fingerprints"] == {fingerprint: 2}
    assert private_name not in json.dumps(health)
    assert "synthetic-private-stream-url" not in json.dumps(health)
    await service._handle_diagnostic_frame(None, b'{"functionName":"\\ud800"}')
    assert json.loads((await service._health(None)).text)[
        "unlisted_function_frames"] == 3
    config["diagnostic_function_fingerprints_until"] = 0
    assert json.loads((await service._health(None)).text)[
        "unlisted_function_fingerprints"] == {}


@pytest.mark.asyncio
async def test_face_database_request_fails_closed_without_private_uri_echo(tmp_path):
    config = fixture_state(tmp_path)
    service = CandidateService(config, tmp_path)
    service._params_agreed = True

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    private_uri = "https://192.0.2.1/internal/face-db/latest?private=secret"
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "UpdateFaceDBRequest", "messageId": 15,
        "responseExpected": True, "payload": {"uri": private_uri},
    }).encode())
    assert sink.messages == [{
        "from": "ubnt_avclient", "to": "UniFiVideo", "responseExpected": False,
        "functionName": "UpdateFaceDBRequest", "messageId": 2,
        "inResponseTo": 15, "statusCode": 501,
        "payload": {"description": "face_database_unavailable"},
    }]
    health = json.loads((await service._health(None)).text)
    assert health["face_db_requests_rejected"] == 1
    assert private_uri not in json.dumps(health)
    assert private_uri not in json.dumps(sink.messages)


@pytest.mark.asyncio
async def test_smart_settings_request_fails_closed_until_detector_exists(tmp_path):
    config = fixture_state(tmp_path)
    service = CandidateService(config, tmp_path)
    service._params_agreed = True

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    private_policy = "synthetic-private-zone-and-recognition-policy"
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 16,
        "responseExpected": True,
        "payload": {"zones": {private_policy: {"objectTypes": ["person"]}},
                    "enableSmartDetect": True},
    }).encode())
    assert sink.messages == [{
        "from": "ubnt_avclient", "to": "UniFiVideo", "responseExpected": False,
        "functionName": "ChangeSmartDetectSettings", "messageId": 2,
        "inResponseTo": 16, "statusCode": 501,
        "payload": {"description": "smart_detection_unavailable"},
    }]
    health = json.loads((await service._health(None)).text)
    assert health["smart_settings_requests_rejected"] == 1
    assert private_policy not in json.dumps(health)
    assert private_policy not in json.dumps(sink.messages)


@pytest.mark.asyncio
async def test_smart_settings_subset_is_counted_but_not_acknowledged(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    service = CandidateService(config, tmp_path)
    service._params_agreed = True

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    payload = {"deviceID": "2A1122334455", "enableSmartDetect": ["person"],
               "eventStartMSec": 1000, "eventStopMSec": 3000, "zones": {},
               "recognitionAccuracy": {"face": 80, "licensePlate": 80}}
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 16,
        "responseExpected": True, "payload": payload,
    }).encode())
    assert sink.messages[0]["statusCode"] == 501
    health = json.loads((await service._health(None)).text)
    assert health["smart_settings_subset_matches"] == 1
    assert health["smart_settings_requests_rejected"] == 1
    assert "eventStartMSec" not in json.dumps(health)
    assert "person" not in json.dumps(health)
    await service.stop()


@pytest.mark.asyncio
async def test_smart_probe_signals_ready_then_records_only_shape(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 90
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    config["diagnostic_smart_probe_until"] = config["diagnostic_hello_until"]
    service = CandidateService(config, tmp_path)
    service._params_agreed = True

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    await service._send_stream_status(sink, streaming=True)
    assert sink.messages[-2]["functionName"] == "EventFeatureFlagsUpdated"
    assert sink.messages[-2]["payload"] == {
        "deviceID": "2A1122334455", "smartDetect": ["person"]}
    assert sink.messages[-1]["payload"]["isSmartDetectReady"] is True
    assert service.smart_feature_probe_events == 1
    private_name = "private-front-door-zone"
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 16,
        "responseExpected": True,
        "payload": {"deviceID": "2A1122334455", "enableSmartDetect": ["person"],
                    "eventStartMSec": 1000, "eventStopMSec": 3000,
                    "zones": {private_name: {"points": [[123, 456]]}}},
    }).encode())
    assert sink.messages[-1]["statusCode"] == 501
    health = json.loads((await service._health(None)).text)
    assert health["smart_settings_probe_requests"] == 1
    assert health["smart_settings_probe_shape"]["zones_count"] == 1
    assert private_name not in json.dumps(health)
    config["diagnostic_smart_probe_until"] = int(time.time()) - 1
    await service._send_stream_status(sink, streaming=True)
    assert sink.messages[-1]["payload"]["isSmartDetectReady"] is False
    assert service.smart_feature_probe_events == 1
    assert json.loads((await service._health(None)).text)["smart_settings_probe_shape"] is None
    await service.stop()


@pytest.mark.asyncio
async def test_multi_camera_candidate_routes_status_without_smart_readiness(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    cameras = ("2A1122334455", "2A1122334456")
    config["diagnostic_streams"] = [
        {"camera_mac": mac, "source_ip": "192.168.10.1",
         "ffmpeg_path": sys.executable} for mac in cameras]
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    active = {}

    async def control(payload):
        mac = payload["deviceID"]
        if payload["streaming"]:
            active[mac] = {"deviceID": mac, "points": 5}
        else:
            active.pop(mac, None)
        return {"status": "started" if payload["streaming"] else "stopped",
                "usedPoints": 5 if payload["streaming"] else 0}

    async def close():
        active.clear()

    service.ingress.control = control
    service.ingress.list_streams = lambda: list(active.values())
    service.ingress.close = close

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    for index, mac in enumerate(cameras, 1):
        command = {"functionName": "UiStreamControl", "messageId": index,
                   "responseExpected": True,
                   "payload": {"deviceID": mac, "streaming": True,
                               "ip": "192.168.10.1", "port": 7447,
                               "uri": f"synthetic-{index}", "width": 3840,
                               "height": 2160, "fps": 15}}
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        assert sink.messages[-2]["statusCode"] == 0
        assert sink.messages[-1]["functionName"] == "EventAIPortStatus"
        assert sink.messages[-1]["payload"]["deviceID"] == mac
        assert sink.messages[-1]["payload"]["isSmartDetectReady"] is False
    health = json.loads((await service._health(None)).text)
    assert health["active_streams"] == 2
    assert health["streams_with_decoded_frames"] == 0
    assert all(mac not in json.dumps(health) for mac in cameras)
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "GetStreamList", "messageId": 3,
        "responseExpected": True, "payload": {},
    }).encode())
    assert len(sink.messages[-1]["payload"]["list"]) == 2
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 5,
        "responseExpected": True,
        "payload": {"deviceID": cameras[0], "enableSmartDetect": ["person"],
                    "eventStartMSec": 1000, "eventStopMSec": 3000},
    }).encode())
    assert sink.messages[-1]["statusCode"] == 501
    assert service.smart_settings_probe_acks == 0
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ResetAIPortStreams", "messageId": 4,
        "responseExpected": True, "payload": {},
    }).encode())
    assert not active
    stopped = [message for message in sink.messages
               if message.get("functionName") == "EventAIPortStatus"
               and message["payload"]["isStreaming"] is False]
    assert {message["payload"]["deviceID"] for message in stopped} == set(cameras)
    await service.stop()


@pytest.mark.asyncio
async def test_pool_event_probe_routes_two_cameras_without_cross_policy(tmp_path, monkeypatch):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_pool_event_until"] = config["diagnostic_hello_until"]
    cameras = ("2A1122334455", "2A1122334456")
    config["diagnostic_streams"] = [
        {"camera_mac": mac, "source_ip": "192.168.10.1",
         "ffmpeg_path": sys.executable} for mac in cameras]
    config["diagnostic_pool_detector"] = {
        "checkpoint_path": "/tmp/nano.pth", "checkpoint_sha256": "a" * 64,
        "threshold": 0.5, "max_frames_per_camera": 3}

    class Model:
        def detect(self, frame):
            return (ObjectObservation("person", "person", 0.9,
                                      (0.2, 0.2, 0.5, 0.8)),)

    monkeypatch.setattr(RFDetrNanoDetector, "from_checkpoint", lambda *a, **k: Model())
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": mac} for mac in cameras]

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    for mac in cameras:
        await service._send_stream_status(sink, streaming=True, camera_mac=mac)
    statuses = [message for message in sink.messages
                if message["functionName"] == "EventAIPortStatus"]
    assert len(statuses) == 2
    assert all(message["payload"]["isSmartDetectReady"] for message in statuses)

    policies = []
    for index, mac in enumerate(cameras, 1):
        command = {"functionName": "ChangeSmartDetectSettings", "messageId": index,
                   "payload": {"deviceID": mac, "algoVersion": "beta",
                               "enableSmartDetect": ["person"],
                               "eventStartMSec": 1000, "eventStopMSec": 3000,
                               "zones": {str(index): {
                                   "coord": [100, 100, 900, 100, 900, 900, 100, 900],
                                   "objectTypes": ["person"],
                                   "triggerAccessTypes": []}}}}
        policies.append(command["payload"])
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        assert sink.messages[-1]["statusCode"] == 0
    assert all(service._camera_engine.has_policy(mac) for mac in cameras)

    stale_generation = service._camera_engine.policy_generation(cameras[0])
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 8,
        "payload": policies[0],
    }).encode())
    assert service._camera_engine.policy_generation(cameras[0]) > stale_generation
    await service._observe_pool_result(cameras[0], (
        ObjectObservation("person", "person", 0.9,
                          (0.2, 0.2, 0.5, 0.8)),), stale_generation)
    assert service.smart_events_entered == 0

    for _ in range(2):
        for mac in cameras:
            await service._observe_pool_frame(mac, b"private-pool-frame")
        await service._inference.join()
    events = [message for message in sink.messages
              if message["functionName"] == "EventSmartDetect"]
    for event in events:
        envelope_time = datetime.fromisoformat(event["timeStamp"].replace("Z", "+00:00"))
        assert envelope_time.tzinfo == timezone.utc
        assert abs(envelope_time.timestamp() * 1000 - event["payload"]["clockWall"]) < 1
    assert [(event["payload"]["deviceID"],
             event["payload"]["descriptors"][0]["zones"])
            for event in events] == [(cameras[0], [1]), (cameras[1], [2])]
    assert len({message["messageId"] for message in sink.messages}) == len(sink.messages)
    await service._observe_pool_frame(cameras[0], b"private-pool-frame")
    await service._inference.join()
    readiness = [message for message in sink.messages
                 if message["functionName"] == "EventAIPortStatus"
                 and message["payload"]["deviceID"] == cameras[0]]
    assert readiness[-1]["payload"]["isSmartDetectReady"] is False
    assert not service._camera_engine.has_policy(cameras[0])
    assert service._camera_engine.has_policy(cameras[1])
    assert service._inference.is_available(cameras[1])
    closed = [message for message in sink.messages
              if message["functionName"] == "EventSmartDetect"
              and message["payload"]["edgeType"] == "leave"]
    assert len(closed) == 1
    assert closed[0]["payload"]["deviceID"] == cameras[0]

    prior_messages = len(sink.messages)
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 3,
        "payload": {"deviceID": cameras[0], "enableSmartDetect": [],
                    "eventStartMSec": 1000, "eventStopMSec": 3000},
    }).encode())
    assert len(sink.messages) == prior_messages + 1
    assert sink.messages[-1]["statusCode"] == 501
    assert not service._camera_engine.has_policy(cameras[0])
    assert service._camera_engine.has_policy(cameras[1])

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 4,
        "payload": {"deviceID": "2A1122334457", "enableSmartDetect": ["person"],
                    "eventStartMSec": 1000, "eventStopMSec": 3000},
    }).encode())
    assert sink.messages[-1]["statusCode"] == 501
    assert service._camera_engine.has_policy(cameras[1])
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "UiStreamControl", "messageId": 5,
        "payload": {"streaming": False, "deviceID": cameras[1]},
    }).encode())
    assert not service._camera_engine.has_policy(cameras[1])
    assert sink.messages[-1]["functionName"] == "EventAIPortStatus"
    assert sink.messages[-1]["payload"]["isSmartDetectReady"] is False
    assert any(message["functionName"] == "EventSmartDetect"
               and message["payload"]["deviceID"] == cameras[1]
               and message["payload"]["edgeType"] == "leave"
               for message in sink.messages)
    health = json.loads((await service._health(None)).text)
    assert health["pool_inference"]["successes"] == 5
    assert health["smart_events_entered"] == 2
    assert "private-pool-frame" not in json.dumps(health)
    await service.stop()


@pytest.mark.asyncio
async def test_event_probe_acks_person_policy_then_sends_one_real_track_pair(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 90
    config["diagnostic_smart_probe_until"] = config["diagnostic_hello_until"]
    config["diagnostic_event_until"] = config["diagnostic_hello_until"]
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    config["diagnostic_detector"] = {"checkpoint_path": "/tmp/model.pth",
                                     "checkpoint_sha256": "0" * 64,
                                     "threshold": 0.5, "max_frames": 8}
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"active": True}]

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    payload = {"deviceID": "2A1122334455", "algoVersion": "beta",
               "enableSmartDetect": ["person"], "eventStartMSec": 1000,
               "eventStopMSec": 3000, "zones": {}, "lines": {},
               "reVerificationPolicy": {
                   kind: {"enable": False}
                   for kind in ("person", "vehicle", "animal")}}
    command = {"functionName": "ChangeSmartDetectSettings", "messageId": 16,
               "responseExpected": True, "payload": payload}
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert sink.messages[-1]["statusCode"] == 0
    track = TrackChange("enter", 1, "person", "person", 0.88,
                        (0.2, 0.2, 0.5, 0.8))
    await service._publish_bounded_smart_changes((track,))
    await service._publish_bounded_smart_changes((track,))
    assert service.smart_events_entered == 1
    assert sink.messages[-1]["functionName"] == "EventSmartDetect"
    assert sink.messages[-1]["payload"]["descriptors"][0]["objectType"] == "person"
    await service._publish_bounded_smart_changes((
        TrackChange("leave", track.track_id, track.kind, track.label,
                    track.score, track.box),))
    assert service.smart_events_left == 1
    assert sink.messages[-1]["payload"]["edgeType"] == "leave"
    command["messageId"] = 17
    command["payload"] = {**payload, "enableSmartDetect": []}
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert sink.messages[-1]["statusCode"] == 501
    await service._publish_bounded_smart_changes((track,))
    assert service.smart_events_entered == 1
    health = json.loads((await service._health(None)).text)
    assert health["smart_settings_probe_acks"] == 1
    assert health["smart_events_entered"] == 1
    assert "0.2" not in json.dumps(health)
    await service.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("command_name", ["UiStreamControl", "ResetAIPortStreams"])
async def test_single_camera_stream_stop_closes_active_native_event(tmp_path, command_name):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 90
    config["diagnostic_event_until"] = config["diagnostic_hello_until"]
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    config["diagnostic_detector"] = {"checkpoint_path": "/tmp/model.pth",
                                     "checkpoint_sha256": "0" * 64,
                                     "threshold": 0.5, "max_frames": 8}
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    streams = [{"deviceID": "2A1122334455", "active": True}]
    service.ingress.list_streams = lambda: streams

    async def stop_stream(_payload):
        streams.clear()
        return {"status": "stopped"}

    async def close_stream():
        streams.clear()

    service.ingress.control = stop_stream
    service.ingress.close = close_stream
    service._smart_policy = SmartPolicy("2A1122334455", frozenset({"person"}),
                                       1000, 3000, (), (), False)

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    track = TrackChange("enter", 1, "person", "person", 0.95,
                        (0.2, 0.2, 0.5, 0.8))
    await service._publish_bounded_smart_changes((track,))
    assert service.smart_events_entered == 1
    payload = ({"streaming": False, "deviceID": "2A1122334455"}
               if command_name == "UiStreamControl" else {})
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": command_name, "messageId": 19, "payload": payload,
    }).encode())
    events = [message for message in sink.messages
              if message["functionName"] == "EventSmartDetect"]
    assert [message["payload"]["edgeType"] for message in events] == [
        "enter", "leave"]
    assert events[1]["payload"]["deviceID"] == "2A1122334455"
    assert service.smart_events_left == 1
    assert service._event_track is None
    assert service._smart_policy is None
    assert next(message for message in sink.messages
                if message["functionName"] == command_name)["statusCode"] == 0
    await service.stop()


@pytest.mark.asyncio
async def test_event_probe_drops_outside_zone_and_uncertain_person_before_tracking(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 90
    config["diagnostic_smart_probe_until"] = config["diagnostic_hello_until"]
    config["diagnostic_event_until"] = config["diagnostic_hello_until"]
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    config["diagnostic_detector"] = {"checkpoint_path": "/tmp/model.pth",
                                     "checkpoint_sha256": "0" * 64,
                                     "threshold": 0.5, "max_frames": 8}
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"active": True}]

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    command = {"functionName": "ChangeSmartDetectSettings", "messageId": 16,
               "responseExpected": True,
               "payload": {"deviceID": "2A1122334455", "algoVersion": "beta",
                           "enableSmartDetect": ["person"],
                           "eventStartMSec": 1000, "eventStopMSec": 3000,
                           "zones": {"7": {
                               "coord": [100, 100, 900, 100, 900, 900, 100, 900],
                               "objectTypes": ["person"], "sensitivity": 50,
                               "triggerLight": True, "triggerAccessTypes": []}},
                           "reVerificationPolicy": {
                               "person": {"enable": True, "mode": "custom",
                                          "minPresenceProbability": 40,
                                          "maxPresenceProbability": 80},
                               "vehicle": {"enable": False},
                               "animal": {"enable": False}}}}
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert sink.messages[-1]["statusCode"] == 0

    class Detector:
        def __init__(self):
            self.observations = iter(((0.95, (0.01, 0.2, 0.5, 0.8)),
                                      (0.6, (0.2, 0.2, 0.5, 0.8)),
                                      (0.9, (0.2, 0.2, 0.5, 0.8)),
                                      (0.91, (0.2, 0.2, 0.5, 0.8))))

        def detect(self, _frame):
            score, box = next(self.observations)
            return (ObjectObservation("person", "person", score, box),)

    service._detector = Detector()
    await service._observe_frame(b"ignored by fake detector")
    assert service.detector_tracks_entered == 0
    assert service.smart_events_entered == 0
    await service._observe_frame(b"ignored by fake detector")
    assert service.detector_tracks_entered == 0
    await service._observe_frame(b"ignored by fake detector")
    assert service.detector_tracks_entered == 0
    await service._observe_frame(b"ignored by fake detector")
    assert service.detector_objects_seen == 4
    assert service.detector_tracks_entered == 1
    assert service.smart_events_entered == 1
    assert sink.messages[-1]["functionName"] == "EventSmartDetect"
    assert sink.messages[-1]["payload"]["descriptors"][0]["confidenceLevel"] == 91
    assert sink.messages[-1]["payload"]["descriptors"][0]["zones"] == [7]
    assert sink.messages[-1]["payload"]["zonesStatus"]["7"]["status"] == "enter"
    service._event_last_moving_at -= 2
    await service._publish_bounded_smart_changes((TrackChange(
        "moving", 1, "person", "person", 0.93,
        (0.21, 0.2, 0.51, 0.8)),))
    assert service.smart_events_moved == 1
    assert sink.messages[-1]["payload"]["edgeType"] == "moving"
    assert sink.messages[-1]["payload"]["zonesStatus"] == {}
    assert sink.messages[-1]["payload"]["descriptors"][0]["confidenceLevel"] == 93
    command["messageId"] = 17
    command["payload"]["enableSmartDetect"] = []
    command["payload"]["zones"] = {}
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert sink.messages[-2]["functionName"] == "EventSmartDetect"
    assert sink.messages[-2]["payload"]["zonesStatus"] == {
        "7": {"status": "leave"}}
    assert sink.messages[-1]["statusCode"] == 501
    assert service.smart_events_left == 1
    assert service._event_track is None
    await service.stop()


@pytest.mark.asyncio
async def test_motion_probe_acks_only_one_camera_during_permit(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_smart_probe_until"] = config["diagnostic_hello_until"]
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    service = CandidateService(config, tmp_path)
    service._params_agreed = True

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    payload = {"algoVersion": "beta", "deviceID": "2A1122334455",
               "enable": True, "eventMaxDurationMSec": 600_000,
               "bgmodel": "default", "lingerEventStartMSec": 1000,
               "lingerEventStopMSec": 3000,
               "zones": {"private-zone": {"points": [[1, 2]]}}}
    command = {"functionName": "ChangeSmartMotionSettings", "messageId": 16,
               "responseExpected": True, "payload": payload}
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert sink.messages[-1]["statusCode"] == 0
    health = json.loads((await service._health(None)).text)
    assert health["smart_motion_probe_requests"] == 1
    assert health["smart_motion_probe_acks"] == 1
    assert health["smart_motion_probe_zones"] == 1
    assert "private-zone" not in json.dumps(health)
    command["messageId"] = 17
    command["payload"] = {**payload, "deviceID": "2A1122334456"}
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert sink.messages[-1]["statusCode"] == 5
    config["diagnostic_smart_probe_until"] = int(time.time()) - 1
    command["messageId"] = 18
    command["payload"] = payload
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert sink.messages[-1]["statusCode"] == 501
    assert service.smart_motion_probe_acks == 1
    await service.stop()


@pytest.mark.asyncio
async def test_hello_diagnostic_answers_minimal_provisioning_queries(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    service = CandidateService(config, tmp_path)
    service._params_agreed = True

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    for message_id, function in ((30, "ChangeVideoSettings"),
                                 (31, "ChangeIspSettings")):
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": function, "messageId": message_id,
            "payload": {}}).encode())
    assert [(m["functionName"], m["inResponseTo"], m["statusCode"])
            for m in sink.messages] == [
                ("ChangeVideoSettings", 30, 0), ("ChangeIspSettings", 31, 0)]
    assert sink.messages[0]["payload"] == {
        "video": {"videoMode": "default"}, "audio": {"volume": 0}}
    assert sink.messages[1]["payload"] == {"irLedLevel": 255}
    assert service.provision_video_replies == 1
    assert service.provision_isp_replies == 1

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "StopService", "messageId": 33,
        "payload": {"service": "ssh"}}).encode())
    assert sink.messages[-1]["statusCode"] == 0
    assert sink.messages[-1]["payload"] == {}
    assert service.ssh_stop_replies == 1

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "StartService", "messageId": 34,
        "payload": {"service": "ssh"}}).encode())
    assert sink.messages[-1]["statusCode"] == 501
    assert sink.messages[-1]["payload"] == {"description": "ssh_unavailable"}
    assert service.ssh_start_rejections == 1

    secret = "synthetic-private-camera-setting"
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeVideoSettings", "messageId": 32,
        "payload": {"unexpected": secret}}).encode())
    assert sink.messages[-1]["statusCode"] == 5
    assert sink.messages[-1]["payload"] == {"description": "unsupported_settings_change"}
    assert secret not in json.dumps(sink.messages)


@pytest.mark.asyncio
async def test_stream_status_reports_only_verified_streaming_readiness(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    private_alias = "synthetic-private-camera-stream-alias"

    class FakeIngress:
        camera_mac = "2A1122334455"

        async def control(self, payload):
            return {"status": "started" if payload["streaming"] else "stopped",
                    "usedPoints": 2 if payload["streaming"] else 0}

    class FakeWebSocket:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    service.ingress = FakeIngress()
    ws = FakeWebSocket()
    for message_id, streaming in ((10, True), (11, False)):
        await service._handle_diagnostic_frame(ws, json.dumps({
            "functionName": "UiStreamControl", "messageId": message_id,
            "payload": {"streaming": streaming, "uri": private_alias},
        }).encode())

    assert [m["functionName"] for m in ws.messages] == [
        "UiStreamControl", "EventAIPortStatus",
        "UiStreamControl", "EventAIPortStatus"]
    assert [m["messageId"] for m in ws.messages] == [2, 3, 4, 5]
    assert [m["inResponseTo"] for m in ws.messages] == [10, 0, 11, 0]
    assert [m["payload"] for m in ws.messages[1::2]] == [
        {"deviceID": "2A1122334455", "isStreaming": streaming,
         "isSmartDetectReady": False, "isAudioEventReady": False}
        for streaming in (True, False)]
    assert service.stream_status_events_sent == 2
    assert private_alias not in json.dumps(ws.messages)


@pytest.mark.asyncio
async def test_bounded_hello_answers_readonly_and_rejects_stream_control(tmp_path):
    config = fixture_state(tmp_path)
    config["controller_ip"] = "127.0.0.1"
    config["diagnostic_hello_until"] = int(time.time()) + 60
    negotiated = asyncio.Event()
    reply_ids = []

    async def websocket(request):
        ws = web.WebSocketResponse(protocols=["secure_transfer"])
        await ws.prepare(request)
        async for frame in ws:
            if frame.type != aiohttp.WSMsgType.BINARY:
                continue
            message = json.loads(frame.data)
            if message["functionName"] == "ubnt_avclient_hello":
                assert message["payload"]["fwVersion"] == "5.1.12"
                assert message["payload"]["ip"] == config["device_ip"]
                assert message["responseExpected"] is True
                await ws.send_bytes(json.dumps({"functionName": "ubnt_avclient_hello",
                    "messageId": 10, "inResponseTo": message["messageId"],
                    "payload": {"controllerVersion": "synthetic"}}).encode())
                await ws.send_bytes(json.dumps({"functionName": "ubnt_avclient_paramAgreement",
                    "messageId": 11, "inResponseTo": 0, "payload": {"enableStatusCodes": True}}).encode())
            elif message["functionName"] == "ubnt_avclient_paramAgreement":
                reply_ids.append(message["messageId"])
                assert message["inResponseTo"] == 11
                assert message["statusCode"] == 0
                assert message["payload"] == {}
                await ws.send_bytes(json.dumps({"functionName": "GetStreamList",
                    "messageId": 12, "inResponseTo": 0, "payload": {}}).encode())
                await ws.send_bytes(json.dumps({"functionName": "UiStreamControl",
                    "messageId": 13, "inResponseTo": 0,
                    "payload": {"uri": "synthetic-private-camera-stream"}}).encode())
            elif message["inResponseTo"] == 12:
                reply_ids.append(message["messageId"])
                assert message["functionName"] == "GetStreamList"
                assert message["statusCode"] == 0
                assert message["payload"] == {"list": []}
            elif message["inResponseTo"] == 13:
                reply_ids.append(message["messageId"])
                assert message["functionName"] == "UiStreamControl"
                assert message["statusCode"] != 0
                assert "synthetic-private-camera-stream" not in json.dumps(message)
                negotiated.set()
        return ws

    app = web.Application()
    app.router.add_get("/camera/1.0/ws", websocket)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server = TestServer(app)
    await server.start_server(ssl=server_context)
    try:
        service = CandidateService(config, tmp_path, control_port=server.port)
        task = asyncio.create_task(service._connect_loop())
        try:
            await asyncio.wait_for(negotiated.wait(), timeout=3)
            assert service.hello_sent == 1
            assert service.param_agreements == 1
            assert service.ws_binary_frames == 4
            assert service.stream_lists_answered == 1
            assert service.stream_controls_rejected == 1
            assert reply_ids == [2, 3, 4]
            public = await service._health(None)
            assert "synthetic-private-camera-stream" not in public.text
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await server.close()
