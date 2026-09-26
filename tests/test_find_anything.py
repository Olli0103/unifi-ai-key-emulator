"""Basic Find Anything: local CLIP query vectors and thumbnailTags indexing (#2, #21)."""

import asyncio
import io
import json
import shutil
from urllib.parse import urlencode

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
import pytest
import pytest_asyncio

from aikey import clip
from aikey.clip_server import InputError, build_app, parse_regions, preprocess
from aikey.config import ConfigError, validate_config
from aikey.device import DeviceService
from aikey.protocol import decode_message, encode_message
from aikey.search import SearchService
from aikey.worker import JobProcessor, WorkerError
from test_basic_descriptions import device_config, wire

CAMERA, EVENT = "index-camera-fixture", "index-event-fixture"
START, END = 1_700_000_000_000, 1_700_000_004_000
UNIT = [1.0] + [0.0] * 767


def vector(index):
    values = [0.0] * 768
    values[index] = 2.0
    return values


class Controller:
    def __init__(self):
        self.video = b""
        self.callbacks, self.clip_requests, self.vision_requests, self.text_requests = [], [], [], []
        self.vision_reply = None

    async def export(self, request):
        return web.Response(body=self.video, content_type="video/mp4",
                            headers={"x-start-timestamp": str(START)})

    async def image(self, request):
        reader = await request.multipart()
        parts = {}
        while (part := await reader.next()) is not None:
            parts[part.name] = await part.read(decode=False)
        regions = json.loads(parts["regions"])
        self.clip_requests.append({"regions": regions, "jpeg": parts["image"][:3] == b"\xff\xd8\xff"})
        return web.json_response({"model": clip.MODEL, "dim": 768,
                                  "embeddings": [vector(i) for i in range(len(regions))]})

    async def crop(self, request):
        self.crop_requests = getattr(self, "crop_requests", []) + [request.match_info["image"]]
        from PIL import Image
        out = io.BytesIO()
        Image.new("RGB", (96, 160), (90, 90, 90)).save(out, "JPEG" if request.match_info["image"] != "png1" else "PNG")
        return web.Response(body=out.getvalue(), content_type="image/jpeg")

    async def text(self, request):
        self.text_requests.append(await request.json())
        return web.json_response({"model": clip.MODEL, "dim": 768, "embeddings": [vector(5)]})

    async def vision(self, request):
        self.vision_requests.append(True)
        if self.vision_reply is None:
            return web.json_response({}, status=500)
        return web.json_response(self.vision_reply)

    async def callback(self, request):
        reader = await request.multipart()
        parts = {}
        while (part := await reader.next()) is not None:
            body = await part.read(decode=False)
            parts[part.name] = json.loads(body) if part.name == "ram" else (
                part.headers.get("Content-Type"), body[:3])
        self.callbacks.append(parts)
        return web.json_response({"ram": None})


