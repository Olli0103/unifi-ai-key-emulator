"""Deep understanding (Protect 7.3.70): re-ID, session descriptions, mode and prompt sync.

Synthetic crops, prompts and model replies only.
"""

import base64
import hashlib
import io
import json

from aiohttp import web
import pytest
import pytest_asyncio
from PIL import Image

from aikey import clip, deep_mode
from aikey.device import DeviceService
from aikey.protocol import decode_message, encode_message
from aikey.worker import JobProcessor, WorkerError
from test_basic_descriptions import device_config, wire

CAMERA, EVENT = "deep-camera-fixture", "deep-event-fixture"
SCHEMA = {"type": "object", "properties": {"description": {"type": "string"},
                                           "labels": {"type": "array", "items": {"type": "string"}}},
          "required": ["description", "labels"]}


def b64(text):
    return base64.b64encode(text.encode()).decode()


def prompt_entry(kind):
    return {"systemPrompt": b64(f"You describe ONE {kind}."), "userPrompt": b64(f"Describe {kind}"),
            "temperature": 0, "topK": 20, "topP": 0.8, "minP": 0, "repeatPenalty": 1.05,
            "presencePenalty": 0, "bboxMargin": 0.1, "describeSchema": b64(json.dumps(SCHEMA))}


def prompts_body():
    prompts = {key: prompt_entry(key.split("+")[0])
               for key in ("person", "face", "face+person", "vehicle", "animal")}
    digest = hashlib.sha1(json.dumps(prompts).encode()).hexdigest()
    return {"describePrompts": prompts, "configHash": digest}


# --- pure contract helpers ----------------------------------------------------

def test_the_combo_key_and_prompt_selection_follow_protect():
    assert deep_mode.combo_key(["person", "Face ", "person"]) == "face+person"
    prompts, digest = deep_mode.validate_prompts(prompts_body())
    assert deep_mode.select_prompt(prompts, ["face", "person"])["system"] == "You describe ONE face."
    fallback = deep_mode.select_prompt(prompts, ["vehicle", "animal"])       # no combo: first type
    assert fallback["system"] == "You describe ONE vehicle." and fallback["schema"] == SCHEMA
    assert fallback["sampling"]["topK"] == 20 and fallback["margin"] == 0.1
    with pytest.raises(deep_mode.DeepModeError):
        deep_mode.select_prompt({"person": prompts["person"]}, ["vehicle"])


@pytest.mark.parametrize("change", [
    {"configHash": "short"}, {"describePrompts": {}}, {"extra": 1},
    {"describePrompts": {"person": {"systemPrompt": "!!!", "userPrompt": b64("x"),
                                    "describeSchema": b64("{}")}}},
    {"describePrompts": {"person": {**prompt_entry("person"), "describeSchema": b64("not json")}}},
    {"describePrompts": {"Person Car": prompt_entry("person")}},
])
def test_malformed_prompt_configs_are_refused(change):
    with pytest.raises((deep_mode.DeepModeError, ValueError)):
        deep_mode.validate_prompts({**prompts_body(), **change})


def test_the_model_answer_becomes_a_description_and_clean_labels():
    text = '```json\n{"description": "A person in a red hoodie.", "labels": ' \
           '["top:hoodie", "topColor:red", "top:hoodie", "nonsense", "gender:", 5]}\n```'
    assert deep_mode.parse_description(text) == ("A person in a red hoodie.", ["top:hoodie", "topColor:red"])
    for bad in ("not json", '{"labels": []}', '{"description": "", "labels": []}', "[1]"):
        with pytest.raises(deep_mode.DeepModeError):
            deep_mode.parse_description(bad)


def test_reid_vectors_are_normalized_and_padded_to_the_session_column():
    padded = deep_mode.padded_reid([3.0, 4.0])
    assert len(padded) == 512 and padded[:2] == [0.6, 0.8] and set(padded[2:]) == {0.0}
    for bad in ([], [0.0, 0.0], [1.0] * 513):
        with pytest.raises(deep_mode.DeepModeError):
            deep_mode.padded_reid(bad)


