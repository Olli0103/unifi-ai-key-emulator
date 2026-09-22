"""Local fixture tests. Synthetic vectors here are never production fallbacks."""

import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import ssl
from types import SimpleNamespace

from aiohttp import web
import pytest

from aikey.protocol import decode_message, encode_message
from aikey.search import EmbeddingError, EmbeddingService, SearchService, normalize_embedding
from aikey.tls import ensure_identity_certificate, server_context


@pytest.fixture
async def embedding_api():
    seen = []

    async def embeddings(request):
        payload = await request.json()
        seen.append(payload)
        return web.json_response({"data": [
            {"index": index, "embedding": [3.0, 4.0] + [0.0] * 382}
            for index in reversed(range(len(payload["input"])))
        ]})

    app = web.Application()
    app.router.add_post("/v1/embeddings", embeddings)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", seen
    finally:
        await runner.cleanup()


async def test_http_encoder_uses_e5_prefixes_and_normalizes(embedding_api):
    url, seen = embedding_api
    service = EmbeddingService({"backend": "http", "base_url": url})
    try:
        queries = await service.encode_queries(["a red car", "a dog"])
        documents = await service.encode_documents(["A red car drives away."])
    finally:
        await service.close()
    assert seen[0]["input"] == ["query: a red car", "query: a dog"]
    assert seen[1]["input"] == ["passage: A red car drives away."]
    assert seen[0]["model"] == "multilingual-e5-small"
    assert queries[0][:2] == [0.6, 0.8]
    assert math.hypot(*documents[0]) == pytest.approx(1)


@pytest.mark.parametrize("vector", [
    [0.0] * 384, [1.0] * 768, [float("nan")] + [1.0] * 383,
    [True] + [1.0] * 383, [float("inf")] + [1.0] * 383,
    [10 ** 500] + [1.0] * 383,
])
def test_invalid_vectors_are_never_padded_or_faked(vector):
    with pytest.raises(EmbeddingError):
        normalize_embedding(vector)


async def test_missing_backend_fails_instead_of_returning_vectors():
    with pytest.raises(EmbeddingError, match="No embedding backend"):
        await EmbeddingService({}).encode_queries(["person"])


async def test_search_does_not_start_without_explicit_boolean_enablement(tmp_path):
    service = SearchService({"search": {"enabled": "false"}}, tmp_path)
    await service.start()
    assert service._task is None
    assert not (tmp_path / "search-profile.json").exists()


@pytest.mark.parametrize("result", [[], {"data": []}, {"model": "another-model", "data": []}])
async def test_invalid_http_shapes_raise_embedding_error(result):
    class Response:
        status = 200

        async def chunks(self, size):
            yield json.dumps(result).encode()

        @property
        def content(self):
            return SimpleNamespace(iter_chunked=self.chunks)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    service = EmbeddingService({"backend": "http", "base_url": "http://localhost:9000"})
    service._session = SimpleNamespace(closed=False, post=lambda *args, **kwargs: Response())
    with pytest.raises(EmbeddingError):
        await service.encode_queries(["vehicle"])


async def test_session_response_uses_requested_id_and_explicit_profile(tmp_path, embedding_api):
    url, _ = embedding_api
    service = SearchService({"embeddings": {"backend": "http", "base_url": url}}, tmp_path)
    try:
        response = await service.handle_message(encode_message(
            {"id": "query-1", "type": "request", "timestamp": 1, "action": "NL_PARSE"},
            {"querySentence": "red car", "model": "multilingual-e5-small"},
        ))
        message = decode_message(response)
        assert message.header["id"] == "query-1"
        assert message.header["errorCode"] == 0
        assert message.body["model"] == "multilingual-e5-small"
        assert len(message.body["txtEmbed"]) == 384
        assert message.body["exact_match"] is False
        assert message.body["keyTags"] == []
    finally:
        await service.stop()


@pytest.mark.parametrize("body", [
    {"querySentence": "person"},
    {"querySentence": "person", "model": "clip-ViT-L-14"},
    {"querySentence": "", "model": "multilingual-e5-small"},
])
async def test_unsupported_queries_have_error_without_fake_success(tmp_path, body):
    service = SearchService({}, tmp_path)
    response = await service.handle_message(encode_message(
        {"id": "unsupported", "type": "request", "timestamp": 1, "action": "NL_PARSE"}, body,
    ))
    message = decode_message(response)
    assert message.header["errorCode"] != 0
    assert "txtEmbed" not in message.body


