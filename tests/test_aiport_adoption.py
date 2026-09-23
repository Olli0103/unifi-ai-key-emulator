"""AI Port adoption needs verified credentials, a short window and a token-bound reconnect."""

import asyncio
import json
import os
import stat
import time

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey.aiport_adoption import AdoptionError, AdoptionStore, validate_management
from aikey.aiport_candidate import CandidateService
from test_aiport_candidate import fixture_state
from test_aiport_credentials import synthetic_digest


def adoption_store(tmp_path):
    return AdoptionStore(tmp_path, "192.168.10.1", "a" * 64, 7442)


def test_management_requires_exact_controller_and_safe_token():
    payload = {"token": "synthetic-token-123456", "hosts": ["192.168.10.1:7442"],
               "protocol": "wss", "username": "synthetic-user",
               "password": "synthetic-password", "mode": 0, "nvr": "synthetic-console",
               "controller": "Protect", "consoleId": "synthetic-id",
               "consoleName": "Synthetic Console"}
    assert validate_management(payload, "192.168.10.1", 7442, "synthetic-user",
                               "synthetic-password") == payload["token"]
    for altered in (
        {**payload, "hosts": ["192.168.10.2:7442"]},
        {**payload, "hosts": ["192.168.10.1:7442", "bad-host"]},
        {**payload, "protocol": "ws"},
        {**payload, "token": "a\nprivate"},
        {**payload, "password": "other"},
        {**payload, "mode": 1},
        {**payload, "mode": False},
        {**payload, "extra": True},
    ):
        with pytest.raises(AdoptionError):
            validate_management(altered, "192.168.10.1", 7442, "synthetic-user",
                                "synthetic-password")


def test_pending_adoption_persists_privately_and_confirmation_removes_token(tmp_path):
    store = adoption_store(tmp_path)
    token = "synthetic-token-123456"
    store.begin(token, int(time.time()) + 60)
    path = tmp_path / "aiport-adoption.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert adoption_store(tmp_path).pending_token == token
    store.confirm()
    assert store.adopted
    assert adoption_store(tmp_path).adopted
    assert token.encode() not in path.read_bytes()
    with pytest.raises(AdoptionError):
        store.begin(token, int(time.time()) + 60)
    with pytest.raises(AdoptionError):
        AdoptionStore(tmp_path, "192.168.10.2", "a" * 64, 7442)


def test_expired_or_unsafe_adoption_state_cannot_activate(tmp_path):
    path = tmp_path / "aiport-adoption.json"
    path.write_text(json.dumps({"controller_ip": "192.168.10.1",
                                "controller_pin": "a" * 64, "control_port": 7442,
                                "phase": "pending", "token": "synthetic-token-123456",
                                "expires_at": int(time.time()) - 1}))
    path.chmod(0o600)
    assert adoption_store(tmp_path).pending_token is None
    path.chmod(0o644)
    with pytest.raises(AdoptionError):
        adoption_store(tmp_path)


def test_failed_state_write_keeps_candidate_unadopted(tmp_path, monkeypatch):
    store = adoption_store(tmp_path)

    def refuse_replace(source, target):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(os, "replace", refuse_replace)
    with pytest.raises(AdoptionError):
        store.begin("synthetic-token-123456", int(time.time()) + 60)
    assert not store.adopted
    assert store.pending_token is None


@pytest.mark.asyncio
async def test_manage_requires_rotation_and_expiring_adoption_window(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_adoption_until"] = int(time.time()) + 60
    service = CandidateService(config, tmp_path)
    server = TestServer(service.app())
    await server.start_server(ssl=service._server_context())
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as client:
            url = str(server.make_url("/api/1.2/manage"))
            body = {"username": "synthetic-user", "password": "synthetic-password",
                    "mgmt": {"token": "synthetic-token-123456",
                             "hosts": ["192.168.10.1:7442"], "protocol": "wss",
                             "username": "synthetic-user", "password": "synthetic-password",
                             "mode": 0, "nvr": "synthetic-console",
                             "controller": "Protect", "consoleId": "synthetic-id",
                             "consoleName": "Synthetic Console"}}
            assert (await client.post(url, json=body)).status == 503
            service.credentials.rotate({"username": "synthetic-user",
                                        "hashedPassword": synthetic_digest()})
            assert (await client.post(url, json={**body, "password": "wrong"})).status == 401
            duplicate = ('{"username":"synthetic-user","username":"synthetic-user",'
                         '"password":"synthetic-password","mgmt":{}}')
            assert (await client.post(url, data=duplicate,
                                      headers={"Content-Type": "application/json"})).status == 401
            assert (await client.post(url, json=body)).status == 200
            assert service.adoption.pending_token == body["mgmt"]["token"]
            health = await (await client.get(str(server.make_url("/healthz")))).text()
            assert body["mgmt"]["token"] not in health
            assert "synthetic-password" not in health
            service.config["diagnostic_adoption_until"] = int(time.time()) - 1
            assert (await client.post(url, json=body)).status == 503
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_token_bound_websocket_confirms_adoption_without_camera_access(tmp_path):
    config = fixture_state(tmp_path)
    config["controller_ip"] = "127.0.0.1"
    config["diagnostic_adoption_until"] = int(time.time()) + 60
    token = "synthetic-token-123456"
    first_connected = asyncio.Event()
    adopted_connected = asyncio.Event()
    seen = []

    async def websocket(request):
        seen.append((request.headers["Adopted"], request.query.get("token")))
        ws = web.WebSocketResponse(protocols=["secure_transfer"])
        await ws.prepare(request)
        if request.query.get("token") == token:
            adopted_connected.set()
        else:
            first_connected.set()
        async for _ in ws:
            pass
        return ws

    app = web.Application()
    app.router.add_get("/camera/1.0/ws", websocket)
    control = TestServer(app)
    await control.start_server(ssl=CandidateService(config, tmp_path)._server_context())
    service = CandidateService(config, tmp_path, control_port=control.port)
    service.credentials.rotate({"username": "synthetic-user",
                                "hashedPassword": synthetic_digest()})
    management = TestServer(service.app())
    await management.start_server(ssl=service._server_context())
    task = asyncio.create_task(service._connect_loop())
    try:
        await asyncio.wait_for(first_connected.wait(), 3)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as client:
            body = {"username": "synthetic-user", "password": "synthetic-password",
                    "mgmt": {"token": token,
                             "hosts": [f"127.0.0.1:{control.port}"], "protocol": "wss"}}
            response = await client.post(str(management.make_url("/api/1.2/manage")), json=body)
            assert response.status == 200
            await asyncio.wait_for(adopted_connected.wait(), 8)
            for _ in range(100):
                if service.adoption.adopted:
                    break
                await asyncio.sleep(0.01)
            assert service.adoption.adopted
            assert service.ingress is None
            assert seen[:2] == [("false", None), ("true", token)]
            assert token.encode() not in (tmp_path / "aiport-adoption.json").read_bytes()
            health = await (await client.get(str(management.make_url("/healthz")))).text()
            assert '"adopted": true' in health
            assert '"stream_ingest_enabled": false' in health
            assert token not in health
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await management.close()
        await control.close()
