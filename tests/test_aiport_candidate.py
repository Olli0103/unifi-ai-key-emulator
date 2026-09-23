"""The isolated AI Port candidate stays unadopted and never stores credentials."""

import asyncio
import hashlib
import json
from pathlib import Path
import ssl

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
    cert, _ = ensure_identity_certificate(tmp_path, "2A9D75736D4E")
    private_file(tmp_path / "controller-ca.pem", cert.read_bytes())
    config = {"controller_ip": "192.168.10.1", "device_ip": "192.168.10.20",
              "mac": "2A9D75736D4E", "controller_pin": hashlib.sha256(ssl.PEM_cert_to_DER_cert(
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
