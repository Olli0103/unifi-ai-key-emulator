"""Local-only AI Key face recognition (#20). Synthetic video and embeddings only."""

import asyncio
import json
import shutil
from urllib.parse import urlencode

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
import pytest
import pytest_asyncio

from aikey.device import DeviceService
from aikey.face_server import build_app, parse_regions, ImageError
from aikey.faces import FaceStore, FaceStoreError, MATCH_THRESHOLD
from aikey.protocol import decode_message
from aikey.worker import JobProcessor, WorkerError
from test_basic_descriptions import device_config, wire

CAMERA, EVENT = "face-camera-fixture", "face-event-fixture"
START, END = 1_700_000_000_000, 1_700_000_004_000
ALICE = [1.0] + [0.0] * 127
STRANGER = [0.0, 1.0] + [0.0] * 126


class Controller:
    def __init__(self):
        self.video = b""
        self.callbacks, self.face_requests, self.vision_requests = [], [], []
        self.face_reply = {"faces": [{"box": [0.4, 0.3, 0.5, 0.45], "score": 0.97,
                                      "embedding": ALICE}]}

    async def export(self, request):
        return web.Response(body=self.video, content_type="video/mp4",
                            headers={"x-start-timestamp": str(START)})

    async def faces(self, request):
        reader = await request.multipart()
        parts = {}
        while (part := await reader.next()) is not None:
            parts[part.name] = await part.read(decode=False)
        self.face_requests.append({"regions": json.loads(parts["regions"]),
                                   "jpeg": parts["image"][:3] == b"\xff\xd8\xff"})
        return web.json_response(self.face_reply)

    async def vision(self, request):
        self.vision_requests.append(True)
        return web.json_response({}, status=500)

    async def callback(self, request):
        reader = await request.multipart()
        parts = {}
        while (part := await reader.next()) is not None:
            body = await part.read(decode=False)
            parts[part.name] = (part.headers.get("Content-Type"),
                                json.loads(body) if part.name == "face" else body[:3])
        self.callbacks.append(parts)
        return web.json_response({"ram": None})


