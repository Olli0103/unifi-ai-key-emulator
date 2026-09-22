"""Local HTTP integration fixtures. No controller or model is contacted."""

import asyncio
import base64
import hashlib
import json
import math
import shutil
import ssl

from aiohttp import web
import pytest
import pytest_asyncio

from aikey.worker import JobProcessor, WorkerError


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWioAAAAASUVORK5CYII="
)
DESCRIPTION = "Synthetic local test response. No production footage was analyzed."


class LocalServices:
    def __init__(self):
        self.requests = []
        self.embedding_requests = []
        self.callbacks = []
        self.media_requests = []
        self.video_queries = []
        self.media = PNG
        self.media_status = 200
        self.model_status = 200
        self.callback_status = 200
        self.model_content = DESCRIPTION
        self.finish_reason = "stop"
        self.redirect = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.video = b"unsupported UBV fixture"

    async def image(self, request):
        self.media_requests.append(dict(request.headers))
        if self.redirect:
            raise web.HTTPFound(self.redirect)
        return web.Response(status=self.media_status, body=self.media, content_type="image/png")

    async def video_handler(self, request):
        self.media_requests.append(dict(request.headers))
        self.video_queries.append(request.query_string)
        return web.Response(body=self.video, content_type="video/mp4", headers={"x-start-timestamp": "1000"})

    async def model(self, request):
        self.requests.append(await request.json())
        self.entered.set()
        await self.release.wait()
        return web.json_response({"choices": [{"finish_reason": self.finish_reason,
                                              "message": {"content": self.model_content}}]},
                                 status=self.model_status)

    async def embeddings(self, request):
        self.embedding_requests.append(await request.json())
        return web.json_response({"data": [{"index": 0, "embedding": [1.0] + [0.0] * 383}]})

    async def callback(self, request):
        if request.content_type == "multipart/form-data":
            reader = await request.multipart()
            part = await reader.next()
            data = {"name": part.name, "filename": part.filename,
                    "content_type": part.headers["Content-Type"],
                    "payload": json.loads(await part.read()), "extra": await reader.next()}
        else:
            data = await request.json()
        self.callbacks.append({"path": request.path, "payload": data, "headers": dict(request.headers)})
        if self.redirect:
            raise web.HTTPFound(self.redirect)
        return web.json_response({"received": True}, status=self.callback_status)


@pytest_asyncio.fixture
async def services():
    services = LocalServices()
    app = web.Application()
    app.router.add_get("/internal/aiprocessors/image/{image}", services.image)
    app.router.add_get("/internal/aiprocessors/video/export", services.video_handler)
    app.router.add_post("/v1/chat/completions", services.model)
    app.router.add_post("/v1/embeddings", services.embeddings)
    app.router.add_post("/internal/aiprocessors/descriptions/{task}", services.callback)
    app.router.add_post("/internal/aiprocessors/recognize-anything", services.callback)
    app.router.add_post("/internal/camera-upload/{token}", services.callback)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    services.origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield services
    finally:
        services.release.set()
        await runner.cleanup()


def configuration(services):
    return {"runtime": {"mode": "lab"}, "controller_origins": [services.origin],
            "device": {"mac": "02:00:00:00:00:99"},
            "inference": {"base_url": services.origin + "/v1", "model": "synthetic-test-only"},
            "worker": {"max_queue": 2, "max_concurrency": 1, "timeout_s": 5,
                       "legacy_profile": "protect-7.2.105"}}


def command(task="task-1"):
    return {"targetUri": ":7968/describe", "timeoutMs": 5000,
            "resUrl": f"/internal/aiprocessors/descriptions/{task}",
            "payload": {"camera": "camera-fixture", "event": "event-fixture", "pass": "initial",
                        "images": [{"reqUrl": "/internal/aiprocessors/image/image-fixture"}]}}


