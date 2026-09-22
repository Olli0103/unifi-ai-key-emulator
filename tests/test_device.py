"""Local protocol and TLS tests. No controller or camera is contacted."""

import asyncio
from copy import deepcopy
import hashlib
import json
import ssl

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey.device import DeviceService, VerifiedConnector, verify_peer_pin
from aikey.protocol import ContractError, decode_message, encode_message
from aikey.tls import ensure_identity_certificate, server_context


@pytest.fixture
def config():
    return {
        "device": {"mac": "02:00:00:00:00:01", "ip": "127.0.0.1", "name": "Lab",
                   "management_username": "lab-admin", "management_password": "test-only-password"},
        "controller": {"host": "127.0.0.1", "control_port": 7442},
        "runtime": {"mode": "lab", "bind": "127.0.0.1"},
    }


async def accepted(body):
    return {"accepted": True}


def service(config, tmp_path, **kwargs):
    return DeviceService(config, tmp_path, accepted, **kwargs)


def request(action, body=None, request_id="request-1"):
    return encode_message({"type": "request", "action": action, "id": request_id, "timestamp": 100}, body or {})


def adoption(config):
    return {"username": config["device"]["management_username"],
            "password": config["device"]["management_password"],
            "hosts": [f"127.0.0.1:{config['controller']['control_port']}"],
            "protocol": "wss", "mode": 0, "token": "synthetic-lab-token", "controller": "Protect"}


async def test_info_reports_observed_fields_and_explicit_disabled_capabilities(config, tmp_path):
    device = service(config, tmp_path)
    response = decode_message(await device.handle_message(request("getInfo")))
    assert response.header["errorCode"] == 0
    assert set(response.body) == {"type", "sysid", "version", "mac", "uptime", "poeType", "storageSize", "featureFlags"}
    assert response.body["mac"] == "020000000001"
    assert response.body["featureFlags"]["supportFaceEnhancement"]["enabled"] is False
    assert response.body["featureFlags"]["supportRecognizeAnything"]["enabled"] is False
    assert response.body["featureFlags"]["supportDeepMode"] is False


async def test_unknown_and_host_commands_fail_without_success(config, tmp_path):
    device = service(config, tmp_path)
    for action in ("reboot", "factoryReset", "sshService", "execute", "getTaskQueueInfo"):
        response = decode_message(await device.handle_message(request(action, request_id=action)))
        assert response.header["errorCode"] == 95
        assert response.body == {}
    assert not list(tmp_path.iterdir())


async def test_request_ai_only_echoes_after_admission_and_deduplicates(config, tmp_path):
    called = []
    released = asyncio.Event()
    async def admit(body):
        called.append(deepcopy(body))
        await released.wait()
        return {"accepted": True}
    device = DeviceService(config, tmp_path, admit)
    body = {"targetUri": ":7968/on_demand_inference", "timeoutMs": 15000,
            "payload": {"event": "test-event"}, "resUrl": "/internal/test"}
    wire = request("RequestAI", body)
    one = asyncio.create_task(device.handle_message(wire))
    two = asyncio.create_task(device.handle_message(wire))
    await asyncio.sleep(0)
    assert len(called) == 1
    released.set()
    responses = await asyncio.gather(one, two)
    assert responses[0] == responses[1]
    assert decode_message(responses[0]).body == body
    assert await device.handle_message(wire) == responses[0]
    assert len(called) == 1
    with pytest.raises(ContractError):
        await device.handle_message(request("getInfo"))


async def test_request_ai_invalid_or_rejected_never_acknowledges(config, tmp_path):
    called = []
    async def reject(body):
        called.append(body)
        raise ValueError("secret input must not reach the response")
    device = DeviceService(config, tmp_path, reject)
    bad = {"targetUri": "http://example.invalid/shell", "timeoutMs": 100, "payload": {}}
    assert decode_message(await device.handle_message(request("RequestAI", bad))).header["errorCode"] != 0
    assert not called
    good = {"targetUri": ":7445/generate-description", "timeoutMs": 100, "payload": {}}
    reply = decode_message(await device.handle_message(request("RequestAI", good, "two")))
    assert reply.header["errorCode"] == 22
    assert "secret" not in reply.header["error"]


