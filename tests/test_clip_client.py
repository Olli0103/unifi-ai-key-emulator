"""The CLIP client refuses wrong modality, dimensions and vectors before use (#8).

Every reply comes from a synthetic local server; no model is loaded.
"""

import math

from aiohttp import web
import pytest
import pytest_asyncio

from aikey import clip
from aikey.clip import ClipClient, ClipError, normalize

GOOD = [0.0] * 767 + [2.0]


@pytest.mark.parametrize("vector,message", [
    ([0.0] * 768, "zero or invalid norm"),
    ([1.0] * 384, "exactly 768"),                        # an E5-sized vector: wrong encoder
    ([1.0] * 769, "exactly 768"),
    ([float("nan")] + [1.0] * 767, "non-finite"),
    ([float("inf")] + [1.0] * 767, "non-finite"),
    ([True] + [1.0] * 767, "nonnumeric"),
    (["1"] + [1.0] * 767, "nonnumeric"),
    ([1e200] * 768, "zero or invalid norm"),            # squares overflow: refused, not scaled
    ("not a list", "exactly 768"),
])
def test_invalid_vectors_are_refused_not_repaired(vector, message):
    with pytest.raises(ClipError, match=message):
        normalize(vector)


def test_a_valid_vector_is_unit_length():
    value = normalize(GOOD)
    assert value[-1] == 1.0 and math.isclose(math.fsum(v * v for v in value), 1.0)


class Server:
    def __init__(self):
        self.reply = {"model": clip.MODEL, "dim": clip.DIMENSIONS, "embeddings": [GOOD]}
        self.status = 200

    async def handle(self, request):
        await request.read()
        return web.json_response(self.reply, status=self.status)


@pytest_asyncio.fixture
async def server():
    service = Server()
    app = web.Application()
    app.router.add_post("/v1/text", service.handle)
    app.router.add_post("/v1/image", service.handle)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    service.client = ClipClient({"clip_server": f"http://127.0.0.1:{port}"})
    try:
        yield service
    finally:
        await service.client.close()
        await runner.cleanup()


async def test_a_matching_reply_is_accepted(server):
    assert (await server.client.embed_text("a person"))[-1] == 1.0
    server.reply["embeddings"] = [GOOD, GOOD]
    assert len(await server.client.embed_regions(b"\xff\xd8\xff", [[0, 0, 1, 1], [0, 0, 0.5, 0.5]])) == 2


@pytest.mark.parametrize("change,message", [
    ({"model": "intfloat/multilingual-e5-small"}, "incompatible model"),   # a text-only encoder
    ({"model": "clip-ViT-B-32"}, "incompatible model"),                     # another CLIP model
    ({"dim": 512}, "incompatible model"),
    ({"dim": None}, "incompatible model"),
    ({"embeddings": []}, "invalid reply"),                                  # wrong count
    ({"embeddings": [GOOD, GOOD]}, "invalid reply"),
    ({"embeddings": "vectors"}, "invalid reply"),
    ({"embeddings": [[0.0] * 768]}, "zero or invalid norm"),
    ({"embeddings": [[1.0] * 512]}, "exactly 768"),
])
async def test_a_mismatched_reply_is_refused(server, change, message):
    server.reply.update(change)
    with pytest.raises(ClipError, match=message):
        await server.client.embed_text("a person")


async def test_a_non_object_or_failed_reply_is_refused(server):
    server.reply = [GOOD]
    with pytest.raises(ClipError, match="invalid reply"):
        await server.client.embed_text("a person")
    server.reply, server.status = {"error": "down"}, 500
    with pytest.raises(ClipError, match="HTTP 500"):
        await server.client.embed_text("a person")


async def test_other_weights_are_refused_once_a_revision_is_pinned(server):
    server.reply["revision"] = "b" * 64
    server.client.expected_revision = "a" * 64
    with pytest.raises(ClipError, match="weights differ"):
        await server.client.embed_text("a person")
    server.reply["revision"] = "a" * 64
    assert (await server.client.embed_text("a person"))[-1] == 1.0


@pytest.mark.parametrize("server_url", ["https://127.0.0.1:1", "http://8.8.8.8:1", "http://127.0.0.1:1/v1",
                                        "http://user:pw@127.0.0.1:1"])
def test_the_encoder_must_be_a_local_http_server(server_url):
    with pytest.raises(ClipError, match="local HTTP server"):
        ClipClient({"clip_server": server_url})
