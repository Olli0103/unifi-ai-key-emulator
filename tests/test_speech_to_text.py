"""Native speechToText task with synthetic audio and a loopback transcriber.

Shapes follow the Protect 7.3.60 bundle: dispatch ``speechToText`` with an
audio-only event export, callback ``{camera, event, stt:[{startMs, endMs,
text}]}`` at /internal/aiprocessors/speech-to-text. No real audio is used.
"""

import asyncio
import json
import shutil
from urllib.parse import urlencode

from aiohttp import web
import pytest
import pytest_asyncio

from aikey.device import DeviceService
from aikey.protocol import decode_message
from aikey.speech import SpeechError, SpeechProvider, validate_speech_config
from aikey.worker import JobProcessor, WorkerError
from test_basic_descriptions import device_config, wire


CAMERA, EVENT = "speech-camera-fixture", "speech-event-fixture"
START, END = 1_700_000_000_000, 1_700_000_008_000


class Controller:
    def __init__(self):
        self.audio = b""
        self.export_headers = {"x-start-timestamp": str(START)}
        self.media_requests, self.callbacks, self.transcriptions = [], [], []
        self.reply = {"text": "hello there", "segments": [
            {"start": 0.5, "end": 1.75, "text": " Hello there. ", "no_speech_prob": 0.01}]}
        self.reply_status = 200

    async def export(self, request):
        self.media_requests.append(request.query_string)
        return web.Response(body=self.audio, content_type="video/mp4", headers=self.export_headers)

    async def transcribe(self, request):
        fields, audio = {}, b""
        reader = await request.multipart()
        while (part := await reader.next()) is not None:
            if part.name == "file":
                audio = await part.read()
            else:
                fields.setdefault(part.name, []).append((await part.read()).decode())
        self.transcriptions.append({"fields": fields, "audio_header": audio[:44],
                                    "audio_bytes": len(audio)})
        return web.json_response(self.reply, status=self.reply_status)

    async def callback(self, request):
        self.callbacks.append({"path": request.path, "payload": await request.json()})
        return web.json_response({"stt": len((await request.json())["stt"])})


@pytest_asyncio.fixture
async def controller(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("Real ffmpeg executable unavailable")
    service = Controller()
    clip = tmp_path / "synthetic-tone.mp4"
    process = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=8", "-c:a", "aac", "-y", str(clip))
    assert await process.wait() == 0
    service.audio, service.ffmpeg = clip.read_bytes(), ffmpeg
    app = web.Application()
    app.router.add_get("/internal/aiprocessors/video/export", service.export)
    app.router.add_post("/v1/audio/transcriptions", service.transcribe)
    app.router.add_post("/internal/aiprocessors/speech-to-text", service.callback)
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
            "speech_to_text": {"provider": "openai-compatible", "model": "synthetic-whisper",
                               "base_url": service.origin + "/v1", "camera_ids": [CAMERA]},
            "worker": {"max_queue": 2, "timeout_s": 10, "ffmpeg_path": service.ffmpeg}}


def task(**changes):
    """Exactly what Protect 7.3.60 dispatchSpeechToText sends."""
    body = {"camera": CAMERA, "event": EVENT, "channel": 0, "start": START, "end": END,
            "type": "rotating", "format": "mp4", "skipVideo": True, "createEvent": False}
    body.update(changes.pop("body", {}))
    query = {key: ("true" if value is True else "false" if value is False else str(value))
             for key, value in body.items()}
    query.update(changes.pop("query", {}))
    payload = {"reqUrl": "/internal/aiprocessors/video/export?" + urlencode(query),
               "resUrl": "/internal/aiprocessors/speech-to-text", **body, **changes}
    return {"command": "speechToText", "payload": payload}


async def test_a_speech_event_is_transcribed_and_posted_in_protects_shape(controller, tmp_path):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        result = await worker.handle(task())
    finally:
        await worker.stop()
    assert controller.callbacks == [{"path": "/internal/aiprocessors/speech-to-text", "payload": {
        "camera": CAMERA, "event": EVENT,
        "stt": [{"startMs": START + 500, "endMs": START + 1750, "text": "Hello there."}]}}]
    sent = controller.transcriptions[0]
    assert sent["fields"]["model"] == ["synthetic-whisper"]
    assert sent["fields"]["response_format"] == ["verbose_json"]
    header = sent["audio_header"]
    assert header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    assert int.from_bytes(header[22:24], "little") == 1             # mono
    assert int.from_bytes(header[24:28], "little") == 16000         # 16 kHz
    assert 8 * 32000 - 4096 < sent["audio_bytes"] <= 8 * 32000 + 4096
    # The journal keeps a count, never the transcript.
    assert result["result"] == {"segments": 1}
    journal = "".join(p.read_text() for p in (tmp_path / "worker-jobs").glob("*.json"))
    assert "Hello" not in journal and "completed" in journal


async def test_the_exports_own_start_header_anchors_the_times(controller, tmp_path):
    controller.export_headers = {"x-timestamp": str(START + 250), "x-start-timestamp": str(START)}
    worker = JobProcessor(config(controller), tmp_path)
    try:
        await worker.handle(task())
    finally:
        await worker.stop()
    assert controller.callbacks[0]["payload"]["stt"][0]["startMs"] == START + 750