async def test_adoption_checks_credentials_scope_and_persists_no_password(config, tmp_path):
    device = service(config, tmp_path)
    client = TestClient(TestServer(device.create_app()))
    await client.start_server()
    try:
        body = adoption(config)
        bad = {**body, "password": "wrong"}
        assert (await client.post("/api/adopt", json=bad)).status == 401
        assert not device.state_path.exists()
        wrong_host = {**body, "hosts": ["192.0.2.20:7442"]}
        assert (await client.post("/api/adopt", json=wrong_host)).status == 400
        assert (await client.post("/api/adopt", data='{"username":1,"username":2}', headers={"Content-Type": "application/json"})).status == 422
        reply = await client.post("/api/adopt", json=body)
        assert reply.status == 200
        assert "password" not in await reply.text()
        raw = device.state_path.read_text()
        assert body["password"] not in raw
        assert body["token"] in raw  # Retained only until authenticated WS acceptance.
        assert device.state_path.stat().st_mode & 0o777 == 0o600
        reloaded = service(config, tmp_path)
        assert reloaded._headers()["x-token"] == body["token"]
        assert not reloaded.status["adopted"]
    finally:
        await client.close()


async def test_password_rotation_is_persistent_and_checks_old_password(config, tmp_path):
    device = service(config, tmp_path)
    body = {"username": "lab-admin", "passwordOld": "wrong", "passwordNew": "new-test-password"}
    failed = decode_message(await device.handle_message(request("changeUserPassword", body)))
    assert failed.header["errorCode"] == 13
    body["passwordOld"] = "test-only-password"
    assert decode_message(await device.handle_message(request("changeUserPassword", body, "two"))).header["errorCode"] == 0
    raw = device.state_path.read_text()
    assert "new-test-password" not in raw and "test-only-password" not in raw
    reloaded = service(config, tmp_path)
    assert reloaded._password_matches("lab-admin", "new-test-password")
    assert not reloaded._password_matches("lab-admin", "test-only-password")


async def test_metadata_stays_local_and_unknown_mutations_fail(config, tmp_path):
    device = service(config, tmp_path)
    body = {"controller": {"id": "lab-console", "protectVersion": "7.2.105", "supportsDbCredential": True}}
    assert decode_message(await device.handle_message(request("setConsoleInfo", body))).body == body
    assert json.loads(device.state_path.read_text())["console_info"]["id"] == "lab-console"
    assert decode_message(await device.handle_message(request("setInfo", {"hostname": "new-name", "shell": "anything"}, "two"))).header["errorCode"] != 0


async def test_queue_provider_must_supply_truthful_observed_contract(config, tmp_path):
    counts = dict.fromkeys(("UI_AUDIO_RAM", "UI_AUTO_FACE_ENHANCE", "UI_AUTO_RAM", "UI_AUTO_STT", "UI_MANUAL_FACE_ENHANCE", "UI_TASK_NUM"), 0)
    counts["UI_TASK_NUM"] = 2
    device = service(config, tmp_path, queue_status=lambda: counts)
    assert decode_message(await device.handle_message(request("getTaskQueueInfo"))).body == counts
    device.queue_status = lambda: {"queued": 3, "active": 1, "pending": 4, "capacity": 8}
    assert decode_message(await device.handle_message(request("getTaskQueueInfo", request_id="worker"))).body["UI_TASK_NUM"] == 4


def test_lab_rejects_external_controller_and_corrupt_state(config, tmp_path):
    config["controller"]["host"] = "192.0.2.1"
    with pytest.raises(ValueError, match="loopback"):
        service(config, tmp_path)
    config["controller"]["host"] = "127.0.0.1"
    (tmp_path / "device-state.json").write_text('{"schema":1,"mac":"020000000002","adopted":false}')
    with pytest.raises(ValueError, match="identity"):
        service(config, tmp_path)


async def test_missing_or_insecure_trust_is_rejected(config, tmp_path):
    with pytest.raises(ValueError, match="TLS context"):
        await service(config, tmp_path).start()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError, match="certificate verification"):
        VerifiedConnector(ssl_context=ctx)
    ctx.verify_mode = ssl.CERT_REQUIRED
    with pytest.raises(ValueError, match="hostname"):
        VerifiedConnector(ssl_context=ctx)


def test_pin_checker_requires_real_tls_and_correct_leaf():
    class Peer:
        def getpeercert(self, binary_form):
            return b"synthetic-unit-certificate"
    with pytest.raises(ssl.SSLError):
        verify_peer_pin(None, None)
    with pytest.raises(ssl.SSLError):
        verify_peer_pin(Peer(), b"\0" * 32)
    verify_peer_pin(Peer(), hashlib.sha256(b"synthetic-unit-certificate").digest())


