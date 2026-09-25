"""AI Port candidate keeps camera access behind explicit private policy."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import ssl
import sys
import time
from types import SimpleNamespace

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest
from PIL import Image, ImageDraw

from aikey.aiport_api_detection import ApiObjectDetector
from aikey.aiport_candidate import CandidateError, CandidateService, load_config
from aikey.aiport_detection import DetectionError, ObjectObservation, RFDetrNanoDetector
from aikey.aiport_snapshots import SmartSnapshot
from aikey.aiport_smart_settings import SmartPolicy
from aikey.aiport_camera_engine import CameraEventCandidate
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


def test_live_pool_accepts_explicit_capped_api_provider(tmp_path):
    config = fixture_state(tmp_path)
    config["paired_streams"] = [
        {"camera_mac": mac, "source_ip": "192.168.10.1",
         "ffmpeg_path": sys.executable}
        for mac in ("2A1122334455", "2A1122334456")]
    config["live_pool_detector"] = {
        "inference_backend": "vision_api", "threshold": 0.8,
        "smart_types": ["person"], "max_events_per_hour": 12,
        "max_requests_per_hour": 24,
        "provider_config": {"provider": "openai", "model": "gpt-6-luna",
                            "base_url": "https://api.openai.com/v1",
                            "allow_remote": True, "max_output_tokens": 256,
                            "api_key_file": str(tmp_path / "api-key")}}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["live_pool_detector"] == (
        config["live_pool_detector"])
    config["live_pool_detector"]["smart_types"] = [
        "person", "vehicle", "animal", "package"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["live_pool_detector"]["smart_types"] == [
        "person", "vehicle", "animal", "package"]
    config["live_pool_detector"]["max_requests_per_hour"] = 0
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="API detector"):
        load_config(tmp_path / "config.json")
    # The request cap is an optional cost control: absent or null means off.
    del config["live_pool_detector"]["max_requests_per_hour"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert "max_requests_per_hour" not in load_config(
        tmp_path / "config.json")["live_pool_detector"]
    config["live_pool_detector"]["max_requests_per_hour"] = None
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["live_pool_detector"][
        "max_requests_per_hour"] is None


@pytest.mark.asyncio
async def test_live_api_frames_publish_native_person_enter_and_snapshot_leave_without_network(
        tmp_path, monkeypatch):
    """Exercise the full API-frame-to-native-event path with a fake reply."""
    camera = "2A1122334455"
    config = fixture_state(tmp_path)
    private_file(tmp_path / "api-key", b"synthetic-test-key\n")
    config["paired_streams"] = [{
        "camera_mac": camera, "source_ip": "192.168.10.1",
        "ffmpeg_path": sys.executable}]
    config["live_pool_detector"] = {
        "inference_backend": "vision_api", "threshold": 0.8,
        "smart_types": ["person"], "max_events_per_hour": 12,
        "max_requests_per_hour": 12,
        "provider_config": {"provider": "openai", "model": "gpt-6-luna",
                            "base_url": "https://api.openai.com/v1",
                            "allow_remote": True, "max_output_tokens": 256,
                            "api_key_file": str(tmp_path / "api-key")}}
    calls = []

    def fake_provider(_url, _headers, payload):
        calls.append(payload)
        return {"status": "completed", "output": [{
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": json.dumps({
                "detections": [{"kind": "person", "label": "person",
                                "score": 0.95, "box": [0.2, 0.1, 0.4, 0.8]}]})}],
        }]}

    monkeypatch.setattr(
        "aikey.aiport_candidate.ApiObjectDetector",
        lambda provider, state_dir, **options: ApiObjectDetector(
            provider, state_dir, transport=fake_provider, **options))
    service = CandidateService(config, tmp_path)
    service.adoption.state = {**service.adoption.binding, "phase": "adopted"}
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": camera}]

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    frame_io = BytesIO()
    Image.new("RGB", (640, 360), "gray").save(frame_io, format="JPEG")
    frame = frame_io.getvalue()
    try:
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "ChangeSmartDetectSettings", "messageId": 1,
            "payload": {"deviceID": camera, "enableSmartDetect": ["person"],
                        "eventStartMSec": 1000, "eventStopMSec": 3000,
                        "zones": {}}}).encode())
        assert sink.messages[-1]["statusCode"] == 0
        for _ in range(2):
            await service._observe_pool_frame(camera, frame)
            await service._inference.join()
        enters = [message for message in sink.messages
                  if message.get("functionName") == "EventSmartDetect"
                  and message["payload"]["edgeType"] == "enter"]
        assert len(calls) == 2
        assert len(enters) == 1
        assert enters[0]["payload"]["deviceID"] == camera
        assert enters[0]["payload"]["objectTypes"] == ["person"]
        assert service._inference.camera_snapshot()[0]["observations"]["person"] == 2
        # A motion-gated frame makes no paid request, but still needs to
        # close the native track and release its crop/full-frame references.
        later = time.monotonic() + 21
        monkeypatch.setattr(
            "aikey.aiport_candidate.time",
            SimpleNamespace(monotonic=lambda: later, time=time.time))
        await service._observe_pool_frame(camera, frame)
        await service._inference.join()
        leaves = [message for message in sink.messages
                  if message.get("functionName") == "EventSmartDetect"
                  and message["payload"]["edgeType"] == "leave"]
        assert len(calls) == 2
        assert len(leaves) == 1
        assert leaves[0]["payload"]["objectTypes"] == ["person"]
        assert len(leaves[0]["payload"]["smartDetectSnapshots"]) == 1
        assert service._pool_pending_snapshots
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_snapshot_request_keeps_image_until_matching_one_use_upload(
        tmp_path, monkeypatch):
    config = fixture_state(tmp_path)
    config["paired_stream"] = {"camera_mac": "2A1122334455",
                               "source_ip": "192.168.10.1",
                               "ffmpeg_path": sys.executable}
    service = CandidateService(config, tmp_path)
    service.adoption.state = {**service.adoption.binding, "phase": "adopted"}
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"active": True}]
    filename = "smartdetectsnap_zone_421790000000000.jpg"
    service._pending_snapshot = (SmartSnapshot(
        filename, b"jpeg", {}, filename.replace(".jpg", "_fullfov.jpg"),
        b"full-jpeg", 640, 360),
                                 time.monotonic() + 60)
    service._pending_full_fov = service._pending_snapshot
    uploads = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        class content:
            @staticmethod
            async def read(_limit):
                return b'{"success":true}'

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, url, *, data, allow_redirects):
            uploads.append((url, data, allow_redirects))
            return Response()

    monkeypatch.setattr("aikey.aiport_candidate.aiohttp.ClientSession",
                        lambda **_kwargs: Session())

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    payload = {"what": "smartDetectZoneSnapshot", "filename": "other.jpg",
               "quality": "medium", "timeoutMs": 60_000,
               "uri": ("https://192.168.10.1:6666/internal/camera-upload/"
                       "01234567-89ab-4def-8123-0123456789ab")}
    command = {"functionName": "GetRequest", "messageId": 41,
               "responseExpected": True, "payload": payload}
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert sink.messages[-1]["statusCode"] == 5
    assert service._pending_snapshot is not None
    assert uploads == []
    command["messageId"] = 42
    payload["filename"] = service._pending_full_fov[0].full_fov_filename
    payload["what"] = "smartDetectZoneSnapshotFullFoV"
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert sink.messages[-1]["statusCode"] == 0
    assert service._pending_full_fov is None
    assert service._pending_snapshot is not None
    command["messageId"] = 43
    payload["filename"] = filename
    payload["what"] = "smartDetectZoneSnapshot"
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
    assert sink.messages[-1]["statusCode"] == 0
    assert service._pending_snapshot is None
    assert service.snapshot_uploads == 2
    assert len(uploads) == 2 and all(item[2] is False for item in uploads)
    await service.stop()


@pytest.mark.asyncio
async def test_snapshot_upload_uses_pinned_multipart_payload(tmp_path, monkeypatch):
    config = fixture_state(tmp_path)
    config["paired_stream"] = {"camera_mac": "2A1122334455",
                               "source_ip": "192.168.10.1",
                               "ffmpeg_path": sys.executable}
    service = CandidateService(config, tmp_path)
    service.adoption.state = {**service.adoption.binding, "phase": "adopted"}
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"active": True}]
    filename = "smartdetectsnap_zone_421790000000000.jpg"
    image = BytesIO()
    Image.new("RGB", (64, 64), "blue").save(image, format="JPEG")
    jpeg = image.getvalue()
    service._pending_snapshot = (SmartSnapshot(
        filename, jpeg, {}, filename.replace(".jpg", "_fullfov.jpg"),
        jpeg, 64, 64), time.monotonic() + 60)
    received = []

    async def upload(request):
        assert request.content_type == "multipart/form-data"
        assert request.transport.get_extra_info("peercert") is not None
        reader = await request.multipart()
        field = await reader.next()
        assert field.name == "payload"
        assert field.filename == filename
        assert field.headers["Content-Type"] == "image/jpeg"
        received.append(await field.read())
        assert await reader.next() is None
        return web.json_response({"success": True})

    app = web.Application()
    app.router.add_post("/internal/camera-upload/{token}", upload)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server_context.load_verify_locations(cafile=tmp_path / "device.crt")
    server_context.verify_mode = ssl.CERT_REQUIRED
    server = TestServer(app)
    await server.start_server(ssl=server_context)
    url = str(server.make_url("/internal/camera-upload/" +
                              "01234567-89ab-4def-8123-0123456789ab"))
    monkeypatch.setattr("aikey.aiport_candidate.validated_upload_url",
                        lambda *_args, **_kwargs: url)

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    command = {"functionName": "GetRequest", "messageId": 44,
               "responseExpected": True,
               "payload": {"what": "smartDetectZoneSnapshot", "filename": filename}}
    try:
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        assert sink.messages[-1]["statusCode"] == 0
        assert received == [jpeg]
        assert service.snapshot_uploads == 1
    finally:
        await server.close()
        await service.stop()


def test_snapshot_cleanup_removes_expired_single_camera_media(tmp_path):
    config = fixture_state(tmp_path)
    service = CandidateService(config, tmp_path)
    snapshot = SmartSnapshot("crop.jpg", b"crop", {}, "full.jpg", b"full", 64, 64)
    expired = time.monotonic() - 1
    service._event_snapshot = snapshot
    service._event_snapshot_expires = expired
    service._pending_snapshot = (snapshot, expired)
    service._pending_full_fov = (snapshot, expired)
    service._prune_snapshots()
    assert service._event_snapshot is None
    assert service._pending_snapshot is None
    assert service._pending_full_fov is None


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


@pytest.mark.asyncio
async def test_adopted_paired_stream_accepts_control_without_diagnostic_expiry(tmp_path):
    config = fixture_state(tmp_path)
    config["paired_stream"] = {"camera_mac": "2A1122334455",
                               "source_ip": "192.168.10.1",
                               "ffmpeg_path": sys.executable}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    loaded = load_config(tmp_path / "config.json")
    service = CandidateService(loaded, tmp_path)
    assert service.ingress is not None
    assert service._tracker is None
    assert service._inference is None
    service._params_agreed = True

    class FakeIngress:
        camera_mac = "2A1122334455"

        async def control(self, payload):
            assert payload == {"streaming": True}
            return {"status": "started", "usedPoints": 1}

    class FakeWebSocket:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    service.ingress = FakeIngress()
    ws = FakeWebSocket()
    await service._handle_diagnostic_frame(ws, json.dumps({
        "functionName": "UiStreamControl", "messageId": 10,
        "payload": {"streaming": True}}).encode())
    assert [message["functionName"] for message in ws.messages] == [
        "UiStreamControl", "EventAIPortStatus"]
    assert ws.messages[0]["statusCode"] == 0
    assert ws.messages[1]["payload"] == {
        "deviceID": "2A1122334455", "isStreaming": True,
        "isSmartDetectReady": False, "isAudioEventReady": False}
    assert service.stream_controls_started == 1
    assert service.stream_controls_rejected == 0


def test_paired_stream_rejects_other_sources_and_diagnostic_overlap(tmp_path):
    config = fixture_state(tmp_path)
    config["paired_stream"] = {"camera_mac": "2A1122334455",
                               "source_ip": "8.8.8.8",
                               "ffmpeg_path": sys.executable}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")
    config["paired_stream"]["source_ip"] = "192.168.10.1"
    config["diagnostic_hello_until"] = int(time.time()) + 60
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")
    del config["diagnostic_hello_until"]
    config["diagnostic_smart_probe_until"] = int(time.time()) + 60
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


def test_paired_stream_accepts_bounded_live_detector_without_hello_expiry(tmp_path):
    config = fixture_state(tmp_path)
    config["paired_stream"] = {"camera_mac": "2A1122334455",
                               "source_ip": "192.168.10.1",
                               "ffmpeg_path": sys.executable}
    until = int(time.time()) + 60
    config["diagnostic_smart_probe_until"] = until
    config["diagnostic_event_until"] = until
    config["diagnostic_detector"] = {
        "checkpoint_path": str(tmp_path / "model.pt"),
        "checkpoint_sha256": "a" * 64, "threshold": 0.3, "max_frames": 600}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    loaded = load_config(tmp_path / "config.json")
    service = CandidateService(loaded, tmp_path)
    assert service.ingress is not None
    assert service.ingress.frame_observer is not None
    assert service._tracker is not None
    assert service._inference is None

    config["diagnostic_detector"]["max_frames"] = 601
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="bounded detector"):
        load_config(tmp_path / "config.json")


def test_live_detector_requires_exact_paired_camera_and_valid_budget(tmp_path):
    config = fixture_state(tmp_path)
    detector = {"checkpoint_path": str(tmp_path / "model.pth"),
                "checkpoint_sha256": "a" * 64, "threshold": 0.3,
                "smart_type": "person", "max_events_per_hour": 2}
    config["live_detector"] = detector
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")

    config["paired_stream"] = {"camera_mac": "2A1122334455",
                               "source_ip": "192.168.10.1",
                               "ffmpeg_path": sys.executable}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["live_detector"] == detector

    config["diagnostic_event_until"] = int(time.time()) + 60
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")
    del config["diagnostic_event_until"]
    detector["max_events_per_hour"] = 0
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="live detector"):
        load_config(tmp_path / "config.json")


def test_live_pool_requires_unique_allowlisted_cameras_and_local_detector(tmp_path):
    config = fixture_state(tmp_path)
    cameras = ("2A1122334455", "2A1122334456")
    config["paired_streams"] = [
        {"camera_mac": mac, "source_ip": "192.168.10.1",
         "ffmpeg_path": sys.executable} for mac in cameras]
    config["live_pool_detector"] = {
        "checkpoint_path": str(tmp_path / "model.pth"),
        "checkpoint_sha256": "a" * 64, "threshold": 0.3,
        "smart_types": ["person", "vehicle"], "max_events_per_hour": 120}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    loaded = load_config(tmp_path / "config.json")
    assert [item["camera_mac"] for item in loaded["paired_streams"]] == list(cameras)

    config["paired_streams"][1]["camera_mac"] = cameras[0]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="Duplicate live camera"):
        load_config(tmp_path / "config.json")
    config["paired_streams"][1]["camera_mac"] = cameras[1]
    config["diagnostic_hello_until"] = int(time.time()) + 60
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="Invalid live camera pool"):
        load_config(tmp_path / "config.json")
    del config["diagnostic_hello_until"]
    config["live_pool_detector"]["smart_types"] = ["person", "person"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="live pool detector"):
        load_config(tmp_path / "config.json")


def test_live_pool_accepts_explicit_pinned_onnx_provider(tmp_path):
    config = fixture_state(tmp_path)
    config["paired_streams"] = [
        {"camera_mac": mac, "source_ip": "192.168.10.1",
         "ffmpeg_path": sys.executable}
        for mac in ("2A1122334455", "2A1122334456")]
    config["live_pool_detector"] = {
        "inference_backend": "onnx_openvino_gpu",
        "model_path": str(tmp_path / "model.onnx"), "model_sha256": "a" * 64,
        "threshold": 0.3, "smart_types": ["person"], "max_events_per_hour": 120}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["live_pool_detector"] == (
        config["live_pool_detector"])
    config["live_pool_detector"]["smart_types"] = ["package"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="live pool detector"):
        load_config(tmp_path / "config.json")
    config["live_pool_detector"]["smart_types"] = ["person"]
    config["live_pool_detector"]["inference_backend"] = "unknown"
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="live pool detector"):
        load_config(tmp_path / "config.json")
    config["live_pool_detector"]["inference_backend"] = []
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="live pool detector"):
        load_config(tmp_path / "config.json")


@pytest.mark.asyncio
async def test_live_detector_runs_past_probe_limit_and_enforces_hourly_event_budget(
        tmp_path):
    config = fixture_state(tmp_path)
    config["paired_stream"] = {"camera_mac": "2A1122334455",
                               "source_ip": "192.168.10.1",
                               "ffmpeg_path": sys.executable}
    config["live_detector"] = {"checkpoint_path": str(tmp_path / "model.pth"),
                               "checkpoint_sha256": "a" * 64, "threshold": 0.3,
                               "smart_type": "person", "max_events_per_hour": 1}
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": "2A1122334455"}]

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    await service._send_stream_status(sink, streaming=True)
    assert sink.messages[-2]["payload"]["smartDetect"] == ["person"]
    assert sink.messages[-1]["payload"]["isSmartDetectReady"] is True

    settings = {"deviceID": "2A1122334455", "algoVersion": "beta",
                "enableSmartDetect": ["person"], "eventStartMSec": 1000,
                "eventStopMSec": 3000, "zones": {}, "lines": {},
                "reVerificationPolicy": {kind: {"enable": False}
                                         for kind in ("person", "vehicle", "animal")}}
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 16,
        "responseExpected": True, "payload": settings}).encode())
    assert sink.messages[-1]["statusCode"] == 0

    class EmptyDetector:
        @staticmethod
        def detect(_frame):
            return ()

    service._detector = EmptyDetector()
    for _ in range(601):
        await service._observe_frame(b"test-frame")
    assert service.detector_frames_succeeded == 601
    assert service.detector_error is None

    enter = TrackChange("enter", 1, "person", "person", 0.9,
                        (0.2, 0.2, 0.5, 0.8))
    leave = replace(enter, edge="leave")
    await service._publish_bounded_smart_changes((enter, leave))
    assert service.smart_events_entered == 1
    await service._publish_bounded_smart_changes((replace(enter, track_id=2),))
    assert service.smart_events_entered == 1
    service._live_event_times[0] -= 3601
    service._event_budget.clock_ns = lambda: time.time_ns() + 3_600_000_000_000
    await service._publish_bounded_smart_changes((replace(enter, track_id=3),))
    assert service.smart_events_entered == 2
    health = json.loads((await service._health(None)).text)
    assert health["detection_mode"] == "live"
    assert health["live_event_budget_remaining"] == 0
    assert health["live_event_budget_healthy"] is True
    assert (tmp_path / "aiport-event-budget.json").is_file()
    await service.stop()


@pytest.mark.asyncio
async def test_live_detector_failure_revokes_readiness_without_unpairing(tmp_path):
    config = fixture_state(tmp_path)
    config["paired_stream"] = {"camera_mac": "2A1122334455",
                               "source_ip": "192.168.10.1",
                               "ffmpeg_path": sys.executable}
    config["live_detector"] = {"checkpoint_path": str(tmp_path / "model.pth"),
                               "checkpoint_sha256": "a" * 64, "threshold": 0.3,
                               "smart_type": "person", "max_events_per_hour": 1}
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": "2A1122334455"}]

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    settings = {"deviceID": "2A1122334455", "algoVersion": "beta",
                "enableSmartDetect": ["person"], "eventStartMSec": 1000,
                "eventStopMSec": 3000, "zones": {}, "lines": {},
                "reVerificationPolicy": {kind: {"enable": False}
                                         for kind in ("person", "vehicle", "animal")}}
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 16,
        "responseExpected": True, "payload": settings}).encode())
    assert sink.messages[-1]["statusCode"] == 0

    class FailingDetector:
        @staticmethod
        def detect(_frame):
            raise DetectionError("detector_inference_failed")

    service._detector = FailingDetector()
    await service._observe_frame(b"test-frame")
    assert service.detector_error == "detector_inference_failed"
    assert service._smart_policy is None
    assert sink.messages[-1]["payload"]["isSmartDetectReady"] is False
    assert service.ingress is not None
    await service.stop()


@pytest.mark.asyncio
async def test_paired_event_expiry_revokes_ai_without_disconnecting_stream(tmp_path):
    config = fixture_state(tmp_path)
    config["paired_stream"] = {"camera_mac": "2A1122334455",
                               "source_ip": "192.168.10.1",
                               "ffmpeg_path": sys.executable}
    config["diagnostic_smart_probe_until"] = int(time.time())
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    service._smart_policy = object()
    service.ingress.list_streams = lambda: [{"deviceID": "2A1122334455"}]

    class FakeWebSocket:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

        async def close(self):
            raise AssertionError("pairing control must remain connected")

    ws = FakeWebSocket()
    service._current_ws = ws
    await service._expire_paired_event_probe(ws, int(time.time()))
    assert service._smart_policy is None
    assert ws.messages[-1]["functionName"] == "EventAIPortStatus"
    assert ws.messages[-1]["payload"] == {
        "deviceID": "2A1122334455", "isStreaming": True,
        "isSmartDetectReady": False, "isAudioEventReady": False}


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


@pytest.mark.parametrize("kinds", [
    [], ["person", "person"], ["person", "vehicle", "animal", "person"],
    ["person", "face"], "person", [True],
])
def test_pool_smart_types_reject_invalid_or_duplicate_classes(tmp_path, kinds):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_pool_event_until"] = config["diagnostic_hello_until"]
    config["diagnostic_streams"] = [
        {"camera_mac": mac, "source_ip": "192.168.10.1",
         "ffmpeg_path": sys.executable}
        for mac in ("2A1122334455", "2A1122334456")]
    config["diagnostic_pool_detector"] = {
        "checkpoint_path": "/tmp/nano.pth", "checkpoint_sha256": "a" * 64,
        "threshold": 0.5, "max_frames_per_camera": 3}
    config["diagnostic_pool_smart_types"] = kinds
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="Pool smart types"):
        load_config(tmp_path / "config.json")


def test_pool_smart_types_require_pool_event_permit_without_single_type(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_pool_event_until"] = config["diagnostic_hello_until"]
    config["diagnostic_streams"] = [
        {"camera_mac": mac, "source_ip": "192.168.10.1",
         "ffmpeg_path": sys.executable}
        for mac in ("2A1122334455", "2A1122334456")]
    config["diagnostic_pool_detector"] = {
        "checkpoint_path": "/tmp/nano.pth", "checkpoint_sha256": "a" * 64,
        "threshold": 0.5, "max_frames_per_camera": 3}
    config["diagnostic_pool_smart_types"] = ["person", "vehicle", "animal"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["diagnostic_pool_smart_types"] == [
        "person", "vehicle", "animal"]
    del config["diagnostic_pool_event_until"]
    del config["diagnostic_pool_detector"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="Pool smart types"):
        load_config(tmp_path / "config.json")
    config["diagnostic_pool_event_until"] = config["diagnostic_hello_until"]
    config["diagnostic_pool_detector"] = {
        "checkpoint_path": "/tmp/nano.pth", "checkpoint_sha256": "a" * 64,
        "threshold": 0.5, "max_frames_per_camera": 3}
    config["diagnostic_smart_type"] = "person"
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError, match="Pool smart types"):
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


def test_recorded_probe_can_use_existing_paired_stream(tmp_path):
    config = recorded_probe_config(tmp_path)
    stream = config.pop("diagnostic_stream")
    config.pop("diagnostic_hello_until")
    config["paired_stream"] = stream
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    loaded = load_config(tmp_path / "config.json")
    assert loaded["paired_stream"]["camera_mac"] == stream["camera_mac"]
    assert loaded["diagnostic_recorded_event_probe"]["camera_mac"] == stream["camera_mac"]


@pytest.mark.asyncio
async def test_recorded_event_probe_sends_original_time_once(tmp_path, monkeypatch):
    config = recorded_probe_config(tmp_path)
    loaded = load_config(tmp_path / "config.json")
    model_calls = []

    def infer(_probe):
        model_calls.append(True)
        # Protect may refresh an equivalent policy while inference runs.
        service._smart_policy = replace(service._smart_policy)
        from aikey.aiport_recorded_probe import RecordedPersonTrack
        return RecordedPersonTrack(
            TrackChange("enter", 1, "person", "person", 0.94,
                        (0.2, 0.2, 0.5, 0.8)),
            TrackChange("moving", 1, "person", "person", 0.95,
                        (0.21, 0.2, 0.51, 0.8)))

    monkeypatch.setattr("aikey.aiport_candidate.infer_recorded_person", infer)
    policy = {"deviceID": "2A1122334455", "algoVersion": "beta",
              "enableSmartDetect": ["person"], "eventStartMSec": 1000,
              "eventStopMSec": 3000,
              "zones": {"7": {"coord": [100, 100, 900, 100, 900, 900,
                                      100, 900], "objectTypes": ["person"],
                              "triggerAccessTypes": []}}, "lines": {},
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
            assert events[0]["payload"]["descriptors"][0]["zones"] == [7]
            assert events[0]["payload"]["zonesStatus"] == {
                "7": {"status": "enter", "level": 94}}
            assert [event["payload"]["objectTypes"] for event in events] == [
                ["person"], ["person"], ["person"]]
            assert [event["payload"]["descriptors"][0]["trackerID"]
                    for event in events] == [1, 1, 1]
            assert events[2]["payload"]["descriptors"][0]["coord"] == (
                events[1]["payload"]["descriptors"][0]["coord"])
            assert events[2]["payload"]["descriptors"][0]["zones"] == [7]
            assert [event["payload"]["clockWall"] for event in events] == [
                config["diagnostic_recorded_event_probe"]["frames"][0]["captured_ms"],
                config["diagnostic_recorded_event_probe"]["frames"][1]["captured_ms"],
                config["diagnostic_recorded_event_probe"]["frames"][1]["captured_ms"]
                + 2000]
            assert [event["payload"]["descriptors"][0]["firstShownTimeMs"]
                    for event in events] == [
                config["diagnostic_recorded_event_probe"]["frames"][0]["captured_ms"]
            ] * 3
            assert sink.native_event_times[1] - sink.native_event_times[0] >= 1.8
            assert sink.native_event_times[2] - sink.native_event_times[1] >= 1.8
            assert service.recorded_probe_claimed == 1
            assert service.recorded_probe_attempts == 1
            assert service.recorded_probe_phase == "leave_sent"
            assert service.recorded_probe_zone_status == "matched"
            assert service.smart_events_entered == service.smart_events_left == 1
            assert service.smart_events_moved == 1
        else:
            assert events == []
            assert service.recorded_probe_claimed == 0
            assert service.recorded_probe_phase == "already_claimed"
        await service.stop()
    marker = tmp_path / (".native-event-probe-" + "b" * 32)
    assert marker.stat().st_mode & 0o777 == 0o600
    assert len(model_calls) == 1


@pytest.mark.asyncio
async def test_recorded_event_probe_does_not_claim_without_person_zone(
        tmp_path, monkeypatch):
    recorded_probe_config(tmp_path)
    from aikey.aiport_recorded_probe import RecordedPersonTrack

    monkeypatch.setattr(
        "aikey.aiport_candidate.infer_recorded_person",
        lambda _probe: RecordedPersonTrack(
            TrackChange("enter", 1, "person", "person", 0.94,
                        (0.2, 0.2, 0.5, 0.8)),
            TrackChange("moving", 1, "person", "person", 0.95,
                        (0.21, 0.2, 0.51, 0.8))))
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

    service = CandidateService(load_config(tmp_path / "config.json"), tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"active": True}]
    sink = Sink()
    service._current_ws = sink
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 16,
        "responseExpected": True, "payload": policy}).encode())
    assert service._recorded_probe_task is not None
    await service._recorded_probe_task
    assert not any(message["functionName"] == "EventSmartDetect"
                   for message in sink.messages)
    assert service.recorded_probe_claimed == 0
    assert service.recorded_probe_qualified == 0
    assert service.recorded_probe_phase == "zone_gate"
    assert service.recorded_probe_zone_status == "not_configured"
    assert not list(tmp_path.glob(".native-event-probe-*"))
    await service.stop()


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
        restart_attempts = 0
        restart_successes = 0
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
    # Protect's identical re-sends after connecting keep the policy, its
    # generation and any in-flight startup sample.
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 7,
        "payload": policies[0],
    }).encode())
    assert sink.messages[-1]["statusCode"] == 0
    assert service._camera_engine.policy_generation(cameras[0]) == stale_generation
    assert service.smart_settings_repeats == 1
    # Protect's separate plate-reader flag carries no policy: acknowledge it
    # without touching the policy; count requests for plates that are not read.
    rejected = service.smart_settings_requests_rejected
    for message_id, lpr, status in ((20, False, 0), (21, True, 0), (22, "yes", 501)):
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "ChangeSmartDetectSettings", "messageId": message_id,
            "payload": {"deviceID": cameras[0], "isLprCamera": lpr}}).encode())
        assert sink.messages[-1]["statusCode"] == status
    assert service.smart_settings_lpr_acks == 2
    assert service.smart_settings_lpr_requested == 1
    assert service.smart_settings_rejection_reasons == {"invalid_lpr_flag": 1}
    assert service.smart_settings_requests_rejected == rejected + 1
    assert service._camera_engine.has_policy(cameras[0])
    assert service._camera_engine.policy_generation(cameras[0]) == stale_generation
    # A changed policy makes results of older frames stale.
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 8,
        "payload": {**policies[0], "eventStopMSec": 4000},
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
    camera_health = json.loads((await service._health(None)).text)["pool_cameras"]
    assert [camera["policy_rejection"] for camera in camera_health] == [
        "disabled_policy", None]

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 6,
        "payload": {"deviceID": cameras[0], "enableSmartDetect": ["person"],
                    "eventStartMSec": 1000, "eventStopMSec": 3000,
                    "enableTamperDetection": True},
    }).encode())
    camera_health = json.loads((await service._health(None)).text)["pool_cameras"]
    assert camera_health[0]["policy_rejection"] == "unsupported_smart_feature:tamper"
    assert camera_health[1]["policy_rejection"] is None

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 7,
        "payload": {"deviceID": cameras[0], "enableSmartDetect": ["person"],
                    "eventStartMSec": 1000, "eventStopMSec": 3000,
                    "secondLensZones": {"7": {
                        "coord": [0, 0, 1000, 0, 1000, 1000],
                        "objectTypes": ["person"]}}},
    }).encode())
    health_text = (await service._health(None)).text
    camera_health = json.loads(health_text)["pool_cameras"]
    assert camera_health[0]["policy_rejection"] == (
        "unsupported_smart_feature:regions:secondLensZones")
    assert camera_health[0]["secondary_lens_shape"] == {
        "zone_count": 1, "schema_valid": True,
        "animal_selected": False, "package_selected": False,
        "person_selected": True, "vehicle_selected": False}
    assert '"coord"' not in health_text

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 9,
        "payload": {"deviceID": cameras[1], "enableSmartDetect": ["person"],
                    "eventStartMSec": 1000, "eventStopMSec": 3000,
                    "secondLensZones": {"7": {
                        "coord": [0, 0, 1000, 0, 1000, 1000],
                        "objectTypes": []}}},
    }).encode())
    camera_health = json.loads((await service._health(None)).text)["pool_cameras"]
    assert sink.messages[-1]["statusCode"] == 0
    assert camera_health[1]["policy_enabled"] is True
    assert camera_health[1]["policy_rejection"] is None
    assert camera_health[1]["secondary_lens_shape"] is None

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 10,
        "payload": {"deviceID": cameras[1], "enableSmartDetect": ["person"],
                    "eventStartMSec": 1000, "eventStopMSec": 3000,
                    "recognitionAccuracy": {"face": "private-setting",
                                            "licensePlate": None}},
    }).encode())
    health_text = (await service._health(None)).text
    camera_health = json.loads(health_text)["pool_cameras"]
    assert sink.messages[-1]["statusCode"] == 501
    assert camera_health[1]["policy_rejection"] == (
        "invalid_smart_settings:recognition_accuracy")
    assert camera_health[1]["recognition_accuracy_shape"] == {
        "object": True, "key_count": 2, "unknown_key_count": 0,
        "face": "other_string", "licensePlate": "null"}
    assert "private-setting" not in health_text

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 11,
        "payload": {"deviceID": cameras[1], "enableSmartDetect": ["person"],
                    "eventStartMSec": 1000, "eventStopMSec": 3000},
    }).encode())
    camera_health = json.loads((await service._health(None)).text)["pool_cameras"]
    assert camera_health[1]["recognition_accuracy_shape"] is None
    assert camera_health[1]["policy_enabled"] is True

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 12,
        "payload": {"deviceID": cameras[0], "enableSmartDetect": ["person"],
                    "eventStartMSec": 1000, "eventStopMSec": 3000,
                    "excludeZones": {"9": {
                        "coord": [0, 0], "objectTypes": ["person"]}}},
    }).encode())
    camera_health = json.loads((await service._health(None)).text)["pool_cameras"]
    assert camera_health[0]["policy_rejection"] == "invalid_exclude_zone"
    assert camera_health[1]["policy_enabled"] is True

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
    assert len(health["pool_cameras"]) == 2
    assert [item["index"] for item in health["pool_cameras"]] == [0, 1]
    assert all("observations" in item and "events_entered" in item
               and "score_eligible_observations" in item
               and "eligible_frames" in item
               and "stream_restart_failures" in item
               for item in health["pool_cameras"])
    assert cameras[0] not in json.dumps(health["pool_cameras"])
    assert health["smart_events_entered"] == 2
    assert "private-pool-frame" not in json.dumps(health)
    await service.stop()


@pytest.mark.asyncio
async def test_pool_multiclass_probe_keeps_camera_policies_and_tracks_separate(
        tmp_path, monkeypatch):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_pool_event_until"] = config["diagnostic_hello_until"]
    config["diagnostic_pool_smart_types"] = ["person", "vehicle", "animal"]
    cameras = ("2A1122334455", "2A1122334456")
    config["diagnostic_streams"] = [
        {"camera_mac": mac, "source_ip": "192.168.10.1",
         "ffmpeg_path": sys.executable} for mac in cameras]
    config["diagnostic_pool_detector"] = {
        "checkpoint_path": "/tmp/nano.pth", "checkpoint_sha256": "a" * 64,
        "threshold": 0.5, "max_frames_per_camera": 3}

    observations = (
        ObjectObservation("person", "person", 0.91, (0.1, 0.1, 0.3, 0.7)),
        ObjectObservation("vehicle", "car", 0.92, (0.35, 0.2, 0.65, 0.7)),
        ObjectObservation("animal", "dog", 0.93, (0.7, 0.2, 0.9, 0.7)),
    )

    class Model:
        def detect(self, frame):
            return observations

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
    flags = [message for message in sink.messages
             if message["functionName"] == "EventFeatureFlagsUpdated"]
    assert len(flags) == 2
    assert all(message["payload"]["smartDetect"] == [
        "person", "vehicle", "animal"] for message in flags)

    for index, (mac, kinds) in enumerate(zip(cameras, (
            ["person", "vehicle", "animal"], ["person"])), 1):
        payload = {"deviceID": mac, "enableSmartDetect": kinds,
                   "eventStartMSec": 1000, "eventStopMSec": 3000}
        if index == 1:
            payload["enableSmartDetect"] = []
            payload["zones"] = {
                str(zone_id): {
                    "coord": [50, 50, 950, 50, 950, 950, 50, 950],
                    "objectTypes": [kind], "triggerAccessTypes": []}
                for zone_id, kind in enumerate(kinds, 7)}
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "ChangeSmartDetectSettings", "messageId": index,
            "payload": payload,
        }).encode())
        assert sink.messages[-1]["statusCode"] == 0

    for _ in range(2):
        for mac in cameras:
            await service._observe_pool_frame(mac, b"bounded-test-frame")
        await service._inference.join()
    enters = [message["payload"] for message in sink.messages
              if message["functionName"] == "EventSmartDetect"
              and message["payload"]["edgeType"] == "enter"]
    # Protect keeps one smart event per camera, so the first object opens
    # it and later objects join through moving updates with all objects.
    assert [(event["deviceID"], event["objectTypes"]) for event in enters] == [
        (cameras[0], ["person"]), (cameras[1], ["person"])]
    assert [event["descriptors"][0]["zones"] for event in enters] == [[7], []]
    joined = [message["payload"] for message in sink.messages
              if message["functionName"] == "EventSmartDetect"
              and message["payload"]["edgeType"] == "moving"
              and message["payload"]["deviceID"] == cameras[0]]
    assert joined[-1]["objectTypes"] == ["person", "vehicle", "animal"]
    assert [d["zones"] for d in joined[-1]["descriptors"]] == [[7], [8], [9]]
    assert len({d["trackerID"] for d in joined[-1]["descriptors"]}) == 3
    assert service.smart_events_entered == 2
    assert service.smart_objects_joined == 2

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 3,
        "payload": {"deviceID": cameras[0], "enableSmartDetect": ["person"],
                    "eventStartMSec": 1000, "eventStopMSec": 3000},
    }).encode())
    assert sink.messages[-1]["statusCode"] == 0
    leaves = [message["payload"] for message in sink.messages
              if message["functionName"] == "EventSmartDetect"
              and message["payload"]["edgeType"] == "leave"]
    assert [(event["deviceID"], event["objectTypes"]) for event in leaves] == [
        (cameras[0], ["person", "vehicle", "animal"])]
    assert sorted(leaves[0]["trackerIDAttrMap"].values(), key=lambda v: v["zone"]) == [
        {"objectType": "person", "zone": [7]},
        {"objectType": "vehicle", "zone": [8]},
        {"objectType": "animal", "zone": [9]}]
    assert service._camera_engine.has_policy(cameras[1])

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "messageId": 5,
        "payload": {"deviceID": cameras[0], "enableSmartDetect": ["face"],
                    "eventStartMSec": 1000, "eventStopMSec": 3000},
    }).encode())
    assert sink.messages[-1]["statusCode"] == 501
    assert not service._camera_engine.has_policy(cameras[0])
    assert service._camera_engine.has_policy(cameras[1])

    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "UiStreamControl", "messageId": 4,
        "payload": {"streaming": False, "deviceID": cameras[1]},
    }).encode())
    assert not service._camera_engine.has_policy(cameras[1])
    leaves = [message["payload"] for message in sink.messages
              if message["functionName"] == "EventSmartDetect"
              and message["payload"]["edgeType"] == "leave"]
    assert (leaves[-1]["deviceID"], leaves[-1]["objectTypes"]) == (
        cameras[1], ["person"])
    await service.stop()


@pytest.mark.asyncio
async def test_live_pool_routes_two_camera_events_and_pinned_snapshots(
        tmp_path, monkeypatch):
    config = fixture_state(tmp_path)
    cameras = ("2A1122334455", "2A1122334456")
    config["paired_streams"] = [
        {"camera_mac": mac, "source_ip": "192.168.10.1",
         "ffmpeg_path": sys.executable} for mac in cameras]
    config["live_pool_detector"] = {
        "checkpoint_path": str(tmp_path / "model.pth"),
        "checkpoint_sha256": "a" * 64, "threshold": 0.3,
        "smart_types": ["person", "vehicle"], "max_events_per_hour": 120}
    frames = []
    for color in ("red", "blue"):
        image = BytesIO()
        Image.new("RGB", (640, 360), color).save(image, format="JPEG")
        frames.append(image.getvalue())
    observations = (
        ObjectObservation("person", "person", 0.9, (0.2, 0.2, 0.5, 0.8)),
        ObjectObservation("vehicle", "car", 0.9, (0.2, 0.2, 0.6, 0.7)))

    class Model:
        def detect(self, frame):
            return (observations[frames.index(frame)],)

    monkeypatch.setattr(RFDetrNanoDetector, "from_checkpoint", lambda *a, **k: Model())
    service = CandidateService(config, tmp_path)
    service.adoption.state = {**service.adoption.binding, "phase": "adopted"}
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": mac} for mac in cameras]

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    for index, (camera, kind) in enumerate(zip(cameras, ("person", "vehicle")), 1):
        await service._send_stream_status(sink, streaming=True, camera_mac=camera)
        assert sink.messages[-1]["payload"]["isSmartDetectReady"] is True
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "ChangeSmartDetectSettings", "messageId": index,
            "payload": {"deviceID": camera, "algoVersion": "beta",
                        "enableSmartDetect": [kind], "eventStartMSec": 1000,
                        "eventStopMSec": 3000, "zones": {}, "lines": {}}}).encode())
        assert sink.messages[-1]["statusCode"] == 0
    for _ in range(2):
        for camera, frame in zip(cameras, frames):
            await service._observe_pool_frame(camera, frame)
            await service._inference.join()
    entered = [message for message in sink.messages
               if message["functionName"] == "EventSmartDetect"
               and message["payload"]["edgeType"] == "enter"]
    assert [(event["payload"]["deviceID"],
             event["payload"]["descriptors"][0]["objectType"])
            for event in entered] == list(zip(cameras, ("person", "vehicle")))
    for camera in cameras:
        changes = service._camera_engine.observe(camera, (), now=time.monotonic() + 4)
        await service._publish_pool_candidates(changes)
    left = [message for message in sink.messages
            if message["functionName"] == "EventSmartDetect"
            and message["payload"]["edgeType"] == "leave"]
    assert len(left) == 2
    assert all("smartDetectSnapshots" in event["payload"] for event in left)
    snapshots = [service._pool_pending_snapshots[
        event["payload"]["smartDetectSnapshots"][0]["smartDetectSnapshot"]]
        for event in left]
    assert [item.camera_mac for item in snapshots] == list(cameras)
    assert snapshots[0].snapshot.filename != snapshots[1].snapshot.filename

    received = []

    async def upload(request):
        assert request.transport.get_extra_info("peercert") is not None
        reader = await request.multipart()
        field = await reader.next()
        received.append((field.filename, await field.read()))
        return web.json_response({"success": True})

    app = web.Application()
    app.router.add_post("/internal/camera-upload/{token}", upload)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server_context.load_verify_locations(cafile=tmp_path / "device.crt")
    server_context.verify_mode = ssl.CERT_REQUIRED
    server = TestServer(app)
    await server.start_server(ssl=server_context)
    url = str(server.make_url("/internal/camera-upload/" +
                              "01234567-89ab-4def-8123-0123456789ab"))
    monkeypatch.setattr("aikey.aiport_candidate.validated_upload_url",
                        lambda *_args, **_kwargs: url)
    command = {"functionName": "GetRequest", "messageId": 40,
               "payload": {"what": "smartDetectZoneSnapshot",
                           "filename": snapshots[0].snapshot.filename,
                           "deviceID": cameras[1]}}
    try:
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        assert sink.messages[-1]["statusCode"] == 5
        assert not received
        command["messageId"] = 41
        command["payload"]["deviceID"] = cameras[0]
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        assert sink.messages[-1]["statusCode"] == 0
        command["messageId"] = 42
        command["payload"] = {"what": "smartDetectZoneSnapshotFullFoV",
                              "filename": snapshots[1].snapshot.full_fov_filename,
                              "deviceID": cameras[1]}
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        assert sink.messages[-1]["statusCode"] == 0
        assert received == [
            (snapshots[0].snapshot.filename, snapshots[0].snapshot.jpeg),
            (snapshots[1].snapshot.full_fov_filename,
             snapshots[1].snapshot.full_fov_jpeg)]
        assert snapshots[0].crop_available is False
        assert snapshots[1].full_available is False
        assert service._inference.is_available(cameras[0])
        assert service._inference.is_available(cameras[1])
        for pending in service._pool_pending_snapshots.values():
            pending.expires = time.monotonic() - 1
        service._pool_event_snapshots[(cameras[0], 99)] = (
            snapshots[0].snapshot, time.monotonic() - 1)
        service._prune_snapshots()
        assert not service._pool_pending_snapshots
        assert not service._pool_event_snapshots
    finally:
        await server.close()
        await service.stop()


@pytest.mark.asyncio
async def test_live_pool_exclusion_policy_suppresses_inside_but_emits_outside(
        tmp_path):
    camera = "2A1122334455"
    config = fixture_state(tmp_path)
    config["paired_streams"] = [{
        "camera_mac": camera, "source_ip": "192.168.10.1",
        "ffmpeg_path": sys.executable}]
    config["live_pool_detector"] = {
        "checkpoint_path": str(tmp_path / "model.pth"),
        "checkpoint_sha256": "a" * 64, "threshold": 0.3,
        "smart_types": ["person"], "max_events_per_hour": 12}
    service = CandidateService(config, tmp_path)
    service.adoption.state = {**service.adoption.binding, "phase": "adopted"}
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": camera}]

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    policy = {"deviceID": camera, "algoVersion": "beta",
              "enableSmartDetect": ["person"], "eventStartMSec": 1000,
              "eventStopMSec": 3000, "zones": {},
              "excludeZones": {"4": {
                  "coord": [450, 100, 550, 100, 550, 900, 450, 900],
                  "objectTypes": ["person"], "patrolSetID": -1}}}
    try:
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "ChangeSmartDetectSettings", "messageId": 16,
            "payload": policy}).encode())
        assert sink.messages[-1]["statusCode"] == 0
        generation = service._camera_engine.policy_generation(camera)
        excluded = (ObjectObservation("person", "person", 0.9,
                                      (0.4, 0.2, 0.5, 0.8)),)
        admitted = (ObjectObservation("person", "person", 0.9,
                                      (0.2, 0.2, 0.4, 0.8)),)
        for _ in range(2):
            await service._observe_pool_result(camera, excluded, generation)
        assert service.smart_events_entered == 0
        for _ in range(2):
            await service._observe_pool_result(camera, admitted, generation)
        enters = [message for message in sink.messages
                  if message["functionName"] == "EventSmartDetect"
                  and message["payload"]["edgeType"] == "enter"]
        assert len(enters) == 1
        assert enters[0]["payload"]["deviceID"] == camera
    finally:
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
    image = BytesIO()
    Image.new("RGB", (640, 360), "blue").save(image, format="JPEG")
    await service._publish_bounded_smart_changes((track,), frame=image.getvalue())
    await service._publish_bounded_smart_changes((track,))
    assert service.smart_events_entered == 1
    assert sink.messages[-1]["functionName"] == "EventSmartDetect"
    assert sink.messages[-1]["payload"]["descriptors"][0]["objectType"] == "person"
    await service._publish_bounded_smart_changes((
        TrackChange("leave", track.track_id, track.kind, track.label,
                    track.score, track.box),))
    assert service.smart_events_left == 1
    assert sink.messages[-1]["payload"]["edgeType"] == "leave"
    snapshot = sink.messages[-1]["payload"]["smartDetectSnapshots"][0]
    assert snapshot["smartDetectSnapshotType"] == "person"
    assert service._pending_snapshot[0].filename == snapshot["smartDetectSnapshot"]
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
    assert service.detector_objects_enabled == 4
    assert service.detector_objects_score_eligible == 3
    assert service.detector_objects_zone_eligible == 2
    assert service.detector_tracks_entered == 1
    assert service.smart_events_entered == 1
    assert sink.messages[-1]["functionName"] == "EventSmartDetect"
    assert sink.messages[-1]["payload"]["descriptors"][0]["confidenceLevel"] == 91
    assert sink.messages[-1]["payload"]["descriptors"][0]["zones"] == [7]
    assert sink.messages[-1]["payload"]["zonesStatus"]["7"]["status"] == "enter"
    health = json.loads((await service._health(None)).text)
    assert health["detector_objects_zone_eligible"] == 2
    assert "coord" not in json.dumps(health)
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
        "7": {"status": "leave", "level": 93}}
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


@pytest.mark.asyncio
async def test_live_api_package_enters_camera_event_with_zone_and_camera_owned_lens(
        tmp_path, monkeypatch):
    """Package is advertised for primary zones and sent as a one-shot edge."""
    camera = "2A1122334455"
    config = fixture_state(tmp_path)
    private_file(tmp_path / "api-key", b"synthetic-test-key\n")
    config["paired_streams"] = [{
        "camera_mac": camera, "source_ip": "192.168.10.1",
        "ffmpeg_path": sys.executable}]
    config["live_pool_detector"] = {
        "inference_backend": "vision_api", "threshold": 0.8,
        "smart_types": ["person", "vehicle", "animal", "package"],
        "max_events_per_hour": 12, "max_requests_per_hour": 12,
        "provider_config": {"provider": "openai", "model": "gpt-6-luna",
                            "base_url": "https://api.openai.com/v1",
                            "allow_remote": True, "max_output_tokens": 256,
                            "api_key_file": str(tmp_path / "api-key")}}

    def fake_provider(_url, _headers, payload):
        # The second request is the close-up package check.
        answer = ({"kind": "package", "label": "package"}
                  if "close-up crop" in json.dumps(payload) else
                  {"detections": [{"kind": "package", "label": "package",
                                   "score": 0.93, "box": [0.3, 0.6, 0.45, 0.8]}]})
        return {"status": "completed", "output": [{
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": json.dumps(answer)}],
        }]}

    monkeypatch.setattr(
        "aikey.aiport_candidate.ApiObjectDetector",
        lambda provider, state_dir, **options: ApiObjectDetector(
            provider, state_dir, transport=fake_provider, **options))
    service = CandidateService(config, tmp_path)
    service.adoption.state = {**service.adoption.binding, "phase": "adopted"}
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": camera}]

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    await service._send_stream_status(sink, streaming=True, camera_mac=camera)
    flags, = [message for message in sink.messages
              if message["functionName"] == "EventFeatureFlagsUpdated"]
    assert flags["payload"]["smartDetect"] == [
        "person", "vehicle", "animal", "package", "packageMaincam"]
    frame_io = BytesIO()
    Image.new("RGB", (640, 360), "gray").save(frame_io, format="JPEG")
    frame = frame_io.getvalue()
    try:
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "ChangeSmartDetectSettings", "messageId": 1,
            "payload": {"deviceID": camera, "algoVersion": "beta",
                        "enableSmartDetect": ["person", "package"],
                        "eventStartMSec": 1000, "eventStopMSec": 3000,
                        "zones": {"4": {"coord": [0, 0, 1000, 0, 1000, 1000, 0, 1000],
                                        "objectTypes": ["person", "package"]}},
                        "secondLensZones": {"9": {
                            "coord": [100, 100, 900, 100, 900, 900],
                            "objectTypes": ["package"]}}}}).encode())
        assert sink.messages[-1]["statusCode"] == 0
        for _ in range(2):
            await service._observe_pool_frame(camera, frame)
            await service._inference.join()
        events = [message["payload"] for message in sink.messages
                  if message.get("functionName") == "EventSmartDetect"]
        # Protect 7.3.68 resolved an AI Port packageDetected by the AI Port's
        # own MAC and dropped it; the enter lifecycle is routed by deviceID.
        assert [event["edgeType"] for event in events] == ["enter"]
        package = events[0]
        assert package["deviceID"] == camera
        assert package["objectTypes"] == ["package"]
        assert package["zonesStatus"] == {"4": {"status": "enter", "level": 93}}
        assert package["descriptors"][0]["objectType"] == "package"
        health = json.loads((await service._health(None)).text)
        camera_health = health["pool_cameras"][0]
        assert health["smart_package_events"] == 1
        assert camera_health["events_entered_by_kind"]["package"] == 1
        assert camera_health["secondary_lens"] == {
            "zones": 1, "classes": ["package"], "processed_by": "camera"}
        assert "coord" not in json.dumps(camera_health)
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_live_pool_advertises_enhanced_motion_and_sends_zone_motion(tmp_path):
    camera = "2A1122334455"
    config = fixture_state(tmp_path)
    private_file(tmp_path / "api-key", b"synthetic-test-key\n")
    config["paired_streams"] = [{
        "camera_mac": camera, "source_ip": "192.168.10.1",
        "ffmpeg_path": sys.executable}]
    config["live_pool_detector"] = {
        "inference_backend": "vision_api", "threshold": 0.8,
        "smart_types": ["person"], "max_events_per_hour": 12,
        "max_requests_per_hour": 12,
        "provider_config": {"provider": "openai", "model": "gpt-6-luna",
                            "base_url": "https://api.openai.com/v1",
                            "allow_remote": True, "max_output_tokens": 256,
                            "api_key_file": str(tmp_path / "api-key")}}
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": camera}]

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    await service._send_stream_status(sink, streaming=True, camera_mac=camera)
    flags, = [message for message in sink.messages
              if message["functionName"] == "EventFeatureFlagsUpdated"]
    assert flags["payload"]["motionDetect"] == ["enhanced"]

    def picture(box=None):
        image = Image.new("RGB", (320, 180), (40, 40, 40))
        if box is not None:
            ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
        data = BytesIO()
        image.save(data, format="JPEG", quality=85)
        return data.getvalue()

    try:
        command = {"functionName": "ChangeSmartMotionSettings", "messageId": 7,
                   "payload": {"algoVersion": "beta", "deviceID": "2A1122334456",
                               "enable": True, "eventMaxDurationMSec": 300000,
                               "bgmodel": "default", "lingerEventStartMSec": 0,
                               "lingerEventStopMSec": 1000,
                               "zones": {"1": {"coord": [0, 0, 500, 0, 500, 1000, 0, 1000],
                                               "level": 50, "triggerLight": True}}}}
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        assert sink.messages[-1]["statusCode"] == 5     # not an allowlisted camera
        command["payload"]["deviceID"] = camera
        command["messageId"] = 8
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        assert sink.messages[-1]["statusCode"] == 0
        await service._observe_pool_frame(camera, picture())
        await service._observe_pool_frame(camera, picture((40, 40, 110, 160)))
        motion = [message for message in sink.messages
                  if message["functionName"] == "EventSmartMotion"]
        assert [m["payload"]["edgeType"] for m in motion] == ["start"]
        assert motion[0]["payload"]["deviceID"] == camera
        assert motion[0]["timeStamp"].endswith("Z")
        await service._revoke_pool_policy(camera)
        motion = [message for message in sink.messages
                  if message["functionName"] == "EventSmartMotion"]
        assert [m["payload"]["edgeType"] for m in motion] == ["start", "stop"]
        health = json.loads((await service._health(None)).text)
        assert health["smart_motion_settings_acks"] == 1
        assert health["smart_motion_settings_rejected"] == 1
        assert health["pool_cameras"][0]["motion"]["starts"] == 1
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_pool_keeps_one_protect_event_until_the_last_object_leaves(tmp_path):
    camera = "2A1122334455"
    config = fixture_state(tmp_path)
    private_file(tmp_path / "api-key", b"synthetic-test-key\n")
    config["paired_streams"] = [{"camera_mac": camera, "source_ip": "192.168.10.1",
                                 "ffmpeg_path": sys.executable}]
    config["live_pool_detector"] = {
        "inference_backend": "vision_api", "threshold": 0.8,
        "smart_types": ["person", "vehicle"], "max_events_per_hour": 12,
        "max_requests_per_hour": 12,
        "provider_config": {"provider": "openai", "model": "gpt-6-luna",
                            "base_url": "https://api.openai.com/v1",
                            "allow_remote": True, "max_output_tokens": 256,
                            "api_key_file": str(tmp_path / "api-key")}}
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": camera}]

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    person = TrackChange("enter", 1, "person", "person", 0.9, (0.1, 0.2, 0.3, 0.8))
    car = TrackChange("enter", 2, "vehicle", "car", 0.9, (0.5, 0.5, 0.9, 0.9))
    try:
        for change in (person, car,
                       TrackChange("leave", 1, "person", "person", 0.9, person.box),
                       TrackChange("leave", 2, "vehicle", "car", 0.9, car.box)):
            await service._publish_pool_candidates(
                (CameraEventCandidate(camera, change, ()),))
        edges = [(m["payload"]["edgeType"], m["payload"]["objectTypes"])
                 for m in sink.messages if m["functionName"] == "EventSmartDetect"]
        assert edges == [("enter", ["person"]), ("moving", ["person", "vehicle"]),
                         ("moving", ["vehicle"]), ("leave", ["person", "vehicle"])]
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_restart_does_not_reannounce_a_parked_package(tmp_path, monkeypatch):
    """The package cooldown survives an AI Port restart on the same state."""
    camera = "2A1122334455"
    config = fixture_state(tmp_path)
    private_file(tmp_path / "api-key", b"synthetic-test-key\n")
    config["paired_streams"] = [{
        "camera_mac": camera, "source_ip": "192.168.10.1",
        "ffmpeg_path": sys.executable}]
    config["live_pool_detector"] = {   # uncapped default: no request cap
        "inference_backend": "vision_api", "threshold": 0.8,
        "smart_types": ["person", "package"], "max_events_per_hour": 12,
        "provider_config": {"provider": "openai", "model": "gpt-6-luna",
                            "base_url": "https://api.openai.com/v1",
                            "allow_remote": True, "max_output_tokens": 256,
                            "api_key_file": str(tmp_path / "api-key")}}

    def fake_provider(_url, _headers, payload):
        answer = ({"kind": "package", "label": "package"}
                  if "close-up crop" in json.dumps(payload) else
                  {"detections": [{"kind": "package", "label": "package",
                                   "score": 0.93, "box": [0.3, 0.6, 0.45, 0.8]}]})
        return {"status": "completed", "output": [{
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": json.dumps(answer)}],
        }]}

    monkeypatch.setattr(
        "aikey.aiport_candidate.ApiObjectDetector",
        lambda provider, state_dir, **options: ApiObjectDetector(
            provider, state_dir, transport=fake_provider, **options))
    frame_io = BytesIO()
    Image.new("RGB", (640, 360), "gray").save(frame_io, format="JPEG")
    frame = frame_io.getvalue()

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    async def run_once():
        service = CandidateService(config, tmp_path)
        service.adoption.state = {**service.adoption.binding, "phase": "adopted"}
        service._params_agreed = True
        service.ingress.list_streams = lambda: [{"deviceID": camera}]
        sink = Sink()
        service._current_ws = sink
        try:
            await service._send_stream_status(sink, streaming=True, camera_mac=camera)
            await service._handle_diagnostic_frame(sink, json.dumps({
                "functionName": "ChangeSmartDetectSettings", "messageId": 1,
                "payload": {"deviceID": camera, "algoVersion": "beta",
                            "enableSmartDetect": ["person", "package"],
                            "eventStartMSec": 1000, "eventStopMSec": 3000,
                            "zones": {"4": {"coord": [0, 0, 1000, 0, 1000, 1000, 0, 1000],
                                            "objectTypes": ["person"]}}}}).encode())
            assert sink.messages[-1]["statusCode"] == 0
            for _ in range(2):   # the startup pair samples the parked parcel
                await service._observe_pool_frame(camera, frame)
                await service._inference.join()
            health = json.loads((await service._health(None)).text)
            enters = [m["payload"] for m in sink.messages
                      if m.get("functionName") == "EventSmartDetect"
                      and m["payload"]["edgeType"] == "enter"]
            return enters, health
        finally:
            await service.stop()

    first, _ = await run_once()
    assert [event["objectTypes"] for event in first] == [["package"]]
    second, health = await run_once()          # same state dir: a restart
    assert second == []
    assert health["package_cooldown_skips"] == 1
    assert health["pool_cameras"][0]["events_entered_by_kind"]["package"] == 0
