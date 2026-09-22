"""Explicit short-lived enrollment, tested with synthetic local credentials."""

import asyncio
import hashlib
import json
import ssl
import time
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey.config import ConfigError, defaults, validate_config
from aikey.device import DeviceService
from aikey.protocol import decode_message, encode_message
from aikey.tls import ensure_identity_certificate, server_context


STRONG = "synthetic-generated-strong-password"
ROTATED = "synthetic-new-controller-password"


def config(*, until=None):
    return {
        "device": {"mac": "020000000001", "ip": "127.0.0.1", "management_username": "ui",
                   "management_password": STRONG,
                   "factory_enrollment_until": int(time.time()) + 60 if until is None else until},
        "controller": {"host": "127.0.0.1", "control_port": 7442, "expected_fingerprint": "11" * 32},
        "runtime": {"mode": "lab", "bind": "127.0.0.1"},
    }


async def admit(body):
    raise AssertionError("Enrollment must not submit AI work")


def adopt(options, *, password="ui"):
    return {"username": "ui", "password": password, "protocol": "wss", "mode": 0,
            "hosts": [f"127.0.0.1:{options['controller']['control_port']}"], "token": "synthetic-token"}


def rotation(request_id="rotate", old="ui", new=ROTATED):
    return encode_message({"type": "request", "action": "changeUserPassword", "id": request_id},
                          {"username": "ui", "passwordOld": old, "passwordNew": new})


def configured_socket(device):
    socket = SimpleNamespace(closed=False)
    device._ws = socket
    device._confirmed_control_connection = socket
    device._state.update(adopted=True, factory_enrollment_used=True)
    return socket


@pytest.mark.parametrize("setting", [None, 0, "expired"])
async def test_factory_credentials_default_off_and_expired_but_strong_credentials_work(tmp_path, setting):
    options = config(until=int(time.time()) - 1 if setting == "expired" else 0)
    if setting is None:
        options["device"].pop("factory_enrollment_until")
    device = DeviceService(options, tmp_path, admit)
    async with TestClient(TestServer(device.create_app())) as client:
        assert (await client.post("/api/info", json={"username": "ui", "password": "ui"})).status == 401
        assert (await client.post("/api/adopt", json=adopt(options))).status == 401
        assert not device.state_path.exists()
        assert (await client.post("/api/info", json={"username": "ui", "password": STRONG})).status == 200
        assert (await client.post("/api/adopt", json=adopt(options, password=STRONG))).status == 200
        assert "factory_enrollment_used" not in device._state


async def test_active_factory_enrollment_keeps_strong_password_and_validates_target(tmp_path):
    options = config()
    device = DeviceService(options, tmp_path, admit)
    async with TestClient(TestServer(device.create_app())) as client:
        assert (await client.post("/api/info", json={"username": "ui", "password": "ui"})).status == 200
        saved = json.loads(device.state_path.read_text())
        assert saved["factory_enrollment_used"] is True
        assert "credential" not in saved
        assert STRONG not in device.state_path.read_text()
        assert (await client.post("/api/info", json={"username": "ui", "password": STRONG})).status == 200
        invalid = adopt(options)
        invalid["hosts"] = ["192.0.2.1:7442"]
        assert (await client.post("/api/adopt", json=invalid)).status == 400
        invalid = adopt(options)
        invalid.pop("token")
        assert (await client.post("/api/adopt", json=invalid)).status == 400
        assert not device.status["adopted"]
        assert "management" not in device._state
        assert (await client.post("/api/adopt", json=adopt(options))).status == 200
        assert device._state["management"]["token"] == "synthetic-token"
        reloaded = DeviceService(options, tmp_path, admit)
        assert reloaded._state["factory_enrollment_used"] is True
        assert reloaded._password_matches("ui", STRONG)
        assert not reloaded._password_matches("ui", "ui")
        assert reloaded._confirmed_control_connection is None


async def test_factory_http_auth_stops_at_expiry_and_adoption(tmp_path, monkeypatch):
    options = config()
    device = DeviceService(options, tmp_path, admit)
    async with TestClient(TestServer(device.create_app())) as client:
        assert (await client.post("/api/info", json={"username": "ui", "password": "ui"})).status == 200
        device._state["adopted"] = True
        assert (await client.post("/api/info", json={"username": "ui", "password": "ui"})).status == 401
        device._state["adopted"] = False
        monkeypatch.setattr("aikey.device.time.time", lambda: options["device"]["factory_enrollment_until"])
        assert (await client.post("/api/info", json={"username": "ui", "password": "ui"})).status == 401
        assert (await client.post("/api/adopt", json=adopt(options))).status == 401


async def test_factory_pair_is_exact_and_requires_configured_ui_account(tmp_path):
    options = config()
    device = DeviceService(options, tmp_path / "ui", admit)
    async with TestClient(TestServer(device.create_app())) as client:
        for username, password in (("ubnt", "ui"), ("ui", "ubnt"), ("ui", "UI"), ("ui ", "ui")):
            assert (await client.post("/api/info", json={"username": username, "password": password})).status == 401
    options["device"]["management_username"] = "custom-account"
    custom = DeviceService(options, tmp_path / "custom", admit)
    async with TestClient(TestServer(custom.create_app())) as client:
        assert (await client.post("/api/info", json={"username": "ui", "password": "ui"})).status == 401


