"""Protect's hybrid session-search rerank sidecar, relayed to the local vision server.

Protect 7.3.70 provisions a ``plpython3u`` function ``rerank(qtext, ids,
texts)`` on an external search host. It posts ``{"query", "documents"}`` to
``http://127.0.0.1:8123/rerank`` inside the PostgreSQL network namespace and
expects ``{"scores"}``, one cross-encoder logit per document. The search host's
PostgreSQL shares the Key's namespace, so the Key answers on loopback and
relays to the vision server's ``/v1/rerank``.

Any failure answers 503: Protect's function then returns NULL scores and its
read SQL drops those rows (fail closed). Texts are never logged or stored.
"""

from __future__ import annotations

import math

import aiohttp
from aiohttp import web

PORT = 8123
MAX_BODY_BYTES = 1024 * 1024
MAX_DOCUMENTS = 32
TIMEOUT_S = 4                 # Protect's function gives up after 5 s per socket operation


def valid_request(body) -> bool:
    return (isinstance(body, dict) and set(body) == {"query", "documents"}
            and isinstance(body["query"], str) and body["query"].strip() != ""
            and isinstance(body["documents"], list) and 1 <= len(body["documents"]) <= MAX_DOCUMENTS
            and all(isinstance(document, str) for document in body["documents"]))


class RerankRelay:
    def __init__(self, server: str, *, host: str = "127.0.0.1", port: int = PORT):
        self.url = server.rstrip("/") + "/v1/rerank"
        self.host, self.port = host, port
        self.status = {"enabled": True, "requests": 0, "documents": 0, "rejected": 0, "failed": 0}
        self.runner: web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None

    def app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY_BYTES)
        app.router.add_post("/rerank", self.rerank)
        return app

    async def rerank(self, request: web.Request) -> web.Response:
        self.status["requests"] += 1
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not valid_request(body):
            self.status["rejected"] += 1
            return web.json_response({"error": "invalid_request"}, status=400)
        try:
            async with self._session.post(self.url, json=body, allow_redirects=False) as response:
                reply = await response.json() if response.status == 200 else None
        except (aiohttp.ClientError, TimeoutError, ValueError):
            reply = None
        scores = reply.get("scores") if isinstance(reply, dict) else None
        if (not isinstance(scores, list) or len(scores) != len(body["documents"])
                or any(type(s) not in (int, float) or not math.isfinite(s) for s in scores)):
            self.status["failed"] += 1
            return web.json_response({"error": "rerank_unavailable"}, status=503)
        self.status["documents"] += len(scores)
        return web.json_response({"scores": scores})

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_S))
        self.runner = web.AppRunner(self.app(), access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, self.host, self.port).start()

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
        if self._session is not None:
            await self._session.close()
            self._session = None