@pytest_asyncio.fixture
async def controller(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("Real ffmpeg executable unavailable")
    service = Controller()
    video = tmp_path / "synthetic.mp4"
    process = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=gray:s=320x240:r=5:d=4", "-c:v", "mpeg4", "-y", str(video))
    assert await process.wait() == 0
    service.video, service.ffmpeg = video.read_bytes(), ffmpeg
    app = web.Application()
    app.router.add_get("/internal/aiprocessors/video/export", service.export)
    app.router.add_post("/v1/image", service.image)
    app.router.add_get("/internal/aiprocessors/image/{image}", service.crop)
    app.router.add_post("/v1/text", service.text)
    app.router.add_post("/v1/chat/completions", service.vision)
    app.router.add_post("/internal/aiprocessors/recognize-anything", service.callback)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    service.origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield service
    finally:
        await runner.cleanup()


def config(service, **extra):
    options = {"runtime": {"mode": "lab"}, "controller_origins": [service.origin],
               "device": {"mac": "02:00:00:00:00:98"},
               "inference": {"base_url": service.origin + "/v1", "model": "synthetic-vision"},
               "search": {"enabled": True, "profile": clip.PROFILE},
               "find_anything": {"clip_server": service.origin, "index_camera_ids": [CAMERA]},
               "worker": {"max_queue": 2, "timeout_s": 20, "ffmpeg_path": service.ffmpeg}}
    options.update(extra)
    return options


def roi(tracker, ts, coord, confidence=0.9, kind="person"):
    return {"roi": {"name": "", "coord": coord, "trackerId": tracker,
                    "attributes": {"objectType": kind}, "confidence": confidence,
                    "objectType": kind}, "ts": ts}


def task(camera=CAMERA, meta=None, ram_type="video"):
    body = {"camera": camera, "event": EVENT, "channel": 0, "start": START, "end": END,
            "type": "rotating", "mute": True, "format": "mp4", "createEvent": False}
    query = {k: ("true" if v is True else "false" if v is False else str(v)) for k, v in body.items()}
    payload = {"reqUrl": "/internal/aiprocessors/video/export?" + urlencode(query),
               "resUrl": "/internal/aiprocessors/recognize-anything", "ramType": ram_type,
               "keyMoments": [START + 1000], "postVLM": False, "thumbnailMs": [START + 1500],
               "thumbnailMeta": meta if meta is not None else [
                   roi(3, START + 1500, [100, 200, 300, 400]),
                   roi(4, START + 1500, [600, 100, 200, 500], confidence=0.7, kind="vehicle"),
                   roi(3, START + 9000, [0, 0, 100, 100])],        # outside the export
               **body}
    return {"command": "recognizeKeyFrames", "payload": payload}


async def test_objects_are_embedded_locally_and_posted_as_thumbnail_tags(controller, tmp_path):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        result = await worker.handle(task())
    finally:
        await worker.stop()
    assert controller.vision_requests == []                 # never the vision provider
    # One decoded frame; both objects cropped with 10% padding, best first.
    assert controller.clip_requests == [{"jpeg": True, "regions": [
        [0.07, 0.16, 0.43, 0.64], [0.58, 0.05, 0.82, 0.65]]}]
    [parts] = controller.callbacks
    assert set(parts) == {"ram"}
    ram = parts["ram"]
    assert ram["cameraId"] == CAMERA and ram["eventId"] == EVENT and ram["status"] == "success"
    assert ram["description"] == "" and ram["keyMomentsTags"] == []
    tags = ram["thumbnailTags"]
    # saveEventTagging matches (trackerID, keyMomentMs) to trackerId and exact detectedAt.
    assert [(t["trackerID"], t["keyMomentMs"], t["tags"]) for t in tags] == [
        (3, START + 1500, []), (4, START + 1500, [])]
    assert all(len(t["imgEmbed"]) == 768 for t in tags)
    assert tags[0]["imgEmbed"][0] == 1.0 and tags[1]["imgEmbed"][1] == 1.0   # normalized
    assert result["result"] == {"indexed": 2, "snapshots": 0}
    journal = "".join(p.read_text() for p in (tmp_path / "worker-jobs").glob("*.json"))
    assert "imgEmbed" not in journal


async def test_index_jobs_never_consume_a_caption_permit(controller, tmp_path):
    options = config(controller)
    options["worker"]["test_scope"] = {"kind": "recognizeKeyFrames", "permit_id": "other-camera",
                                       "camera_id": "caption-camera-fixture"}
    worker = JobProcessor(options, tmp_path)
    try:
        await worker.handle(task())
    finally:
        await worker.stop()
    assert list((tmp_path / "worker-test-scopes").glob("*.json")) == []


async def test_tasks_without_indexable_objects_are_refused_before_media(controller, tmp_path):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        for meta in ([], [roi(3, START + 9000, [0, 0, 10, 10])]):
            with pytest.raises(WorkerError, match="no indexable objects"):
                await worker.handle(task(meta=meta))
        with pytest.raises(WorkerError, match="Region metadata entries"):
            await worker.handle(task(meta=[roi(3, START, [0, 0, 1200, 10])]))
        with pytest.raises(WorkerError):
            await worker.handle(task(ram_type="image"))
    finally:
        await worker.stop()
    assert controller.clip_requests == [] and controller.callbacks == []


async def test_other_cameras_are_not_indexed(controller, tmp_path):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError):
            await worker.handle(task(camera="unlisted-camera"))
    finally:
        await worker.stop()
    assert controller.clip_requests == []


