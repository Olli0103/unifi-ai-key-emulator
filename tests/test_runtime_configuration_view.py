"""Private HTTPS startup comparison, fresh saved read and authenticated UI."""

import hashlib
import json
import asyncio

import pytest

from aikey.admin_runtime import RuntimeObserver
from aikey.startup_configuration import observation
from test_admin_runtime_observation import key_health, target
from test_control_site_secrets import signed_in, assert_private_page


@pytest.mark.parametrize("profile", ["aikey", "aiport"])
@pytest.mark.parametrize("kind", ["match", "different", "legacy", "malformed"])
async def test_runtime_api_and_page_never_infer_a_startup_match(tmp_path, profile, kind):
    site, server, client, cookie, key = await signed_in(tmp_path)
    peer = None
    try:
        path = key if profile == "aikey" else site.aiport.path
        saved = path.read_bytes()
        original_other = (site.aiport.path if profile == "aikey" else key).read_bytes()
        loaded = hashlib.sha256(saved).hexdigest() if kind == "match" else "a" * 64
        value = key_health() if profile == "aikey" else {
            "service": "aiport-candidate", "adopted": True, "control_connected": True}
        if kind != "legacy":
            value["startup_configuration"] = observation(loaded)
        if kind == "malformed":
            value["startup_configuration"]["scope"] = "fictional-current-effective-configuration"
        peer, calls, url = await target(path.parent, value)
        site.runtime_observer = RuntimeObserver(site._backup_profiles(), {profile: url})
        reply = await client.get(f"/api/admin/v1/profiles/{profile}/runtime")
        assert reply.status == 200
        result = await reply.json()
        expected = True if kind == "match" else False if kind == "different" else None
        assert result["saved_revision_matches_runtime_startup"] is expected
        assert result["runtime_configuration_revision"] == (loaded if kind in {"match", "different"} else None)
        assert result["saved_revision"] == hashlib.sha256(saved).hexdigest()
        page = await client.get("/runtime-status")
        html = await page.text()
        assert page.status == 200
        assert_private_page(page, html)
        assert "configuration file bytes loaded at process start" in html
        assert ("Matches startup snapshot" in html) is (expected is True)
        assert ("Different from startup snapshot" in html) is (expected is False)
        assert "Unknown" in html  # The other profile has no runtime peer.
        assert str(tmp_path) not in html and "fictional-private" not in html
        assert "<button" not in html and "<form" not in html
        assert path.read_bytes() == saved
        assert (site.aiport.path if profile == "aikey" else key).read_bytes() == original_other
        assert len(calls) == 1  # Same two-second pinned observation; no extra read.
        path.write_bytes(saved + b"\n")
        result = await (await client.get(f"/api/admin/v1/profiles/{profile}/runtime")).json()
        assert result["saved_revision"] != hashlib.sha256(saved).hexdigest()
        assert result["saved_revision_matches_runtime_startup"] is (False if expected is not None else None)
        assert len(calls) == 1  # Saved bytes are fresh even inside runtime cache.
        assert (await client.get("/runtime-status?target=arbitrary")).status == 400
        await client.post("/logout", data={"csrf": site.security.csrf_token(cookie)},
                          headers={"Origin": site.origin}, allow_redirects=False)
        response = await client.get("/runtime-status", allow_redirects=False)
        assert response.status == 303 and response.headers["Location"] == "/login"
        assert (await client.get(f"/api/admin/v1/profiles/{profile}/runtime")).status == 401
    finally:
        if peer is not None:
            await peer.close()
        await client.close()
        await server.close()


@pytest.mark.parametrize("bad", [True, [], {},
    {"schema": "aikey-startup-configuration/1", "scope": "file_bytes_at_process_start", "revision": None},
    {"schema": "aikey-startup-configuration/1", "scope": "file_bytes_at_process_start", "revision": "A" * 64},
    {**observation("b" * 64), "private_extra": "fictional-secret"},
])
async def test_ambiguous_startup_claim_is_not_published(tmp_path, bad):
    value = {**key_health(), "startup_configuration": bad}
    peer, calls, url = await target(tmp_path, value)
    observer = RuntimeObserver({"aikey": ("aikey", tmp_path)}, {"aikey": url})
    try:
        result = await observer.read("aikey")
        assert result["observation"] == "unavailable" and result["runtime"] is None
        assert result["runtime_configuration_revision"] is None
        assert "fictional" not in json.dumps(result)
    finally:
        await peer.close()


@pytest.mark.parametrize("route", ["/runtime-status", "/api/admin/v1/profiles/aikey/runtime"])
async def test_session_revoked_during_observation_cannot_receive_comparison(tmp_path, route):
    site, server, client, cookie, key = await signed_in(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def read(profile):
        entered.set()
        await release.wait()
        return RuntimeObserver.unavailable()
    site.runtime_observer.read = read
    try:
        pending = asyncio.create_task(client.get(route, allow_redirects=False))
        await asyncio.wait_for(entered.wait(), 2)
        await client.post("/logout", data={"csrf": site.security.csrf_token(cookie)},
                          headers={"Origin": site.origin}, allow_redirects=False)
        release.set()
        response = await pending
        assert response.status == (303 if route == "/runtime-status" else 401)
        assert "saved_revision" not in await response.text()
    finally:
        release.set()
        await client.close()
        await server.close()