@pytest_asyncio.fixture
async def controller(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("Real ffmpeg executable unavailable")
    service = Controller()
    clip = tmp_path / "synthetic.mp4"
    process = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=gray:s=320x240:r=5:d=4", "-c:v", "mpeg4", "-y", str(clip))
    assert await process.wait() == 0
    service.video, service.ffmpeg = clip.read_bytes(), ffmpeg
    app = web.Application()
    app.router.add_get("/internal/aiprocessors/video/export", service.export)
    app.router.add_post("/v1/faces", service.faces)
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


def config(service):
    return {"runtime": {"mode": "lab"}, "controller_origins": [service.origin],
            "device": {"mac": "02:00:00:00:00:99"},
            "inference": {"base_url": service.origin + "/v1", "model": "synthetic-vision"},
            "face_recognition": {"server": service.origin, "camera_ids": [CAMERA]},
            "worker": {"max_queue": 2, "timeout_s": 20, "ffmpeg_path": service.ffmpeg}}


def task(camera=CAMERA, face_meta=True, ram_type="videoWithRecognition"):
    body = {"camera": camera, "event": EVENT, "channel": 0, "start": START, "end": END,
            "type": "rotating", "mute": True, "format": "mp4", "createEvent": False}
    query = {k: ("true" if v is True else "false" if v is False else str(v)) for k, v in body.items()}
    payload = {"reqUrl": "/internal/aiprocessors/video/export?" + urlencode(query),
               "resUrl": "/internal/aiprocessors/recognize-anything", "ramType": ram_type,
               "keyMoments": [START + 1000], "postVLM": True, **body}
    if face_meta:
        payload["faceMeta"] = [
            {"roi": {"name": "", "coord": [380, 280, 140, 200], "trackerId": 7,
                     "attributes": {"objectType": "face"}, "confidence": 0.9,
                     "objectType": "face"}, "ts": START + 1500},
            {"roi": {"name": "", "coord": [100, 100, 100, 100], "trackerId": 7,
                     "attributes": {"objectType": "face"}, "confidence": 0.4,
                     "objectType": "face"}, "ts": START + 2500}]
    return {"command": "recognizeKeyFrames", "payload": payload}


async def test_a_known_face_is_matched_locally_and_posted_as_a_native_face_part(controller, tmp_path):
    FaceStore(tmp_path).enroll("Synthetic Alice", ALICE)
    worker = JobProcessor(config(controller), tmp_path)
    try:
        result = await worker.handle(task())
    finally:
        await worker.stop()
    assert controller.vision_requests == []                 # nothing to the vision provider
    assert controller.face_requests == [{"regions": [[0.345, 0.23, 0.555, 0.53]], "jpeg": True}]
    [parts] = controller.callbacks
    assert set(parts) == {"face", "7"} and "ram" not in parts
    content_type, face = parts["face"]
    assert content_type == "application/json"
    assert face["cameraId"] == CAMERA and face["eventId"] == EVENT and face["status"] == "success"
    assert face["faceAttrs"]["7"]["matchedName"] == "Synthetic Alice"
    assert face["faceAttrs"]["7"]["namesTopK"] == ["Synthetic Alice"]
    [snapshot] = face["faceSnapshots"]
    assert snapshot["trackerID"] == 7 and snapshot["clockBestWall"] == START + 1500
    assert snapshot["smartDetectSnapshotType"] == "face"
    assert parts["7"] == ("image/jpeg", b"\xff\xd8\xff")
    assert result["result"] == {"faces": 1, "matched": 1}
    journal = "".join(p.read_text() for p in (tmp_path / "worker-jobs").glob("*.json"))
    assert "Alice" not in journal


async def test_an_unknown_face_stays_unknown(controller, tmp_path):
    FaceStore(tmp_path).enroll("Synthetic Alice", ALICE)
    controller.face_reply["faces"][0]["embedding"] = STRANGER
    worker = JobProcessor(config(controller), tmp_path)
    try:
        await worker.handle(task())
    finally:
        await worker.stop()
    face = controller.callbacks[0]["face"][1]
    assert face["faceAttrs"]["7"]["matchedName"] == "" and face["faceAttrs"]["7"]["namesTopK"] == []


async def test_no_detected_face_completes_the_task_without_snapshots(controller, tmp_path):
    controller.face_reply = {"faces": []}
    worker = JobProcessor(config(controller), tmp_path)
    try:
        result = await worker.handle(task())
    finally:
        await worker.stop()
    assert set(controller.callbacks[0]) == {"face"}
    assert controller.callbacks[0]["face"][1]["faceSnapshots"] == []
    assert result["result"] == {"faces": 0, "matched": 0}


@pytest.mark.parametrize("change", [
    {"camera": "another-camera"}, {"face_meta": False}, {"ram_type": "video"}])
async def test_other_recognition_tasks_never_reach_local_faces(controller, tmp_path, change):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError):
            await worker.handle(task(**change))
    finally:
        await worker.stop()
    assert controller.face_requests == [] and controller.callbacks == []


async def test_a_broken_face_server_posts_nothing(controller, tmp_path):
    controller.face_reply = {"faces": [{"box": "bad"}]}
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError):
            await worker.handle(task())
    finally:
        await worker.stop()
    assert controller.callbacks == []


@pytest.mark.parametrize("server", ["https://faces.example.com", "http://8.8.8.8:8179",
                                    "http://192.168.64.1:8179/other"])
def test_face_processing_is_local_only(tmp_path, server):
    options = config(type("S", (), {"origin": "http://127.0.0.1:1", "ffmpeg": "/bin/true"}))
    options["face_recognition"]["server"] = server
    with pytest.raises(WorkerError):
        JobProcessor(options, tmp_path)


async def test_the_device_routes_face_camera_recognition_to_the_worker(tmp_path):
    calls = []

    async def admit(body):
        calls.append(body)
        return {"accepted": True}
    options = device_config()
    options["face_recognition"] = {"server": "http://127.0.0.1:8179", "camera_ids": [CAMERA]}
    device = DeviceService(options, tmp_path, admit)
    reply = decode_message(await device.handle_message(wire("recognizeKeyFrames",
                                                            task()["payload"])))
    assert reply.header["errorCode"] == 0 and calls[0]["command"] == "recognizeKeyFrames"


def test_templates_are_private_matched_by_threshold_and_deletable(tmp_path):
    store = FaceStore(tmp_path)
    assert store.enroll("Synthetic Alice", ALICE) == 1
    near = [0.9, 0.1] + [0.0] * 126
    assert store.match(near)[0] == "Synthetic Alice"
    far = [MATCH_THRESHOLD - 0.05, 1.0] + [0.0] * 126
    assert store.match(far)[0] is None
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"
    assert oct(store.directory.stat().st_mode & 0o777) == "0o700"
    assert store.delete("Synthetic Alice") and store.names() == []
    store.enroll("Synthetic Bob", STRANGER)
    store.purge()
    assert store.names() == []
    for bad in ([1.0] * 3, [float("nan")] * 128, [0.0] * 128):
        with pytest.raises(FaceStoreError):
            store.enroll("X", bad)
    with pytest.raises(FaceStoreError):
        store.enroll("bad\nname", ALICE)