async def test_indexing_needs_the_enabled_clip_search_profile(controller, tmp_path):
    options = config(controller, search={"enabled": False, "profile": clip.PROFILE})
    worker = JobProcessor(options, tmp_path)
    try:
        with pytest.raises(WorkerError):
            await worker.handle(task())
    finally:
        await worker.stop()
    assert controller.clip_requests == []


async def test_a_captioned_camera_carries_thumbnail_tags_in_its_caption_callback(controller, tmp_path):
    options = config(controller)
    options["worker"]["test_scope"] = {"kind": "recognizeKeyFrames", "permit_id": "once",
                                       "camera_id": CAMERA}
    controller.vision_reply = {"choices": [{"finish_reason": "stop",
                                             "message": {"content": "A person walks."}}]}
    worker = JobProcessor(options, tmp_path)
    command = task()
    command["payload"]["postVLM"] = True
    try:
        await worker.handle(command)
    finally:
        await worker.stop()
    [parts] = controller.callbacks
    assert [t["trackerID"] for t in parts["ram"]["thumbnailTags"]] == [3, 4]


async def test_the_device_routes_index_cameras_to_the_worker(tmp_path):
    calls = []

    async def admit(body):
        calls.append(body)
        return {"accepted": True}
    options = device_config()
    options["search"] = {"enabled": True, "profile": clip.PROFILE}
    options["find_anything"] = {"clip_server": "http://127.0.0.1:8180", "index_camera_ids": [CAMERA]}
    device = DeviceService(options, tmp_path, admit)
    reply = decode_message(await device.handle_message(wire("recognizeKeyFrames", task()["payload"])))
    assert reply.header["errorCode"] == 0 and calls[0]["payload"]["camera"] == CAMERA
    assert device.status["recognize_key_frames"]["metadata_presence_counts"]["thumbnailMeta"] == 1
    counts = device.status["recognize_key_frames"]["thumbnail_meta_counts"]
    assert {k: v for k, v in counts.items() if v} == {
        "tasks_with_objects": 1, "objects_inside": 2, "objects_after_end": 1, "name_empty": 3,
        "ts_in_thumbnail_ms": 2, "ts_not_in_thumbnail_ms": 1}
    shapes = device.status["recognize_key_frames"]["region_shape_counts"]["thumbnailMeta"]
    assert {k: v for k, v in shapes.items() if v} == {
        "entries": 3, "max_le_1000": 3, "xywh_fits_1000": 3, "xyxy_ordered": 2,
        "type_person": 2, "type_vehicle": 1}


async def test_nl_parse_is_answered_with_a_local_clip_text_vector(controller, tmp_path):
    options = {"search": {"enabled": True, "profile": clip.PROFILE},
               "find_anything": {"clip_server": controller.origin}}
    service = SearchService(options, tmp_path)
    try:
        response = decode_message(await service.handle_message(encode_message(
            {"id": "q1", "type": "request", "action": "NL_PARSE", "timestamp": 1},
            {"querySentence": "red car", "model": clip.MODEL})))
        assert response.header["errorCode"] == 0 and response.header["id"] == "q1"
        body = response.body
        assert body["model"] == clip.MODEL and body["dim"] == 768 and body["exact_match"] is False
        assert body["keyTags"] == [] and body["objectTypes"] == []
        assert body["txtEmbed"][5] == 1.0 and len(body["txtEmbed"]) == 768
        # Protect's default model is clip-ViT-L-14 when a request omits it.
        response = decode_message(await service.handle_message(encode_message(
            {"id": "q2", "type": "request", "action": "NL_PARSE", "timestamp": 1},
            {"querySentence": "red car"})))
        assert response.header["errorCode"] == 0
        # Deep-mode E5 queries are not answered with CLIP vectors.
        response = decode_message(await service.handle_message(encode_message(
            {"id": "q3", "type": "request", "action": "NL_PARSE", "timestamp": 1},
            {"querySentence": "red car", "model": "multilingual-e5-small"})))
        assert response.header["errorCode"] == 1 and response.body == {}
        assert service.status["queries"] == 2 and service.status["query_failures"] == 1
        assert controller.text_requests == [{"texts": ["red car"]}, {"texts": ["red car"]}]
        service._check_profile()
        profile = json.loads((tmp_path / "search-profile.json").read_text())
        assert profile["profile"] == clip.PROFILE and profile["dimensions"] == 768
    finally:
        await service.stop()


