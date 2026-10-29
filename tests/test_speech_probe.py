"""Silence-only probes use disposable loopback APIs, never real audio/models."""

import asyncio
import gzip
from io import BytesIO
import json
import wave

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey import provider_probe as probe
from aikey.speech import SpeechProvider

KEY = "SYNTHETIC-SPEECH-PROBE-KEY"


def test_generated_audio_contains_exactly_one_second_of_silence():
    raw = probe._silence()
    assert len(raw) == 32044
    with wave.open(BytesIO(raw), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                audio.getnframes(), audio.getcomptype()) == (1, 2, 16000, 16000, "NONE")
        assert audio.readframes(16001) == bytes(32000)


@pytest.mark.parametrize("provider", ["openai", "openai-compatible"])
@pytest.mark.parametrize("reply,matched", [({"segments": []}, True), ({"text": ""}, True),
    ({"segments": [{"start": 0, "end": 1, "text": "synthetic-hallucination-canary"}]}, False)])
async def test_saved_adapter_uploads_one_generated_file_and_returns_no_text(provider, reply, matched):
    seen = []
    async def endpoint(request):
        reader = await request.multipart()
        fields, upload = [], None
        while (part := await reader.next()) is not None:
            if part.name == "file":
                upload = (part.filename, part.headers.get("Content-Type"), bytes(await part.read()))
            else:
                fields.append((part.name, await part.text()))
        seen.append((request.path, dict(request.headers), fields, upload))
        return web.json_response(reply)
    app = web.Application()
    app.router.add_post("/v1/audio/transcriptions", endpoint)
    async with TestServer(app) as server:
        selected = SpeechProvider({"provider": provider, "model": "fictional-speech",
            "base_url": str(server.make_url("/v1")), "api_key": KEY, "language": "de"}, lab=True)
        report = await probe.probe_speech(selected)
    assert report == {"synthetic_silence_matched": matched, "elapsed_ms": report["elapsed_ms"],
                      "model_identity_verified": False, "quality_verified": False}
    assert type(report["elapsed_ms"]) is int and report["elapsed_ms"] >= 0
    assert KEY not in json.dumps(report) and "canary" not in json.dumps(report)
    assert len(seen) == 1
    path, headers, fields, upload = seen[0]
    assert path == "/v1/audio/transcriptions"
    assert headers["Authorization"] == "Bearer " + KEY
    assert headers["Accept-Encoding"] == "identity"
    assert not {"Cookie", "x-ident", "Camera-MAC"} & set(headers)
    assert fields == selected.form_fields()
    assert upload == ("synthetic-silence.wav", "audio/wav", probe._silence())


@pytest.mark.parametrize("failure", ["redirect", "status", "mime", "encoding", "oversize",
    "json", "duplicate", "nonfinite", "segments", "error", "delay"])
async def test_unusable_audio_replies_fail_once_with_redacted_error(monkeypatch, failure):
    seen = []
    async def endpoint(request):
        seen.append(request.path)
        await request.read()
        if failure == "redirect":
            return web.Response(status=307, headers={"Location": "/do-not-follow"})
        if failure == "status":
            return web.json_response({"error": KEY}, status=503)
        if failure == "mime":
            return web.Response(text=KEY, content_type="text/plain")
        if failure == "encoding":
            return web.Response(body=gzip.compress(b'{"segments":[]}'),
                                headers={"Content-Encoding": "gzip"}, content_type="application/json")
        if failure == "delay":
            await asyncio.sleep(.05)
            return web.json_response({"segments": []})
        raw = {"oversize": b" " * (probe.MAX_REPLY_BYTES + 1), "json": KEY.encode(),
               "duplicate": b'{"segments":[],"segments":[]}',
               "error": b'{"segments":[],"error":"PRIVATE-CANARY"}',
               "nonfinite": b'{"segments":[{"start":NaN,"end":1,"text":"private-canary"}]}',
               "segments": b'{"segments":[{"start":-1,"end":1,"text":"private-canary"}]}'}[failure]
        return web.Response(body=raw, content_type="application/json")
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", endpoint)
    if failure == "delay":
        monkeypatch.setattr(probe, "TOTAL_SECONDS", .02)
    async with TestServer(app) as server:
        selected = SpeechProvider({"provider": "openai-compatible", "model": "fictional-speech",
            "base_url": str(server.make_url("/v1")), "api_key": KEY})
        with pytest.raises(probe.ProbeError, match="^probe_unavailable$"):
            await probe.probe_speech(selected)
    assert seen == ["/v1/audio/transcriptions"]


async def test_address_refusal_is_before_http_and_cancellation_is_not_swallowed(monkeypatch):
    from aikey.provider_network import ProviderAddressError
    selected = SpeechProvider({"provider": "openai-compatible", "model": "fictional-speech",
                               "base_url": "http://127.0.0.1:1/v1"})
    def refused():
        raise ProviderAddressError("PRIVATE-CANARY")
    monkeypatch.setattr(probe, "ProviderConnector", refused)
    with pytest.raises(probe.ProbeError, match="^probe_not_started$"):
        await probe.probe_speech(selected)
    def cancelled():
        raise asyncio.CancelledError()
    monkeypatch.setattr(probe, "ProviderConnector", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await probe.probe_speech(selected)