@pytest.mark.parametrize("missing", ["connection", "current", "confirmed", "adopted", "marker", "window", "closed"])
async def test_factory_rotation_requires_current_confirmed_socket_and_bounded_marker(tmp_path, monkeypatch, missing):
    monkeypatch.setattr("aikey.device._FACTORY_CONFIRMATION_TIMEOUT", .02)
    device = DeviceService(config(), tmp_path, admit)
    socket = configured_socket(device)
    supplied = socket
    if missing == "connection":
        supplied = None
    elif missing == "current":
        device._ws = SimpleNamespace(closed=False)
    elif missing == "confirmed":
        device._confirmed_control_connection = None
    elif missing == "adopted":
        device._state["adopted"] = False
    elif missing == "marker":
        device._state.pop("factory_enrollment_used")
    elif missing == "window":
        device._factory_until = 0
    else:
        socket.closed = True
    reply = decode_message(await device.handle_message(rotation(), _connection=supplied))
    assert reply.header["errorCode"] == 13
    assert "credential" not in device._state


async def test_factory_rotation_erases_marker_and_cannot_keep_public_password(tmp_path):
    device = DeviceService(config(), tmp_path, admit)
    socket = configured_socket(device)
    assert decode_message(await device.handle_message(rotation(new="ui"), _connection=socket)).header["errorCode"] == 22
    assert device._state["factory_enrollment_used"] is True
    reply = decode_message(await device.handle_message(rotation("valid"), _connection=socket))
    assert reply.header["errorCode"] == 0
    assert "factory_enrollment_used" not in device._state
    assert device._password_matches("ui", ROTATED)
    assert not device._password_matches("ui", STRONG)
    assert not device._password_matches("ui", "ui")
    saved = device.state_path.read_text()
    assert ROTATED not in saved and STRONG not in saved
    assert "passwordOld" not in saved and "passwordNew" not in saved
    assert decode_message(await device.handle_message(rotation("again"), _connection=socket)).header["errorCode"] == 13


async def test_failed_database_rotation_preserves_factory_marker_and_strong_password(tmp_path):
    async def failing_rotation(username, password):
        raise RuntimeError("Synthetic database failure")
    device = DeviceService(config(), tmp_path, admit, credential_handler=failing_rotation)
    socket = configured_socket(device)
    reply = decode_message(await device.handle_message(rotation(), _connection=socket))
    assert reply.header["errorCode"] == 5
    assert device._state["factory_enrollment_used"] is True
    assert device._password_matches("ui", STRONG)
    assert "credential" not in device._state


async def test_pinned_tls_control_confirms_then_rotates_factory_enrollment(tmp_path):
    cert, _ = ensure_identity_certificate(tmp_path / "controller", "020000000010")
    observed = asyncio.get_running_loop().create_future()
    async def controller(req):
        assert req.headers["x-token"] == "synthetic-token"
        socket = web.WebSocketResponse(protocols=("ucp4",))
        await socket.prepare(req)
        sync = decode_message((await socket.receive()).data)
        t0 = sync.body["t0"]
        # Native startup need not wait for the clock response. The rotation
        # must wait without blocking the concurrent confirmation handler.
        await socket.send_bytes(rotation())
        await socket.send_bytes(encode_message({"type": "response", "id": sync.header["id"], "errorCode": 0, "error": ""},
                                               {"t0": t0, "t1": t0, "t2": t0}))
        observed.set_result(decode_message((await socket.receive()).data))
        async for _ in socket:
            pass
        return socket
    app = web.Application()
    app.router.add_get("/", controller)
    server = TestServer(app)
    await server.start_server(ssl=server_context(tmp_path / "controller"))
    options = config()
    options["controller"].update(control_port=server.port,
        expected_fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest())
    device = DeviceService(options, tmp_path / "device", admit, tls_context=ssl.create_default_context(cafile=cert))
    try:
        async with TestClient(TestServer(device.create_app())) as client:
            assert (await client.post("/api/adopt", json=adopt(options))).status == 200
            await device.start()
            result = await asyncio.wait_for(observed, 3)
            assert result.header["errorCode"] == 0
            assert device.status["adopted"]
            assert "factory_enrollment_used" not in device._state
            assert (await client.post("/api/info", json={"username": "ui", "password": "ui"})).status == 401
            assert (await client.post("/api/info", json={"username": "ui", "password": ROTATED})).status == 200
    finally:
        await device.stop()
        await server.close()


@pytest.mark.parametrize("deadline", [True, -1, "123", None, 1.5, "too_far"])
def test_enrollment_deadline_is_explicit_and_at_most_ten_minutes(tmp_path, deadline):
    options = defaults(tmp_path, "020000000001")
    options["device"]["factory_enrollment_until"] = int(time.time()) + 601 if deadline == "too_far" else deadline
    with pytest.raises(ConfigError, match="factory_enrollment_until"):
        validate_config(options)


def test_active_enrollment_and_device_service_profile_require_pin(tmp_path):
    options = defaults(tmp_path, "020000000001")
    options["device"]["factory_enrollment_until"] = int(time.time()) + 60
    with pytest.raises(ConfigError, match="pin"):
        validate_config(options)
    options["controller"]["expected_fingerprint"] = "11" * 32
    assert validate_config(options)["device"]["factory_enrollment_until"] > time.time()
    options["device"]["factory_enrollment_until"] = 1
    options["controller"].pop("expected_fingerprint")
    assert validate_config(options)["device"]["factory_enrollment_until"] == 1
    options["controller"]["control_profile"] = "device-service"
    with pytest.raises(ConfigError, match="pin"):
        validate_config(options)
    options["controller"]["control_profile"] = "anything"
    with pytest.raises(ConfigError, match="control_profile"):
        validate_config(options)
