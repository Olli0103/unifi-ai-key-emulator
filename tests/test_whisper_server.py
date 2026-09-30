"""Local transcription server with a fake model. No real audio or model."""

import io
import wave

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey.whisper_server import build_app


def _wav(seconds=1.0, rate=16000, channels=1, width=2):
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(b"\x00\x01" * int(seconds * rate) * channels * (width // 2))
    return out.getvalue()


@pytest.fixture
async def client():
    calls = []

    def transcribe(samples, language):
        calls.append((len(samples), language))
        return [(0.5, 1.25, " Hallo zusammen. ")], None if language == "auto" else language
    async with TestClient(TestServer(build_app(transcribe))) as test_client:
        test_client.calls = calls
        yield test_client


def _form(audio, **fields):
    form = FormData()
    for name, value in {"model": "local", "response_format": "verbose_json", **fields}.items():
        form.add_field(name, value)
    form.add_field("file", audio, filename="audio.wav", content_type="audio/wav")
    return form


async def test_a_wav_is_transcribed_into_openai_style_segments(client):
    response = await client.post("/v1/audio/transcriptions", data=_form(_wav(2.0), language="de"))
    assert response.status == 200
    body = await response.json()
    assert body["segments"] == [{"start": 0.5, "end": 1.25, "text": " Hallo zusammen. "}]
    assert body["text"] == "Hallo zusammen." and body["language"] == "de"
    assert body["duration"] == 2.0
    assert client.calls == [(32000, "de")]


@pytest.mark.parametrize("audio", [_wav(rate=8000), _wav(channels=2), b"not a wav", _wav(0)])
async def test_only_16_khz_mono_pcm_is_accepted(client, audio):
    response = await client.post("/v1/audio/transcriptions", data=_form(audio))
    assert response.status == 400 and client.calls == []


async def test_language_is_validated_and_health_exposes_only_counts(client):
    response = await client.post("/v1/audio/transcriptions", data=_form(_wav(), language="german"))
    assert response.status == 400
    await client.post("/v1/audio/transcriptions", data=_form(_wav()))
    health = await (await client.get("/healthz")).json()
    processing = health.pop("processing_seconds")
    assert health == {"status": "ok", "requests": 2, "transcribed": 1, "rejected": 1,
                      "failed": 0, "abandoned": 0, "audio_seconds": 1.0}
    assert 0 <= processing < 5
    assert client.calls == [(16000, "auto")]


async def test_a_model_failure_returns_no_text():
    def broken(samples, language):
        raise RuntimeError("model crashed")
    async with TestClient(TestServer(build_app(broken))) as test_client:
        response = await test_client.post("/v1/audio/transcriptions", data=_form(_wav()))
        assert response.status == 500
        assert await response.json() == {"error": "transcription_failed"}


async def test_a_request_abandoned_while_waiting_for_the_model_is_not_transcribed():
    import asyncio
    import threading
    release, calls = threading.Event(), []

    def transcribe(samples, language):
        calls.append(len(samples))
        release.wait(5)
        return [], None
    async with TestClient(TestServer(build_app(transcribe))) as client:
        first = asyncio.create_task(client.post("/v1/audio/transcriptions", data=_form(_wav())))
        while not calls:
            await asyncio.sleep(0.01)
        waiting = asyncio.create_task(client.post("/v1/audio/transcriptions", data=_form(_wav())))
        await asyncio.sleep(0.2)
        waiting.cancel()                       # the Key's timeout closes the connection
        await asyncio.gather(waiting, return_exceptions=True)
        await asyncio.sleep(0.2)
        release.set()
        assert (await first).status == 200
        for _ in range(50):
            health = await (await client.get("/healthz")).json()
            if health["abandoned"]:
                break
            await asyncio.sleep(0.05)
        assert (health["abandoned"], calls) == (1, [16000])


def test_the_openvino_backend_returns_timestamped_segments_and_maps_the_language(monkeypatch):
    import sys
    from types import SimpleNamespace

    from aikey.whisper_server import openvino_transcriber
    seen = {}

    class Pipeline:
        def __init__(self, model_dir, device, **options):
            seen.update(model_dir=model_dir, device=device, options=options)

        def get_generation_config(self):
            return SimpleNamespace(task=None, return_timestamps=False, language="x")

        def generate(self, samples, config):
            seen.update(samples=len(samples), language=config.language, task=config.task,
                        timestamps=config.return_timestamps)
            return SimpleNamespace(chunks=[SimpleNamespace(start_ts=0.5, end_ts=1.25, text=" Hallo.")])

    monkeypatch.setitem(sys.modules, "openvino_genai", SimpleNamespace(WhisperPipeline=Pipeline))
    transcribe = openvino_transcriber("/models/turbo", "NPU", "/cache")
    assert transcribe([0.0] * 16000, "de") == ([(0.5, 1.25, " Hallo.")], "de")
    assert seen == {"model_dir": "/models/turbo", "device": "NPU", "options": {"CACHE_DIR": "/cache"},
                    "samples": 16000, "language": "<|de|>", "task": "transcribe", "timestamps": True}
    assert transcribe([0.0], "auto")[1] is None and seen["language"] is None
