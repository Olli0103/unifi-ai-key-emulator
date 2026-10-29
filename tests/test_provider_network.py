"""Synthetic DNS answers and loopback servers only; never public network I/O."""

import asyncio
import socket

import aiohttp
from aiohttp import web
from aiohttp.abc import AbstractResolver
from aiohttp.test_utils import TestServer
import pytest

from aikey import model_catalog, search, worker
from aikey.model_catalog import CatalogError, fetch_catalog
from aikey.provider_network import ProviderAddressError, ProviderConnector, validate_provider_host
from aikey.providers import ProviderError, validate_inference_config
from test_deep_mode import Registry, worker_config


class Resolver(AbstractResolver):
    def __init__(self, addresses):
        self.addresses = addresses
        self.calls = 0
        self.records = []

    async def resolve(self, host, port=0, family=socket.AF_INET):
        self.calls += 1
        self.records = [{"hostname": host, "host": address, "port": port,
                         "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
                         "proto": socket.IPPROTO_TCP, "flags": 0} for address in self.addresses]
        return self.records

    async def close(self):
        pass


@pytest.mark.parametrize("answer", [
    "127.0.0.1", "::1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "fc00::1",
    "100.64.0.1", "169.254.169.254", "fe80::1", "0.0.0.0", "::", "224.0.0.1",
    "ff02::1", "255.255.255.255", "192.0.2.1", "2001:db8::1", "::ffff:127.0.0.1",
    "64:ff9b::7f00:1", "64:ff9b:1::a00:1", "2002:7f00:1::1", "2001::1", "4000::1",
])
async def test_public_hostname_cannot_resolve_to_non_global_or_transition_addresses(answer):
    resolver = Resolver([answer])
    async with ProviderConnector(resolver=resolver) as connector:
        with pytest.raises(ProviderAddressError, match="^provider_address_policy_refused$"):
            await connector._resolve_host("configured.example", 443)
    assert resolver.calls == 1


@pytest.mark.parametrize("addresses", [
    ["8.8.8.8", "127.0.0.1"], ["2606:4700:4700::1111", "::ffff:10.0.0.1"],
    [], ["8.8.8.8"] * 65,
])
async def test_entire_dns_answer_is_checked_not_just_first_usable_address(addresses):
    async with ProviderConnector(resolver=Resolver(addresses)) as connector:
        with pytest.raises(ProviderAddressError):
            await connector._resolve_host("configured.example", 443)


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "localhost."])
async def test_localhost_must_really_resolve_to_loopback(host):
    async with ProviderConnector(resolver=Resolver(["8.8.8.8"])) as connector:
        with pytest.raises(ProviderAddressError):
            await connector._resolve_host(host, 80)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "::1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "100.64.0.1",
    "fc00::1", "8.8.8.8", "2606:4700:4700::1111", "::ffff:127.0.0.1",
])
async def test_explicit_ordinary_unicast_addresses_remain_available_without_dns(address):
    resolver = Resolver([])
    async with ProviderConnector(resolver=resolver) as connector:
        assert (await connector._resolve_host(address, 1234))[0]["host"] == address
    assert resolver.calls == 0


@pytest.mark.parametrize("address", [
    "169.254.169.254", "fe80::1", "0.0.0.0", "::", "224.0.0.1", "ff02::1",
    "0.1.2.3", "192.0.2.1", "2001:db8::1", "64:ff9b::7f00:1", "2002:7f00:1::1", "4000::1",
])
async def test_special_addresses_are_not_allowed_even_as_explicit_literals(address):
    async with ProviderConnector() as connector:
        with pytest.raises(ProviderAddressError):
            await connector._resolve_host(address, 80)


@pytest.mark.parametrize("host", [
    "ⓛⓞⓒⓐⓛⓗⓞⓢⓣ", "１２７.０.０.１", "127.1", "2130706433", "01.2.3.4",
    "foo/bar", "foo%00bar", "foo_bar", "a..b", "-a.example", "a.example\n", "K.example",
])
def test_noncanonical_or_compatibility_hostnames_fail_before_io(host):
    with pytest.raises(ProviderAddressError):
        validate_provider_host(host)
    if "/" in host:
        return  # In a URL this is a path, not part of its parsed hostname.
    with pytest.raises(ProviderError):
        validate_inference_config({"provider": "openai-compatible", "model": "synthetic",
                                   "base_url": "http://" + host + "/v1", "allow_remote": True,
                                   "allow_insecure_http": True})


@pytest.mark.parametrize("host", ["xn--bcher-kva.example", "xn--hxajbheg2az3al.example",
                                  "xn--mgbh0fb.example"])
async def test_ascii_idna_names_keep_their_identity_and_can_use_global_answers(host):
    provider = validate_inference_config({"provider": "openai-compatible", "model": "synthetic",
        "base_url": "https://" + host + "/v1", "allow_remote": True})
    assert provider.base_url == "https://" + host + "/v1"
    async with ProviderConnector(resolver=Resolver(["8.8.8.8"])) as connector:
        assert (await connector._resolve_host(host, 443))[0]["hostname"] == host


