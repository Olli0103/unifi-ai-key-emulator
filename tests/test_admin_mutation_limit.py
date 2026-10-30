"""HTTP admission limits apply before file/provider work, across all sessions."""

import asyncio
import json

import aiohttp
import pytest
from yarl import URL

from test_control_site_secrets import signed_in, provider_form, key_files, PASSWORD, SECRET
from test_first_run_site import site_for, form, selection

LIMIT = 60


def unknown_role(site, cookie):
    return {"csrf": site.security.csrf_token(cookie), "profile": "unattached-proof",
            "role": "caption", "model": "synthetic-model", "revision": "0" * 64}


async def fill(site, client, cookie):
    for _ in range(LIMIT):
        response = await client.post("/role-model", data=unknown_role(site, cookie),
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 400
        await response.read()


async def test_burst_refuses_before_provider_key_creation_or_config_apply(tmp_path, monkeypatch):
    site, server, client, cookie, path = await signed_in(tmp_path)
    calls = []
    before, keys = path.read_bytes(), key_files(path)
    def no_apply(*args, **kwargs):
        calls.append(True)
        return site.aikey.snapshot()
    monkeypatch.setattr(site.aikey, "apply_inference", no_apply)
    try:
        await fill(site, client, cookie)
        response = await client.post("/provider", data=provider_form(site, cookie),
                                     headers={"Origin": site.origin}, allow_redirects=False)
        body = await response.text()
        assert response.status == 429
        assert 1 <= int(response.headers["Retry-After"]) <= 60
        assert response.headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        assert "No configuration change or provider request" in body
        assert SECRET not in body and not calls
        for route in ("/provider", "/provider/rollback", "/role-model", "/role-model-preview", "/speech-provider",
                      "/speech-permissions", "/aiport-permissions", "/model-catalog",
                      "/provider-probe", "/aiport-rollout", "/ocr-provider/preview", "/ocr-provider/apply",
                      "/camera-thresholds/preview", "/camera-thresholds/apply", "/camera-thresholds/undo"):
            blocked = await client.post(route, data={"csrf": site.security.csrf_token(cookie)},
                                        headers={"Origin": site.origin}, allow_redirects=False)
            assert blocked.status == 429, route
            await blocked.read()
        assert path.read_bytes() == before and key_files(path) == keys
        assert not site.aikey.history_dir.exists()
        assert len(site._mutation_times) == LIMIT
    finally:
        await client.close()
        await server.close()


async def test_new_login_and_forged_forwarding_headers_do_not_reset_shared_limit(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    other = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    before = path.read_bytes()
    try:
        await fill(site, client, cookie)
        response = await other.post(site.origin + "/login", data={"password": PASSWORD},
                                    headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        await response.read()
        second = other.cookie_jar.filter_cookies(URL(site.origin))[site.cookie_name].value
        response = await other.post(site.origin + "/role-model", data=unknown_role(site, second),
                                    headers={"Origin": site.origin, "X-Forwarded-For": "198.51.100.99",
                                             "Forwarded": "for=203.0.113.1"}, allow_redirects=False)
        assert response.status == 429
        assert path.read_bytes() == before
    finally:
        await other.close()
        await client.close()
        await server.close()


async def test_simultaneous_requests_cannot_over_admit_and_state_stays_bounded(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        async def post():
            r = await client.post("/role-model", data=unknown_role(site, cookie),
                                  headers={"Origin": site.origin}, allow_redirects=False)
            await r.read()
            return r.status
        statuses = await asyncio.gather(*(post() for _ in range(LIMIT + 15)))
        assert statuses.count(400) == LIMIT and statuses.count(429) == 15
        assert len(site._mutation_times) == LIMIT
    finally:
        await client.close()
        await server.close()


async def test_limit_expires_on_monotonic_window_without_logging_out_or_writing(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    clock = [100.0]
    site._mutation_clock = lambda: clock[0]
    before = path.read_bytes()
    try:
        await fill(site, client, cookie)
        clock[0] = 159.999
        response = await client.post("/role-model", data=unknown_role(site, cookie),
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 429 and response.headers["Retry-After"] == "1"
        clock[0] = 160.0
        response = await client.post("/role-model", data=unknown_role(site, cookie),
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 400
        assert len(site._mutation_times) == 1 and path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("fault", ["csrf", "origin", "host"])
async def test_invalid_authorization_never_consumes_authenticated_quota(tmp_path, fault):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        fields = unknown_role(site, cookie)
        headers = {"Origin": site.origin}
        if fault == "csrf":
            fields["csrf"] = "wrong"
        elif fault == "origin":
            headers["Origin"] = "https://unrelated.invalid"
        else:
            headers["Host"] = "unrelated.invalid"
        for _ in range(LIMIT + 1):
            response = await client.post("/role-model", data=fields, headers=headers, allow_redirects=False)
            assert response.status == 403
            await response.read()
        await fill(site, client, cookie)
        response = await client.post("/role-model", data=unknown_role(site, cookie),
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 429
    finally:
        await client.close()
        await server.close()


async def test_read_and_valid_signout_remain_available_after_quota_is_full(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    before = path.read_bytes()
    try:
        await fill(site, client, cookie)
        assert (await client.get("/")).status == 200
        assert (await client.get("/healthz")).status == 200
        r = await client.post("/logout", data={"csrf": site.security.csrf_token(cookie)},
                              headers={"Origin": site.origin}, allow_redirects=False)
        assert r.status == 303
        assert (await client.get("/", allow_redirects=False)).status == 303
        assert path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


async def test_exhausted_first_run_cannot_start_identity_or_file_work(tmp_path, monkeypatch):
    site, server, client, directory = await site_for(tmp_path)
    calls = []
    async def no_file_action(*args):
        calls.append(True)
        raise AssertionError("throttled request reached file action")
    monkeypatch.setattr(site, "_file_action", no_file_action)
    try:
        hidden, _ = await form(client, "/create")
        for _ in range(LIMIT):
            response = await client.post("/create", data={"csrf": hidden["csrf"]},
                                         headers={"Origin": site.origin}, allow_redirects=False)
            assert response.status == 400
            await response.read()
        response = await client.post("/create", data=selection(hidden),
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 429
        assert not calls and not directory.exists() and not site._operations
    finally:
        await client.close()
        await server.close()


async def test_throttled_catalog_does_not_contact_provider(tmp_path, monkeypatch):
    site, server, client, cookie, path = await signed_in(tmp_path)
    calls = []
    async def no_catalog(*args, **kwargs):
        calls.append(True)
        raise AssertionError("throttled request reached provider")
    monkeypatch.setattr("aikey.control_site.fetch_catalog", no_catalog)
    monkeypatch.setattr(site.aikey, "catalog_configuration", lambda *args:
                        ({"provider": "ollama", "model": "synthetic", "base_url": "http://127.0.0.1:11434"},
                         path.parent, False))
    try:
        await fill(site, client, cookie)
        response = await client.post("/model-catalog", data={"csrf": site.security.csrf_token(cookie),
                                     "profile": "aikey", "role": "caption",
                                     "revision": site.aikey.snapshot().revision},
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 429 and not calls
    finally:
        await client.close()
        await server.close()


async def test_denial_audit_is_fixed_sanitized_and_not_amplified_for_a_burst(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    from aikey.admin_audit import AuditLog
    audit = tmp_path / "mutation-audit.jsonl"
    site.audit = AuditLog(audit)
    try:
        await fill(site, client, cookie)
        for _ in range(8):
            fields = {**unknown_role(site, cookie), "model": SECRET}
            response = await client.post("/role-model", data=fields,
                                         headers={"Origin": site.origin}, allow_redirects=False)
            assert response.status == 429
            await response.read()
        rows = [json.loads(x) for x in audit.read_text().splitlines()]
        denied = [x for x in rows if x["action"] == "mutation"]
        assert len(denied) == 1 and denied[0]["result"] == "rate_limited"
        assert set(denied[0]) == {"at", "action", "result"}
        assert SECRET not in audit.read_text()
    finally:
        await client.close()
        await server.close()
