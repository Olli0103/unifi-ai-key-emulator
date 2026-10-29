"""An AI Key can be administered without inventing an AI Port identity."""

import json

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey import control_site
from aikey.admin_security import AdminSecurity
from aikey.config import initialize
from aikey.control_site import ControlSite, _COOKIE
from test_control_site import fixture
from test_control_site_secrets import PASSWORD, assert_private_page


async def sign_in(site):
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"q" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    response = await client.post("/login", data={"password": PASSWORD},
                                 headers={"Origin": site.origin}, allow_redirects=False)
    assert response.status == 303
    cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
    return server, client, site.security.csrf_token(cookie)


@pytest.mark.asyncio
async def test_key_only_login_save_rollback_and_absent_port_refusal(tmp_path):
    path = tmp_path / "key" / "config.json"
    initialize(path, path.parent, controller_host="127.0.0.1")
    original = path.read_bytes()
    site = ControlSite(path, signing_key=b"q" * 32,
                       password_record=AdminSecurity.create_password_record(PASSWORD), port=8765)
    server, client, csrf = await sign_in(site)
    try:
        response = await client.get("/")
        page = await response.text()
        assert response.status == 200 and "AI Key" in page
        assert "No AI Port configuration is attached" in page
        assert "name='profile' value='aiport" not in page
        assert_private_page(response, page)
        response = await client.get("/state-backup-preflight")
        assert response.status == 200 and "<td>aiport" not in await response.text()
        fields = {"csrf": csrf, "profile": "aikey", "role": "caption",
                  "revision": site.aikey.snapshot().revision, "model": "synthetic-new-caption"}
        forbidden = await client.post("/role-model", data={**fields, "csrf": "wrong"},
                                      headers={"Origin": site.origin}, allow_redirects=False)
        assert forbidden.status == 403 and path.read_bytes() == original
        for profile in ("aiport", "aiport:missing"):
            absent = await client.post("/provider", data={**fields, "profile": profile},
                                       headers={"Origin": site.origin}, allow_redirects=False)
            assert absent.status == 400 and path.read_bytes() == original
        saved = await client.post("/role-model", data=fields,
                                   headers={"Origin": site.origin}, allow_redirects=False)
        assert saved.status == 303
        after = json.loads(path.read_text())
        before = json.loads(original)
        after["inference"]["model"] = before["inference"]["model"]
        assert after == before
        rolled_back = await client.post("/provider/rollback", data={"csrf": csrf,
            "profile": "aikey", "revision": site.aikey.snapshot().revision},
            headers={"Origin": site.origin}, allow_redirects=False)
        assert rolled_back.status == 303 and path.read_bytes() == original
        assert sorted(p.name for p in tmp_path.iterdir()) == ["key"]
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_named_port_can_be_managed_without_a_default_slot(tmp_path):
    key, port = fixture(tmp_path)
    site = ControlSite(key, aiport_instances={"slot-2": port}, signing_key=b"q" * 32,
                       password_record=AdminSecurity.create_password_record(PASSWORD), port=8765)
    key_before = key.read_bytes()
    server, client, csrf = await sign_in(site)
    try:
        page = await (await client.get("/")).text()
        assert "AI Port slot-2" in page and "No AI Port configuration is attached" not in page
        port_before = json.loads(port.read_text())
        fields = {"csrf": csrf, "profile": "aiport:slot-2",
                  "revision": site.aiports["aiport:slot-2"].snapshot().revision,
                  "provider": "ollama", "base_url": "http://127.0.0.1:11434",
                  "model": "synthetic-slot-model", "max_output_tokens": "128",
                  "threshold": "0.8", "smart_types": "person"}
        response = await client.post("/provider", data=fields,
                                      headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303 and key.read_bytes() == key_before
        updated = json.loads(port.read_text())
        assert updated["live_pool_detector"]["provider_config"]["model"] == "synthetic-slot-model"
        assert updated["paired_streams"] == port_before["paired_streams"]
        assert updated["mac"] == port_before["mac"]
        assert site.aiport is None and list(site.aiports) == ["aiport:slot-2"]
    finally:
        await client.close()
        await server.close()


def test_real_cli_allows_key_only_but_rejects_an_explicit_missing_port(tmp_path, monkeypatch):
    path = tmp_path / "key" / "config.json"
    initialize(path, path.parent, controller_host="127.0.0.1")
    admin = tmp_path / "admin"
    assert control_site.main(["init", "--state-dir", str(admin), "--generate"]) == 0
    calls = []

    async def no_server(site, port):
        calls.append(site)
        assert not site.aiports and site.origin == "http://127.0.0.1:8765"

    monkeypatch.setattr(control_site, "_run", no_server)
    args = ["run", "--state-dir", str(admin), "--aikey-config", str(path)]
    assert control_site.main(args) == 0 and len(calls) == 1
    original = path.read_bytes()
    assert control_site.main(args + ["--aiport-config", str(tmp_path / "missing-port/config.json")]) == 2
    assert len(calls) == 1 and path.read_bytes() == original
    assert not (tmp_path / "missing-port").exists()
