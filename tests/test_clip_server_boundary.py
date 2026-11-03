"""CLIP loopback transport and fake encoders, no checkpoint or real media."""

import asyncio
import threading

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey import clip_server


JPEG = b"\xff\xd8\xff" + b"synthetic" * 8
VECTOR = [1.0] + [0.0] * 767


def form(*fields):
    data = FormData()
    for name, value in fields or (("image", JPEG),):
        data.add_field(name, value, filename="fixture" if isinstance(value, bytes) else None)
    return data


def app(calls):
    def texts(value):
        calls.append(("text", value))
        return [VECTOR] * len(value)

    def image(*value):
        calls.append(("image", value))
        return [VECTOR]

    return clip_server.build_app(texts, image, revision="f" * 64)


@pytest.mark.parametrize(
    "fields",
    [
        (("image", JPEG), ("image", JPEG)),
        (("image", JPEG), ("regions", "[[0,0,1,1]]"), ("regions", "[[0,0,1,1]]")),
        (("image", JPEG), ("unknown", "private fixture")),
        (("image", JPEG), ("regions", "[[0,0,1,1]]" + " " * 65536)),
    ],
)
async def test_duplicate_unknown_or_oversized_fields_never_encode(fields):
    calls = []
    async with TestClient(TestServer(app(calls))) as c:
        response = await c.post("/v1/image", data=form(*fields))
        assert response.status == 400 and await response.json() == {"error": "invalid_request"}
        assert calls == []


@pytest.mark.parametrize(
    "wire,content_type",
    [
        (b"broken", "multipart/form-data"),
        (
            b"--fixture\r\nbad header\r\n\r\nx\r\n--fixture--\r\n",
            "multipart/form-data; boundary=fixture",
        ),
    ],
)
async def test_malformed_multipart_is_a_fixed_client_error(wire, content_type):
    calls = []
    async with TestClient(TestServer(app(calls))) as c:
        response = await c.post("/v1/image", data=wire, headers={"Content-Type": content_type})
        assert response.status == 400 and await response.json() == {"error": "invalid_request"}
        assert calls == []


