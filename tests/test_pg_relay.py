"""Source-filtering PostgreSQL relay for the macOS search host (#2)."""

import asyncio
import json

import pytest

from aikey.pg_relay import Relay


async def echo_server():
    async def echo(reader, writer):
        while data := await reader.read(1024):
            writer.write(b"pg:" + data)
            await writer.drain()
        writer.close()
    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def relay_on(target_port, allowed, tmp_path, **kwargs):
    relay = Relay(("127.0.0.1", 0), ("127.0.0.1", target_port), allowed,
                  status_path=str(tmp_path / "relay.json"), **kwargs)
    server = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
    return relay, server, server.sockets[0].getsockname()[1]


async def test_an_allowed_peer_reaches_postgres_and_bytes_pass_unchanged(tmp_path):
    upstream, upstream_port = await echo_server()
    relay, server, port = await relay_on(upstream_port, ["127.0.0.1/32"], tmp_path)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"\x00\x00\x00\x08\x04\xd2\x16\x2f")      # SSLRequest
        await writer.drain()
        assert await reader.readexactly(11) == b"pg:\x00\x00\x00\x08\x04\xd2\x16\x2f"
        writer.close()
        await asyncio.sleep(0.05)
    finally:
        server.close()
        upstream.close()
    assert relay.counters["accepted"] == 1 and relay.counters["refused_source"] == 0


async def test_other_sources_are_refused_before_any_upstream_connection(tmp_path):
    upstream, upstream_port = await echo_server()
    relay, server, port = await relay_on(upstream_port, ["192.168.0.1/32"], tmp_path)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert await reader.read(10) == b""
        writer.close()
    finally:
        server.close()
        upstream.close()
    assert relay.counters == {"accepted": 0, "refused_source": 1, "refused_capacity": 0,
                              "upstream_failures": 0, "closed": 0}


async def test_capacity_and_upstream_failures_are_counted(tmp_path):
    relay, server, port = await relay_on(1, ["127.0.0.1/32"], tmp_path)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert await reader.read(10) == b""
        writer.close()
        relay.max_connections = 0
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert await reader.read(10) == b""
        writer.close()
    finally:
        server.close()
    assert relay.counters["upstream_failures"] == 1 and relay.counters["refused_capacity"] == 1
    relay.write_status()
    status = json.loads((tmp_path / "relay.json").read_text())
    assert status["allowed"] == ["127.0.0.1/32"] and status["accepted"] == 1
    assert oct((tmp_path / "relay.json").stat().st_mode & 0o777) == "0o600"


def test_admission_is_exact():
    relay = Relay(("127.0.0.1", 1), ("127.0.0.1", 2), ["192.168.0.1/32", "192.168.64.0/24"])
    assert relay.admits("192.168.0.1") and relay.admits("192.168.64.125")
    assert relay.admits("::ffff:192.168.0.1")
    for peer in ("192.168.0.2", "192.168.65.1", "10.0.0.1", "not-an-ip", ""):
        assert not relay.admits(peer)
    for allowed in ([], ["192.168.0.0/16"], ["192.168.0.1/24"]):
        with pytest.raises(ValueError):
            Relay(("127.0.0.1", 1), ("127.0.0.1", 2), allowed)