@pytest.mark.asyncio
async def test_real_http_media_model_callback_and_persistent_dedup(services, tmp_path):
    config = configuration(services)
    processor = JobProcessor(config, tmp_path)
    try:
        result = await processor.handle(command())
        assert result["callback"] == "http_accepted"
        assert services.callbacks[0]["path"].endswith("/task-1")
        assert services.callbacks[0]["payload"] == {
            "camera": "camera-fixture", "event": "event-fixture", "pass": "initial",
            "description": DESCRIPTION, "model": "synthetic-test-only"}
        sent_image = services.requests[0]["messages"][0]["content"][1]["image_url"]["url"]
        assert sent_image == "data:image/png;base64," + base64.b64encode(PNG).decode()
        assert services.requests[0]["model"] == "synthetic-test-only"
        assert services.media_requests[0]["x-ident"] == "020000000099"
        assert services.callbacks[0]["headers"]["x-type"] == "UP-AI-KEY"
        assert (await processor.submit(command()))["duplicate"] is True
    finally:
        await processor.stop()
    restarted = JobProcessor(config, tmp_path)
    try:
        assert await restarted.handle(command()) == result
        assert len(services.requests) == len(services.callbacks) == 1
        changed = command()
        changed["payload"]["event"] = "different-event"
        with pytest.raises(WorkerError, match="different input"):
            await restarted.submit(changed)
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_submit_returns_before_inference_and_queue_is_bounded(services, tmp_path):
    services.release.clear()
    config = configuration(services)
    config["worker"]["max_queue"] = 1
    processor = JobProcessor(config, tmp_path)
    try:
        admission = await asyncio.wait_for(processor.submit(command()), timeout=0.3)
        await asyncio.wait_for(services.entered.wait(), timeout=1)
        assert admission["accepted"] is True
        assert (await processor.submit(command()))["duplicate"] is True
        await processor.submit(command("task-2"))
        with pytest.raises(WorkerError, match="queue is full"):
            await processor.submit(command("task-3"))
        assert services.callbacks == []
        services.release.set()
        await processor.wait_for_idle()
        assert processor.get_status(admission["jobId"])["state"] == "completed"
        assert len(services.requests) == len(services.callbacks) == 2
    finally:
        services.release.set()
        await processor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda c, s: c.update(resUrl="http://localhost:1/internal/aiprocessors/descriptions/t"),
    lambda c, s: c.update(resUrl=s.origin + "/api/settings"),
    lambda c, s: c["payload"]["images"][0].update(reqUrl="https://untrusted.invalid/image"),
    lambda c, s: c["payload"]["images"][0].update(reqUrl=s.origin + "/api/keys"),
    lambda c, s: c["payload"]["images"][0].update(reqUrl=s.origin + "/internal/aiprocessors/image/%2e%2e"),
    lambda c, s: c.update(targetUri=":22/arbitrary-command"),
])
async def test_untrusted_origins_and_unsupported_paths_rejected_before_io(services, tmp_path, change):
    processor = JobProcessor(configuration(services), tmp_path)
    try:
        request = command()
        change(request, services)
        with pytest.raises(WorkerError):
            await processor.submit(request)
        assert services.requests == services.callbacks == services.media_requests == []
    finally:
        await processor.stop()


@pytest.mark.asyncio
async def test_redirect_is_never_followed(services, tmp_path):
    services.redirect = services.origin + "/api/private"
    processor = JobProcessor(configuration(services), tmp_path)
    try:
        with pytest.raises(WorkerError, match="HTTP 302"):
            await processor.handle(command())
        assert services.requests == services.callbacks == []
        assert len(services.media_requests) == 1
    finally:
        await processor.stop()


@pytest.mark.asyncio
async def test_media_byte_limit_prevents_inference(services, tmp_path):
    services.media = PNG + b"x" * 1000
    config = configuration(services)
    config["worker"]["max_media_bytes"] = 100
    processor = JobProcessor(config, tmp_path)
    try:
        with pytest.raises(WorkerError, match="byte limit"):
            await processor.handle(command())
        assert services.requests == services.callbacks == []
    finally:
        await processor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["empty", "length", "http_error"])