@pytest.mark.parametrize("reply", [
    {"text": "", "segments": []},
    {"text": "thanks", "segments": [{"start": 0, "end": 2, "text": "Thanks.", "no_speech_prob": 0.9}]},
    {"text": "x", "segments": [{"start": i, "end": i + 1, "text": "Thank you."} for i in range(3)]},
])
async def test_silence_noise_and_repeated_hallucinations_post_no_transcript(controller, tmp_path, reply):
    controller.reply = reply
    worker = JobProcessor(config(controller), tmp_path)
    try:
        await worker.handle(task())
    finally:
        await worker.stop()
    assert controller.callbacks[0]["payload"]["stt"] == []


@pytest.mark.parametrize("status,reply", [(500, {"error": "down"}), (200, {"segments": "bad"}),
                                          (200, [1, 2]), (200, {"segments": [{"text": 1}]})])
async def test_a_failing_backend_posts_nothing(controller, tmp_path, status, reply):
    controller.reply_status, controller.reply = status, reply
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError):
            await worker.handle(task())
    finally:
        await worker.stop()
    assert controller.callbacks == []


@pytest.mark.parametrize("changes", [
    {"body": {"camera": "another-camera"}, "query": {"camera": "another-camera"}},
    {"body": {"skipVideo": False}},
    {"body": {"end": START + 600_000}},
    {"query": {"event": "different-event"}},
    {"query": {"mute": "true"}},
    {"resUrl": "/internal/aiprocessors/recognize-anything"},
    {"extra": "field"},
])
async def test_anything_but_the_exact_native_task_is_refused_before_media_or_audio(
        controller, tmp_path, changes):
    worker = JobProcessor(config(controller), tmp_path)
    try:
        with pytest.raises(WorkerError):
            await worker.handle(task(**changes))
    finally:
        await worker.stop()
    assert controller.media_requests == [] and controller.transcriptions == []


async def test_without_a_speech_backend_the_task_is_refused(controller, tmp_path):
    options = config(controller)
    del options["speech_to_text"]
    worker = JobProcessor(options, tmp_path)
    try:
        with pytest.raises(WorkerError, match="speech backend"):
            await worker.handle(task())
    finally:
        await worker.stop()


async def test_the_device_routes_speech_to_text_only_for_allowed_cameras(tmp_path):
    calls = []

    async def admit(body):
        calls.append(body)
        return {"accepted": True}
    options = device_config()
    options["speech_to_text"] = {"provider": "openai-compatible", "model": "m",
                                 "base_url": "http://127.0.0.1:9/v1", "camera_ids": [CAMERA]}
    device = DeviceService(options, tmp_path, admit)
    assert "speechToText" in device.status["supported_commands"]
    body = task()["payload"]
    reply = decode_message(await device.handle_message(wire("speechToText", body)))
    assert reply.header["errorCode"] == 0 and calls == [{"command": "speechToText", "payload": body}]
    other = decode_message(await device.handle_message(
        wire("speechToText", {**body, "camera": "other"}, "second")))
    assert other.header["errorCode"] == 95 and len(calls) == 1
    status = json.dumps(device.status)
    assert device.status["control_commands"]["speechToText"]["count"] == 2
    assert CAMERA not in status and EVENT not in status


async def test_without_speech_configuration_the_device_refuses_it(tmp_path):
    device = DeviceService(device_config(), tmp_path, None)
    assert "speechToText" not in device.status["supported_commands"]
    reply = decode_message(await device.handle_message(wire("speechToText", task()["payload"])))
    assert reply.header["errorCode"] == 95


def test_audio_goes_only_to_the_official_api_or_a_local_server():
    with pytest.raises(SpeechError):
        SpeechProvider({"provider": "openai-compatible", "model": "m",
                        "base_url": "https://transcribe.example.com/v1"})
    with pytest.raises(SpeechError):
        SpeechProvider({"provider": "openai", "model": "m", "api_key": "k",
                        "base_url": "https://proxy.example.com/v1"})
    with pytest.raises(SpeechError):
        SpeechProvider({"provider": "openai", "model": "m"})          # no key
    official = SpeechProvider({"provider": "openai", "model": "whisper-1", "api_key": "k",
                               "language": "de"})
    assert official.url == "https://api.openai.com/v1/audio/transcriptions"
    assert ("language", "de") in official.form_fields()
    with pytest.raises(SpeechError):
        SpeechProvider({"provider": "openai", "model": "m", "api_key": "k", "language": "german"})


def test_camera_scope_is_explicit():
    base = {"provider": "openai-compatible", "model": "m", "base_url": "http://127.0.0.1:1/v1"}
    for cameras in (None, [], ["a", "a"], ["bad id"]):
        with pytest.raises(SpeechError):
            validate_speech_config({**base, "camera_ids": cameras})
    assert validate_speech_config({**base, "camera_ids": ["a"]})[1] == frozenset({"a"})


def test_a_text_only_reply_covers_the_clip_and_long_text_is_marked_uncertain():
    provider = SpeechProvider({"provider": "openai-compatible", "model": "m",
                               "base_url": "http://127.0.0.1:1/v1"})
    assert provider.parse({"text": " Open the door "}, 4000) == [(0, 4000, "Open the door")]
    [(begin, end, text)] = provider.parse(
        {"segments": [{"start": 1, "end": 99, "text": "a" * 1200}]}, 5000)
    assert (begin, end) == (1000, 5000) and text.endswith("[inaudible]") and len(text) < 1100
