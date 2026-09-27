"""One provider role's credential never reaches another role (#8).

Each role talks to its own synthetic local double on a separate port. The
doubles record the credentials and cookies they receive. Positive controls
prove each intended role still authenticates. No external provider is called.
"""

import json
import logging

from aiohttp import web
import pytest
import pytest_asyncio

from aikey.config_store import ConfigurationStore
from aikey.search import EmbeddingError, EmbeddingService
from aikey.worker import JobProcessor, WorkerError
from test_config_store import store as config_store_fixture
from test_worker import PNG

VISION_KEY = "synthetic-vision-key-7f3a"
SPEECH_KEY = "synthetic-speech-key-91bc"
EMBED_KEY = "synthetic-embedding-key-c04d"
SECRETS = (VISION_KEY, SPEECH_KEY, EMBED_KEY)


class Double:
    """A local provider double that records what reaches it."""

    def __init__(self, name, reply, *, cookie=None, redirect_to=None):
        self.name, self.reply, self.cookie, self.redirect_to = name, reply, cookie, redirect_to
        self.seen = []

    async def handle(self, request):
        await request.read()
        self.seen.append({"authorization": request.headers.get("Authorization"),
                          "x_api_key": request.headers.get("x-api-key"),
                          "cookie": request.headers.get("Cookie")})
        if self.redirect_to:
            raise web.HTTPTemporaryRedirect(self.redirect_to)
        response = web.json_response(self.reply)
        if self.cookie:
            response.set_cookie("gateway_session", self.cookie, path="/")
        return response


async def serve(double):
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", double.handle)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    double.origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    return runner


@pytest_asyncio.fixture
async def doubles():
    made = {
        "vision": Double("vision", {"choices": [{"message": {"content": "A synthetic scene."}}]},
                         cookie="vision-gateway-cookie"),
        "speech": Double("speech", {"text": "hi", "segments": [
            {"start": 0, "end": 1, "text": "hi", "no_speech_prob": 0.0}]}),
        "faces": Double("faces", {"faces": []}),
        "evil": Double("evil", {}),
    }
    runners = [await serve(d) for d in made.values()]
    try:
        yield made
    finally:
        for runner in runners:
            await runner.cleanup()


def worker_options(d):
    return {"runtime": {"mode": "lab"}, "controller_origins": [d["vision"].origin],
            "device": {"mac": "02:00:00:00:00:98"},
            "inference": {"base_url": d["vision"].origin + "/v1", "model": "synthetic-vision",
                          "api_key": VISION_KEY},
            "speech_to_text": {"provider": "openai-compatible", "model": "synthetic-whisper",
                               "base_url": d["speech"].origin + "/v1", "camera_ids": ["cam-a"],
                               "api_key": SPEECH_KEY},
            "face_recognition": {"server": d["faces"].origin, "camera_ids": ["cam-a"]},
            "worker": {"max_queue": 2, "timeout_s": 10}}


async def exercise_all_roles(worker):
    await worker.start()
    try:
        await worker._infer([PNG])
    except WorkerError:
        pass                                  # only the request matters here
    await worker._transcribe(b"RIFF" + b"\0" * 60, 1000)
    await worker._detect_faces(PNG, [0.0, 0.0, 1.0, 1.0])
    await worker._detect_faces(PNG, [0.0, 0.0, 1.0, 1.0])   # second call: after any cookie was set


