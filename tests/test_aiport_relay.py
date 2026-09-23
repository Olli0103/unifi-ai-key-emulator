"""The bounded relay forwards TLS unchanged and denies other source IPs."""

import asyncio
import ssl

import aiohttp
from aiohttp.test_utils import TestServer
import pytest

from aikey.aiport_candidate import CandidateService
from aikey.aiport_relay import BoundedRelay, RelayError
from test_aiport_candidate import fixture_state


@pytest.mark.asyncio
async def test_relay_passes_tls_to_the_candidate_without_reading_http(tmp_path):
    config = fixture_state(tmp_path)
    cert = tmp_path / "device.crt"
    candidate = CandidateService(config, tmp_path)
    upstream = TestServer(candidate.app())
    await upstream.start_server(ssl=candidate._server_context())
    relay = BoundedRelay(listen_ip="127.0.0.1", listen_port=0,
                         upstream_port=upstream.port, allowed_source_ip="127.0.0.1",
                         allow_loopback=True)
    await relay.start()
    try:
        context = ssl.create_default_context(cafile=str(cert))
        context.check_hostname = False
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=context),
                                         trust_env=False) as client:
            async with client.post(f"https://127.0.0.1:{relay.listen_port}/api/1.2/manage",
                                   json={"username": "private", "password": "private"}) as response:
                assert response.status == 503
                assert (await response.json())["error"] == "Adoption requires rotated credentials"
        assert relay.accepted == 1
        assert candidate.manage_requests == 1
        assert candidate.last_manage_shape["recognized_fields"] == ["password", "username"]
    finally:
        await relay.stop()
        await upstream.close()


@pytest.mark.asyncio
async def test_relay_rejects_non_controller_source():
    relay = BoundedRelay(listen_ip="127.0.0.1", listen_port=0,
                         upstream_port=8443, allowed_source_ip="127.0.0.2",
                         allow_loopback=True)
    await relay.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", relay.listen_port)
        assert await asyncio.wait_for(reader.read(1), timeout=2) == b""
        assert relay.rejected == 1
        assert relay.accepted == 0
        writer.close()
        await writer.wait_closed()
    finally:
        await relay.stop()


def test_relay_requires_distinct_private_endpoints():
    with pytest.raises(RelayError):
        BoundedRelay(listen_ip="8.8.8.8", listen_port=443,
                     upstream_port=8443, allowed_source_ip="192.168.10.1")
    with pytest.raises(RelayError):
        BoundedRelay(listen_ip="192.168.10.20", listen_port=443,
                     upstream_port=443, allowed_source_ip="192.168.10.1")