@pytest.mark.parametrize("server", ["https://127.0.0.1:8180", "http://8.8.8.8:8180",
                                    "http://clip.example:8180", "http://127.0.0.1:8180/v1",
                                    "http://user:pw@127.0.0.1:8180"])
def test_the_clip_server_must_be_local(server):
    with pytest.raises(clip.ClipError):
        clip.validate_find_anything_config({"clip_server": server})


def test_config_validates_find_anything(tmp_path):
    from aikey.config import defaults
    base = defaults(tmp_path, "02:00:00:00:00:98")
    base["search"]["profile"] = clip.PROFILE
    with pytest.raises(ConfigError):
        validate_config(base, base=tmp_path)            # clip profile needs find_anything
    base["find_anything"] = {"clip_server": "http://192.168.64.1:8180/", "index_camera_ids": ["a"]}
    assert validate_config(base, base=tmp_path)["find_anything"]["clip_server"] == "http://192.168.64.1:8180"
    base["search"]["profile"] = "unknown"
    with pytest.raises(ConfigError):
        validate_config(base, base=tmp_path)


def test_vectors_are_checked_and_normalized():
    assert clip.normalize(vector(2))[2] == 1.0
    for bad in ([1.0] * 767, [float("nan")] * 768, [0.0] * 768, ["1"] * 768):
        with pytest.raises(clip.ClipError):
            clip.normalize(bad)


def test_regions_are_validated():
    assert parse_regions("[[0.1, 0.1, 0.5, 0.5]]") == [(0.1, 0.1, 0.5, 0.5)]
    for raw in ("[]", "[[0.5, 0.1, 0.1, 0.5]]", "[[0, 0, 2, 1]]", "nope", "[[0,0,1]]"):
        with pytest.raises(InputError):
            parse_regions(raw)


def test_preprocessing_matches_clip_geometry():
    from PIL import Image
    pixels = preprocess(Image.new("RGB", (640, 360), (255, 255, 255)))
    assert pixels.shape == (3, 224, 224)
    assert abs(float(pixels[0, 0, 0]) - (1 - 0.48145466) / 0.26862954) < 1e-4


async def test_the_clip_server_returns_normalized_vectors_and_counts_only():
    seen = []

    def text(texts):
        seen.append(texts)
        return [vector(1) for _ in texts]

    def image(jpeg, regions):
        return [vector(0) for _ in (regions or [None])]
    async with TestClient(TestServer(build_app(text, image))) as client:
        body = await (await client.post("/v1/text", json={"texts": ["a dog "]})).json()
        assert body["model"] == clip.MODEL and body["dim"] == 768 and body["embeddings"][0][1] == 1.0
        assert seen == [["a dog"]]
        form = FormData()
        form.add_field("image", b"\xff\xd8\xff" + b"0" * 64, filename="f.jpg", content_type="image/jpeg")
        form.add_field("regions", "[[0, 0, 0.5, 0.5], [0.5, 0.5, 1, 1]]")
        body = await (await client.post("/v1/image", data=form)).json()
        assert len(body["embeddings"]) == 2
        assert (await client.post("/v1/text", json={"texts": []})).status == 400
        assert (await client.post("/v1/text", json={"texts": ["x" * 2000]})).status == 400
        form = FormData()
        form.add_field("image", b"GIF89a", filename="f.gif", content_type="image/gif")
        assert (await client.post("/v1/image", data=form)).status == 400
        health = await (await client.get("/healthz")).json()
        assert health == {"status": "ok", "model": clip.MODEL, "dim": 768, "text_requests": 3,
                          "image_requests": 2, "texts": 1, "regions": 2, "rejected": 3, "failed": 0}


