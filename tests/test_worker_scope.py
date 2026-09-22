"""Single-use camera authorization with synthetic loopback media and inference."""

import asyncio
import base64
import json
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode

from aiohttp import web
import pytest

from aikey.config import ConfigError, defaults, validate_config
from aikey.worker import JobProcessor, WorkerError


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWioAAAAASUVORK5CYII="
)
CAPTION = "Synthetic scope test caption."


def configuration(origin="http://127.0.0.1:9000"):
    return {"runtime": {"mode": "lab"}, "controller_origins": [origin],
            "device": {"mac": "020000000001"},
            "inference": {"base_url": origin + "/v1", "model": "synthetic-test-only"},
            "worker": {"request_mp4_exports": True, "max_queue": 2,
                       "test_scope": {"permit_id": "camera-test-1", "camera_id": "camera-fixture"}}}


def command(task="task-fixture"):
    query = {"camera": "camera-fixture", "channel": "0", "type": "rotating", "mute": "true",
             "format": "ubv", "createEvent": "false", "event": "event-fixture", "start": "1000", "end": "11000"}
    return {"targetUri": ":7968/on_demand_inference", "timeoutMs": 30000,
            "resUrl": "/internal/camera-upload/" + task,
            "payload": {"cameraId": "camera-fixture", "eventId": "event-fixture", "timestamp": 6000,
                        "videoUrl": "/internal/aiprocessors/video/export?" + urlencode(query)}}


def query_change(payload, key, value):
    path, query = payload["payload"]["videoUrl"].split("?", 1)
    fields = dict(parse_qsl(query))
    if value is None:
        fields.pop(key)
    else:
        fields[key] = value
    payload["payload"]["videoUrl"] = path + "?" + urlencode(fields)


@pytest.mark.parametrize("scope", [None, False, {}, {"camera_id": "one"},
    {"permit_id": "one", "camera_id": ""},
    {"permit_id": "../escape", "camera_id": "one"},
    {"permit_id": "one", "camera_id": "one", "allow_other_cameras": True}])
def test_malformed_scope_fails_in_runtime_and_configuration(tmp_path, scope):
    options = configuration()
    options["worker"]["test_scope"] = scope
    with pytest.raises(WorkerError, match="scope"):
        JobProcessor(options, tmp_path / "runtime")
    stored = defaults(tmp_path / "config", "020000000001")
    stored["worker"]["test_scope"] = scope
    with pytest.raises(ConfigError, match="scope"):
        validate_config(stored)


@pytest.fixture
async def services(tmp_path):
    seen = SimpleNamespace(media=[], model=[], callbacks=[], block_model=False,
                           media_status=200, callback_status=200,
                           entered=asyncio.Event(), release=asyncio.Event())
    seen.release.set()
    async def media(request):
        reservations = list((tmp_path / "worker-test-scopes").glob("*.json"))
        assert len(reservations) == 1, "The permit must be durable before the first fetch"
        seen.media.append(dict(request.query))
        assert json.loads(reservations[0].read_text())["camera_id"] == "camera-fixture"
        return web.Response(body=b"synthetic-video-for-decoder-fixture", status=seen.media_status)
    async def model(request):
        seen.model.append(await request.json())
        seen.entered.set()
        await seen.release.wait()
        return web.json_response({"choices": [{"finish_reason": "stop", "message": {"content": CAPTION}}]})
    async def callback(request):
        seen.callbacks.append(await request.json())
        return web.json_response({"accepted": True}, status=seen.callback_status)
    app = web.Application()
    app.router.add_get("/internal/aiprocessors/video/export", media)
    app.router.add_post("/v1/chat/completions", model)
    app.router.add_post("/internal/camera-upload/{token}", callback)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    seen.origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield seen
    finally:
        seen.release.set()
        await runner.cleanup()


def processor(services, tmp_path, monkeypatch, *, options=None):
    worker = JobProcessor(options or configuration(services.origin), tmp_path)
    async def decode_fixture(*args):
        return PNG
    # The unchanged video decoder has separate real-ffmpeg tests. This fixture
    # keeps scope tests focused on authorization and actual HTTP side effects.
    monkeypatch.setattr(worker, "_video_frame", decode_fixture)
    return worker


@pytest.mark.parametrize("variant", ["camera", "missing_camera", "describe", "unknown_operation",
    "url_camera", "url_event", "channel", "missing_channel", "interval", "timestamp",
    "mute", "create_event", "type", "unknown_query", "duplicate_camera", "missing_event", "path"])
async def test_scope_rejects_out_of_scope_jobs_before_any_network(services, tmp_path, monkeypatch, variant):
    item = command()
    if variant == "camera":
        item["payload"]["cameraId"] = "other-camera"
    elif variant == "missing_camera":
        item["payload"].pop("cameraId")
    elif variant == "describe":
        item = {"targetUri": ":7968/describe", "resUrl": "/internal/aiprocessors/descriptions/task",
                "payload": {"camera": "camera-fixture", "event": "event-fixture",
                            "images": [{"reqUrl": "/internal/aiprocessors/image/image"}]}}
    elif variant == "unknown_operation":
        item["targetUri"] = ":7968/anything"
    elif variant == "duplicate_camera":
        item["payload"]["videoUrl"] += "&camera=camera-fixture"
    elif variant == "timestamp":
        item["payload"]["timestamp"] = 11000
    elif variant == "path":
        item["payload"]["videoUrl"] = item["payload"]["videoUrl"].replace("/aiprocessors", "")
    else:
        key, value = {"url_camera": ("camera", "other-camera"), "url_event": ("event", "other-event"),
                      "channel": ("channel", "1"), "missing_channel": ("channel", None),
                      "interval": ("end", "11001"), "mute": ("mute", "false"),
                      "create_event": ("createEvent", "true"), "type": ("type", "anything"),
                      "unknown_query": ("extra", "value"), "missing_event": ("event", None)}[variant]
        query_change(item, key, value)
    worker = processor(services, tmp_path, monkeypatch)
    try:
        with pytest.raises(WorkerError):
            await worker.submit(item)
        assert services.media == services.model == services.callbacks == []
        assert not list((tmp_path / "worker-test-scopes").glob("*.json"))
    finally:
        await worker.stop()


