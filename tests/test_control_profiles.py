"""Device Service profile tests use only synthetic loopback TLS controllers."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import ssl

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey.device import DeviceService
from aikey.protocol import decode_message, encode_message
from aikey.tls import ensure_identity_certificate, server_context


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.01)


@asynccontextmanager
async def fixture(tmp_path, handler, *, profile="device-service", pending=True, adopted=False,
                  pin="correct"):
    controller_state = tmp_path / "controller"
    cert, _ = ensure_identity_certificate(controller_state, "020000000010")
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app)
    await server.start_server(ssl=server_context(controller_state))
    config = {
        "device": {"mac": "020000000001", "ip": "127.0.0.1", "name": "Lab",
                   "management_username": "ui", "management_password": "private-fixture-password"},
        "controller": {"host": "127.0.0.1", "control_port": server.port},
        "runtime": {"mode": "lab", "bind": "127.0.0.1"},
    }
    if profile is not None:
        config["controller"]["control_profile"] = profile
    if pin is not None:
        config["controller"]["expected_fingerprint"] = digest if pin == "correct" else pin
    device_state = tmp_path / "device"
    client_cert, client_key = ensure_identity_certificate(device_state, config["device"]["mac"])
    context = ssl.create_default_context(cafile=str(cert))
    context.load_cert_chain(client_cert, client_key)

    async def accept_job(body):
        return {"accepted": True}

    device = DeviceService(config, device_state, accept_job, tls_context=context)
    device._state["adopted"] = adopted
    if pending:
        device._state["management"] = {"token": "synthetic-profile-token"}
    device._save_state()
    try:
        yield device
    finally:
        await device.stop()
        await server.close()


async def answer_sync(ws, *, valid=True):
    frame = await ws.receive()
    assert frame.type == aiohttp.WSMsgType.BINARY
    message = decode_message(frame.data)
    assert message.header["action"] == "timeSync"
    t0 = message.body["t0"]
    await ws.send_bytes(encode_message(
        {"type": "response", "id": message.header["id"] if valid else "wrong-id", "errorCode": 0},
        {"t0": t0, "t1": t0, "t2": t0}))
    await ws.send_bytes(encode_message({"type": "request", "action": "getInfo", "id": "barrier"}, {}))
    reply = decode_message((await ws.receive()).data)
    assert reply.header["id"] == "barrier"


async def test_device_service_missing_subprotocol_confirms_only_matching_timesync(tmp_path):
    barrier = asyncio.Event()

    async def controller(req):
        assert req.headers["Sec-WebSocket-Protocol"] == "ucp4"
        assert req.headers["x-token"] == "synthetic-profile-token"
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        await answer_sync(ws)
        barrier.set()
        async for _ in ws:
            pass
        return ws

    async with fixture(tmp_path, controller) as device:
        await device.start()
        await asyncio.wait_for(barrier.wait(), 3)
        assert device.status["adopted"] is True
        assert "x-token" not in device._headers()
        saved = json.loads(device.state_path.read_text())
        assert saved["adopted"] is True and "token" not in saved["management"]


@pytest.mark.parametrize("profile,pending,adopted,header", [
    (None, True, False, None),
    ("ucp4", True, False, None),
    ("device-service", False, False, None),
    ("device-service", True, False, "unexpected"),
])
async def test_missing_or_wrong_protocol_does_not_bypass_scope(tmp_path, profile, pending, adopted, header):
    frames = []

    async def controller(req):
        ws = web.WebSocketResponse()
        if header is not None:
            ws.headers["Sec-WebSocket-Protocol"] = header
        await ws.prepare(req)
        async for frame in ws:
            frames.append(frame)
        return ws

    async with fixture(tmp_path, controller, profile=profile, pending=pending, adopted=adopted) as device:
        original = device.state_path.read_bytes()
        await device.start()
        await wait_until(lambda: device.status["last_error"] == "ContractError")
        assert frames == []
        assert device.status["connections"] == 0
        assert device.state_path.read_bytes() == original


async def test_previously_confirmed_device_reconnects_without_token(tmp_path):
    barrier = asyncio.Event()

    async def controller(req):
        assert req.headers["x-adopted"] == "true"
        assert "x-token" not in req.headers
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        await answer_sync(ws)
        barrier.set()
        async for _ in ws:
            pass
        return ws

    async with fixture(tmp_path, controller, pending=False, adopted=True) as device:
        original = device.state_path.read_bytes()
        await device.start()
        await asyncio.wait_for(barrier.wait(), 3)
        assert device.status["adopted"] is True
        assert device.status["clock_offset_ms"] is not None
        assert device.state_path.read_bytes() == original


async def test_missing_subprotocol_invalid_timesync_preserves_pending_token(tmp_path):
    barrier = asyncio.Event()

    async def controller(req):
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        await answer_sync(ws, valid=False)
        barrier.set()
        async for _ in ws:
            pass
        return ws

    async with fixture(tmp_path, controller) as device:
        original = device.state_path.read_bytes()
        await device.start()
        await asyncio.wait_for(barrier.wait(), 3)
        assert device.status["adopted"] is False
        assert device._headers()["x-token"] == "synthetic-profile-token"
        assert device.state_path.read_bytes() == original


@pytest.mark.parametrize("payload", ["text", "malformed-binary"])
async def test_device_service_keeps_strict_binary_ucp_parsing(tmp_path, payload):
    closed = asyncio.Event()
    received_close_codes = []

    async def controller(req):
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        sync = await ws.receive()
        assert decode_message(sync.data).header["action"] == "timeSync"
        if payload == "text":
            await ws.send_str("not-ucp4")
        else:
            await ws.send_bytes(b"not-ucp4")
        close = await ws.receive()
        assert close.type == aiohttp.WSMsgType.CLOSE
        received_close_codes.append(close.data)
        closed.set()
        return ws

    async with fixture(tmp_path, controller) as device:
        original = device.state_path.read_bytes()
        await device.start()
        await asyncio.wait_for(closed.wait(), 3)
        await wait_until(lambda: device.status["last_close_code"] is not None)
        if payload == "text":
            assert received_close_codes == [1003]
        else:
            # Concurrent aiohttp receive/close paths can finish with 1000.
            # The invariant is rejection, closure and preserved pending state.
            await wait_until(lambda: device.status["last_error"] == "ContractError")
            assert len(received_close_codes) == 1
        assert device.state_path.read_bytes() == original


@pytest.mark.parametrize("profile,pin", [
    ("device-service", None), ("device-service", "00" * 32), ("unknown", "correct"),
])
async def test_profile_and_pin_failures_send_no_http_or_token(tmp_path, profile, pin):
    requests = []

    async def controller(req):
        requests.append(req)
        return web.Response(status=400)

    async with fixture(tmp_path, controller, profile=profile, pin=pin) as device:
        original = device.state_path.read_bytes()
        await device.start()
        await wait_until(lambda: device.status["last_error"] is not None)
        assert requests == []
        assert device.state_path.read_bytes() == original