def test_real_clip_weights_rank_the_matching_text_first():
    """Runs only where the pinned ONNX export is present (not in CI)."""
    from pathlib import Path
    models = Path(__file__).resolve().parents[1] / "state" / "clip-models"
    if not (models / "onnx" / "vision_model.onnx").is_file():
        pytest.skip("CLIP ONNX weights are not present")
    pytest.importorskip("onnxruntime")
    from PIL import Image, ImageDraw
    from aikey.clip_server import onnx_encoders
    encode_text, encode_image = onnx_encoders(str(models), threads=2)
    picture = Image.new("RGB", (640, 360), "white")
    ImageDraw.Draw(picture).rectangle([220, 80, 420, 280], fill="red")
    jpeg = io.BytesIO()
    picture.save(jpeg, "JPEG")
    [image_vector] = [clip.normalize(v) for v in encode_image(jpeg.getvalue(), None)]
    texts = [clip.normalize(v) for v in encode_text(["a red square", "a blue circle", "a car"])]
    scores = [sum(a * b for a, b in zip(image_vector, t)) for t in texts]
    assert scores[0] == max(scores)
    # Protect's text slider maps to cosine distances 0.60..0.92.
    assert 0.60 <= 1 - scores[0] <= 0.92


def test_the_search_port_can_carry_its_own_certificate_pin(tmp_path):
    options = {"controller": {"expected_fingerprint": "aa" * 32, "search_expected_fingerprint": "bb" * 32}}
    assert SearchService(options, tmp_path)._fingerprint() == "bb" * 32
    assert SearchService({"controller": {"expected_fingerprint": "aa" * 32}}, tmp_path)._fingerprint() == "aa" * 32
    from aikey.config import defaults
    base = defaults(tmp_path, "02:00:00:00:00:98")
    base["controller"]["search_expected_fingerprint"] = "bb" * 32
    with pytest.raises(ConfigError, match="go together"):
        validate_config(base, base=tmp_path)
    base["controller"]["search_ca_file"] = "search-ca.pem"
    assert validate_config(base, base=tmp_path)["controller"]["search_ca_file"] == str(tmp_path / "search-ca.pem")
    base["controller"]["search_expected_fingerprint"] = "not-a-pin"
    with pytest.raises(ConfigError):
        validate_config(base, base=tmp_path)


def test_protect_error_replies_with_an_empty_string_body_decode():
    import struct
    header = json.dumps({"id": "echo-1", "type": "error", "errorCode": 1002, "error": "No handlers"}).encode()
    wire = struct.pack(">BBBBI", 1, 1, 0, 0, len(header)) + header + struct.pack(">BBBBI", 2, 2, 0, 0, 0)
    message = decode_message(wire)
    assert message.header["type"] == "error" and message.body == {}
    from aikey.protocol import ContractError
    with pytest.raises(ContractError):
        decode_message(struct.pack(">BBBBI", 1, 1, 0, 0, len(header)) + header
                       + struct.pack(">BBBBI", 2, 2, 0, 0, 2) + b"hi")


def live_roi(ts, *objects):
    """The live 7.3.68 shape: one entry per timestamp with a list of objects."""
    return {"ts": ts, "roi": [{"coord": coord, "trackerId": tracker, "confidence": confidence,
                               "name": kind, "objectType": kind} for tracker, coord, kind, confidence in objects]}


async def test_live_list_shaped_thumbnail_meta_is_indexed(controller, tmp_path):
    meta = [live_roi(START + 1500, (3, [100, 200, 300, 400], "person", 0.9),
                     (4, [600, 100, 200, 500], "vehicle", 0.7))]
    worker = JobProcessor(config(controller), tmp_path)
    try:
        await worker.handle(task(meta=meta))
    finally:
        await worker.stop()
    [parts] = controller.callbacks
    assert [(t["trackerID"], t["keyMomentMs"]) for t in parts["ram"]["thumbnailTags"]] == [
        (3, START + 1500), (4, START + 1500)]


