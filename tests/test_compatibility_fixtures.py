"""Replay sanitized synthetic compatibility fixtures over real loopback TLS/UCP4.

The synthetic controller is independent test code. It mirrors the gate order
recorded in docs/evidence/adoption-evidence.md: a controller-issued token on
the first connection, then only the pinned client certificate, tokenless and
adopted. Framing is assembled here without the production codec. Passing these
tests proves this emulator's local contract only, never native acceptance.
"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import ssl
import struct

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey.device import DeviceService
from aikey.tls import ensure_identity_certificate, server_context
from aikey.worker import JobProcessor


FIXTURE = Path(__file__).parent / "fixtures" / "compatibility" / "ucp4-control-v1.json"


def load_fixture():
    return json.loads(FIXTURE.read_text())


# Independent framing, deliberately separate from aikey.protocol.
def frame(header=None, body=None, *, raw_header=None, raw_body=None, header_format=1,
          header_compression=0, truncate_bytes=0, append=b""):
    header_raw = raw_header if raw_header is not None else json.dumps(header, separators=(",", ":")).encode()
    body_raw = raw_body if raw_body is not None else json.dumps(body or {}, separators=(",", ":")).encode()
    wire = (struct.pack(">BBBBI", 1, header_format, header_compression, 0, len(header_raw)) + header_raw
            + struct.pack(">BBBBI", 2, 1, 0, 0, len(body_raw)) + body_raw)
    if truncate_bytes:
        wire = wire[:-truncate_bytes]
    return wire + append


def read_frame(wire):
    records, offset = [], 0
    for kind in (1, 2):
        record_type, fmt, compression, reserved, size = struct.unpack_from(">BBBBI", wire, offset)
        assert (record_type, fmt, compression, reserved) == (kind, 1, 0, 0)
        offset += 8
        records.append(json.loads(wire[offset:offset + size]) if size else {})
        offset += size
    assert offset == len(wire)
    return records


def substitute(value, origin):
    if isinstance(value, str):
        return value.replace("{controller_origin}", origin)
    if isinstance(value, list):
        return [substitute(item, origin) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, origin) for key, item in value.items()}
    return value


class Connection:
    def __init__(self, ws, meta):
        self.ws, self.meta = ws, meta
        self.futures, self.response_ids = {}, []
        self.closed = asyncio.Event()
        self.close_code = None
        self.received_close = None
        self.synced = asyncio.Event()

    async def request(self, action, body, request_id, *, kind="request"):
        future = asyncio.get_running_loop().create_future()
        self.futures[request_id] = future
        await self.ws.send_bytes(frame({"type": kind, "action": action, "id": request_id,
                                        "timestamp": 1700000000000}, body))
        return await asyncio.wait_for(future, timeout=5)


class SyntheticController:
    """Loopback TLS controller that pins the first token-authorized client certificate."""

    def __init__(self, fixture, *, negotiate_ucp4):
        identity = fixture["identity"]
        self.mac = identity["mac"].replace(":", "").upper()
        self.identity, self.token = identity, identity["adoption_token"]
        self.negotiate_ucp4 = negotiate_ucp4
        self.pinned = None
        self.rejected = []
        self.connections = asyncio.Queue()
        self.history = []

    async def handle(self, request):
        ssl_object = request.transport.get_extra_info("ssl_object")
        der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
        if not der:
            self.rejected.append("no_client_certificate")
            raise web.HTTPForbidden()
        fingerprint = hashlib.sha256(der).hexdigest()
        headers = request.headers
        if (headers.get("x-ident") != self.mac or headers.get("x-mode") != "0"
                or headers.get("x-sysid") != self.identity["sysid"]
                or headers.get("x-type") != self.identity["type"]
                or headers.get("x-version") != self.identity["firmware_version"]):
            self.rejected.append("identity_headers")
            raise web.HTTPForbidden()
        if self.pinned is None:
            if headers.get("x-token") != self.token:
                self.rejected.append("no_token_before_adoption")
                raise web.HTTPForbidden()
            self.pinned = fingerprint
        elif (fingerprint != self.pinned or "x-token" in headers
              or headers.get("x-adopted") != "true"):
            self.rejected.append("pin_or_token_mismatch")
            raise web.HTTPForbidden()
        meta = {"fingerprint": fingerprint, "token": "x-token" in headers,
                "adopted": headers.get("x-adopted"), "ident": headers.get("x-ident")}
        self.history.append(meta)
        ws = web.WebSocketResponse(protocols=("ucp4",) if self.negotiate_ucp4 else ())
        await ws.prepare(request)
        connection = Connection(ws, meta)
        await self.connections.put(connection)
        try:
            while True:
                incoming = await ws.receive()
                if incoming.type == aiohttp.WSMsgType.CLOSE:
                    connection.received_close = incoming.data  # The code the device sent.
                    break
                if incoming.type in (aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED,
                                     aiohttp.WSMsgType.ERROR):
                    break
                if incoming.type != aiohttp.WSMsgType.BINARY:
                    continue
                header, body = read_frame(incoming.data)
                if header.get("type") == "response":
                    connection.response_ids.append(header.get("id"))
                    future = connection.futures.pop(header.get("id"), None)
                    if future is not None and not future.done():
                        future.set_result((header, body))
                elif header.get("action") == "timeSync":
                    await ws.send_bytes(frame({"type": "response", "id": header["id"],
                                               "timestamp": body["t0"], "error": None,
                                               "errorCode": 0},
                                              {"t0": body["t0"], "t1": body["t0"], "t2": body["t0"]}))
                    connection.synced.set()
        finally:
            connection.close_code = ws.close_code
            connection.closed.set()
        return ws


async def wait_until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@asynccontextmanager
async def environment(tmp_path, profile_name, control_profile):
    fixture = load_fixture()
    identity = fixture["identity"]
    profile = fixture["profiles"][profile_name]
    device_state = tmp_path / "device"
    device_cert, device_key = ensure_identity_certificate(device_state, identity["mac"].replace(":", ""))
    controller_state = tmp_path / "controller"
    controller_cert, _ = ensure_identity_certificate(controller_state, "020000000C01")
    controller_context = server_context(controller_state)
    controller_context.verify_mode = ssl.CERT_REQUIRED
    controller_context.load_verify_locations(device_cert)
    controller = SyntheticController(fixture, negotiate_ucp4=(control_profile == "ucp4"))
    app = web.Application()
    app.router.add_get("/", controller.handle)
    server = TestServer(app)
    await server.start_server(ssl=controller_context)
    origin = f"https://127.0.0.1:{server.port}"
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(controller_cert.read_text())).hexdigest()
    config = {
        "device": {"mac": identity["mac"], "ip": "127.0.0.1", "name": "Synthetic",
                   "model": identity["type"], "sysid": identity["sysid"],
                   "firmware_version": identity["firmware_version"],
                   "management_username": identity["management_username"],
                   "management_password": identity["management_password"]},
        "controller": {"host": "127.0.0.1", "control_port": server.port,
                       "control_profile": control_profile, "expected_fingerprint": pin},
        "runtime": {"mode": "lab", "bind": "127.0.0.1", "state_dir": str(device_state)},
        "controller_origins": [origin],
        "inference": {"base_url": "http://127.0.0.1:9/v1", "model": "synthetic-fixture-model"},
        "worker": deepcopy(profile["worker"]),
    }
    client_context = ssl.create_default_context(cafile=str(controller_cert))
    client_context.load_cert_chain(device_cert, device_key)
    # A real worker validates every admission. Its queue is never started, so no
    # media, model or callback request can leave this process.
    processor = JobProcessor(config, device_state, ssl_context=client_context)
    admitted = []

    async def admit(body):
        processor._normalize(body)
        admitted.append(body)
        return {"accepted": True, "synthetic": True}

    def build():
        return DeviceService(config, device_state, admit, tls_context=client_context,
                             queue_status=processor.status)

    env = {"fixture": fixture, "profile": profile, "controller": controller, "origin": origin,
           "config": config, "admitted": admitted, "build": build, "device": None,
           "device_cert": device_cert}
    try:
        yield env
    finally:
        if env["device"] is not None:
            await env["device"].stop()
        await server.close()


async def adopt_and_connect(env):
    """Administrator adoption over the management API, then token connection."""
    identity = env["fixture"]["identity"]
    device = env["build"]()
    env["device"] = device
    async with TestClient(TestServer(device.create_app())) as client:
        body = {"username": identity["management_username"], "password": identity["management_password"],
                "hosts": [f"127.0.0.1:{env['config']['controller']['control_port']}"],
                "protocol": "wss", "mode": 0, "token": identity["adoption_token"],
                "consoleName": "Synthetic Console"}
        reply = await client.post("/api/adopt", json=body)
        assert reply.status == 200
        assert identity["adoption_token"] not in await reply.text()
    await device.start()
    connection = await asyncio.wait_for(env["controller"].connections.get(), 5)
    assert connection.meta["token"] is True
    await asyncio.wait_for(connection.synced.wait(), 5)
    await wait_until(lambda: device.status["adopted"])
    return device, connection


def persisted(device):
    state = json.loads(device.state_path.read_text())
    return {"mac": state["mac"], "adopted": state["adopted"],
            "token": state.get("management", {}).get("token"),
            "credential": state.get("credential")}


async def run_exchange(env, device, connection, exchange):
    request = substitute(exchange["request"], env["origin"])
    expect = exchange["expect"]
    request_id = f"fx-{exchange['id']}"
    if expect.get("no_response"):
        await connection.ws.send_bytes(frame({"type": request.get("type", "request"),
                                              "action": request["action"], "id": request_id,
                                              "timestamp": 1700000000000}, request["body"]))
        header, _ = await connection.request("getInfo", {}, f"{request_id}-barrier")
        assert header["errorCode"] == 0
        assert request_id not in connection.response_ids
        return
    header, body = await connection.request(request["action"], request["body"], request_id)
    assert header["id"] == request_id and header["type"] == "response"
    assert header["errorCode"] == expect["errorCode"], (exchange["id"], header)
    if expect["errorCode"] == 0:
        assert header["error"] in (None, "")
    else:
        assert isinstance(header["error"], str) and header["error"]
    if "error" in expect:
        assert header["error"] == expect["error"]
    if expect.get("echo"):
        assert body == request["body"]
    if "body" in expect:
        assert body == expect["body"]
    if "body_keys" in expect:
        assert set(body) == set(expect["body_keys"])
    for name in expect.get("disabled_flags", []):
        assert body["featureFlags"][name]["enabled"] is False
    for name in expect.get("false_flags", []):
        assert body["featureFlags"][name] is False
    if expect.get("identity_mac"):
        assert body["mac"] == env["fixture"]["identity"]["mac"].replace(":", "").upper()
    if "admitted" in expect:
        assert len(env["admitted"]) == expect["admitted"], exchange["id"]
    for key, value in expect.get("status", {}).items():
        assert device.status["compatibility"][key] == value, (exchange["id"], key)
    # No accepted or rejected command may disturb adoption or identity.
    assert device.status["adopted"] is True
    assert persisted(device)["token"] is None


@pytest.mark.parametrize("control_profile", ["ucp4", "device-service"])
@pytest.mark.parametrize("profile_name", ["unscoped", "recognize_key_frames_scope"])
async def test_fixture_commands_over_tls_keep_adoption_identity_and_reconnect(
        tmp_path, profile_name, control_profile):
    async with environment(tmp_path, profile_name, control_profile) as env:
        device, connection = await adopt_and_connect(env)
        first_fingerprint = connection.meta["fingerprint"]
        before = persisted(device)
        assert before["adopted"] is True and before["token"] is None
        for exchange in env["profile"]["exchanges"]:
            await run_exchange(env, device, connection, exchange)
        after = persisted(device)
        assert after["mac"] == before["mac"] and after["adopted"] is True
        if profile_name == "unscoped":
            assert after["credential"] is not None  # accepted rotation persisted as a hash
            assert "synthetic-rotated-password-0001" not in device.state_path.read_text()

        # Restart: same identity and certificate, pinned reconnect without a token.
        await device.stop()
        env["device"] = device = env["build"]()
        await device.start()
        connection = await asyncio.wait_for(env["controller"].connections.get(), 5)
        await asyncio.wait_for(connection.synced.wait(), 5)
        assert connection.meta == {"fingerprint": first_fingerprint, "token": False,
                                   "adopted": "true", "ident": before["mac"]}
        header, info = await connection.request("getInfo", {}, "fx-after-restart")
        assert header["errorCode"] == 0 and info["mac"] == before["mac"]
        if profile_name == "unscoped":
            # The last console metadata had an unrecognized version; it persists as a category.
            assert device.status["compatibility"]["controller_version_evidence"] == "unrecognized_format"
            assert device.status["compatibility"]["controller_version"] is None
        assert env["controller"].rejected == []
        assert persisted(device) == after


@pytest.mark.parametrize("control_profile", ["ucp4", "device-service"])
async def test_malformed_frames_close_then_supported_baseline_reconnects(tmp_path, control_profile):
    async with environment(tmp_path, "unscoped", control_profile) as env:
        device, connection = await adopt_and_connect(env)
        first_fingerprint = connection.meta["fingerprint"]
        header, _ = await connection.request("getInfo", {}, "fx-completed-id")
        assert header["errorCode"] == 0
        before = persisted(device)
        for case in env["fixture"]["malformed_frames"]:
            construct = case["construct"]
            if "text" in construct:
                await connection.ws.send_str(construct["text"])
            elif construct.get("reuse_completed_id"):
                await connection.ws.send_bytes(frame({"type": "request", "action": "getInfo",
                    "id": "fx-completed-id", "timestamp": 1700000000000}, {"changed": True}))
            else:
                base_header = construct.get("header", {"type": "request", "action": "getInfo",
                                                       "id": f"fx-{case['id']}", "timestamp": 1700000000000})
                await connection.ws.send_bytes(frame(
                    base_header, {},
                    raw_header=construct["raw_header_json"].encode() if "raw_header_json" in construct else None,
                    raw_body=construct["raw_body_json"].encode() if "raw_body_json" in construct else None,
                    header_format=construct.get("header_format", 1),
                    header_compression=construct.get("header_compression", 0),
                    truncate_bytes=construct.get("truncate_bytes", 0),
                    append=bytes.fromhex(construct.get("append_hex", ""))))
            await asyncio.wait_for(connection.closed.wait(), 5)
            assert connection.received_close == case["expect_close"], case["id"]
            assert f"fx-{case['id']}" not in connection.response_ids
            # A protocol violation ends the socket, never the adoption.
            assert device.status["adopted"] is True
            assert persisted(device) == before
            device._wake.set()  # Skip the backoff delay; the reconnect path is unchanged.
            connection = await asyncio.wait_for(env["controller"].connections.get(), 5)
            await asyncio.wait_for(connection.synced.wait(), 5)
            assert connection.meta == {"fingerprint": first_fingerprint, "token": False,
                                       "adopted": "true", "ident": before["mac"]}
            header, info = await connection.request("getInfo", {}, f"fx-baseline-{case['id']}")
            assert header["errorCode"] == 0 and info["mac"] == before["mac"]
        assert env["controller"].rejected == []


async def test_processing_failure_closes_with_1011_and_preserves_adoption(tmp_path):
    async with environment(tmp_path, "unscoped", "ucp4") as env:
        device, connection = await adopt_and_connect(env)
        before = persisted(device)
        original_handler = device.handle_message

        async def fail_processing(_wire, *, _connection=None):
            raise RuntimeError("synthetic unhandled processing failure")

        device.handle_message = fail_processing
        await connection.ws.send_bytes(frame({
            "type": "request", "action": "getInfo", "id": "fx-processing-failure",
            "timestamp": 1700000000000,
        }, {}))
        await asyncio.wait_for(connection.closed.wait(), 5)
        assert connection.received_close == 1011
        assert persisted(device) == before
        assert device.status["adopted"] is True

        device.handle_message = original_handler
        device._wake.set()
        reconnected = await asyncio.wait_for(env["controller"].connections.get(), 5)
        await asyncio.wait_for(reconnected.synced.wait(), 5)
        header, info = await reconnected.request("getInfo", {}, "fx-after-processing-failure")
        assert header["errorCode"] == 0 and info["mac"] == before["mac"]


async def test_readoption_of_adopted_device_is_refused_without_losing_state(tmp_path):
    async with environment(tmp_path, "unscoped", "ucp4") as env:
        device, _ = await adopt_and_connect(env)
        before = device.state_path.read_bytes()
        identity = env["fixture"]["identity"]
        async with TestClient(TestServer(device.create_app())) as client:
            reply = await client.post("/api/adopt", json={
                "username": identity["management_username"], "password": identity["management_password"],
                "hosts": [f"127.0.0.1:{env['config']['controller']['control_port']}"],
                "protocol": "wss", "mode": 0, "token": "synthetic-second-token-0002"})
            assert reply.status == 409
        assert device.state_path.read_bytes() == before
        assert "x-token" not in device._headers()