async def test_streaming_upload_is_refused_before_sender_finishes(monkeypatch):
    monkeypatch.setattr(clip_server, "_MAX_IMAGE_BYTES", 1024)
    release, sent = asyncio.Event(), asyncio.Event()

    async def upload():
        yield b'--fixture\r\nContent-Disposition: form-data; name="image"\r\n\r\n'
        yield JPEG + b"x" * 32768
        sent.set()
        await release.wait()
        yield b"\r\n--fixture--\r\n"

    calls = []
    async with TestClient(TestServer(app(calls))) as c:
        request = asyncio.create_task(
            c.post(
                "/v1/image",
                data=upload(),
                headers={"Content-Type": "multipart/form-data; boundary=fixture"},
            )
        )
        try:
            await asyncio.wait_for(sent.wait(), 2)
            response = await asyncio.wait_for(asyncio.shield(request), 0.5)
            assert response.status == 400 and await response.json() == {"error": "invalid_request"}
            assert calls == [] and not release.is_set()
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("first_kind", ["text", "image"])
async def test_cancelled_waiter_cannot_overlap_text_and_image_threads(first_kind):
    loop = asyncio.get_running_loop()
    started, second_started = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    handlers, calls = asyncio.Queue(), []

    def encode(*_):
        calls.append(1)
        loop.call_soon_threadsafe((started if len(calls) == 1 else second_started).set)
        release.wait(5)
        return [VECTOR]

    @web.middleware
    async def capture(request, handler):
        handlers.put_nowait(request.task)
        return await handler(request)

    application = clip_server.build_app(encode, encode)
    application.middlewares.append(capture)
    async with TestClient(TestServer(application)) as c:

        async def send(kind):
            return await c.post(
                "/v1/" + kind,
                **({"json": {"texts": ["fixture"]}} if kind == "text" else {"data": form()}),
            )

        first = asyncio.create_task(send(first_kind))
        handler = await asyncio.wait_for(handlers.get(), 2)
        second = None
        try:
            await asyncio.wait_for(started.wait(), 2)
            handler.cancel()
            await asyncio.sleep(0.02)
            handler.cancel()
            second = asyncio.create_task(send("image" if first_kind == "text" else "text"))
            await asyncio.wait_for(handlers.get(), 2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(second_started.wait(), 0.15)
            assert calls == [1]
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        assert second is not None and (await second).status == 200 and calls == [1, 1]


@pytest.mark.parametrize("kind", ["text", "image"])
async def test_valid_query_and_image_keep_the_same_embedding_space_and_bytes(kind):
    calls = []
    async with TestClient(TestServer(app(calls))) as c:
        response = await (
            c.post("/v1/text", json={"texts": [" fixture "]})
            if kind == "text"
            else c.post("/v1/image", data=form(("regions", "[[0,0,1,1]]"), ("image", JPEG)))
        )
        assert response.status == 200 and await response.json() == {
            "model": clip_server.MODEL,
            "dim": 768,
            "revision": "f" * 64,
            "embeddings": [VECTOR],
        }
        assert calls == (
            [("text", ["fixture"])]
            if kind == "text"
            else [("image", (JPEG, [(0.0, 0.0, 1.0, 1.0)]))]
        )


@pytest.mark.parametrize("extra", [0, 1])
async def test_inclusive_image_limit(monkeypatch, extra):
    monkeypatch.setattr(clip_server, "_MAX_IMAGE_BYTES", len(JPEG))
    calls = []
    async with TestClient(TestServer(app(calls))) as c:
        response = await c.post("/v1/image", data=form(("image", JPEG + b"x" * extra)))
        assert response.status == (400 if extra else 200)
        assert len(calls) == (0 if extra else 1)


async def test_exact_region_byte_limit_keeps_original_image():
    calls = []
    async with TestClient(TestServer(app(calls))) as c:
        response = await c.post(
            "/v1/image", data=form(("regions", "[[0,0,1,1]]" + " " * 65525), ("image", JPEG))
        )
        assert response.status == 200 and calls == [("image", (JPEG, [(0.0, 0.0, 1.0, 1.0)]))]


@pytest.mark.parametrize(
    "headers,body",
    [
        ("Content-Transfer-Encoding: base64\r\n", JPEG),
        ("Content-Encoding: gzip\r\n", JPEG),
        ("Content-Type: multipart/mixed; boundary=nested\r\n", b"--nested--\r\n"),
    ],
)
async def test_encoded_and_nested_parts_are_not_model_input(headers, body):
    calls = []
    wire = (
        b'--fixture\r\nContent-Disposition: form-data; name="image"\r\n'
        + headers.encode()
        + b"\r\n"
        + body
        + b"\r\n--fixture--\r\n"
    )
    async with TestClient(TestServer(app(calls))) as c:
        response = await c.post(
            "/v1/image",
            data=wire,
            headers={"Content-Type": "multipart/form-data; boundary=fixture"},
        )
        assert response.status == 400 and await response.json() == {"error": "invalid_request"}
        assert calls == []


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (clip_server.InputError("private fixture"), 400, "invalid_input"),
        (clip_server.ClipError("private fixture"), 500, "embedding_failed"),
        (RuntimeError("private fixture"), 500, "embedding_failed"),
    ],
)
async def test_fixed_encoder_errors_preserve_shared_lock_recovery(failure, status, code):
    calls = []

    def encode(*_):
        calls.append(1)
        if len(calls) == 1:
            raise failure
        return [VECTOR]

    async with TestClient(TestServer(clip_server.build_app(encode, encode))) as c:
        response = await c.post("/v1/text", json={"texts": ["fixture"]})
        assert response.status == status and await response.json() == {"error": code}
        response = await c.post("/v1/image", data=form())
        assert response.status == 200 and (await response.json())["embeddings"] == [VECTOR]
        health = await (await c.get("/healthz")).json()
        assert health["rejected" if status == 400 else "failed"] == 1 and health["regions"] == 1
        assert set(health) == {
            "status",
            "model",
            "dim",
            "text_requests",
            "image_requests",
            "texts",
            "regions",
            "rejected",
            "failed",
        }


async def test_default_handler_disconnected_queued_image_does_not_run():
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    calls = []

    def encode(*_):
        calls.append(1)
        loop.call_soon_threadsafe(started.set)
        release.wait(5)
        return [VECTOR]

    async with TestClient(TestServer(clip_server.build_app(encode, encode))) as c:
        first = asyncio.create_task(c.post("/v1/text", json={"texts": ["fixture"]}))
        waiting = None
        try:
            await asyncio.wait_for(started.wait(), 2)
            waiting = asyncio.create_task(c.post("/v1/image", data=form()))
            for _ in range(100):
                if (await (await c.get("/healthz")).json())["image_requests"] == 1:
                    break
                await asyncio.sleep(0.005)
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            await asyncio.sleep(0.05)
        finally:
            release.set()
            await asyncio.gather(first, *([waiting] if waiting else []), return_exceptions=True)
        assert (await c.post("/v1/text", json={"texts": ["fixture"]})).status == 200 and calls == [
            1,
            1,
        ]


async def test_cleanup_waits_for_abandoned_failing_encoder_thread():
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    handlers = asyncio.Queue()

    def encode(*_):
        loop.call_soon_threadsafe(started.set)
        release.wait(5)
        raise RuntimeError("private fixture")

    @web.middleware
    async def capture(request, handler):
        handlers.put_nowait(request.task)
        return await handler(request)

    application = clip_server.build_app(encode, encode)
    application.middlewares.append(capture)
    c = TestClient(TestServer(application))
    await c.start_server()
    request = asyncio.create_task(c.post("/v1/text", json={"texts": ["fixture"]}))
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