async def test_key_moment_regions_become_search_snapshots_with_crops(controller, tmp_path):
    command = task(meta=[])
    command["payload"]["roiMeta"] = [
        live_roi(START + 1000, (5, [100, 100, 200, 300], "person", 80), (6, [500, 500, 100, 100], "face", 99)),
        live_roi(START + 2000, (5, [120, 100, 200, 300], "person", 60), (7, [0, 0, 400, 200], "vehicle", 70)),
        live_roi(START + 9000, (8, [0, 0, 100, 100], "animal", 99))]            # outside the export
    worker = JobProcessor(config(controller), tmp_path)
    try:
        result = await worker.handle(command)
    finally:
        await worker.stop()
    assert controller.vision_requests == []
    [parts] = controller.callbacks
    ram = parts["ram"]
    assert ram.get("thumbnailTags") == [] and ram["description"] == ""
    moments = ram["keyMomentsTags"]
    # One snapshot per tracker (best confidence), faces never, outside-export never.
    assert [(m["keyMomentMs"], m["searchSnapshots"][0]["trackerID"],
             m["searchSnapshots"][0]["smartDetectSnapshotType"]) for m in moments] == [
        (START + 1000, 5, "person"), (START + 2000, 7, "vehicle")]
    snapshot = moments[0]["searchSnapshots"][0]
    # Protect's snapshotSchema fields, exactly.
    assert set(snapshot) == {"clockBestMonotonic", "clockBestWall", "smartDetectHeatmap",
                             "smartDetectSnapshot", "smartDetectSnapshotName",
                             "smartDetectSnapshotType", "trackerID"}
    assert snapshot["clockBestWall"] == START + 1000 and snapshot["smartDetectSnapshot"] == "5.jpg"
    assert all(len(m["imgEmbed"]) == 768 and m["tags"] == [] for m in moments)
    assert parts["5"] == ("image/jpeg", b"\xff\xd8\xff") and parts["7"] == ("image/jpeg", b"\xff\xd8\xff")
    assert result["result"] == {"indexed": 0, "snapshots": 2}


async def test_existing_objects_take_precedence_over_snapshots(controller, tmp_path):
    command = task()
    command["payload"]["roiMeta"] = [live_roi(START + 1000, (5, [100, 100, 200, 300], "person", 80))]
    worker = JobProcessor(config(controller), tmp_path)
    try:
        await worker.handle(command)
    finally:
        await worker.stop()
    [parts] = controller.callbacks
    assert parts["ram"]["keyMomentsTags"] == [] and len(parts["ram"]["thumbnailTags"]) == 2
    assert set(parts) == {"ram"}


class _FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

        class _Content:
            async def iter_chunked(_self, size):
                yield body
        self.content = _Content()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    closed = False

    def __init__(self, status=200, body=b""):
        self.status, self.body, self.requests = status, body, []

    def get(self, url, headers=None, **kwargs):
        self.requests.append((url, headers))
        return _FakeResponse(self.status, self.body)


def _image(fmt):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(out, fmt)
    return out.getvalue()


def image_search(uri, identifier="img-1"):
    return encode_message({"id": identifier, "type": "request", "action": "IMAGE_SEARCH", "timestamp": 1},
                          {"imgUri": uri})


async def test_search_by_image_returns_a_local_clip_vector(controller, tmp_path):
    options = {"search": {"enabled": True, "profile": clip.PROFILE},
               "find_anything": {"clip_server": controller.origin},
               "controller": {"host": "192.168.0.1"}, "device": {"mac": "02:9d:90:a5:48:ca"}}
    service = SearchService(options, tmp_path)
    uri = "https://192.168.0.1:7444/internal/files/recognizeImage/upload-1.jpg"
    try:
        for body in (_image("JPEG"), _image("PNG")):
            service._media_session = _FakeSession(body=body)
            response = decode_message(await service.handle_message(image_search(uri)))
            assert response.header["errorCode"] == 0 and set(response.body) == {"imgEmbed"}
            assert len(response.body["imgEmbed"]) == 768 and response.body["imgEmbed"][0] == 1.0
            [(url, headers)] = service._media_session.requests
            assert url == uri and headers["x-ident"] == "029D90A548CA"
        assert controller.clip_requests[-1] == {"jpeg": True, "regions": [[0.0, 0.0, 1.0, 1.0]]}
        assert service.status["image_queries"] == 2 and service.status["image_failures"] == {}
    finally:
        service._media_session = None
        await service.stop()