async def test_tls_pin_failure_sends_no_http_or_token(config, tmp_path):
    server_dir = tmp_path / "server"
    cert, _ = ensure_identity_certificate(server_dir, "020000000010")
    received = []
    async def handler(req):
        received.append(dict(req.headers))
        return web.Response(status=403)
    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app)
    await server.start_server(ssl=server_context(server_dir))
    config["controller"].update(control_port=server.port, expected_fingerprint="00" * 32)
    ctx = ssl.create_default_context(cafile=str(cert))
    device = service(config, tmp_path / "device", tls_context=ctx)
    device._state["management"] = {"token": "DO-NOT-LEAK"}
    try:
        await device.start()
        async with asyncio.timeout(3):
            while device.status["last_error"] is None:
                await asyncio.sleep(.01)
        assert not received
        assert not device.status["adopted"]
        assert device._headers()["x-token"] == "DO-NOT-LEAK"
    finally:
        await device.stop()
        await server.close()


async def test_tls_control_handshake_pin_then_adoption_and_info(config, tmp_path):
    server_dir = tmp_path / "server"
    cert, _ = ensure_identity_certificate(server_dir, "020000000010")
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    obtained = asyncio.Future()
    headers = []
    async def controller(req):
        headers.append(dict(req.headers))
        ws = web.WebSocketResponse(protocols=("ucp4",))
        await ws.prepare(req)
        await ws.send_bytes(request("getInfo", request_id="controller-info"))
        async for frame in ws:
            if frame.type != aiohttp.WSMsgType.BINARY:
                continue
            message = decode_message(frame.data)
            if message.header.get("action") == "timeSync":
                t0 = message.body["t0"]
                await ws.send_bytes(encode_message({"type": "response", "id": message.header["id"], "errorCode": 0}, {"t0": t0, "t1": t0, "t2": t0}))
            elif message.header.get("id") == "controller-info":
                if not obtained.done():
                    obtained.set_result(message)
        return ws
    app = web.Application()
    app.router.add_get("/", controller)
    server = TestServer(app)
    await server.start_server(ssl=server_context(server_dir))
    config["controller"].update(control_port=server.port, expected_fingerprint=hashlib.sha256(der).hexdigest())
    ctx = ssl.create_default_context(cafile=str(cert))
    device = service(config, tmp_path / "device", tls_context=ctx)
    device._state["management"] = {"token": "synthetic-lab-token"}
    try:
        await device.start()
        result = await asyncio.wait_for(obtained, 3)
        assert result.body["mac"] == "020000000001"
        assert headers[0]["x-token"] == "synthetic-lab-token"
        assert headers[0]["x-mode"] == "0"
        assert device.status["adopted"]
        assert "token" not in device._state["management"]
        persisted = json.loads(device.state_path.read_text())
        assert persisted["adopted"] and "token" not in persisted["management"]
    finally:
        await device.stop()
        await server.close()


async def test_redirect_cannot_forward_token(config, tmp_path):
    server_dir = tmp_path / "server"
    cert, _ = ensure_identity_certificate(server_dir, "020000000010")
    redirected_requests = []
    async def redirect(req):
        raise web.HTTPFound("http://127.0.0.1:%d/stolen" % server.port)
    async def stolen(req):
        redirected_requests.append(dict(req.headers))
        return web.Response()
    app = web.Application()
    app.router.add_get("/", redirect)
    app.router.add_get("/stolen", stolen)
    server = TestServer(app)
    await server.start_server(ssl=server_context(server_dir))
    config["controller"]["control_port"] = server.port
    ctx = ssl.create_default_context(cafile=str(cert))
    device = service(config, tmp_path / "device", tls_context=ctx)
    device._state["management"] = {"token": "DO-NOT-REDIRECT"}
    try:
        await device.start()
        async with asyncio.timeout(3):
            while device.status["last_error"] is None:
                await asyncio.sleep(.01)
        assert device.status["last_error"] == "ContractError"
        assert not redirected_requests
    finally:
        await device.stop()
        await server.close()


async def test_search_credential_rotation_requires_handler_and_acknowledges_only_success(config, tmp_path):
    config["search"] = {"enabled": True}
    body = {"username": "lab-admin", "passwordOld": "test-only-password", "passwordNew": "new-test-password"}
    device = service(config, tmp_path)
    assert decode_message(await device.handle_message(request("changeUserPassword", body))).header["errorCode"] == 95
    assert device._password_matches("lab-admin", "test-only-password")
    rotations = []
    async def rotate(username, password):
        rotations.append((username, password))
    device.credential_handler = rotate
    assert decode_message(await device.handle_message(request("changeUserPassword", body, "second"))).header["errorCode"] == 0
    assert rotations == [("lab-admin", "new-test-password")]
    assert device._password_matches("lab-admin", "new-test-password")
