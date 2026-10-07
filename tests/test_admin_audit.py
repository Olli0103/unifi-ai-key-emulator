"""Private, bounded audit trail of control-site actions (#13). Synthetic state."""

import json
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey.admin_audit import AuditLog
from aikey.admin_security import AdminSecurity
from aikey.control_site import ControlSite, _COOKIE
from test_control_site import fixture

PASSWORD = "synthetic-admin-passphrase"
KEY = "-".join(("synthetic", "audit", "provider", "credential"))


def test_entries_are_allowlisted_private_and_newest_first(tmp_path):
    clock = iter(range(1000, 2000))
    log = AuditLog(tmp_path / "audit.jsonl", clock=lambda: next(clock))
    assert log.record("login", "signed_in")
    assert log.record("provider_save", "saved", profile="aiport:nas-2")
    assert log.record("provider_rollback", "refused", profile="../../etc")      # not a profile
    assert oct((tmp_path / "audit.jsonl").stat().st_mode)[-3:] == "600"
    assert log.recent() == [
        {"at": 1002, "action": "provider_rollback", "result": "refused", "profile": "unknown"},
        {"at": 1001, "action": "provider_save", "result": "saved", "profile": "aiport:nas-2"},
        {"at": 1000, "action": "login", "result": "signed_in"}]
    for action, result in (("delete_everything", "ok"), ("login", "Signed In!"), ("login", "x" * 50)):
        with pytest.raises(ValueError):
            log.record(action, result)


def test_the_log_is_bounded_and_skips_malformed_lines(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", max_bytes=400)
    for _ in range(40):
        log.record("provider_save", "saved", profile="aikey")
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["audit.jsonl", "audit.jsonl.1"]                          # one rotation file
    assert sum(p.stat().st_size for p in tmp_path.iterdir()) <= 2 * 400
    with open(tmp_path / "audit.jsonl", "ab") as handle:
        handle.write(b"{not json}\n" + b"x" * 900 + b"\n"
                     + json.dumps({"at": 1, "action": "evil", "result": "x"}).encode() + b"\n")
    assert all(entry["action"] == "provider_save" for entry in log.recent(100))


def test_a_symlinked_log_is_never_written_through(tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text("")
    (tmp_path / "audit.jsonl").symlink_to(target)
    log = AuditLog(tmp_path / "audit.jsonl")
    assert log.record("login", "signed_in") is False and log.failures == 1
    assert target.read_text() == ""


async def test_every_control_site_action_is_audited_without_secrets(tmp_path):
    key_config, port_config = fixture(tmp_path)
    data = json.loads(key_config.read_text())
    data["runtime"]["state_dir"] = "/state"
    key_config.write_text(json.dumps(data) + "\n")
    audit_path = tmp_path / "admin" / "admin-audit.jsonl"
    audit_path.parent.mkdir(mode=0o700)
    site = ControlSite(key_config, port_config, signing_key=b"a" * 32,
                       password_record=AdminSecurity.create_password_record(PASSWORD), port=8765,
                       aiport_runtime_state_dir=Path("/state"), audit_log=audit_path)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"a" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    origin = {"Origin": site.origin}
    try:
        await client.post("/login", data={"password": "wrong-password"}, headers=origin)
        await client.post("/login", data={"password": PASSWORD}, headers=origin, allow_redirects=False)
        cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        csrf = site.security.csrf_token(cookie)
        form = {"csrf": csrf, "profile": "aikey", "revision": site.aikey.snapshot().revision,
                "provider": "openai", "model": "synthetic-vision",
                "base_url": "https://api.openai.com/v1", "allow_remote": "on",
                "max_output_tokens": "256", "api_key": KEY}
        await client.post("/provider", data={**form, "csrf": "forged"}, headers=origin)
        await client.post("/provider", data={**form, "revision": "0" * 64}, headers=origin)
        await client.post("/provider", data=form, headers=origin, allow_redirects=False)
        await client.post("/provider/rollback", data={
            "csrf": csrf, "profile": "aikey", "revision": site.aikey.snapshot().revision},
            headers=origin, allow_redirects=False)
        page = await (await client.get("/audit")).text()
        await client.post("/logout", data={"csrf": csrf}, headers=origin, allow_redirects=False)
        entries = [(e["action"], e["result"], e.get("profile")) for e in site.audit.recent(20)]
        assert entries == [
            ("logout", "signed_out", None),
            ("provider_rollback", "rolled_back", "aikey"),
            ("provider_save", "saved", "aikey"),
            ("provider_save", "rejected", "aikey"),
            ("provider_save", "forbidden", None),
            ("login", "signed_in", None),
            ("login", "invalid_credentials", None)]
        raw = audit_path.read_text()
        for secret in (PASSWORD, "wrong-password", KEY, "provider-key-", "127.0.0.1", str(tmp_path)):
            assert secret not in raw and secret not in page
        assert "provider save" in page and "rolled_back" in page and "<form" not in page.split("</h1>")[1]
        assert (await client.post("/audit", headers=origin)).status == 405
    finally:
        await client.close()
        await server.close()


async def test_without_a_log_the_page_says_so(tmp_path):
    key_config, port_config = fixture(tmp_path)
    site = ControlSite(key_config, port_config, signing_key=b"b" * 32,
                       password_record=AdminSecurity.create_password_record(PASSWORD), port=8765)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"b" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        assert (await client.get("/audit", allow_redirects=False)).status == 303
        await client.post("/login", data={"password": PASSWORD}, headers={"Origin": site.origin},
                          allow_redirects=False)
        assert "No audit log is configured" in await (await client.get("/audit")).text()
    finally:
        await client.close()
        await server.close()
