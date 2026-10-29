"""No secret reaches the DOM, history, storage or logs after a save (#17).

A key submitted to the control site must not appear in any response body, a
redirect Location (browser history), or a re-rendered form, on success or on
a rejected save. Pages run no script (CSP default-src 'none'), so nothing can
be written to localStorage or a console, and they are never cached.
"""

import json
import logging
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey.admin_security import AdminSecurity
from aikey.control_site import ControlSite, _COOKIE
from test_control_site import fixture

PASSWORD = "synthetic-admin-passphrase"
SECRET = "-".join(("synthetic", "never", "issued", "provider", "credential"))


async def signed_in(tmp_path):
    key_config, port_config = fixture(tmp_path)
    key_data = json.loads(key_config.read_text())
    key_data["runtime"]["state_dir"] = "/state"
    key_config.write_text(json.dumps(key_data) + "\n")
    site = ControlSite(key_config, port_config, signing_key=b"q" * 32,
                       password_record=AdminSecurity.create_password_record(PASSWORD),
                       port=8765, aiport_runtime_state_dir=Path("/state"))
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"q" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    await client.post("/login", data={"password": PASSWORD}, headers={"Origin": site.origin},
                      allow_redirects=False)
    cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
    return site, server, client, cookie, key_config


def provider_form(site, cookie, **changes):
    return {"csrf": site.security.csrf_token(cookie), "profile": "aikey",
            "revision": site.aikey.snapshot().revision, "provider": "openai",
            "model": "synthetic-vision", "base_url": "https://api.openai.com/v1",
            "allow_remote": "on", "max_output_tokens": "256", "api_key": SECRET, **changes}


def key_files(key_config):
    return sorted(p.name for p in key_config.parent.glob("provider-key-*"))


def assert_private_page(response, body):
    policy = response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in policy and "script-src" not in policy
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "<script" not in body.lower() and "localstorage" not in body.lower()
    assert SECRET not in body


async def test_a_saved_key_never_appears_in_pages_history_or_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    site, server, client, cookie, key_config = await signed_in(tmp_path)
    try:
        saved = await client.post("/provider", data=provider_form(site, cookie),
                                  headers={"Origin": site.origin}, allow_redirects=False)
        assert saved.status == 303
        location = saved.headers["Location"]
        assert SECRET not in location and "api_key" not in location        # browser history
        assert len(key_files(key_config)) == 1                              # stored server-side
        for path in ("/", location, "/login"):
            response = await client.get(path)
            assert_private_page(response, await response.text())
        page = await (await client.get("/")).text()
        # The key field is never pre-filled.
        for field in page.split("name='api_key'")[1:]:
            assert "value=" not in field.split(">")[0]
        assert SECRET not in caplog.text
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("change", [
    {"revision": "0" * 64},                                   # another session saved first
    {"max_output_tokens": "not-a-number"},
    {"base_url": "https://not-openai.example/v1"},            # official host required
])
async def test_a_rejected_save_echoes_nothing_and_leaves_no_key_file(tmp_path, caplog, change):
    caplog.set_level(logging.DEBUG)
    site, server, client, cookie, key_config = await signed_in(tmp_path)
    try:
        before = key_config.read_bytes()
        rejected = await client.post("/provider", data=provider_form(site, cookie, **change),
                                     headers={"Origin": site.origin}, allow_redirects=False)
        body = await rejected.text()
        assert rejected.status == 200 and "Settings not saved" in body
        assert_private_page(rejected, body)
        assert "value=" not in body                               # no form re-rendered with input
        assert key_files(key_config) == [] and key_config.read_bytes() == before
        assert SECRET not in caplog.text
    finally:
        await client.close()
        await server.close()


async def test_a_replaced_key_keeps_the_previous_file_for_rollback(tmp_path):
    """Documented trade-off: the archived revision still references the old key."""
    site, server, client, cookie, key_config = await signed_in(tmp_path)
    try:
        await client.post("/provider", data=provider_form(site, cookie),
                          headers={"Origin": site.origin}, allow_redirects=False)
        first = key_files(key_config)
        replacement = "-".join(("synthetic", "replacement", "credential", "value"))
        await client.post("/provider", data=provider_form(site, cookie, api_key=replacement),
                          headers={"Origin": site.origin}, allow_redirects=False)
        both = key_files(key_config)
        assert len(both) == 2 and first[0] in both
        live = Path(json.loads(key_config.read_text())["inference"]["api_key_file"]).name
        assert live != first[0]
        assert (key_config.parent / live).read_text().strip() == replacement
    finally:
        await client.close()
        await server.close()