def test_deep_config_needs_a_local_reid_server():
    assert deep_mode.validate_config({"reid_server": "http://172.30.50.14:8190/"}) == {
        "reid_server": "http://172.30.50.14:8190"}
    assert deep_mode.validate_config(None) is None
    for bad in ({"reid_server": "https://reid.example.com"}, {"reid_server": "http://8.8.8.8"},
                {"reid_server": "http://10.0.0.1:1/x"}, {}, {"reid_server": "http://10.0.0.1", "x": 1}):
        with pytest.raises(deep_mode.DeepModeError):
            deep_mode.validate_config(bad)


# --- device: capability, mode switch and prompt sync --------------------------

def deep_device_config():
    config = device_config()
    config["deep_understanding"] = {"reid_server": "http://127.0.0.1:1"}
    config["embeddings"] = {"backend": "http", "base_url": "http://127.0.0.1:2/v1",
                            "model": "intfloat/multilingual-e5-small"}
    config["inference"] = {"provider": "openai-compatible", "base_url": "http://127.0.0.1:3/v1",
                           "model": "qwen3-vl-8b"}
    return config


async def send(service, action, body, identifier="r"):
    reply = decode_message(await service.handle_message(wire(action, body, identifier)))
    return reply.header["errorCode"], reply.body


async def test_a_deep_capable_key_switches_mode_and_echoes_it(tmp_path):
    async def admit(body):
        return {"accepted": True}
    service = DeviceService(deep_device_config(), tmp_path, admit)
    flags = service.get_info()["featureFlags"]
    assert flags["supportDeepMode"] is True and flags["supportVlm"] is True
    assert flags["aiMode"] == "basic" and flags["describeConfigHash"] == ""
    assert (await send(service, "changeAiInferAgentSettings", {"modelMode": "deep"}))[0] == 0
    assert service.get_info()["featureFlags"]["aiMode"] == "deep"
    assert (await send(service, "changeAiInferAgentSettings", {"modelMode": "turbo"}, "b"))[0] != 0
    reloaded = DeviceService(deep_device_config(), tmp_path, admit)
    assert reloaded.get_info()["featureFlags"]["aiMode"] == "deep"           # persisted


async def test_describe_prompts_are_stored_and_their_hash_echoed(tmp_path):
    async def admit(body):
        return {"accepted": True}
    service = DeviceService(deep_device_config(), tmp_path, admit)
    body = prompts_body()
    code, reply = await send(service, "changeDescribePrompts", body)
    assert code == 0 and reply == {"configHash": body["configHash"]}
    assert service.get_info()["featureFlags"]["describeConfigHash"] == body["configHash"]
    assert deep_mode.load_prompts(tmp_path) == body["describePrompts"]
    assert (tmp_path / deep_mode.PROMPTS_FILE).stat().st_mode & 0o077 == 0
    assert (await send(service, "changeDescribePrompts", {**body, "configHash": "x"}, "b"))[0] != 0


async def test_a_key_without_deep_models_stays_basic(tmp_path):
    async def admit(body):
        return {"accepted": True}
    service = DeviceService(device_config(), tmp_path, admit)
    flags = service.get_info()["featureFlags"]
    assert flags["supportDeepMode"] is False and flags["aiMode"] == "basic"
    assert (await send(service, "changeAiInferAgentSettings", {"modelMode": "deep"}))[0] != 0
    assert (await send(service, "changeDescribePrompts", prompts_body(), "b"))[0] != 0


# --- worker: re-ID embeddings and session descriptions ------------------------