async def test_one_durable_job_deduplicates_inflight_completed_and_restart(services, tmp_path, monkeypatch):
    services.release.clear()
    worker = processor(services, tmp_path, monkeypatch)
    try:
        first = await worker.submit(command())
        await asyncio.wait_for(services.entered.wait(), 2)
        duplicate = await worker.submit(command())
        assert duplicate["duplicate"] and duplicate["jobId"] == first["jobId"]
        with pytest.raises(WorkerError, match="consumed"):
            await worker.submit(command("different-task"))
        services.release.set()
        result = await worker.handle(command())
        assert result["result"] == {"description": CAPTION}
        assert len(services.media) == len(services.model) == len(services.callbacks) == 1
        assert services.media[0]["format"] == "mp4"
        assert (await worker.submit(command()))["duplicate"]
    finally:
        await worker.stop()
    restarted = processor(services, tmp_path, monkeypatch)
    try:
        assert await restarted.handle(command()) == result
        with pytest.raises(WorkerError, match="consumed"):
            await restarted.submit(command("another-task"))
        assert len(services.media) == len(services.model) == len(services.callbacks) == 1
    finally:
        await restarted.stop()


async def test_reserved_job_cannot_resume_after_crash_before_callback(services, tmp_path, monkeypatch):
    services.release.clear()
    worker = processor(services, tmp_path, monkeypatch)
    await worker.submit(command())
    await asyncio.wait_for(services.entered.wait(), 2)
    await worker.stop()
    count = len(services.model)
    restarted = processor(services, tmp_path, monkeypatch)
    try:
        with pytest.raises(WorkerError, match="consumed"):
            await restarted.submit(command())
        assert len(services.model) == count == 1
        assert services.callbacks == []
    finally:
        await restarted.stop()


async def test_second_process_cannot_share_the_same_permit(services, tmp_path, monkeypatch):
    first = processor(services, tmp_path, monkeypatch)
    second = processor(services, tmp_path, monkeypatch)
    services.release.clear()
    try:
        await first.submit(command())
        with pytest.raises(WorkerError, match="consumed"):
            await second.submit(command())
        await asyncio.wait_for(services.entered.wait(), 2)
        assert len(services.media) == len(services.model) == 1
    finally:
        services.release.set()
        await first.stop()
        await second.stop()


async def test_persistence_failure_prevents_fetch_and_does_not_publish_partial_claim(services, tmp_path, monkeypatch):
    worker = processor(services, tmp_path, monkeypatch)
    def fail_link(*args):
        raise OSError("Synthetic local storage failure")
    monkeypatch.setattr("aikey.worker.os.link", fail_link)
    try:
        with pytest.raises(WorkerError, match="persist"):
            await worker.submit(command())
        assert services.media == services.model == services.callbacks == []
        assert list((tmp_path / "worker-test-scopes").iterdir()) == []
    finally:
        await worker.stop()


async def test_expired_inference_is_cancelled_and_does_not_restore_budget(services, tmp_path, monkeypatch):
    services.release.clear()
    worker = processor(services, tmp_path, monkeypatch)
    item = command()
    item["timeoutMs"] = 100
    assert worker._normalize(command())[-1] == 15
    try:
        with pytest.raises(WorkerError):
            await worker.handle(item)
        assert len(services.model) == 1
        assert services.callbacks == []
        with pytest.raises(WorkerError, match="consumed"):
            await worker.submit(item)
        with pytest.raises(WorkerError, match="consumed"):
            await worker.submit(command("another-task"))
    finally:
        services.release.set()
        await worker.stop()


async def test_existing_unscoped_configuration_keeps_normal_admission(services, tmp_path, monkeypatch):
    options = configuration(services.origin)
    options["worker"].pop("test_scope")
    worker = processor(services, tmp_path, monkeypatch, options=options)
    # No HTTP is needed to establish that the absent opt-in adds no restrictions.
    other = command()
    other["payload"]["cameraId"] = "other-camera"
    assert worker._normalize(other)[3]["cameraId"] == "other-camera"
    assert worker._normalize(other)[-1] == 30
    assert worker.test_scope is None
    await worker.stop()


def test_used_permit_cannot_be_retargeted_and_corrupt_state_fails_closed(tmp_path):
    worker = JobProcessor(configuration(), tmp_path)
    path = worker._scope_path
    path.write_text("{invalid state")
    with pytest.raises(WorkerError, match="reservation"):
        JobProcessor(configuration(), tmp_path)
    path.write_text(json.dumps({"schema": 1, "permit_id": "camera-test-1", "camera_id": "different-camera",
                               "job_id": "a" * 64, "fingerprint": "b" * 64, "consumed_at": 1}))
    with pytest.raises(WorkerError, match="reservation"):
        JobProcessor(configuration(), tmp_path)