async def test_api_failure_and_redirect_are_not_followed(tmp_path):
    visited = []

    async def rejected(request):
        return web.Response(status=302, headers={"Location": "/secret"})

    async def secret(request):
        visited.append(True)
        return web.json_response({"data": []})

    app = web.Application()
    app.router.add_post("/v1/embeddings", rejected)
    app.router.add_route("*", "/secret", secret)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    service = EmbeddingService({"backend": "http", "base_url": f"http://127.0.0.1:{port}"})
    try:
        with pytest.raises(EmbeddingError, match="HTTP 302"):
            await service.encode_queries(["car"])
        assert not visited
    finally:
        await service.close()
        await runner.cleanup()


async def test_profile_change_requires_reconciliation(tmp_path):
    original = SearchService({"embeddings": {"backend": "http", "base_url": "http://localhost:9000"}}, tmp_path)
    original._check_profile()
    stored = json.loads((tmp_path / "search-profile.json").read_text())
    assert "fingerprint" in stored
    changed = SearchService({"embeddings": {"backend": "http", "base_url": "http://localhost:9001"}}, tmp_path)
    with pytest.raises(EmbeddingError, match="profile changed"):
        changed._check_profile()


async def test_local_encoder_is_lazy_local_only_and_uses_same_prefixes(tmp_path, monkeypatch):
    calls = []

    class LocalFixtureModel:
        def __init__(self, path, **kwargs):
            calls.append((path, kwargs))

        def encode(self, texts, **kwargs):
            calls.append((texts, kwargs))
            return SimpleNamespace(tolist=lambda: [[1.0] + [0.0] * 383 for _ in texts])

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=LocalFixtureModel))
    service = EmbeddingService({"backend": "sentence-transformers", "model_path": str(tmp_path)})
    assert not calls
    result = await service.encode_documents(["person enters"])
    assert len(result[0]) == 384
    assert calls[0][1]["local_files_only"] is True
    assert calls[0][1]["trust_remote_code"] is False
    assert calls[1][0] == ["passage: person enters"]
    assert calls[1][1]["prompt"] == ""
    assert service._model.max_seq_length == 512


async def test_websocket_registration_and_real_http_adapter(tmp_path, embedding_api):
    url, seen = embedding_api
    received = asyncio.get_running_loop().create_future()

    async def handler(request):
        assert request.headers["x-ident"] == "020000000001"
        websocket = web.WebSocketResponse(protocols=("ucp4",))
        await websocket.prepare(request)
        registration = decode_message((await websocket.receive()).data)
        assert registration.header["action"] == "echo"
        await websocket.send_bytes(encode_message(
            {"id": "native-query", "type": "request", "timestamp": 1, "action": "NL_PARSE"},
            {"querySentence": "vehicle exits", "model": "multilingual-e5-small"},
        ))
        received.set_result(decode_message((await websocket.receive()).data))
        await websocket.close()
        return websocket

    app = web.Application()
    app.router.add_get("/wss/nl-search/v1", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    cert, _ = ensure_identity_certificate(tmp_path, "020000000001")
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_context(tmp_path))
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    service = SearchService({"search": {"enabled": True}, "device": {"mac": "02:00:00:00:00:01"},
                             "controller": {"host": "127.0.0.1", "search_port": port},
                             "embeddings": {"backend": "http", "base_url": url}}, tmp_path,
                            ssl.create_default_context(cafile=cert))
    try:
        await service.start()
        reply = await asyncio.wait_for(received, timeout=3)
        assert reply.header["id"] == "native-query"
        assert len(reply.body["txtEmbed"]) == 384
        assert seen[0]["input"] == ["query: vehicle exits"]
    finally:
        await service.stop()
        await runner.cleanup()


def test_postgres_script_syntax_and_address_rejection(tmp_path):
    script = Path(__file__).resolve().parents[1] / "deployment/postgres/entrypoint.sh"
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    for address in ("0.0.0.0/0", "127.0.0.1\ntrust", "256.1.1.1", "192.168.001.1"):
        result = subprocess.run(["bash", str(script)], env={
            "PATH": "/usr/bin:/bin", "CONSOLE_IP": address,
            "POSTGRES_PASSWORD_FILE": "/missing", "POSTGRES_TLS_CERT_FILE": "/missing",
            "POSTGRES_TLS_KEY_FILE": "/missing",
        }, capture_output=True, text=True)
        assert result.returncode != 0
        assert "IPv4" in result.stderr or "CONSOLE_IP" in result.stderr
