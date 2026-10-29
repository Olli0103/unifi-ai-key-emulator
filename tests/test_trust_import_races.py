"""Certificate-save admission races, using fake handshakes and fictional bytes."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import hashlib
import ssl
import threading

import pytest

from aikey.config import ConfigError
from aikey import tls

DER = b"synthetic-certificate-bytes-for-save-admission-only"
PIN = hashlib.sha256(DER).hexdigest()


def fake_handshakes(monkeypatch, on_certificate):
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def getpeercert(self, binary_form):
            assert binary_form is True
            on_certificate()
            return DER
    class Context:
        def __init__(self, *args):
            pass
        def wrap_socket(self, connection, server_hostname):
            assert server_hostname == "example.invalid"
            return connection
    monkeypatch.setattr(tls.ssl, "SSLContext", Context)
    monkeypatch.setattr(tls.socket, "create_connection", lambda *args, **kwargs: Connection())


def test_trust_created_during_handshake_is_not_overwritten(tmp_path, monkeypatch):
    output = tmp_path / "controller-ca.pem"
    def appeared():
        output.write_bytes(b"preserve-existing-trust")
    fake_handshakes(monkeypatch, appeared)
    with pytest.raises(ConfigError, match="already exists"):
        tls.import_controller_trust("example.invalid", 7442, PIN, output)
    assert output.read_bytes() == b"preserve-existing-trust"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["controller-ca.pem"]


def test_dangling_trust_link_is_refused_before_handshake(tmp_path, monkeypatch):
    output, target = tmp_path / "controller-ca.pem", tmp_path / "unrelated"
    output.symlink_to(target)
    def forbidden(*args, **kwargs):
        pytest.fail("Existing dangling trust link reached the network")
    monkeypatch.setattr(tls.socket, "create_connection", forbidden)
    with pytest.raises(ConfigError, match="already exists"):
        tls.import_controller_trust("example.invalid", 7442, PIN, output)
    assert output.is_symlink() and not target.exists()


def test_two_verified_handshakes_admit_one_new_trust_file(tmp_path, monkeypatch):
    output = tmp_path / "controller-ca.pem"
    barrier = threading.Barrier(2, timeout=3)
    fake_handshakes(monkeypatch, barrier.wait)
    def run(_):
        try:
            return tls.import_controller_trust("example.invalid", 7442, PIN, output)
        except ConfigError:
            return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert sorted(results) == sorted([PIN, "refused"])
    assert output.read_text() == ssl.DER_cert_to_PEM_cert(DER)
    assert output.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["controller-ca.pem"]


async def test_real_loopback_tls_pin_and_exclusive_persistence(tmp_path):
    state = tmp_path / "synthetic-device"
    state.mkdir()
    cert, _ = tls.ensure_identity_certificate(state, "02000000ABCD")
    expected = cert.read_text()
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(expected)).hexdigest()
    seen = asyncio.Queue()
    async def connected(reader, writer):
        try:
            seen.put_nowait(await reader.read(1))
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(connected, "127.0.0.1", 0,
                                        ssl=tls.server_context(state))
    output = tmp_path / "new-controller-ca.pem"
    try:
        port = server.sockets[0].getsockname()[1]
        result = await asyncio.to_thread(tls.import_controller_trust, "127.0.0.1", port, pin, output)
        assert result == pin and output.read_text() == expected
        assert output.stat().st_mode & 0o777 == 0o600
        assert await asyncio.wait_for(seen.get(), timeout=3) == b""
        with pytest.raises(ConfigError, match="already exists"):
            await asyncio.to_thread(tls.import_controller_trust, "127.0.0.1", port, pin, output)
        wrong = tmp_path / "wrong-pin.pem"
        with pytest.raises(ConfigError, match="fingerprint does not match"):
            await asyncio.to_thread(tls.import_controller_trust, "127.0.0.1", port, "00" * 32, wrong)
        assert not wrong.exists() and output.read_text() == expected
        assert await asyncio.wait_for(seen.get(), timeout=3) == b""
        assert seen.empty()
    finally:
        server.close()
        await server.wait_closed()


def test_unsupported_exclusive_publication_never_falls_back_to_replacement(tmp_path, monkeypatch):
    output = tmp_path / "controller-ca.pem"
    fake_handshakes(monkeypatch, lambda: None)
    def unsupported(*args, **kwargs):
        raise OSError("Synthetic unsupported filesystem operation")
    monkeypatch.setattr(tls.os, "link", unsupported)
    with pytest.raises(OSError, match="unsupported filesystem"):
        tls.import_controller_trust("example.invalid", 7442, PIN, output)
    assert list(tmp_path.iterdir()) == []