async def test_each_role_gets_only_its_own_credential_and_no_cookie_crosses(doubles, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    worker = JobProcessor(worker_options(doubles), tmp_path)
    try:
        await exercise_all_roles(worker)
        status = json.dumps(worker.status())
    finally:
        await worker.stop()
    vision, speech, faces = doubles["vision"].seen, doubles["speech"].seen, doubles["faces"].seen
    # Positive controls: each authenticated role sent its own key.
    assert vision and all(s["authorization"] == "Bearer " + VISION_KEY for s in vision)
    assert speech and all(s["authorization"] == "Bearer " + SPEECH_KEY for s in speech)
    # No role received another role's key, and the key-less face server got none.
    assert len(faces) == 2 and all(s["authorization"] is None and s["x_api_key"] is None for s in faces)
    # A cookie set by the vision gateway is never replayed to another role on the same host.
    assert all(s["cookie"] is None for s in speech + faces)
    # Keys stay out of health, logs and the job journal.
    journal = "".join(p.read_text() for p in (tmp_path / "worker-jobs").glob("*.json"))
    for secret in SECRETS:
        assert secret not in status and secret not in caplog.text and secret not in journal


async def test_a_gateway_cookie_is_not_replayed_to_another_role_on_the_same_hostname(doubles, tmp_path):
    """IP-literal hosts never store cookies; a hostname does, and cookies ignore ports."""
    for name in ("vision", "speech"):
        doubles[name].origin = doubles[name].origin.replace("127.0.0.1", "localhost")
    worker = JobProcessor(worker_options(doubles), tmp_path)
    try:
        await exercise_all_roles(worker)
        await worker._transcribe(b"RIFF" + b"\0" * 60, 1000)
    finally:
        await worker.stop()
    speech = doubles["speech"].seen
    assert len(speech) == 2 and all(s["authorization"] == "Bearer " + SPEECH_KEY for s in speech)
    assert [s["cookie"] for s in speech] == [None, None]
    assert all(s["cookie"] is None for s in doubles["vision"].seen)   # not even replayed to itself


async def test_a_redirect_never_carries_the_key_to_another_host(doubles, tmp_path):
    doubles["vision"].redirect_to = doubles["evil"].origin + "/v1/chat/completions"
    doubles["speech"].redirect_to = doubles["evil"].origin + "/v1/audio/transcriptions"
    worker = JobProcessor(worker_options(doubles), tmp_path)
    try:
        await worker.start()
        with pytest.raises(WorkerError, match="HTTP 307"):
            await worker._infer([PNG])
        with pytest.raises(WorkerError, match="HTTP 307"):
            await worker._transcribe(b"RIFF" + b"\0" * 60, 1000)
    finally:
        await worker.stop()
    assert doubles["vision"].seen and doubles["speech"].seen      # the intended hosts were asked
    assert doubles["evil"].seen == []                              # the redirect target never was


async def test_the_embedding_token_reaches_only_its_endpoint(tmp_path):
    token = tmp_path / "embedding-token"
    token.write_text(EMBED_KEY + "\n")
    embed = Double("embed", {"model": "multilingual-e5-small", "data": [{"index": 0, "embedding": [1.0] + [0.0] * 383}]})
    evil = Double("evil", {})
    runners = [await serve(embed), await serve(evil)]
    try:
        service = EmbeddingService({"backend": "http", "endpoint": embed.origin + "/v1/embeddings",
                                    "bearer_token_file": str(token)})
        vector = await service.embed("a person")
        assert vector[0] == 1.0 and embed.seen[0]["authorization"] == "Bearer " + EMBED_KEY
        embed.redirect_to = evil.origin + "/v1/embeddings"
        with pytest.raises(EmbeddingError, match="HTTP 307"):
            await service.embed("a vehicle")
        assert evil.seen == []
        await service._session.close()
    finally:
        for runner in runners:
            await runner.cleanup()


def test_preview_output_never_contains_a_key_value_or_its_file(tmp_path):
    config_store, _ = config_store_fixture(tmp_path)
    (tmp_path / "state" / "provider-key").write_text(VISION_KEY)
    before = config_store.snapshot()
    preview = config_store.preview_inference(before.revision, {
        "provider": "ollama", "model": "synthetic-local-vision",
        "base_url": "http://127.0.0.1:11434", "max_output_tokens": 128})
    encoded = json.dumps([before.configuration, preview.configuration, list(preview.changed_fields)])
    assert VISION_KEY not in encoded and "provider-key" not in encoded
    assert isinstance(config_store, ConfigurationStore)


async def test_the_ai_port_detector_key_reaches_only_its_endpoint(tmp_path):
    import asyncio
    from aikey.aiport_api_detection import ApiDetectionError, _post
    detector = Double("detector", {"ok": True})
    evil = Double("evil", {})
    runners = [await serve(detector), await serve(evil)]
    try:
        headers = {"Authorization": "Bearer " + VISION_KEY}
        await asyncio.to_thread(_post, detector.origin + "/v1/chat/completions", headers, {"x": 1})
        assert detector.seen[0]["authorization"] == "Bearer " + VISION_KEY
        detector.redirect_to = evil.origin + "/v1/chat/completions"
        with pytest.raises(ApiDetectionError):
            await asyncio.to_thread(_post, detector.origin + "/v1/chat/completions", headers, {"x": 1})
        assert len(detector.seen) == 2 and evil.seen == []
    finally:
        for runner in runners:
            await runner.cleanup()