class Controller:
    def __init__(self):
        self.callbacks, self.reid_calls, self.chat_requests, self.embed_requests = [], 0, [], []
        self.reid_status, self.chat_content = 200, json.dumps(
            {"description": "A person in a red hoodie walks to the door.",
             "labels": ["top:hoodie", "topColor:red"]})

    async def crop(self, request):
        out = io.BytesIO()
        Image.new("RGB", (80, 160), (200, 40, 40)).save(out, "JPEG")
        return web.Response(body=out.getvalue(), content_type="image/jpeg")

    async def reid(self, request):
        self.reid_calls += 1
        if self.reid_status != 200:
            return web.json_response({}, status=self.reid_status)
        return web.json_response({"embedding": [0.6, 0.8] + [0.0] * 254, "dim": 256})

    async def chat(self, request):
        self.chat_requests.append(await request.json())
        return web.json_response({"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": self.chat_content}}]})

    async def embeddings(self, request):
        body = await request.json()
        self.embed_requests.append(body["input"])
        return web.json_response({"data": [{"index": i, "embedding": [1.0] + [0.0] * 383}
                                           for i, _ in enumerate(body["input"])]})

    async def callback(self, request):
        self.callbacks.append((request.path, await request.json()))
        return web.json_response({"received": 1})


@pytest_asyncio.fixture
async def controller():
    service = Controller()
    app = web.Application()
    app.router.add_get("/internal/aiprocessors/image/{image}", service.crop)
    app.router.add_post("/v1/reid", service.reid)
    app.router.add_post("/v1/chat/completions", service.chat)
    app.router.add_post("/v1/embeddings", service.embeddings)
    app.router.add_post("/internal/aiprocessors/embeddings/{task}", service.callback)
    app.router.add_post("/internal/aiprocessors/descriptions/{task}", service.callback)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    service.origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield service
    finally:
        await runner.cleanup()


class Registry:
    allowed_ids = frozenset({CAMERA})

    def allows(self, camera_id):
        return camera_id in self.allowed_ids


def worker_config(controller):
    return {"runtime": {"mode": "lab"}, "controller_origins": [controller.origin],
            "device": {"mac": "02:00:00:00:00:98"},
            "inference": {"provider": "openai-compatible", "base_url": controller.origin + "/v1",
                          "model": "qwen3-vl-8b"},
            "embeddings": {"backend": "http", "base_url": controller.origin + "/v1",
                           "model": "intfloat/multilingual-e5-small"},
            "deep_understanding": {"reid_server": controller.origin},
            "worker": {"max_queue": 4, "timeout_s": 20,
                       "continuous": {"enabled": True, "camera_models": ["Fixture"], "unmetered": True}}}


def embed_request(images=None, task="task-1"):
    images = images or [{"thumbnailId": "th1", "objectId": "obj1", "objectType": "person", "trackerId": 3},
                        {"thumbnailId": "th2", "objectId": "obj2", "objectType": "person", "trackerId": None}]
    return {"targetUri": ":7445/generate-embeddings", "timeoutMs": 30000,
            "resUrl": f"/internal/aiprocessors/embeddings/{task}",
            "payload": {"camera": CAMERA, "event": EVENT, "images": [
                {"reqUrl": f"/internal/aiprocessors/image/{i['thumbnailId']}", **i} for i in images]}}


def describe_request(task="task-2", **changes):
    payload = {"camera": CAMERA, "event": EVENT, "pass": "open", "promptProfile": "session-v1",
               "images": [{"reqUrl": "/internal/aiprocessors/image/th1", "thumbnailId": "th1",
                           "objectId": "obj1", "objectType": "person"}]}
    payload.update(changes)
    return {"targetUri": ":7968/describe", "timeoutMs": 30000,
            "resUrl": f"/internal/aiprocessors/descriptions/{task}", "payload": payload}


async def test_person_crops_get_padded_reid_vectors(controller, tmp_path):
    worker = JobProcessor(worker_config(controller), tmp_path, camera_registry=Registry())
    try:
        result = await worker.handle(embed_request())
        status = worker.status()
    finally:
        await worker.stop()
    [(path, body)] = controller.callbacks
    assert path == "/internal/aiprocessors/embeddings/task-1"
    assert [e["objectId"] for e in body["embeddings"]] == ["obj1", "obj2"] and body["failed"] == []
    assert all(len(e["reidEmbed"]) == 512 and e["dim"] == 512 for e in body["embeddings"])
    assert result["result"] == {"embedded": 2, "failed": 0}              # no vectors in the journal
    assert status["deep"]["crops_embedded"] == 2


