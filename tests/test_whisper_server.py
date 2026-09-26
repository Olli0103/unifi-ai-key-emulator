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
    assert health == {"status": "ok", "requests": 2, "transcribed": 1, "rejected": 1,
                      "failed": 0}
    assert client.calls == [(16000, "auto")]


async def test_a_model_failure_returns_no_text():
    def broken(samples, language):
        raise RuntimeError("model crashed")
    async with TestClient(TestServer(build_app(broken))) as test_client:
        response = await test_client.post("/v1/audio/transcriptions", data=_form(_wav()))
        assert response.status == 500
        assert await response.json() == {"error": "transcription_failed"}
