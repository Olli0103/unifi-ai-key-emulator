"""Protect's hybrid-search rerank sidecar on loopback, relayed to the vision server (synthetic texts)."""

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest
import pytest_asyncio

from aikey.rerank_relay import RerankRelay


class Vision:
    def __init__(self):
        self.reply, self.requests = None, []

    async def rerank(self, request):
        body = await request.json()
        self.requests.append(body)
        if self.reply is not None:
            return self.reply
        return web.json_response({"scores": [float(len(d)) for d in body["documents"]], "model": "m"})


@pytest_asyncio.fixture
async def vision():
    service = Vision()
    app = web.Application()
    app.router.add_post("/v1/rerank", service.rerank)
    async with TestServer(app) as server:
        service.origin = str(server.make_url("")).rstrip("/")
        yield service


@pytest_asyncio.fixture
async def relay(vision):
    relay = RerankRelay(vision.origin, port=0)
    import aiohttp
    relay._session = aiohttp.ClientSession()
    async with TestClient(TestServer(relay.app())) as client:
        yield relay, client
    await relay._session.close()


async def test_protects_request_is_relayed_and_scores_come_back_in_order(relay, vision):
    relay, client = relay
    response = await client.post("/rerank", json={"query": "cat", "documents": ["a cat", ""]})
    assert response.status == 200 and await response.json() == {"scores": [5.0, 0.0]}
    assert vision.requests == [{"query": "cat", "documents": ["a cat", ""]}]
    assert relay.status["requests"] == 1 and relay.status["documents"] == 2


@pytest.mark.parametrize("body", [{"query": "", "documents": ["a"]}, {"query": "q", "documents": []},
                                  {"query": "q", "documents": ["a"] * 33}, {"query": "q", "documents": [1]},
                                  {"query": "q", "documents": ["a"], "x": 1}, ["q"]])
async def test_malformed_requests_never_reach_the_vision_server(relay, vision, body):
    relay, client = relay
    assert (await client.post("/rerank", json=body)).status == 400
    assert vision.requests == [] and relay.status["rejected"] == 1


@pytest.mark.parametrize("reply", [web.json_response({"scores": [1.0]}),          # wrong count
                                   web.json_response({"scores": [1.0, "x"]}),
                                   web.json_response({"error": "x"}, status=500),
                                   web.Response(body=b"not json")])
async def test_a_bad_vision_reply_fails_closed_with_503(relay, vision, reply):
    relay, client = relay
    vision.reply = reply
    response = await client.post("/rerank", json={"query": "q", "documents": ["a", "b"]})
    assert response.status == 503 and relay.status["failed"] == 1


async def test_the_relay_binds_loopback_on_protects_port():
    relay = RerankRelay("http://172.30.50.14:8190/")
    assert (relay.host, relay.port, relay.url) == ("127.0.0.1", 8123, "http://172.30.50.14:8190/v1/rerank")