async def test_a_failing_reid_server_reports_failed_objects(controller, tmp_path):
    controller.reid_status = 503
    worker = JobProcessor(worker_config(controller), tmp_path, camera_registry=Registry())
    try:
        await worker.handle(embed_request())
    finally:
        await worker.stop()
    [(_, body)] = controller.callbacks
    assert body["embeddings"] == [] and [f["objectId"] for f in body["failed"]] == ["obj1", "obj2"]


@pytest.mark.parametrize("change", [
    {"resUrl": "/internal/aiprocessors/descriptions/x"},
    {"payload": {"camera": CAMERA, "event": EVENT, "images": []}},
    {"payload": {"camera": CAMERA, "event": EVENT, "images": [
        {"reqUrl": "/internal/aiprocessors/image/other", "thumbnailId": "th1", "objectId": "o"}]}},
])
async def test_malformed_embed_requests_are_refused_before_media(controller, tmp_path, change):
    worker = JobProcessor(worker_config(controller), tmp_path, camera_registry=Registry())
    try:
        with pytest.raises(WorkerError):
            await worker.handle({**embed_request(), **change})
    finally:
        await worker.stop()
    assert controller.reid_calls == 0


async def test_an_open_pass_is_described_with_protects_prompt_and_schema(controller, tmp_path):
    deep_mode.save_prompts(tmp_path, *deep_mode.validate_prompts(prompts_body()))
    worker = JobProcessor(worker_config(controller), tmp_path, camera_registry=Registry())
    try:
        result = await worker.handle(describe_request())
    finally:
        await worker.stop()
    [request] = controller.chat_requests
    assert request["messages"][0] == {"role": "system", "content": "You describe ONE person."}
    assert request["messages"][1]["content"][-1] == {"type": "text", "text": "Describe person"}
    assert request["response_format"]["json_schema"]["schema"] == SCHEMA
    assert request["top_k"] == 20 and request["temperature"] == 0
    [(path, body)] = controller.callbacks
    assert path == "/internal/aiprocessors/descriptions/task-2"
    assert body["description"] == "A person in a red hoodie walks to the door."
    assert body["labels"] == ["top:hoodie", "topColor:red"] and body["pass"] == "open"
    assert len(body["descEmbedding"]) == 384 and body["version"] == "session-v1"
    assert controller.embed_requests == [["passage: A person in a red hoodie walks to the door."]]
    assert "description" not in result["result"] and result["result"]["labels"] == 2


async def test_describing_before_protect_synced_prompts_fails(controller, tmp_path):
    worker = JobProcessor(worker_config(controller), tmp_path, camera_registry=Registry())
    try:
        with pytest.raises(WorkerError, match="not been synced"):
            await worker.handle(describe_request())
    finally:
        await worker.stop()
    assert controller.chat_requests == [] and controller.callbacks == []


async def test_a_describer_answer_without_json_fails_the_task(controller, tmp_path):
    deep_mode.save_prompts(tmp_path, *deep_mode.validate_prompts(prompts_body()))
    controller.chat_content = "A person walks."
    worker = JobProcessor(worker_config(controller), tmp_path, camera_registry=Registry())
    try:
        with pytest.raises(WorkerError, match="description and labels"):
            await worker.handle(describe_request())
    finally:
        await worker.stop()
    assert controller.callbacks == []