async def test_model_failures_do_not_fabricate_or_callback(services, tmp_path, invalid):
    if invalid == "empty":
        services.model_content = ""
    elif invalid == "length":
        services.finish_reason = "length"
    else:
        services.model_status = 503
    processor = JobProcessor(configuration(services), tmp_path)
    try:
        with pytest.raises(WorkerError, match="Inference"):
            await processor.handle(command())
        assert services.callbacks == []
    finally:
        await processor.stop()


@pytest.mark.asyncio
async def test_deadline_prevents_late_callback(services, tmp_path):
    services.release.clear()
    processor = JobProcessor(configuration(services), tmp_path)
    request = command()
    request["timeoutMs"] = 100
    try:
        with pytest.raises(WorkerError, match="timed out"):
            await processor.handle(request)
        services.release.set()
        await processor.wait_for_idle()
        assert services.callbacks == []
    finally:
        services.release.set()
        await processor.stop()


@pytest.mark.asyncio
async def test_uncertain_callback_is_not_replayed_after_restart(services, tmp_path):
    services.callback_status = 500
    config = configuration(services)
    processor = JobProcessor(config, tmp_path)
    try:
        with pytest.raises(WorkerError, match="callback returned HTTP 500"):
            await processor.handle(command())
        assert len(services.callbacks) == 1
    finally:
        await processor.stop()
    services.callback_status = 200
    restarted = JobProcessor(config, tmp_path)
    try:
        with pytest.raises(WorkerError, match="uncertain"):
            await restarted.handle(command())
        assert len(services.callbacks) == len(services.requests) == 1
    finally:
        await restarted.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["key-2.2.8", "protect-7.2.105"])
async def test_explicit_legacy_multipart_profiles(services, tmp_path, profile):
    config = configuration(services)
    config["worker"]["legacy_profile"] = profile
    request = command()
    request["resUrl"] = "/internal/aiprocessors/recognize-anything"
    processor = JobProcessor(config, tmp_path)
    try:
        await processor.handle(request)
        received = services.callbacks[0]["payload"]
        assert received["name"] == "ram"
        assert received["content_type"] == "application/json"
        assert received["extra"] is None
        expected = {"eventId": "event-fixture", "status": "success", "description": DESCRIPTION}
        if profile == "protect-7.2.105":
            expected["cameraId"] = "camera-fixture"
        assert received["payload"] == expected
    finally:
        await processor.stop()


@pytest.mark.asyncio
async def test_callback_disabled_is_explicit_and_does_not_write_controller(services, tmp_path):
    config = configuration(services)
    config["worker"]["callback_mode"] = "disabled"
    processor = JobProcessor(config, tmp_path)
    try:
        result = await processor.handle(command())
        assert result["callback"] == "disabled"
        assert result["result"]["description"] == DESCRIPTION
        assert services.callbacks == []
    finally:
        await processor.stop()


@pytest.mark.asyncio
async def test_stop_cancels_queued_and_running_work_without_callback(services, tmp_path):
    services.release.clear()
    processor = JobProcessor(configuration(services), tmp_path)
    await processor.submit(command())
    await asyncio.wait_for(services.entered.wait(), timeout=1)
    await processor.submit(command("task-2"))
    await processor.stop()
    services.release.set()
    await asyncio.wait_for(processor.wait_for_idle(), timeout=1)
    assert services.callbacks == []


@pytest.mark.asyncio
async def test_ubv_rejected_without_vendor_program_execution(services, tmp_path):
    config = configuration(services)
    config["worker"]["ffmpeg_path"] = shutil.which("ffmpeg") or "/bin/false"
    request = command()
    request["payload"].pop("images")
    request["payload"]["videos"] = [{"reqUrl": "/internal/aiprocessors/video/export?start=1000"}]
    processor = JobProcessor(config, tmp_path)
    try:
        with pytest.raises(WorkerError, match="UBV"):
            await processor.handle(request)
        assert services.requests == services.callbacks == []
    finally:
        await processor.stop()


