"""The isolated AI Port candidate stays unadopted and never stores credentials."""

import asyncio
import hashlib
import json
from pathlib import Path
import ssl
import sys
import time

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey.aiport_candidate import CandidateError, CandidateService, load_config
from aikey.tls import ensure_identity_certificate


def private_file(path: Path, content: bytes):
    path.write_bytes(content)
    path.chmod(0o600)


def fixture_state(tmp_path):
    cert, _ = ensure_identity_certificate(tmp_path, "2A1100F0A55E")
    private_file(tmp_path / "controller-ca.pem", cert.read_bytes())
    config = {"controller_ip": "192.168.10.1", "device_ip": "192.168.10.20",
              "mac": "2A1100F0A55E", "controller_pin": hashlib.sha256(ssl.PEM_cert_to_DER_cert(
                  cert.read_text())).hexdigest(), "firmware_version": "5.1.12"}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    return config


def test_candidate_requires_private_stable_identity(tmp_path):
    config = fixture_state(tmp_path)
    assert load_config(tmp_path / "config.json") == config
    (tmp_path / "config.json").chmod(0o644)
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


@pytest.mark.parametrize("field,value", [
    ("mac", "001122334455"),
    ("device_ip", "127.0.0.1"),
    ("controller_ip", "8.8.8.8"),
    ("controller_pin", "invalid"),
    ("firmware_version", "unverified build"),
])
def test_candidate_rejects_invalid_identity_and_destination(tmp_path, field, value):
    config = fixture_state(tmp_path)
    config[field] = value
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


