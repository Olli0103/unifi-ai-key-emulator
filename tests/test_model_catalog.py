"""Catalog requests are GET-only, bounded and separate from inference/media."""

import asyncio
import gzip
import json
import os
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey import model_catalog as catalog
from aikey.model_catalog import CatalogError, catalog_provider, fetch_catalog
from aikey.providers import VisionProvider

KEY = "-".join(("synthetic", "catalog", "credential"))


async def server_for(handler):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    return server


@pytest.mark.parametrize("provider,path", [("openai", "/v1/models"),
                                           ("openai-compatible", "/v1/models"),
                                           ("anthropic", "/v1/models"),
                                           ("ollama", "/api/tags")])
async def test_each_api_gets_only_its_own_catalog_request(provider, path):
    requests = []

    async def handler(request):
        requests.append((request.method, request.path, await request.read(), dict(request.headers)))
        if provider == "ollama":
            return web.json_response({"models": [{"model": "synthetic:local"},
                                                  {"name": "synthetic:remote", "remote_host": "https://upstream.invalid"}]})
        return web.json_response({"data": [{"id": "synthetic-model"}], "has_more": False})

    server = await server_for(handler)
    try:
        base = str(server.make_url("/" if provider == "ollama" else "/v1")).rstrip("/")
        selected = VisionProvider({"provider": provider, "model": "not-sent-to-catalog",
                                   "base_url": base, "api_key": KEY}, lab=True)
        report = await fetch_catalog(selected)
        assert report["complete"] is True and report["capability_tested"] is False
        assert len(requests) == 1
        method, actual, body, headers = requests[0]
        assert (method, actual, body) == ("GET", path, b"")
        assert "Camera-MAC" not in headers and "x-ident" not in headers
        assert headers.get("x-api-key" if provider == "anthropic" else "Authorization") == (
            KEY if provider == "anthropic" else "Bearer " + KEY)
        assert KEY not in json.dumps(report)
        if provider == "ollama":
            assert [x["provider_reported_remote"] for x in report["models"]] == [False, True]
            assert "upstream.invalid" not in json.dumps(report)
    finally:
        await server.close()


async def test_anthropic_pagination_keeps_no_cookies_and_does_not_infer_capability():
    requests = []

    async def handler(request):
        requests.append((dict(request.query), request.headers.get("Cookie")))
        if "after_id" not in request.query:
            response = web.json_response({"data": [{"id": "model-a", "capabilities": {
                "image_input": {"supported": False}}}], "has_more": True, "last_id": "model-a"})
            response.set_cookie("provider-secret", KEY)
            return response
        return web.json_response({"data": [{"id": "model-b"}], "has_more": False})

    server = await server_for(handler)
    try:
        selected = VisionProvider({"provider": "anthropic", "model": "unused", "api_key": KEY,
                                   "base_url": str(server.make_url("/v1"))}, lab=True)
        report = await fetch_catalog(selected)
        assert [m["id"] for m in report["models"]] == ["model-a", "model-b"]
        assert [m["image_input_advertised"] for m in report["models"]] == [False, None]
        assert requests == [({"limit": "100"}, None), ({"limit": "100", "after_id": "model-a"}, None)]
    finally:
        await server.close()


@pytest.mark.parametrize("reply", [
    [], {"data": "not-a-list"}, {"data": [{"id": "<script>unsafe</script>"}]},
    {"data": [{"id": "duplicate"}, {"id": "duplicate"}]},
    {"data": [], "has_more": True}, {"data": [], "has_more": "false"},
    {"data": [{"id": "model-a"}], "has_more": True, "last_id": "https://untrusted.invalid"},
    {"data": [], "next_page": "https://untrusted.invalid"},
    {"data": [], "links": {"next": "https://untrusted.invalid"}},
])
async def test_malformed_or_partial_catalogs_are_not_presented_as_complete(reply):
    async def handler(request):
        return web.json_response(reply)
    server = await server_for(handler)
    try:
        selected = VisionProvider({"provider": "openai-compatible", "model": "unused",
                                   "base_url": str(server.make_url("/v1"))})
        with pytest.raises(CatalogError):
            await fetch_catalog(selected)
    finally:
        await server.close()