def test_regions_are_validated():
    assert parse_regions("[[0.1, 0.1, 0.5, 0.5]]") == [(0.1, 0.1, 0.5, 0.5)]
    for raw in ("[]", "[[0.5, 0.1, 0.1, 0.5]]", "[[0, 0, 2, 1]]", "nope", "[[0,0,1]]"):
        with pytest.raises(ImageError):
            parse_regions(raw)


async def test_the_face_server_returns_boxes_and_embeddings_only():
    def analyze(jpeg, regions):
        return [((0.1, 0.2, 0.3, 0.4), 0.95, ALICE)]
    async with TestClient(TestServer(build_app(analyze))) as client:
        form = FormData()
        form.add_field("image", b"\xff\xd8\xff" + b"0" * 64, filename="f.jpg",
                       content_type="image/jpeg")
        body = await (await client.post("/v1/faces", data=form)).json()
        assert body == {"faces": [{"box": [0.1, 0.2, 0.3, 0.4], "score": 0.95,
                                   "embedding": ALICE}]}
        form = FormData()
        form.add_field("image", b"GIF89a", filename="f.gif", content_type="image/gif")
        assert (await client.post("/v1/faces", data=form)).status == 400
        health = await (await client.get("/healthz")).json()
        assert health == {"status": "ok", "requests": 2, "analyzed": 1, "faces": 1,
                          "rejected": 1, "failed": 0}


async def test_without_face_regions_the_face_is_searched_in_person_regions_and_linked(
        controller, tmp_path):
    # Wohnzimmer, 26 Sep: recognition tasks carried personMeta but no faceMeta.
    FaceStore(tmp_path).enroll("Synthetic Alice", ALICE)
    command = task(face_meta=False)
    command["payload"]["personMeta"] = [
        {"roi": {"coord": [300, 100, 200, 800], "trackerId": 4,
                 "objectType": "person"}, "ts": START + 1200}]
    worker = JobProcessor(config(controller), tmp_path)
    try:
        await worker.handle(command)
    finally:
        await worker.stop()
    assert controller.face_requests[0]["regions"] == [[0.25, 0.01, 0.55, 0.55]]
    parts = controller.callbacks[0]
    face = parts["face"][1]
    assert set(face["faceAttrs"]) == {"1000004"} and "1000004" in parts
    assert face["faceAttrs"]["1000004"]["linkedPersonTrackerID"] == 4
    assert face["faceAttrs"]["1000004"]["matchedName"] == "Synthetic Alice"
    assert face["faceSnapshots"][0]["trackerID"] == 1_000_004


async def test_live_list_shaped_person_regions_reach_local_faces(controller, tmp_path):
    # Live 7.3.68 (26 Sep): each personMeta entry's roi is a list of objects.
    FaceStore(tmp_path).enroll("Synthetic Alice", ALICE)
    command = task(face_meta=False)
    command["payload"]["personMeta"] = [
        {"ts": START + 1200, "roi": [{"coord": [300, 100, 200, 800], "trackerId": 4,
                                      "objectType": "person"}]},
        {"ts": START + 9000, "roi": [{"coord": [0, 0, 100, 100], "trackerId": 9,
                                      "objectType": "person"}]}]          # outside the export
    worker = JobProcessor(config(controller), tmp_path)
    try:
        await worker.handle(command)
    finally:
        await worker.stop()
    assert controller.face_requests[0]["regions"] == [[0.25, 0.01, 0.55, 0.55]]
    face = controller.callbacks[0]["face"][1]
    assert set(face["faceAttrs"]) == {"1000004"}


async def test_person_regions_all_outside_the_export_are_refused_before_media(controller, tmp_path):
    command = task(face_meta=False)
    command["payload"]["personMeta"] = [
        {"ts": START + 9000, "roi": [{"coord": [0, 0, 100, 100], "trackerId": 9, "objectType": "person"}]}]
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError, match="no regions inside the export"):
            await worker.handle(command)
    finally:
        await worker.stop()
    assert controller.face_requests == [] and controller.callbacks == []
