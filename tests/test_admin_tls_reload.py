"""TLS material can change without replacing a listener or admin authority."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import aiohttp
from aiohttp.test_utils import TestServer
import pytest
from yarl import URL

from aikey.admin_listener import configure_listener
from aikey.admin_audit import AuditLog
from aikey.admin_security import AdminSecurity, SESSION_COOKIE_NAME
from aikey.config import atomic_private, initialize
from aikey.control_site import ControlSite, _reload_admin_tls
from aikey.tls import ensure_identity_certificate
from test_control_site_secrets import PASSWORD


def pair(root, mac):
    root.mkdir(mode=0o700)
    return ensure_identity_certificate(root, mac)


async def peer(port, trust, name):
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", port, ssl=trust, server_hostname=name
    )
    der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    return reader, writer, hashlib.sha256(der).hexdigest()


async def health(reader, writer, host):
    writer.write(f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
    await writer.drain()
    header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
    assert header.startswith(b"HTTP/1.1 200 ")
    length = int(re.search(rb"(?im)^Content-Length: (\d+)\r?$", header).group(1))
    assert json.loads(await asyncio.wait_for(reader.readexactly(length), 3)) == {
        "service": "control-site"
    }


@pytest.mark.parametrize("name", ["127.0.0.1", "localhost"])
async def test_real_rotation_updates_new_handshakes_not_existing_connections_or_sessions(
    tmp_path, name
):
    certificate, key = pair(tmp_path / "live", "AABBCCDDEEFF")
    next_cert, next_key = pair(tmp_path / "next", "AABBCCDDEEFE")
    trust = ssl.create_default_context(cadata=certificate.read_text() + next_cert.read_text())
    path = tmp_path / "key-config/config.json"
    initialize(path, path.parent, controller_host="127.0.0.1")
    original = path.read_bytes()
    site = ControlSite(
        path,
        signing_key=b"q" * 32,
        password_record=AdminSecurity.create_password_record(PASSWORD),
        port=8765,
        origin="https://127.0.0.1:8765",
        tls_certificate=certificate,
        tls_private_key=key,
    )
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server(ssl=site.listener.ssl_context)
    site.origin = f"https://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"q" * 32, site.origin)
    client = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=trust), cookie_jar=aiohttp.CookieJar(unsafe=True)
    )
    old = fresh = None
    try:
        reader, old, old_hash = await peer(server.port, trust, name)
        await health(reader, old, f"127.0.0.1:{server.port}")
        response = await client.post(
            site.origin + "/login",
            data={"password": PASSWORD},
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 303
        await response.read()
        cookie = client.cookie_jar.filter_cookies(URL(site.origin))[SESSION_COOKIE_NAME].value
        primary = site.listener.ssl_context
        atomic_private(certificate, next_cert.read_bytes())
        atomic_private(key, next_key.read_bytes())
        assert site.listener.reload_tls() is True
        assert site.listener.ssl_context is primary and site.listener.tls_generation == 1
        # Existing TLS transport remains usable with the old negotiated context.
        await health(reader, old, f"127.0.0.1:{server.port}")
        fresh_reader, fresh, new_hash = await peer(server.port, trust, name)
        assert new_hash != old_hash
        assert (
            new_hash == hashlib.sha256(ssl.PEM_cert_to_DER_cert(next_cert.read_text())).hexdigest()
        )
        await health(fresh_reader, fresh, f"127.0.0.1:{server.port}")
        # The same cookie remains valid on a fresh TLS connection too.
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=trust)) as new_client:
            response = await new_client.get(
                site.origin + "/",
                allow_redirects=False,
                headers={"Cookie": f"{SESSION_COOKIE_NAME}={cookie}"},
            )
            assert response.status == 200
            await response.read()
        assert path.read_bytes() == original
    finally:
        for writer in (old, fresh):
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        await client.close()
        await server.close()


def test_loopback_http_has_no_certificate_reload_operation():
    listener = configure_listener(8765, "127.0.0.1", "http://127.0.0.1:8765", None, None)
    assert listener.reload_tls() is False and listener.tls_generation == 0


def test_concurrent_reload_candidates_are_serialized_before_publication(tmp_path, monkeypatch):
    certificate, key = pair(tmp_path / "live", "AABBCCDDEEFF")
    listener = configure_listener(8765, "127.0.0.1", "https://localhost:8765", certificate, key)
    contexts = listener._contexts
    initial = contexts.current
    entered, release, second_started = (threading.Event() for _ in range(3))
    candidates = [ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER) for _ in range(2)]
    calls, failures = [], []

    def load():
        index = len(calls)
        calls.append(index)
        if index == 0:
            entered.set()
            assert release.wait(3)
        return candidates[index]

    def reload(second=False):
        if second:
            second_started.set()
        try:
            listener.reload_tls()
        except BaseException as exc:
            failures.append(exc)

    monkeypatch.setattr(contexts, "_load", load)
    workers = [threading.Thread(target=reload), threading.Thread(target=reload, args=(True,))]
    workers[0].start()
    try:
        assert entered.wait(3)
        workers[1].start()
        assert second_started.wait(3)
        assert calls == [0] and contexts.current is initial and listener.tls_generation == 0
    finally:
        release.set()
        for worker in workers:
            if worker.ident is not None:
                worker.join(3)
    assert all(not worker.is_alive() for worker in workers) and not failures
    assert calls == [0, 1] and contexts.current is candidates[1] and listener.tls_generation == 2


@pytest.mark.parametrize(
    "bad", ["missing", "public", "link", "fifo", "mismatch", "encrypted", "oversized"]
)
async def test_failed_reload_keeps_the_entire_previous_context_and_unchanged_generation(
    tmp_path, bad
):
    certificate, key = pair(tmp_path / "live", "AABBCCDDEEFF")
    next_cert, next_key = pair(tmp_path / "next", "AABBCCDDEEFE")
    trust = ssl.create_default_context(cadata=certificate.read_text())
    listener = configure_listener(8765, "127.0.0.1", "https://localhost:8765", certificate, key)
    from aiohttp import web

    app = web.Application()

    async def response(_):
        return web.json_response({"service": "control-site"})

    app.router.add_get("/healthz", response)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server(ssl=listener.ssl_context)
    writer = None
    try:
        if bad == "missing":
            key.unlink()
        elif bad == "public":
            key.chmod(0o644)
        elif bad == "link":
            key.unlink()
            key.symlink_to(next_key)
        elif bad == "fifo":
            key.unlink()
            os.mkfifo(key, 0o600)
        elif bad == "mismatch":
            atomic_private(key, next_key.read_bytes())
        elif bad == "oversized":
            atomic_private(certificate, b" " * 262145)
        else:
            from cryptography.hazmat.primitives import serialization

            private = serialization.load_pem_private_key(key.read_bytes(), password=None)
            atomic_private(
                key,
                private.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.BestAvailableEncryption(b"synthetic-private-passphrase"),
                ),
            )
        with pytest.raises((OSError, ValueError)):
            listener.reload_tls()
        assert listener.tls_generation == 0
        reader, writer, _ = await peer(server.port, trust, "localhost")
        await health(reader, writer, f"127.0.0.1:{server.port}")
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        await server.close()


async def test_handshake_never_reopens_or_reloads_missing_configured_material(
    tmp_path, monkeypatch
):
    certificate, key = pair(tmp_path / "live", "AABBCCDDEEFF")
    trust = ssl.create_default_context(cadata=certificate.read_text())
    listener = configure_listener(8765, "127.0.0.1", "https://localhost:8765", certificate, key)
    load = listener._contexts._load
    calls = []

    def counted():
        calls.append(True)
        return load()

    monkeypatch.setattr(listener._contexts, "_load", counted)
    certificate.unlink()
    key.unlink()
    from aiohttp import web

    app = web.Application()

    async def response(_):
        return web.json_response({"service": "control-site"})

    app.router.add_get("/healthz", response)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server(ssl=listener.ssl_context)
    try:
        for name in ("localhost", "127.0.0.1"):
            reader, writer, _ = await peer(server.port, trust, name)
            await health(reader, writer, f"127.0.0.1:{server.port}")
            writer.close()
            await writer.wait_closed()
        assert calls == [] and listener.tls_generation == 0
        with pytest.raises(OSError):
            listener.reload_tls()
        assert calls == [True] and listener.tls_generation == 0
    finally:
        await server.close()


def test_reload_audit_and_system_output_contain_only_fixed_codes(tmp_path, capsys):
    certificate, key = pair(tmp_path / "live", "AABBCCDDEEFF")
    listener = configure_listener(8765, "127.0.0.1", "https://localhost:8765", certificate, key)
    audit = AuditLog(tmp_path / "audit.jsonl")
    site = SimpleNamespace(listener=listener, audit=audit)
    assert _reload_admin_tls(site)
    assert listener.tls_generation == 1
    key.chmod(0o644)
    assert not _reload_admin_tls(site) and listener.tls_generation == 1
    assert [r["result"] for r in audit.recent()] == ["refused", "ready"]
    output = capsys.readouterr().out + (tmp_path / "audit.jsonl").read_text()
    assert str(tmp_path) not in output and "PRIVATE KEY" not in output
    key.chmod(0o600)

    class BrokenAudit:
        def record(self, *args, **kwargs):
            raise ValueError("private-audit-canary")

    site.audit = BrokenAudit()
    assert _reload_admin_tls(site) and listener.tls_generation == 2
    result = json.loads(capsys.readouterr().out)
    assert result["admin_tls_reload"] == "ready" and result["audit_recorded"] is False


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="Unix CLI signal required")
async def test_real_cli_sighup_keeps_pid_and_cookie_valid_on_new_certificate(tmp_path):
    certificate, key = pair(tmp_path / "live", "AABBCCDDEEFF")
    next_cert, next_key = pair(tmp_path / "next", "AABBCCDDEEFE")
    trust = ssl.create_default_context(cadata=certificate.read_text() + next_cert.read_text())
    state = tmp_path / "admin"
    state.mkdir(mode=0o700)
    atomic_private(state / "admin-signing-key", b"q" * 32)
    atomic_private(state / "admin-password-record", AdminSecurity.create_password_record(PASSWORD))
    config = tmp_path / "key/config.json"
    initialize(config, config.parent, controller_host="127.0.0.1")
    original = config.read_bytes()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"https://127.0.0.1:{port}"
    log = tmp_path / "private-process.log"
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "aikey.control_site",
                "run",
                "--state-dir",
                str(state),
                "--aikey-config",
                str(config),
                "--port",
                str(port),
                "--origin",
                origin,
                "--tls-certificate",
                str(certificate),
                "--tls-private-key",
                str(key),
            ],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            stdout=output,
            stderr=output,
        )
    client = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=trust, force_close=True),
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            assert process.poll() is None
            if b'"admin_tls_reload_protocol": 1' in log.read_bytes():
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("owned CLI startup deadline")
        response = await client.post(
            origin + "/login",
            data={"password": PASSWORD},
            headers={"Origin": origin},
            allow_redirects=False,
        )
        assert response.status == 303
        await response.read()
        cookie = client.cookie_jar.filter_cookies(URL(origin))[SESSION_COOKIE_NAME].value
        atomic_private(certificate, next_cert.read_bytes())
        atomic_private(key, next_key.read_bytes())
        pid = process.pid
        process.send_signal(signal.SIGHUP)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if b'"admin_tls_reload": "ready"' in log.read_bytes():
                break
            assert process.poll() is None
            await asyncio.sleep(0.05)
        else:
            pytest.fail("owned CLI reload deadline")
        assert process.poll() is None and process.pid == pid
        reader, writer, actual = await peer(port, trust, "127.0.0.1")
        writer.close()
        await writer.wait_closed()
        assert actual == hashlib.sha256(ssl.PEM_cert_to_DER_cert(next_cert.read_text())).hexdigest()
        response = await client.get(origin + "/", allow_redirects=False)
        assert response.status == 200
        await response.read()
        assert client.cookie_jar.filter_cookies(URL(origin))[SESSION_COOKIE_NAME].value == cookie
        assert config.read_bytes() == original
        # Refused HUP cannot terminate the CLI or replace its loaded context.
        key.chmod(0o644)
        process.send_signal(signal.SIGHUP)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if b'"admin_tls_reload": "refused"' in log.read_bytes():
                break
            assert process.poll() is None
            await asyncio.sleep(0.05)
        else:
            pytest.fail("owned CLI refused-reload deadline")
        assert process.pid == pid and process.poll() is None
        response = await client.get(origin + "/", allow_redirects=False)
        assert response.status == 200
        await response.read()
        assert config.read_bytes() == original
        key.chmod(0o600)
        assert any(
            r["action"] == "tls_reload" and r["result"] == "ready"
            for r in AuditLog(state / "admin-audit.jsonl").recent()
        )
        assert PASSWORD not in log.read_text() and "PRIVATE KEY" not in log.read_text()
    finally:
        await client.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
            pytest.fail("owned CLI shutdown deadline")
    assert process.returncode == 0