@pytest.mark.asyncio
async def test_on_demand_failure_callback_is_explicit_and_not_replayed(services, tmp_path):
    config = configuration(services)
    config["worker"]["ffmpeg_path"] = shutil.which("ffmpeg") or "/bin/false"
    request = {"targetUri": ":7968/on_demand_inference", "resUrl": "/internal/camera-upload/failure",
               "payload": {"cameraId": "c", "eventId": "e", "timestamp": 1000,
                           "videoUrl": "/internal/aiprocessors/video/export?start=1000"}}
    processor = JobProcessor(config, tmp_path)
    try:
        with pytest.raises(WorkerError, match="UBV"):
            await processor.handle(request)
        assert set(services.callbacks[0]["payload"]) == {"error"}
        assert "UBV" in services.callbacks[0]["payload"]["error"]
        with pytest.raises(WorkerError, match="UBV"):
            await processor.handle(request)
        assert len(services.callbacks) == 1
        assert services.requests == []
    finally:
        await processor.stop()


@pytest.mark.asyncio
async def test_real_ffmpeg_on_demand_frame_and_exact_callback(services, tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("Real ffmpeg executable unavailable")
    video_path = tmp_path / "synthetic.mp4"
    process = await asyncio.create_subprocess_exec(ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=32x32:r=2:d=1", "-c:v", "mpeg4", "-y", str(video_path))
    assert await process.wait() == 0
    services.video = video_path.read_bytes()
    config = configuration(services)
    config["worker"]["ffmpeg_path"] = ffmpeg
    config["worker"]["request_mp4_exports"] = True
    request = {"targetUri": ":7968/on_demand_inference", "timeoutMs": 5000,
               "resUrl": "/internal/camera-upload/synthetic-token",
               "payload": {"cameraId": "camera-fixture", "eventId": "event-fixture", "timestamp": 1000,
                           "videoUrl": "/internal/aiprocessors/video/export?camera=camera-fixture&start=1000&end=2000&format=ubv&event=event-fixture"}}
    processor = JobProcessor(config, tmp_path)
    try:
        await processor.handle(request)
        assert services.callbacks[0]["payload"] == {"description": DESCRIPTION}
        image_url = services.requests[0]["messages"][0]["content"][1]["image_url"]["url"]
        assert image_url.startswith("data:image/jpeg;base64,")
        assert services.video_queries == ["camera=camera-fixture&start=1000&end=2000&format=mp4&event=event-fixture"]
        assert not list((tmp_path / "worker-jobs").glob("aikey-video-*"))
    finally:
        await processor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("video_url", [
    "/internal/aiprocessors/video/export?start=1000&end=2000&format=ubv&token=signed",
    "/internal/aiprocessors/video/export?start=1000&end=2000&format=ubv&format=ubv",
    "/internal/aiprocessors/video/export?start=2000&end=1000&format=ubv",
    "/internal/aiprocessors/video/export?start=0&end=999999999&format=ubv",
    "/internal/video/export?start=1000&end=2000&format=ubv",
])
async def test_mp4_adaptation_rejects_signed_ambiguous_or_unverified_urls(services, tmp_path, video_url):
    config = configuration(services)
    config["worker"]["request_mp4_exports"] = True
    processor = JobProcessor(config, tmp_path)
    request = {"targetUri": ":7968/on_demand_inference", "resUrl": "/internal/camera-upload/token",
               "payload": {"cameraId": "c", "eventId": "e", "timestamp": 1000, "videoUrl": video_url}}
    try:
        with pytest.raises(WorkerError, match="MP4 adaptation"):
            await processor.submit(request)
        assert services.media_requests == services.callbacks == []
    finally:
        await processor.stop()


def test_requires_explicit_inference_and_verified_tls(services, tmp_path):
    config = configuration(services)
    config["inference"].pop("model")
    with pytest.raises(WorkerError, match="inference.model"):
        JobProcessor(config, tmp_path)
    config = configuration(services)
    config["inference"]["base_url"] = "https://unconfigured-model.invalid/v1"
    with pytest.raises(WorkerError, match="Remote inference"):
        JobProcessor(config, tmp_path)
    context = ssl._create_unverified_context()
    with pytest.raises(WorkerError, match="verify certificates"):
        JobProcessor(configuration(services), tmp_path, context)


def test_plain_controller_http_requires_explicit_lab(services, tmp_path):
    config = configuration(services)
    config["runtime"]["mode"] = "device"
    with pytest.raises(WorkerError, match="Controller HTTP"):
        JobProcessor(config, tmp_path)


@pytest.mark.asyncio
async def test_missing_embeddings_fails_explicitly_when_requested(services, tmp_path):
    config = configuration(services)
    config["worker"]["description_embeddings"] = True
    config["embeddings"] = {"backend": "disabled"}
    processor = JobProcessor(config, tmp_path)
    try:
        with pytest.raises(WorkerError, match="embedding backend"):
            await processor.handle(command())
        assert services.callbacks == []
    finally:
        await processor.stop()


@pytest.mark.asyncio
async def test_real_embedding_http_is_used_for_optional_description_vector(services, tmp_path):
    config = configuration(services)
    config["worker"]["description_embeddings"] = True
    config["embeddings"] = {"backend": "http", "base_url": services.origin + "/v1",
                            "model": "intfloat/multilingual-e5-small"}
    processor = JobProcessor(config, tmp_path)
    try:
        await processor.handle(command())
        assert services.embedding_requests[0]["input"] == ["passage: " + DESCRIPTION]
        vector = services.callbacks[0]["payload"]["descEmbedding"]
        assert len(vector) == 384 and math.isclose(math.hypot(*vector), 1)
    finally:
        await processor.stop()


@pytest.mark.asyncio
async def test_worker_mtls_and_pin_checked_before_controller_headers(services, tmp_path):
    from aikey.tls import ensure_identity_certificate

    controller_cert, controller_key = ensure_identity_certificate(tmp_path / "controller", "020000000001")
    client_cert, client_key = ensure_identity_certificate(tmp_path / "client", "020000000002")
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(controller_cert, controller_key)
    server_tls.load_verify_locations(cafile=client_cert)
    server_tls.verify_mode = ssl.CERT_REQUIRED
    client_tls = ssl.create_default_context(cafile=controller_cert)
    client_tls.load_cert_chain(client_cert, client_key)
    app = web.Application()
    app.router.add_get("/internal/aiprocessors/image/{image}", services.image)
    app.router.add_post("/internal/aiprocessors/descriptions/{task}", services.callback)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_tls)
    await site.start()
    origin = f"https://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    config = configuration(services)
    config["runtime"]["mode"] = "device"
    config["controller_origins"] = [origin]
    config["controller"] = {"expected_fingerprint": "00" * 32}
    bad = JobProcessor(config, tmp_path / "bad", client_tls)
    try:
        with pytest.raises(WorkerError, match="fingerprint mismatch"):
            await bad.handle(command())
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        await bad.stop()
    der = ssl.PEM_cert_to_DER_cert(controller_cert.read_text())
    config["controller"]["expected_fingerprint"] = hashlib.sha256(der).hexdigest()
    client_tls.check_hostname = False  # Explicit pin still precedes HTTP writes.
    good = JobProcessor(config, tmp_path / "good", client_tls)
    try:
        result = await good.handle(command())
        assert result["callback"] == "http_accepted"
        assert len(services.media_requests) == len(services.callbacks) == 1
    finally:
        await good.stop()
        await runner.cleanup()
