"""Opt-in local face enhancement: Protect's enhanceImage task and callback (#23)."""

from io import BytesIO

from aiohttp import web
from PIL import Image
import pytest
import pytest_asyncio

from aikey.device import CommandFailure, DeviceService
from aikey.worker import JobProcessor, WorkerError
from test_basic_descriptions import device_config

IMAGE, CAMERA, OBJECT = "crop-fixture-1", "face-camera-fixture", "object-fixture-1"


def jpeg(size, fmt="JPEG"):
    out = BytesIO()
    Image.new("RGB", size, (90, 90, 90)).save(out, fmt)
    return out.getvalue()


class Controller:
    def __init__(self):
        self.original = jpeg((64, 64))
        self.enhancer_reply = (200, jpeg((128, 128)))
        self.uploads, self.enhance_requests = [], 0

    async def image(self, request):
        assert request.match_info["image"] == IMAGE
        return web.Response(body=self.original, content_type="image/jpeg")

    async def enhance(self, request):
        self.enhance_requests += 1
        await request.read()
        status, body = self.enhancer_reply
        return web.Response(status=status, body=body or None, content_type="image/jpeg")

    async def enhanced(self, request):
        reader = await request.multipart()
        parts = {}
        while (part := await reader.next()) is not None:
            parts[part.name] = await part.read(decode=False)
        self.uploads.append(parts)
        return web.json_response({"bytes": len(parts.get("file", b""))})


@pytest_asyncio.fixture
async def controller():
    service = Controller()
    app = web.Application()
    app.router.add_get("/internal/aiprocessors/image/{image}", service.image)
    app.router.add_post("/v1/enhance", service.enhance)
    app.router.add_post("/internal/aiprocessors/image/enhanced", service.enhanced)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    service.origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield service
    finally:
        await runner.cleanup()


def config(service, enhancer=True):
    options = {"runtime": {"mode": "lab"}, "controller_origins": [service.origin],
               "device": {"mac": "02:00:00:00:00:99"},
               "inference": {"base_url": service.origin + "/v1", "model": "synthetic-vision"},
               "worker": {"max_queue": 2, "timeout_s": 20}}
    if enhancer:
        options["face_enhancement"] = {"server": service.origin}
    return options


def task(**changes):
    payload = {"reqUrl": f"/internal/aiprocessors/image/{IMAGE}?type=face&camera={CAMERA}&smartDetectObject={OBJECT}",
               "resUrl": "/internal/aiprocessors/image/enhanced", "imageId": IMAGE,
               "type": "face", "camera": CAMERA, "smartDetectObject": OBJECT, **changes}
    return {"command": "enhanceImage", "payload": payload}


async def test_an_enhanced_face_is_uploaded_as_a_separate_derivative(controller, tmp_path):
    worker = JobProcessor(config(controller), tmp_path)
    original = controller.original
    try:
        result = await worker.handle(task())
        counts = worker.status()["enhance"]
    finally:
        await worker.stop()
    [upload] = controller.uploads
    assert upload["camera"] == CAMERA.encode() and upload["type"] == b"face"
    assert upload["smartDetectObject"] == OBJECT.encode()
    with Image.open(BytesIO(upload["file"])) as picture:
        assert picture.size == (128, 128) and picture.format == "JPEG"
    assert controller.original == original                       # the source is never changed
    assert result["result"] == {"enhanced": True, "bytes": len(upload["file"])}
    assert counts == {"requests": 1, "uploaded": 1, "declined": 0, "rejected_output": 0}


@pytest.mark.parametrize("reply,reason", [
    ((204, b""), "declined"),
    ((200, jpeg((32, 32))), "rejected_output"),                  # smaller than the source
    ((200, jpeg((128, 128), "PNG")), "rejected_output"),          # not a JPEG
    ((200, jpeg((4096, 4096))), "rejected_output"),               # beyond the size bound
])
async def test_declined_or_unsafe_output_uploads_no_modification(controller, tmp_path, reply, reason):
    controller.enhancer_reply = reply
    worker = JobProcessor(config(controller), tmp_path)
    try:
        result = await worker.handle(task())
        counts = worker.status()["enhance"]
    finally:
        await worker.stop()
    [upload] = controller.uploads
    assert upload["file"] == b"" and result["result"] == {"enhanced": False, "bytes": 0}
    assert counts["declined"] == 1 and counts["uploaded"] == 0
    assert counts["rejected_output"] == (1 if reason == "rejected_output" else 0)


async def test_an_enhancer_failure_fails_the_task_without_an_upload(controller, tmp_path):
    controller.enhancer_reply = (500, b"")
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError, match="HTTP 500"):
            await worker.handle(task())
    finally:
        await worker.stop()
    assert controller.uploads == []


@pytest.mark.parametrize("changes,error", [
    ({"type": "person"}, "one face crop"),
    ({"imageId": "../x"}, "one face crop"),
    ({"reqUrl": "/internal/aiprocessors/image/other-crop"}, "named face crop"),
    ({"resUrl": "/internal/aiprocessors/recognize-anything"}, "enhanced-image callback"),
    ({"extra": 1}, "payload fields"),
])
async def test_malformed_enhance_tasks_are_refused_before_any_fetch(controller, tmp_path, changes, error):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError, match=error):
            await worker.handle(task(**changes))
    finally:
        await worker.stop()
    assert controller.enhance_requests == 0 and controller.uploads == []


async def test_without_a_configured_enhancer_the_task_is_refused(controller, tmp_path):
    worker = JobProcessor(config(controller, enhancer=False), tmp_path)
    try:
        with pytest.raises(WorkerError, match="configured local enhancer"):
            await worker.handle(task())
    finally:
        await worker.stop()