@pytest.mark.parametrize("uri,category", [
    ("http://192.168.0.1:7443/internal/files/recognizeImage/a.jpg", "uri"),
    ("https://192.168.0.2:7444/internal/files/recognizeImage/a.jpg", "uri"),
    ("https://192.168.0.1:7444/api/cameras", "uri"),
    ("https://192.168.0.1:7443/internal/files/recognizeImage/a.jpg", "uri"),     # not the media port
    ("https://192.168.0.1:7444/internal/files/../api", "uri"),
    ("https://192.168.0.1:7444/internal/files/recognizeImage/a.jpg?x=1", "uri"),
    (None, "uri"),
])
async def test_search_by_image_fetches_only_the_console_upload_route(tmp_path, uri, category):
    options = {"search": {"enabled": True, "profile": clip.PROFILE},
               "find_anything": {"clip_server": "http://127.0.0.1:1"},
               "controller": {"host": "192.168.0.1"}, "device": {"mac": "02:9d:90:a5:48:ca"}}
    service = SearchService(options, tmp_path)
    service._media_session = _FakeSession(body=_image("JPEG"))
    response = decode_message(await service.handle_message(image_search(uri)))
    assert response.header["errorCode"] == 1 and response.body == {}
    assert service._media_session.requests == [] and service.status["image_failures"] == {category: 1}


async def test_search_by_image_refuses_non_images_and_http_errors(tmp_path):
    options = {"search": {"enabled": True, "profile": clip.PROFILE},
               "find_anything": {"clip_server": "http://127.0.0.1:1"},
               "controller": {"host": "192.168.0.1"}, "device": {"mac": "02:9d:90:a5:48:ca"}}
    service = SearchService(options, tmp_path)
    uri = "https://192.168.0.1:7444/internal/files/recognizeImage/a.jpg"
    for session in (_FakeSession(body=b"GIF89a..."), _FakeSession(status=404)):
        service._media_session = session
        assert decode_message(await service.handle_message(image_search(uri))).header["errorCode"] == 1
    assert service.status["image_failures"] == {"format": 1, "http_4xx": 1}


def test_image_search_is_advertised_only_with_the_clip_profile(tmp_path):
    async def admit(body):
        return {"accepted": True}
    options = device_config()
    assert DeviceService(options, tmp_path / "a", admit).get_info()["featureFlags"]["supportImageSearch"] == {
        "enabled": False, "version": "v1"}
    options["search"] = {"enabled": True, "profile": clip.PROFILE}
    options["find_anything"] = {"clip_server": "http://127.0.0.1:8180"}
    assert DeviceService(options, tmp_path / "b", admit).get_info()["featureFlags"]["supportImageSearch"][
        "enabled"] is True
    options["device"]["feature_flags"] = {"supportImageSearch": {"enabled": False, "version": "v1"}}
    assert DeviceService(options, tmp_path / "c", admit).get_info()["featureFlags"]["supportImageSearch"][
        "enabled"] is False


def test_capability_flags_follow_the_served_features(tmp_path):
    async def admit(body):
        return {"accepted": True}
    options = device_config()
    options["worker"] = {}
    flags = DeviceService(options, tmp_path / "a", admit).get_info()["featureFlags"]
    for name in ("supportTts", "supportFaceRecognition", "supportRecognizeAnything",
                 "supportLicensePlateRecognition", "supportImageSearch"):
        assert flags[name] == {"enabled": False, "version": "v1"}, name
    options["speech_to_text"] = {"provider": "openai-compatible", "camera_ids": ["cam-w"]}
    options["face_recognition"] = {"server": "http://127.0.0.1:8179", "camera_ids": ["cam-w"]}
    options["search"] = {"enabled": True, "profile": clip.PROFILE}
    options["find_anything"] = {"clip_server": "http://127.0.0.1:8180", "index_camera_ids": ["cam-w"]}
    flags = DeviceService(options, tmp_path / "b", admit).get_info()["featureFlags"]
    assert {name: flags[name]["enabled"] for name in (
        "supportTts", "supportFaceRecognition", "supportRecognizeAnything",
        "supportLicensePlateRecognition", "supportImageSearch")} == {
        "supportTts": True, "supportFaceRecognition": True, "supportRecognizeAnything": True,
        "supportLicensePlateRecognition": False, "supportImageSearch": True}
    options["device"]["feature_flags"] = {"supportTts": {"enabled": False, "version": "v1"}}
    assert DeviceService(options, tmp_path / "c", admit).get_info()["featureFlags"]["supportTts"][
        "enabled"] is False