async def test_redirects_fail_without_visiting_the_other_endpoint():
    seen = []

    async def handler(request):
        seen.append(request.path)
        raise web.HTTPFound("/credential-sink")

    server = await server_for(handler)
    try:
        selected = VisionProvider({"provider": "openai-compatible", "model": "unused", "api_key": KEY,
                                   "base_url": str(server.make_url("/v1"))})
        with pytest.raises(CatalogError, match="catalog_http_failure"):
            await fetch_catalog(selected)
        assert seen == ["/v1/models"]
    finally:
        await server.close()


@pytest.mark.parametrize("limit", ["bytes", "models", "pages"])
async def test_aggregate_limits_fail_closed(limit, monkeypatch):
    if limit == "bytes":
        monkeypatch.setattr(catalog, "MAX_BYTES", 24)
    if limit == "models":
        monkeypatch.setattr(catalog, "MAX_MODELS", 1)
    if limit == "pages":
        monkeypatch.setattr(catalog, "MAX_PAGES", 1)

    async def handler(request):
        return web.json_response({"data": [{"id": "model-a"}, {"id": "model-b"}],
                                  "has_more": limit == "pages", "last_id": "model-b"})

    server = await server_for(handler)
    try:
        selected = VisionProvider({"provider": "anthropic", "model": "unused", "api_key": KEY,
                                   "base_url": str(server.make_url("/v1"))}, lab=True)
        with pytest.raises(CatalogError):
            await fetch_catalog(selected)
    finally:
        await server.close()


def test_host_container_key_mapping_is_private_and_role_scoped(tmp_path):
    key = tmp_path / "catalog-key"
    key.write_text(KEY)
    key.chmod(0o600)
    selection = {"provider": "openai", "base_url": "https://api.openai.com/v1", "model": "unused",
                 "allow_remote": True, "api_key_file": "/state/catalog-key"}
    provider = catalog_provider(selection, speech=False, host_state=tmp_path, runtime_state=Path("/state"))
    assert provider.headers == {"Authorization": "Bearer " + KEY}
    key.chmod(0o644)
    with pytest.raises(CatalogError, match="catalog_key_unavailable"):
        catalog_provider(selection, speech=False, host_state=tmp_path, runtime_state=Path("/state"))
    key.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(key)
    with pytest.raises(CatalogError, match="catalog_key_unavailable"):
        catalog_provider({**selection, "api_key_file": "/state/link"}, speech=False,
                         host_state=tmp_path, runtime_state=Path("/state"))
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(CatalogError, match="catalog_key_unavailable"):
        catalog_provider({**selection, "api_key_file": "/state/fifo"}, speech=False,
                         host_state=tmp_path, runtime_state=Path("/state"))
    key.write_text("ü-not-a-bearer-token")
    with pytest.raises(CatalogError, match="catalog_key_unavailable"):
        catalog_provider(selection, speech=False, host_state=tmp_path, runtime_state=Path("/state"))


async def test_overall_deadline_is_enforced_without_retry(monkeypatch):
    release = asyncio.Event()
    seen = []

    async def handler(request):
        seen.append(1)
        await release.wait()
        return web.json_response({"data": []})

    server = await server_for(handler)
    monkeypatch.setattr(catalog, "TOTAL_SECONDS", .05)
    try:
        selected = VisionProvider({"provider": "openai-compatible", "model": "unused",
                                   "base_url": str(server.make_url("/v1"))})
        with pytest.raises(CatalogError, match="catalog_unavailable"):
            await fetch_catalog(selected)
        assert seen == [1]
    finally:
        release.set()
        await server.close()


@pytest.mark.parametrize("reflected", [True, False])
async def test_reflected_credentials_or_compressed_replies_never_become_model_ids(reflected):
    async def handler(request):
        if reflected:
            return web.json_response({"data": [{"id": "prefix-" + KEY}]})
        assert request.headers["Accept-Encoding"] == "identity"
        return web.Response(body=gzip.compress(json.dumps({"data": [{"id": "unsafe-inflated"}]}).encode()),
                            headers={"Content-Encoding": "gzip", "Content-Type": "application/json"})

    server = await server_for(handler)
    try:
        selected = VisionProvider({"provider": "openai-compatible", "model": "unused", "api_key": KEY,
                                   "base_url": str(server.make_url("/v1"))})
        with pytest.raises(CatalogError, match="catalog_response_invalid") as failure:
            await fetch_catalog(selected)
        assert KEY not in str(failure.value) and "unsafe-inflated" not in str(failure.value)
    finally:
        await server.close()