@pytest.mark.parametrize("server", ["https://127.0.0.1:1", "http://8.8.8.8:1", "http://127.0.0.1:1/v1"])
def test_the_enhancer_must_be_local(tmp_path, server):
    options = config(type("S", (), {"origin": "http://127.0.0.1:9"})())
    options["face_enhancement"] = {"server": server}
    with pytest.raises(WorkerError, match="local HTTP server"):
        JobProcessor(options, tmp_path)


async def test_the_capability_and_command_follow_the_opt_in(tmp_path):
    admitted = []

    async def admit(body):
        admitted.append(body)
        return {"accepted": True}
    off = device_config()
    device = DeviceService(off, tmp_path / "off", admit)
    assert device.get_info()["featureFlags"]["supportFaceEnhancement"]["enabled"] is False
    with pytest.raises(CommandFailure):
        await device._command("enhanceImage", task()["payload"])
    on = device_config()
    on["face_enhancement"] = {"server": "http://127.0.0.1:8190"}
    device = DeviceService(on, tmp_path / "on", admit)
    assert device.get_info()["featureFlags"]["supportFaceEnhancement"]["enabled"] is True
    assert await device._command("enhanceImage", task()["payload"]) == {}
    assert admitted[0]["command"] == "enhanceImage"


# --- #3 boundaries of the enhancement route (synthetic media only) ---------

def jpeg_with_extras():
    """A valid JPEG carrying EXIF text and bytes appended after its end marker."""
    out = BytesIO()
    image = Image.new("RGB", (128, 128), (120, 120, 120))
    exif = Image.Exif()
    exif[0x010E] = "hidden-note-in-exif"             # ImageDescription
    image.save(out, "JPEG", exif=exif)
    return out.getvalue() + b"<html>appended polyglot payload</html>"


def huge_declared_jpeg():
    """A tiny JPEG whose header declares 20000x20000 pixels (a decompression bomb shape)."""
    out = BytesIO()
    Image.new("RGB", (8, 8)).save(out, "JPEG")
    data = bytearray(out.getvalue())
    marker = data.index(b"\xff\xc0")                 # SOF0: ... height(2) width(2)
    data[marker + 5:marker + 9] = (20000).to_bytes(2, "big") * 2
    return bytes(data)


async def test_the_enhancer_is_called_without_the_controller_session(controller, tmp_path):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        await worker.start()
        used = []
        real_post = worker._session.post

        def spy(url, *args, **kwargs):
            used.append(url)
            return real_post(url, *args, **kwargs)
        worker._session.post = spy
        await worker.handle(task())
    finally:
        await worker.stop()
    # The controller session (device TLS identity and pin) only posts the callback.
    assert used == [controller.origin + "/internal/aiprocessors/image/enhanced"]
    assert controller.enhance_requests == 1


async def test_metadata_and_appended_bytes_never_reach_protect(controller, tmp_path):
    controller.enhancer_reply = (200, jpeg_with_extras())
    worker = JobProcessor(config(controller), tmp_path)
    try:
        await worker.handle(task())
    finally:
        await worker.stop()
    [upload] = controller.uploads
    stored = upload["file"]
    assert b"hidden-note-in-exif" not in stored and b"polyglot" not in stored
    assert stored.endswith(b"\xff\xd9")
    with Image.open(BytesIO(stored)) as picture:
        assert picture.format == "JPEG" and picture.size == (128, 128)
        assert not picture.getexif()


async def test_a_huge_declared_size_is_refused_before_decoding(controller, tmp_path, monkeypatch):
    controller.enhancer_reply = (200, huge_declared_jpeg())
    decoded = []
    real_load = Image.Image.load

    def counting_load(self):
        if self.size[0] > 4096:
            decoded.append(self.size)
        return real_load(self)
    monkeypatch.setattr(Image.Image, "load", counting_load)
    worker = JobProcessor(config(controller), tmp_path)
    try:
        await worker.handle(task())
        counts = worker.status()["enhance"]
    finally:
        await worker.stop()
    assert decoded == []                               # never decoded
    assert controller.uploads[0]["file"] == b"" and counts["rejected_output"] == 1


@pytest.mark.parametrize("query", [
    f"type=face&camera=other-camera&smartDetectObject={OBJECT}",
    f"type=face&camera={CAMERA}&smartDetectObject=other-object",
    f"type=person&camera={CAMERA}&smartDetectObject={OBJECT}",
    f"type=face&camera={CAMERA}",
    f"type=face&camera={CAMERA}&smartDetectObject={OBJECT}&extra=1",
])
async def test_a_crop_url_for_another_object_is_refused(controller, tmp_path, query):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError, match="does not match the task"):
            await worker.handle(task(reqUrl=f"/internal/aiprocessors/image/{IMAGE}?{query}"))
    finally:
        await worker.stop()
    assert controller.enhance_requests == 0 and controller.uploads == []


@pytest.mark.parametrize("url", ["http://192.0.2.9/internal/aiprocessors/image/crop-fixture-1",
                                 "//evil.example/internal/aiprocessors/image/crop-fixture-1"])
async def test_a_crop_on_another_host_is_refused(controller, tmp_path, url):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError):
            await worker.handle(task(reqUrl=url))
    finally:
        await worker.stop()
    assert controller.enhance_requests == 0


async def test_redirects_are_never_followed(controller, tmp_path):
    controller.enhancer_reply = (302, b"")
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError, match="HTTP 302"):
            await worker.handle(task())
    finally:
        await worker.stop()
    assert controller.uploads == []