def multiple_images(images, camera=CAMERA):
    return {"command": "recognizeKeyFrames", "payload": {
        "resUrl": "/internal/aiprocessors/recognize-anything", "ramType": "multipleImages",
        "format": "jpeg", "camera": camera, "event": EVENT, "channel": 0, "start": START, "end": END,
        "type": "rotating", "images": images}}


def crop_entry(image_id, tracker, moment, confidence=80, kind="person"):
    return {"reqUrl": f"/internal/aiprocessors/image/{image_id}", "imageId": image_id,
            "keyMoment": moment, "confidence": confidence, "objectType": kind,
            "attributes": {"trackerId": tracker}, "trackerId": tracker}


async def test_retroactive_crops_are_embedded_locally_as_thumbnail_tags(controller, tmp_path):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        result = await worker.handle(multiple_images([
            crop_entry("crop1", 11, START + 500), crop_entry("png1", 12, START + 900, 60, "vehicle"),
            crop_entry("crop3", 11, START + 500, 40)]))            # same object, lower confidence
    finally:
        await worker.stop()
    assert controller.vision_requests == []                     # never the vision provider
    assert controller.crop_requests == ["crop1", "png1"]
    assert [r["regions"] for r in controller.clip_requests] == [[[0.0, 0.0, 1.0, 1.0]]] * 2
    [parts] = controller.callbacks
    ram = parts["ram"]
    assert set(parts) == {"ram"} and ram["description"] == "" and ram["keyMomentsTags"] == []
    assert [(t["trackerID"], t["keyMomentMs"]) for t in ram["thumbnailTags"]] == [
        (11, START + 500), (12, START + 900)]
    assert all(len(t["imgEmbed"]) == 768 for t in ram["thumbnailTags"])
    assert result["result"] == {"indexed": 2, "snapshots": 0}


@pytest.mark.parametrize("images", [
    [],
    [{"reqUrl": "/internal/aiprocessors/image/other", "imageId": "crop1", "keyMoment": START, "trackerId": 1}],
    [{"reqUrl": "/internal/aiprocessors/image/crop1", "imageId": "crop1", "keyMoment": START}],
    [{"reqUrl": "/internal/aiprocessors/image/../x", "imageId": "../x", "keyMoment": START, "trackerId": 1}],
])
async def test_malformed_retroactive_tasks_are_refused_before_media(controller, tmp_path, images):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError):
            await worker.handle(multiple_images(images))
        with pytest.raises(WorkerError):
            await worker.handle(multiple_images([crop_entry("crop1", 1, START)], camera="unlisted"))
    finally:
        await worker.stop()
    assert getattr(controller, "crop_requests", []) == [] and controller.clip_requests == []


def test_retroactive_processing_is_advertised_only_on_opt_in(tmp_path):
    async def admit(body):
        return {"accepted": True}
    options = device_config()
    options["search"] = {"enabled": True, "profile": clip.PROFILE}
    options["find_anything"] = {"clip_server": "http://127.0.0.1:8180", "index_camera_ids": ["cam"]}
    flags = DeviceService(options, tmp_path / "a", admit).get_info()["featureFlags"]
    assert flags["supportRetroactiveProcessing"] == {"enabled": False, "version": "v1"}
    options["find_anything"]["retroactive"] = True
    flags = DeviceService(options, tmp_path / "b", admit).get_info()["featureFlags"]
    assert flags["supportRetroactiveProcessing"]["enabled"] is True
    with pytest.raises(clip.ClipError):
        clip.validate_find_anything_config({"clip_server": "http://127.0.0.1:8180", "retroactive": "yes"})
