"""Signed, role-bound catalog reads use synthetic local APIs only."""

import asyncio
import json

from aiohttp import web
import pytest

from aikey import control_site as site_module
from test_control_site_secrets import signed_in, assert_private_page
from test_model_catalog import server_for


async def configured(tmp_path, handler):
    upstream = await server_for(handler)
    site, server, client, cookie, path = await signed_in(tmp_path)
    raw = json.loads(path.read_text())
    raw["inference"] = {"provider": "openai-compatible", "model": "synthetic-vision",
                        "base_url": str(upstream.make_url("/vision/v1")),
                        "api_key_file": "/state/vision-key"}
    raw["speech_to_text"] = {"provider": "openai-compatible", "model": "synthetic-speech",
                             "base_url": str(upstream.make_url("/speech/v1")),
                             "api_key_file": "/state/speech-key", "camera_ids": ["fictional-camera"]}
    path.write_text(json.dumps(raw) + "\n")
    for role, root in [("vision", path.parent), ("speech", path.parent),
                       ("detection", site.aiports["aiport"].path.parent)]:
        p = root / f"{role}-key"
        p.write_text(f"synthetic-{role}-credential")
        p.chmod(0o600)
    port = site.aiports["aiport"]
    port.apply(port.snapshot().revision, {"provider": "openai-compatible", "model": "synthetic-detection",
               "base_url": str(upstream.make_url("/detection/v1")), "api_key_file": "/state/detection-key",
               "allow_remote": False, "allow_insecure_http": False, "max_output_tokens": 256,
               "threshold": .8, "smart_types": ["person"]})
    return upstream, site, server, client, cookie, path


def form(site, cookie, role="caption", profile="aikey"):
    store = site.aikey if profile == "aikey" else site.aiports[profile]
    return {"csrf": site.security.csrf_token(cookie), "profile": profile, "role": role,
            "revision": store.snapshot().revision}


@pytest.mark.parametrize("role,profile,expected", [("caption", "aikey", "vision"),
                                                 ("speech", "aikey", "speech"),
                                                 ("detection", "aiport", "detection")])
async def test_ui_lists_only_the_selected_roles_models_and_does_not_save(tmp_path, role, profile, expected):
    seen = []

    async def handler(request):
        seen.append((request.method, request.path, request.headers.get("Authorization"), await request.read()))
        return web.json_response({"data": [{"id": "synthetic-manual-model"}]})

    upstream, site, server, client, cookie, path = await configured(tmp_path, handler)
    try:
        before = path.read_bytes(), site.aiports["aiport"].path.read_bytes()
        page = await client.get("/")
        html = await page.text()
        assert "Catalog destination:" in html and "Sends no camera media" in html
        response = await client.post("/model-catalog", data=form(site, cookie, role, profile),
                                     headers={"Origin": site.origin})
        body = await response.text()
        assert "synthetic-manual-model" in body and "No inference ran" in body
        assert seen == [("GET", f"/{expected}/v1/models", f"Bearer synthetic-{expected}-credential", b"")]
        assert "credential" not in body and "fictional-camera" not in body
        assert "api_key_file" not in body and "synthetic-vision" not in body
        assert_private_page(response, body)
        assert before == (path.read_bytes(), site.aiports["aiport"].path.read_bytes())
    finally:
        await client.close()
        await server.close()
        await upstream.close()


@pytest.mark.parametrize("failure", ["csrf", "origin", "revision", "unicode_revision", "role", "injected_destination", "expired"])
async def test_invalid_catalog_requests_never_contact_a_provider(tmp_path, failure):
    seen = []

    async def handler(request):
        seen.append(request.path)
        return web.json_response({"data": []})

    upstream, site, server, client, cookie, path = await configured(tmp_path, handler)
    try:
        fields, origin = form(site, cookie), site.origin
        if failure == "origin":
            origin = "http://untrusted.invalid"
        elif failure == "injected_destination":
            fields["base_url"] = "http://untrusted.invalid"
        elif failure == "expired":
            now = site.security._clock()
            site.security._clock = lambda: now + site.security.session_ttl + 1
        elif failure == "unicode_revision":
            fields["revision"] = "ü" * 64
        else:
            fields[failure] = "invalid"
        before = path.read_bytes()
        response = await client.post("/model-catalog", data=fields, headers={"Origin": origin})
        assert response.status in {200, 400, 403} and seen == []
        assert path.read_bytes() == before
    finally:
        await client.close()
        await server.close()
        await upstream.close()


async def test_one_inflight_request_and_cooldown_bound_catalog_reads(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        return web.json_response({"data": []})

    upstream, site, server, client, cookie, _ = await configured(tmp_path, handler)
    calls = []

    async def wait(provider):
        calls.append(1)
        entered.set()
        await release.wait()
        return {"models": [], "complete": True}

    monkeypatch.setattr(site_module, "fetch_catalog", wait)
    try:
        fields = form(site, cookie)
        first = asyncio.create_task(client.post("/model-catalog", data=fields, headers={"Origin": site.origin}))
        await asyncio.wait_for(entered.wait(), 2)
        busy = await client.post("/model-catalog", data=fields, headers={"Origin": site.origin})
        assert busy.status == 429 and calls == [1]
        release.set()
        assert (await first).status == 200
        cooldown = await client.post("/model-catalog", data=fields, headers={"Origin": site.origin})
        assert cooldown.status == 429 and calls == [1]
    finally:
        release.set()
        await client.close()
        await server.close()
        await upstream.close()


async def test_configuration_changed_during_read_is_not_presented_as_current(tmp_path, monkeypatch):
    async def handler(request):
        return web.json_response({"data": []})
    upstream, site, server, client, cookie, _ = await configured(tmp_path, handler)

    async def change(provider):
        site.aikey.apply_role_model(site.aikey.snapshot().revision, "caption", "synthetic-changed-model")
        return {"models": [{"id": "do-not-present-stale", "image_input_advertised": None,
                            "provider_reported_remote": None}]}

    monkeypatch.setattr(site_module, "fetch_catalog", change)
    try:
        response = await client.post("/model-catalog", data=form(site, cookie), headers={"Origin": site.origin})
        body = await response.text()
        assert "Model catalog unavailable" in body and "do-not-present-stale" not in body
    finally:
        await client.close()
        await server.close()
        await upstream.close()