def test_diagnostic_hello_requires_short_lived_private_config(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["diagnostic_hello_until"] == config[
        "diagnostic_hello_until"]
    config["diagnostic_hello_until"] = int(time.time()) + 601
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


def test_stream_diagnostic_requires_expiring_exact_private_policy(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    assert load_config(tmp_path / "config.json")["diagnostic_stream"] == config[
        "diagnostic_stream"]
    del config["diagnostic_hello_until"]
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(tmp_path / "config.json")


def test_expired_stream_diagnostic_restarts_passively(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) - 1
    config["diagnostic_stream"] = {"camera_mac": "2A1122334455",
                                   "source_ip": "192.168.10.1",
                                   "ffmpeg_path": sys.executable}
    private_file(tmp_path / "config.json", json.dumps(config).encode())
    loaded = load_config(tmp_path / "config.json")
    service = CandidateService(loaded, tmp_path)
    assert service.ingress is None


@pytest.mark.asyncio
async def test_https_manage_rejects_adoption_and_keeps_only_field_shape(tmp_path):
    config = fixture_state(tmp_path)
    service = CandidateService(config, tmp_path)
    server = TestServer(service.app())
    await server.start_server(ssl=service._server_context())
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as client:
            response = await client.post(str(server.make_url("/api/1.2/manage")), json={
                "username": "sensitive-user", "password": "sensitive-password",
                "mgmt": {"token": "sensitive-token", "hosts": ["controller"]},
            })
            assert response.status == 501
            assert service.manage_requests == 1
            shape = service.last_manage_shape
            assert shape["recognized_fields"] == ["mgmt", "password", "username"]
            assert shape["mgmt_recognized_fields"] == ["hosts", "token"]
            health = await client.get(str(server.make_url("/healthz")))
            public = await health.text()
            assert "sensitive-" not in public
            assert (await response.json())["error"] == "Adoption is not enabled on this candidate"
    finally:
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("server_protocols", [[], ["secure_transfer"]])
async def test_candidate_uses_pinned_secure_transfer_websocket(tmp_path, server_protocols):
    config = fixture_state(tmp_path)
    config["controller_ip"] = "127.0.0.1"
    connected = asyncio.Event()

    async def websocket(request):
        assert request.headers["Camera-Model"] == "0xa5f1"
        assert request.headers["Camera-MAC"] == config["mac"]
        assert request.headers["Adopted"] == "false"
        ws = web.WebSocketResponse(protocols=server_protocols)
        await ws.prepare(request)
        connected.set()
        await asyncio.sleep(10)
        return ws

    app = web.Application()
    app.router.add_get("/camera/1.0/ws", websocket)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server = TestServer(app)
    await server.start_server(ssl=server_context)
    try:
        service = CandidateService(config, tmp_path, control_port=server.port)
        task = asyncio.create_task(service._connect_loop())
        try:
            await asyncio.wait_for(connected.wait(), timeout=3)
            for _ in range(50):
                if service.upgrades:
                    break
                await asyncio.sleep(0.01)
            assert service.upgrades == 1
            assert service.connected is True
            assert service.last_result == "websocket_101"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_candidate_counts_control_frames_without_exposing_payload(tmp_path):
    config = fixture_state(tmp_path)
    config["controller_ip"] = "127.0.0.1"
    secret = b"synthetic-sensitive-stream-url"
    sent = asyncio.Event()

    async def websocket(request):
        ws = web.WebSocketResponse(protocols=["secure_transfer"])
        await ws.prepare(request)
        await ws.send_bytes(secret)
        await ws.send_str("synthetic-sensitive-token")
        sent.set()
        await asyncio.sleep(1)
        return ws

    app = web.Application()
    app.router.add_get("/camera/1.0/ws", websocket)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server = TestServer(app)
    await server.start_server(ssl=server_context)
    try:
        service = CandidateService(config, tmp_path, control_port=server.port)
        task = asyncio.create_task(service._connect_loop())
        try:
            await asyncio.wait_for(sent.wait(), timeout=3)
            for _ in range(50):
                if service.ws_binary_frames == 1 and service.ws_text_frames == 1:
                    break
                await asyncio.sleep(0.01)
            assert service.ws_binary_frames == 1
            assert service.ws_text_frames == 1
            assert service.ws_last_frame_bytes == len("synthetic-sensitive-token")
            public = await service._health(None)
            assert secret.decode() not in public.text
            assert "synthetic-sensitive-token" not in public.text
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_candidate_records_websocket_close_code_without_reason(tmp_path):
    config = fixture_state(tmp_path)
    config["controller_ip"] = "127.0.0.1"
    close_reason = "synthetic-private-camera-token"

    async def websocket(request):
        ws = web.WebSocketResponse(protocols=["secure_transfer"])
        await ws.prepare(request)
        await ws.close(code=4001, message=close_reason.encode())
        return ws

    app = web.Application()
    app.router.add_get("/camera/1.0/ws", websocket)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server = TestServer(app)
    await server.start_server(ssl=server_context)
    try:
        service = CandidateService(config, tmp_path, control_port=server.port)
        task = asyncio.create_task(service._connect_loop())
        try:
            for _ in range(100):
                if service.websocket_close_codes:
                    break
                await asyncio.sleep(0.01)
            response = await service._health(None)
            health = json.loads(response.text)
            assert health["websocket_close_codes"] == {"4001": 1}
            assert health["last_disconnect_origin"] == "peer_or_transport"
            assert close_reason not in response.text
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_diagnostic_observes_only_fixed_function_names(tmp_path):
    config = fixture_state(tmp_path)
    service = CandidateService(config, tmp_path)
    secret = "synthetic-private-camera-stream-alias"
    await service._handle_diagnostic_frame(None, json.dumps({
        "functionName": "ChangeSmartDetectSettings", "payload": {"secret": secret}
    }).encode())
    await service._handle_diagnostic_frame(None, json.dumps({
        "functionName": secret, "payload": {"secret": secret}
    }).encode())
    await service._handle_diagnostic_frame(None, b"not-json-private-token")
    response = await service._health(None)
    health = json.loads(response.text)
    assert health["observed_function_counts"] == {
        "ChangeSmartDetectSettings": 1}
    assert health["unlisted_function_frames"] == 1
    assert health["unparsed_binary_frames"] == 1
    assert secret not in response.text
    assert "not-json-private-token" not in response.text


@pytest.mark.asyncio
async def test_stream_status_reports_only_verified_streaming_readiness(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    private_alias = "synthetic-private-camera-stream-alias"

    class FakeIngress:
        camera_mac = "2A1122334455"

        async def control(self, payload):
            return {"status": "started" if payload["streaming"] else "stopped",
                    "usedPoints": 2 if payload["streaming"] else 0}

    class FakeWebSocket:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    service.ingress = FakeIngress()
    ws = FakeWebSocket()
    for message_id, streaming in ((10, True), (11, False)):
        await service._handle_diagnostic_frame(ws, json.dumps({
            "functionName": "UiStreamControl", "messageId": message_id,
            "payload": {"streaming": streaming, "uri": private_alias},
        }).encode())

    assert [m["functionName"] for m in ws.messages] == [
        "UiStreamControl", "EventAIPortStatus",
        "UiStreamControl", "EventAIPortStatus"]
    assert [m["messageId"] for m in ws.messages] == [2, 3, 4, 5]
    assert [m["inResponseTo"] for m in ws.messages] == [10, 0, 11, 0]
    assert [m["payload"] for m in ws.messages[1::2]] == [
        {"deviceID": "2A1122334455", "isStreaming": streaming,
         "isSmartDetectReady": False, "isAudioEventReady": False}
        for streaming in (True, False)]
    assert service.stream_status_events_sent == 2
    assert private_alias not in json.dumps(ws.messages)


@pytest.mark.asyncio
async def test_bounded_hello_answers_readonly_and_rejects_stream_control(tmp_path):
    config = fixture_state(tmp_path)
    config["controller_ip"] = "127.0.0.1"
    config["diagnostic_hello_until"] = int(time.time()) + 60
    negotiated = asyncio.Event()
    reply_ids = []

    async def websocket(request):
        ws = web.WebSocketResponse(protocols=["secure_transfer"])
        await ws.prepare(request)
        async for frame in ws:
            if frame.type != aiohttp.WSMsgType.BINARY:
                continue
            message = json.loads(frame.data)
            if message["functionName"] == "ubnt_avclient_hello":
                assert message["payload"]["fwVersion"] == "5.1.12"
                assert message["payload"]["ip"] == config["device_ip"]
                assert message["responseExpected"] is True
                await ws.send_bytes(json.dumps({"functionName": "ubnt_avclient_hello",
                    "messageId": 10, "inResponseTo": message["messageId"],
                    "payload": {"controllerVersion": "synthetic"}}).encode())
                await ws.send_bytes(json.dumps({"functionName": "ubnt_avclient_paramAgreement",
                    "messageId": 11, "inResponseTo": 0, "payload": {"enableStatusCodes": True}}).encode())
            elif message["functionName"] == "ubnt_avclient_paramAgreement":
                reply_ids.append(message["messageId"])
                assert message["inResponseTo"] == 11
                assert message["statusCode"] == 0
                assert message["payload"] == {}
                await ws.send_bytes(json.dumps({"functionName": "GetStreamList",
                    "messageId": 12, "inResponseTo": 0, "payload": {}}).encode())
                await ws.send_bytes(json.dumps({"functionName": "UiStreamControl",
                    "messageId": 13, "inResponseTo": 0,
                    "payload": {"uri": "synthetic-private-camera-stream"}}).encode())
            elif message["inResponseTo"] == 12:
                reply_ids.append(message["messageId"])
                assert message["functionName"] == "GetStreamList"
                assert message["statusCode"] == 0
                assert message["payload"] == {"list": []}
            elif message["inResponseTo"] == 13:
                reply_ids.append(message["messageId"])
                assert message["functionName"] == "UiStreamControl"
                assert message["statusCode"] != 0
                assert "synthetic-private-camera-stream" not in json.dumps(message)
                negotiated.set()
        return ws

    app = web.Application()
    app.router.add_get("/camera/1.0/ws", websocket)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server = TestServer(app)
    await server.start_server(ssl=server_context)
    try:
        service = CandidateService(config, tmp_path, control_port=server.port)
        task = asyncio.create_task(service._connect_loop())
        try:
            await asyncio.wait_for(negotiated.wait(), timeout=3)
            assert service.hello_sent == 1
            assert service.param_agreements == 1
            assert service.ws_binary_frames == 4
            assert service.stream_lists_answered == 1
            assert service.stream_controls_rejected == 1
            assert reply_ids == [2, 3, 4]
            public = await service._health(None)
            assert "synthetic-private-camera-stream" not in public.text
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await server.close()
