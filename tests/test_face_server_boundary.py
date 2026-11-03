"""Owned loopback HTTP and fake analyzers only; no face images or models."""

import asyncio
import threading

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey import face_server


JPEG = b"\xff\xd8\xff" + b"synthetic" * 8


def form(*fields):
    data = FormData()
    for name, value in fields or (("image", JPEG),):
        data.add_field(name, value, filename="fixture" if isinstance(value, bytes) else None)
    return data


@pytest.mark.parametrize("fields", [
    (("image", JPEG), ("image", JPEG)),
    (("image", JPEG), ("regions", "[[0,0,1,1]]"), ("regions", "[[0,0,1,1]]")),
    (("image", JPEG), ("unexpected", "private fixture")),
    (("image", JPEG), ("regions", "[[0,0,1,1]]" + " " * 65536)),
])
async def test_ambiguous_or_oversized_fields_never_reach_analyzer(fields):
    calls = []
    async with TestClient(TestServer(face_server.build_app(lambda *a: calls.append(a) or []))) as c:
        response = await c.post("/v1/faces", data=form(*fields))
        assert response.status == 400
        assert await response.json() == {"error": "invalid_request"}
        assert calls == []


@pytest.mark.parametrize("body,content_type", [
    (b"broken", "multipart/form-data"),
    (b"--fixture\r\nbad header\r\n\r\nx\r\n--fixture--\r\n",
     "multipart/form-data; boundary=fixture"),
])
async def test_malformed_multipart_has_a_fixed_client_error(body, content_type):
    async with TestClient(TestServer(face_server.build_app(lambda *_: []))) as c:
        response = await c.post("/v1/faces", data=body, headers={"Content-Type": content_type})
        assert response.status == 400
        assert await response.json() == {"error": "invalid_request"}


async def test_streaming_image_is_rejected_before_sender_finishes(monkeypatch):
    monkeypatch.setattr(face_server, "_MAX_IMAGE_BYTES", 1024)
    release = asyncio.Event()
    sent = asyncio.Event()

    async def upload():
        yield (b'--fixture\r\nContent-Disposition: form-data; name="image"; filename="f.jpg"'
               b"\r\nContent-Type: image/jpeg\r\n\r\n")
        yield JPEG + b"x" * 32768
        sent.set()
        await release.wait()
        yield b"\r\n--fixture--\r\n"

    calls = []
    async with TestClient(TestServer(face_server.build_app(lambda *a: calls.append(a) or []))) as c:
        request = asyncio.create_task(c.post("/v1/faces", data=upload(), headers={
            "Content-Type": "multipart/form-data; boundary=fixture"}))
        try:
            await asyncio.wait_for(sent.wait(), 2)
            response = await asyncio.wait_for(asyncio.shield(request), 0.5)
            assert response.status == 400
            assert await response.json() == {"error": "invalid_request"}
            assert not release.is_set() and calls == []
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)


