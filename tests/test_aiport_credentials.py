"""Credential rotation must be durable and enforced before acknowledgement."""

import json
import os
import stat
import subprocess
import time

import aiohttp
from aiohttp.test_utils import TestServer
import pytest

from aikey.aiport_candidate import CandidateService
from aikey.aiport_credentials import CredentialError, CredentialStore
from test_aiport_candidate import fixture_state


def synthetic_digest(password="synthetic-password"):
    return subprocess.run(
        ["openssl", "passwd", "-6", "-salt", "abcdefghijklmnop", "-stdin"],
        input=(password + "\n").encode(), capture_output=True, check=True,
    ).stdout.decode().strip()


def test_rotation_persists_only_a_private_hash_and_survives_restart(tmp_path):
    payload = {"username": "synthetic-user", "hashedPassword": synthetic_digest()}
    store = CredentialStore(tmp_path)
    store.rotate(payload)
    path = tmp_path / "management-credential.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert b"synthetic-password" not in path.read_bytes()
    assert CredentialStore(tmp_path).verify("synthetic-user", "synthetic-password")
    assert not CredentialStore(tmp_path).verify("synthetic-user", "wrong")
    assert not CredentialStore(tmp_path).verify("synthetic-user", "synthetic-password\nignored")
    assert not CredentialStore(tmp_path).verify("wrong", "synthetic-password")


@pytest.mark.parametrize("payload", [
    {}, {"username": "u", "hashedPassword": "bad"},
    {"username": "u\n", "hashedPassword": "bad"},
    {"username": "u", "hashedPassword": "bad", "extra": 1},
])
def test_rotation_rejects_bad_shapes_without_writing(tmp_path, payload):
    store = CredentialStore(tmp_path)
    with pytest.raises(CredentialError):
        store.rotate(payload)
    assert not (tmp_path / "management-credential.json").exists()


def test_startup_rejects_unsafe_credential_file(tmp_path):
    path = tmp_path / "management-credential.json"
    path.write_text(json.dumps({"username": "u", "hashedPassword": synthetic_digest()}))
    path.chmod(0o644)
    with pytest.raises(CredentialError):
        CredentialStore(tmp_path)
    path.unlink()
    path.symlink_to(tmp_path / "other")
    with pytest.raises(CredentialError):
        CredentialStore(tmp_path)


def test_rotation_failure_keeps_previous_credential(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path)
    store.rotate({"username": "synthetic-user", "hashedPassword": synthetic_digest()})

    def refuse_replace(source, target):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(os, "replace", refuse_replace)
    with pytest.raises(CredentialError):
        store.rotate({"username": "next-user", "hashedPassword": synthetic_digest("next-pass")})
    assert store.verify("synthetic-user", "synthetic-password")
    assert not store.verify("next-user", "next-pass")


@pytest.mark.asyncio
async def test_control_rotation_acknowledges_only_after_persistence_and_login_enforces(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    service = CandidateService(config, tmp_path)
    service._params_agreed = True

    class Sink:
        def __init__(self):
            self.frames = []

        async def send_bytes(self, raw):
            self.frames.append(json.loads(raw))

    sink = Sink()
    digest = synthetic_digest()
    message = {"functionName": "UpdateUsernamePassword", "messageId": 31,
               "payload": {"username": "synthetic-user", "hashedPassword": digest}}
    await service._handle_diagnostic_frame(sink, json.dumps(message).encode())
    assert sink.frames[-1]["statusCode"] == 0
    assert service.credential_rotations == 1
    assert CredentialStore(tmp_path).verify("synthetic-user", "synthetic-password")

    server = TestServer(service.app())
    await server.start_server(ssl=service._server_context())
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as client:
            login_url = str(server.make_url("/api/1.2/login"))
            response = await client.post(login_url, json={"username": "synthetic-user",
                                                          "password": "wrong"})
            assert response.status == 401
            response = await client.post(login_url, json={"username": "synthetic-user",
                                                          "password": "synthetic-password"})
            assert response.status == 200
            assert response.cookies["AISESSION"]["secure"]
            assert response.cookies["AISESSION"]["httponly"]
            manage_url = str(server.make_url("/api/1.2/manage"))
            response = await client.post(manage_url, json={"username": "synthetic-user",
                                                        "password": "wrong", "mgmt": {}})
            assert response.status == 401
            response = await client.post(manage_url, json={"username": "synthetic-user",
                                                        "password": "synthetic-password",
                                                        "mgmt": {}})
            assert response.status == 503
            health = await client.get(str(server.make_url("/healthz")))
            public = await health.text()
            assert "synthetic-password" not in public
            assert digest not in public
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_rotation_rejects_bad_payload_and_expired_window(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    service = CandidateService(config, tmp_path)
    service._params_agreed = True

    class Sink:
        async def send_bytes(self, raw):
            self.last = json.loads(raw)

    sink = Sink()
    message = {"functionName": "UpdateUsernamePassword", "messageId": 32,
               "payload": {"username": "synthetic-user", "hashedPassword": "bad"}}
    await service._handle_diagnostic_frame(sink, json.dumps(message).encode())
    assert sink.last["statusCode"] != 0
    assert not (tmp_path / "management-credential.json").exists()
    service.config["diagnostic_hello_until"] = int(time.time()) - 1
    message["payload"]["hashedPassword"] = synthetic_digest()
    await service._handle_diagnostic_frame(sink, json.dumps(message).encode())
    assert sink.last["statusCode"] != 0
    assert service.credential_rotations_rejected == 2
    assert not (tmp_path / "management-credential.json").exists()