@pytest.mark.parametrize("change", [
    {"pass": "middle"}, {"promptProfile": "session-v2"}, {"images": []},
    {"images": [{"reqUrl": "/internal/aiprocessors/image/th1", "thumbnailId": "th1",
                 "objectType": "boat"}]},
])
async def test_malformed_describe_requests_are_refused_before_media(controller, tmp_path, change):
    worker = JobProcessor(worker_config(controller), tmp_path, camera_registry=Registry())
    try:
        with pytest.raises(WorkerError):
            await worker.handle(describe_request(**change))
    finally:
        await worker.stop()
    assert controller.chat_requests == []


async def test_deep_requests_are_refused_without_deep_configuration(controller, tmp_path):
    config = worker_config(controller)
    del config["deep_understanding"]
    worker = JobProcessor(config, tmp_path, camera_registry=Registry())
    try:
        with pytest.raises(WorkerError):                      # the existing refusal
            await worker.handle(embed_request())
    finally:
        await worker.stop()


# --- search: E5 session queries next to basic CLIP ----------------------------

async def test_session_search_gets_an_e5_vector_under_the_clip_profile(controller, tmp_path):
    from aikey.search import SearchService
    config = {"search": {"enabled": True, "profile": clip.PROFILE},
              "find_anything": {"clip_server": controller.origin},
              "embeddings": {"backend": "http", "base_url": controller.origin + "/v1",
                             "model": "intfloat/multilingual-e5-small"},
              "controller": {"host": "127.0.0.1"}, "device": {"mac": "02:00:00:00:00:98"}}
    service = SearchService(config, tmp_path)
    frame = encode_message({"id": "q1", "type": "request", "action": "NL_PARSE"},
                           {"querySentence": "a person in a red hoodie", "model": "multilingual-e5-small"})
    reply = decode_message(await service.handle_message(frame))
    assert reply.header["errorCode"] == 0 and reply.body["model"] == "multilingual-e5-small"
    assert len(reply.body["txtEmbed"]) == 384 and reply.body["dim"] == 384
    assert controller.embed_requests == [["query: a person in a red hoodie"]]


async def test_a_close_pass_crops_each_object_box_from_the_video_export(controller, tmp_path):
    import asyncio
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("Real ffmpeg executable unavailable")
    video = tmp_path / "synthetic.mp4"
    process = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=gray:s=320x240:r=5:d=4", "-c:v", "mpeg4", "-y", str(video))
    assert await process.wait() == 0
    start = 1_700_000_000_000
    exports = []

    async def export(request):
        exports.append(dict(request.query))
        return web.Response(body=video.read_bytes(), content_type="video/mp4",
                            headers={"x-start-timestamp": str(start)})
    app = web.Application()
    app.router.add_get("/internal/aiprocessors/video/export", export)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    config = worker_config(controller)
    config["controller_origins"].append(origin)
    config["worker"]["ffmpeg_path"] = ffmpeg
    deep_mode.save_prompts(tmp_path, *deep_mode.validate_prompts(prompts_body()))
    worker = JobProcessor(config, tmp_path, camera_registry=Registry())
    request = describe_request()
    request["payload"].pop("images")
    request["payload"]["pass"] = "close"
    request["payload"]["videos"] = [{
        "reqUrl": f"{origin}/internal/aiprocessors/video/export?camera={CAMERA}&event={EVENT}"
                  f"&start={start}&end={start + 4000}&channel=0&type=rotating",
        "objects": [{"coord": [100, 200, 300, 500], "ts": start + 1000, "objectType": "person",
                     "objectId": "obj1"},
                    {"coord": [500, 100, 200, 300], "ts": start + 2500, "objectType": "face",
                     "objectId": "obj2"}]}]
    try:
        result = await worker.handle(request)
    finally:
        await worker.stop()
        await runner.cleanup()
    [chat] = controller.chat_requests
    assert chat["messages"][0]["content"] == "You describe ONE face."          # the face+person combo
    assert sum(part["type"] == "image_url" for part in chat["messages"][1]["content"]) == 2
    assert len(exports) == 1 and result["result"]["crops"] == 2
    [(_, body)] = controller.callbacks
    assert body["pass"] == "close" and body["labels"] == ["top:hoodie", "topColor:red"]