async def test_repeated_handler_cancellation_cannot_overlap_model_threads():
    loop = asyncio.get_running_loop()
    started, second_started = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    handlers = asyncio.Queue()
    calls = []

    def analyze(*_):
        calls.append(1)
        loop.call_soon_threadsafe((started if len(calls) == 1 else second_started).set)
        release.wait(5)
        return []

    @web.middleware
    async def capture(request, handler):
        if request.path == "/v1/faces":
            handlers.put_nowait(request.task)
        return await handler(request)

    app = face_server.build_app(analyze)
    app.middlewares.append(capture)
    async with TestClient(TestServer(app)) as c:
        first = asyncio.create_task(c.post("/v1/faces", data=form()))
        handler = await asyncio.wait_for(handlers.get(), 2)
        second = None
        try:
            await asyncio.wait_for(started.wait(), 2)
            handler.cancel()
            await asyncio.sleep(0.02)
            handler.cancel()
            second = asyncio.create_task(c.post("/v1/faces", data=form()))
            await asyncio.wait_for(handlers.get(), 2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(second_started.wait(), 0.15)
            assert calls == [1]
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        assert second is not None and (await second).status == 200
        assert calls == [1, 1]


@pytest.mark.parametrize("regions", [None, "[[0.1,0.2,0.8,0.9]]", "[[0,0,1,1]]" + " " * 65525])
async def test_valid_original_bytes_regions_and_health_contract_are_preserved(regions):
    calls = []
    embedding = [1.0] + [0.0] * 127
    def analyze(*args):
        calls.append(args)
        return [((0.123456, 0.2, 0.8, 0.9), 0.987654, embedding)]
    async with TestClient(TestServer(face_server.build_app(analyze))) as c:
        fields = [("image", JPEG)]
        if regions is not None:
            fields.insert(0, ("regions", regions))
        response = await c.post("/v1/faces", data=form(*fields))
        assert response.status == 200
        assert await response.json() == {"faces": [{"box": [0.12346, 0.2, 0.8, 0.9],
             "score": 0.9877, "embedding": embedding}]}
        assert calls == [(JPEG, face_server.parse_regions(regions))]
        assert await (await c.get("/healthz")).json() == {
            "status": "ok", "requests": 1, "analyzed": 1, "faces": 1, "rejected": 0, "failed": 0}


@pytest.mark.parametrize("extra", [0, 1])
async def test_exact_image_limit_is_inclusive(monkeypatch, extra):
    monkeypatch.setattr(face_server, "_MAX_IMAGE_BYTES", len(JPEG))
    calls = []
    async with TestClient(TestServer(face_server.build_app(lambda *a: calls.append(a) or []))) as c:
        response = await c.post("/v1/faces", data=form(("image", JPEG + b"x" * extra)))
        assert response.status == (400 if extra else 200)
        assert len(calls) == (0 if extra else 1)


@pytest.mark.parametrize("headers,body", [
    ("Content-Transfer-Encoding: base64\r\n", JPEG),
    ("Content-Encoding: gzip\r\n", JPEG),
    ("Content-Type: multipart/mixed; boundary=nested\r\n", b"--nested--\r\n"),
])
async def test_encoded_or_nested_parts_are_not_analyzer_input(headers, body):
    calls = []
    wire = (b'--fixture\r\nContent-Disposition: form-data; name="image"\r\n'
            + headers.encode() + b"\r\n" + body + b"\r\n--fixture--\r\n")
    async with TestClient(TestServer(face_server.build_app(lambda *a: calls.append(a) or []))) as c:
        response = await c.post("/v1/faces", data=wire, headers={
            "Content-Type": "multipart/form-data; boundary=fixture"})
        assert response.status == 400 and calls == []
        assert await response.json() == {"error": "invalid_request"}


@pytest.mark.parametrize("failure,code,status", [
    (face_server.ImageError("private fixture"), "invalid_image", 400),
    (RuntimeError("private fixture"), "analysis_failed", 500),
])
async def test_analysis_failure_stays_fixed_and_releases_model(failure, code, status):
    calls = []
    def analyze(*_):
        calls.append(1)
        if len(calls) == 1:
            raise failure
        return []
    async with TestClient(TestServer(face_server.build_app(analyze))) as c:
        response = await c.post("/v1/faces", data=form())
        assert response.status == status and await response.json() == {"error": code}
        response = await c.post("/v1/faces", data=form())
        assert response.status == 200 and await response.json() == {"faces": []}
        health = await (await c.get("/healthz")).json()
        assert health["rejected" if status == 400 else "failed"] == 1
        assert health["analyzed"] == 1 and calls == [1, 1]


async def test_disconnected_default_handler_waiter_does_not_start_analysis():
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    calls = []
    def analyze(*_):
        calls.append(1)
        loop.call_soon_threadsafe(started.set)
        release.wait(5)
        return []
    async with TestClient(TestServer(face_server.build_app(analyze))) as c:
        first = asyncio.create_task(c.post("/v1/faces", data=form()))
        waiting = None
        try:
            await asyncio.wait_for(started.wait(), 2)
            waiting = asyncio.create_task(c.post("/v1/faces", data=form()))
            for _ in range(100):
                if (await (await c.get("/healthz")).json())["requests"] == 2:
                    break
                await asyncio.sleep(0.005)
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            await asyncio.sleep(0.05)
        finally:
            release.set()
            await asyncio.gather(first, *([waiting] if waiting else []), return_exceptions=True)
        # A following completed request proves the abandoned waiter was passed.
        assert (await c.post("/v1/faces", data=form())).status == 200
        assert calls == [1, 1]


async def test_cleanup_drains_abandoned_owned_analysis_and_observes_failure():
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    handlers = asyncio.Queue()
    def analyze(*_):
        loop.call_soon_threadsafe(started.set)
        release.wait(5)
        raise RuntimeError("private fixture")
    @web.middleware
    async def capture(request, handler):
        handlers.put_nowait(request.task)
        return await handler(request)
    app = face_server.build_app(analyze)
    app.middlewares.append(capture)
    c = TestClient(TestServer(app))
    await c.start_server()
    request = asyncio.create_task(c.post("/v1/faces", data=form()))
    closing = None
    try:
        handler = await asyncio.wait_for(handlers.get(), 2)
        await asyncio.wait_for(started.wait(), 2)
        handler.cancel()
        await asyncio.gather(request, return_exceptions=True)
        closing = asyncio.create_task(c.close())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(closing), 0.1)
    finally:
        release.set()
        if closing is not None:
            await asyncio.wait_for(closing, 2)
        await c.close()
        await asyncio.gather(request, return_exceptions=True)
