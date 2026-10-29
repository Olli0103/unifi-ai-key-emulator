"""The address guard must preserve verified TLS and the provider's SNI/Host."""

import ssl

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey.provider_network import ProviderConnector
from aikey.tls import ensure_identity_certificate, server_context
from test_provider_network import Resolver


@pytest.mark.parametrize("trusted", [True, False])
async def test_tls_trust_and_original_sni_are_not_replaced_by_resolved_ip(tmp_path, trusted):
    cert, _ = ensure_identity_certificate(tmp_path, "020000000019")
    server_tls = server_context(tmp_path)
    names, hits = [], []
    server_tls.set_servername_callback(lambda sock, name, context: names.append(name))
    async def endpoint(request):
        hits.append(request.host)
        return web.Response(text="synthetic")
    app = web.Application()
    app.router.add_get("/", endpoint)
    server = TestServer(app, scheme="https")
    await server.start_server(ssl=server_tls)
    client_tls = ssl.create_default_context(cafile=str(cert) if trusted else None)
    assert client_tls.check_hostname and client_tls.verify_mode == ssl.CERT_REQUIRED
    try:
        async with aiohttp.ClientSession(connector=ProviderConnector(
                resolver=Resolver(["127.0.0.1"]), ssl=client_tls), trust_env=False) as client:
            url = f"https://localhost:{server.port}/"
            if trusted:
                async with client.get(url) as response:
                    assert await response.text() == "synthetic"
                assert hits == [f"localhost:{server.port}"]
            else:
                with pytest.raises(aiohttp.ClientConnectorCertificateError):
                    await client.get(url)
                assert hits == []
        assert names == ["localhost"]
    finally:
        await server.close()