async def test_dns_results_are_transport_records_not_a_second_lookup_or_mutable_alias():
    resolver = Resolver(["8.8.8.8", "2606:4700:4700::1111"])
    async with ProviderConnector(resolver=resolver, use_dns_cache=False) as connector:
        records = await connector._resolve_host("configured.example", 443)
        assert resolver.calls == 1 and [r["host"] for r in records] == resolver.addresses
        resolver.records[0]["host"] = "127.0.0.1"
        assert records[0]["host"] == "8.8.8.8"
        resolver.addresses = ["127.0.0.1"]
        with pytest.raises(ProviderAddressError):
            await connector._resolve_host("configured.example", 443)
        assert resolver.calls == 2


async def test_catalog_baseline_reaches_internal_server_but_guarded_client_does_not(monkeypatch):
    received = []
    async def endpoint(request):
        received.append(request.headers.get("Authorization"))
        return web.json_response({"object": "list", "data": [{"id": "synthetic-model"}]})
    app = web.Application()
    app.router.add_get("/v1/models", endpoint)
    async with TestServer(app) as server:
        base = f"http://configured.example:{server.port}/v1"
        provider = validate_inference_config({"provider": "openai-compatible", "model": "fixture",
            "base_url": base, "allow_remote": True, "allow_insecure_http": True,
            "api_key": "SYNTHETIC-ROLE-KEY"})
        # The former ordinary TCPConnector performs the second network leg to
        # the internal DNS answer, with the selected provider's credential.
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                resolver=Resolver(["127.0.0.1"])), trust_env=False) as session:
            async with session.get(base + "/models", headers=provider.headers) as response:
                assert response.status == 200
        assert received == ["Bearer SYNTHETIC-ROLE-KEY"]
        monkeypatch.setattr(model_catalog, "ProviderConnector", lambda: ProviderConnector(
            resolver=Resolver(["127.0.0.1"])))
        with pytest.raises(CatalogError):
            await fetch_catalog(provider)
        assert received == ["Bearer SYNTHETIC-ROLE-KEY"]


async def test_real_localhost_transport_and_model_host_header_are_preserved():
    received = []
    async def endpoint(request):
        received.append(request.host)
        return web.Response(text="ok")
    app = web.Application()
    app.router.add_get("/", endpoint)
    async with TestServer(app) as server:
        async with aiohttp.ClientSession(connector=ProviderConnector(
                resolver=Resolver(["127.0.0.1"])), trust_env=False) as session:
            async with session.get(f"http://localhost:{server.port}/") as response:
                assert await response.text() == "ok"
        assert received == [f"localhost:{server.port}"]


@pytest.mark.parametrize("kind", ["embedding", "worker"])
async def test_embedding_and_worker_transports_refuse_internal_dns_before_request(
        tmp_path, monkeypatch, kind):
    hits = []
    async def endpoint(request):
        hits.append(await request.read())
        return web.json_response({})
    app = web.Application()
    app.router.add_post('/v1/embeddings', endpoint)
    app.router.add_post('/v1/chat/completions', endpoint)
    async with TestServer(app) as server:
        base = f"http://configured.example:{server.port}/v1"
        def factory():
            return ProviderConnector(resolver=Resolver(["127.0.0.1"]))
        if kind == "embedding":
            monkeypatch.setattr(search, "ProviderConnector", factory)
            service = search.EmbeddingService({"backend": "http", "base_url": base,
                "model": "intfloat/multilingual-e5-small", "allow_remote": True,
                "allow_insecure_http": True})
            try:
                with pytest.raises(search.EmbeddingError, match="ProviderAddressError"):
                    await service.encode_queries(["SYNTHETIC-PRIVATE-QUERY"])
            finally:
                await service.close()
        else:
            monkeypatch.setattr(worker, "ProviderConnector", factory)
            config = worker_config(type('Fixture', (), {'origin': str(server.make_url('')).rstrip('/')})())
            service = worker.JobProcessor(config, tmp_path, camera_registry=Registry())
            try:
                await service.start()
                with pytest.raises(aiohttp.ClientError):
                    await service._inference_session.post(base + '/chat/completions',
                        headers={"Authorization": "Bearer SYNTHETIC-ROLE-KEY"},
                        json={"synthetic": "SYNTHETIC-PRIVATE-MEDIA"})
            finally:
                await service.stop()
        assert hits == []


async def test_cancellation_during_resolution_is_not_swallowed_as_a_policy_error():
    entered = asyncio.Event()
    class SlowResolver(Resolver):
        async def resolve(self, host, port=0, family=socket.AF_INET):
            entered.set()
            await asyncio.Event().wait()
    async with ProviderConnector(resolver=SlowResolver([]), use_dns_cache=False) as connector:
        task = asyncio.create_task(connector._resolve_host("configured.example", 443))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
