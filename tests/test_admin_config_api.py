"""Versioned JSON administration uses the existing auth/store boundary."""

import asyncio
import json
import threading

import pytest

from aikey.config_store import ConfigurationStoreError
from aikey.admin_audit import AuditLog
from test_admin_mutation_limit import fill
from test_control_site_secrets import signed_in

ROOT = "/api/admin/v1"


def headers(site, cookie):
    return {"Origin": site.origin, "X-CSRF-Token": site.security.csrf_token(cookie)}


async def test_profile_config_and_role_registry_are_authenticated_private_saved_views(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        original = path.read_bytes()
        response = await client.get(ROOT + "/session")
        assert response.status == 200
        assert (await response.json())["csrf_token"] == site.security.csrf_token(cookie)
        response = await client.get(ROOT + "/profiles")
        assert response.status == 200
        result = await response.json()
        assert result["schema"] == "aikey-admin/1"
        assert [r["id"] for r in result["profiles"]] == ["aikey", "aiport"]
        response = await client.get(ROOT + "/profiles/aikey/runtime")
        assert response.status == 200
        runtime = await response.json()
        assert runtime["observation"] == "not_configured" and runtime["runtime"] is None
        assert runtime["native_operations"]["recording"] == "controller_owned_not_measured"
        response = await client.get(ROOT + "/profiles/aikey/runtime?url=https://127.0.0.1/healthz")
        assert response.status == 400
        for profile in ("aikey", "aiport"):
            response = await client.get(ROOT + f"/profiles/{profile}/configuration")
            assert response.status == 200
            data = await response.json()
            assert data["profile"] == profile and data["activation"] == "saved_not_active_runtime"
            assert len(data["revision"]) == 64
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Referrer-Policy"] == "no-referrer"
            assert "management-password" not in json.dumps(data)
            assert "database-password" not in json.dumps(data)
            assert len(data["roles"]["roles"]) == (9 if profile == "aikey" else 4)
            response = await client.get(ROOT + f"/profiles/{profile}/identity")
            assert response.status == 200
            identity = (await response.json())["identity"]
            assert identity["persisted_adoption"] is None
            assert identity["live_connection_checked"] is False
        assert path.read_bytes() == original
        response = await client.get(ROOT + "/profiles/unknown/configuration")
        assert response.status == 404 and (await response.json())["error"] == "unknown_profile"
        await client.post("/logout", data={"csrf": site.security.csrf_token(cookie)},
                          headers={"Origin": site.origin}, allow_redirects=False)
        response = await client.get(ROOT + "/profiles")
        assert response.status == 401 and "profiles" not in await response.text()
    finally:
        await client.close()
        await server.close()


async def test_api_posts_share_the_browser_quota_before_any_store_access(tmp_path, monkeypatch):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        before = path.read_bytes()
        await fill(site, client, cookie)
        def forbidden(*args):
            pytest.fail("Full shared quota must refuse before store access")
        monkeypatch.setattr(site.aikey, "preview", forbidden)
        for operation in ("preview", "apply", "rollback"):
            response = await client.post(ROOT + "/profiles/aikey/" + operation,
                json={}, headers=headers(site, cookie))
            assert response.status == 429
            assert "Retry-After" in response.headers
        assert path.read_bytes() == before
        assert (await client.get(ROOT + "/profiles")).status == 200
    finally:
        await client.close()
        await server.close()


async def test_revocation_during_preview_refuses_before_apply(tmp_path, monkeypatch):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        before = path.read_bytes()
        revision = site.aikey.snapshot().revision
        patch = {"device": {"name": "Fictional pending"}}
        real = site.aikey.preview
        preview = real(revision, patch)
        def revoke(*args):
            result = real(*args)
            site.security.revoke(cookie)
            return result
        monkeypatch.setattr(site.aikey, "preview", revoke)
        response = await client.post(ROOT + "/profiles/aikey/apply", json={
            "revision": revision, "patch": patch, "preview_revision": preview.resulting_revision},
            headers=headers(site, cookie))
        assert response.status == 403 and path.read_bytes() == before
        assert not site.aikey.history_dir.exists()
    finally:
        await client.close()
        await server.close()


async def test_cancelled_waiter_does_not_cancel_commit_or_final_audit(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    entered, release = threading.Event(), threading.Event()
    site.audit = AuditLog(tmp_path / "private-audit.jsonl")
    try:
        revision = site.aikey.snapshot().revision
        patch = {"device": {"name": "Fictional eventual saved"}}
        def slow_apply():
            entered.set()
            assert release.wait(5)
            return site.aikey.apply(revision, patch)
        waiter = asyncio.create_task(site.admin_api.file_operation(site.admin_api.commit,
            "aikey", "config_apply", slow_apply, ()))
        assert await asyncio.to_thread(entered.wait, 5)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert site.admin_api.operations
        release.set()
        await site.admin_api.drain(None)
        assert not site.admin_api.operations
        assert json.loads(path.read_text())["device"]["name"] == "Fictional eventual saved"
        assert site.audit.recent() == [{"at": site.audit.recent()[0]["at"],
            "action": "config_apply", "result": "saved", "profile": "aikey"}]
        assert "Fictional eventual saved" not in site.audit.path.read_text()
    finally:
        release.set()
        await site.admin_api.drain(None)
        await client.close()
        await server.close()


async def test_preview_apply_and_exact_rollback_preserve_other_profile_and_identity(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        previous, port = path.read_bytes(), site.aiport.path.read_bytes()
        revision = site.aikey.snapshot().revision
        payload = {"revision": revision, "patch": {"device": {"name": "Fictional changed name"}}}
        response = await client.post(ROOT + "/profiles/aikey/preview", json=payload,
                                     headers=headers(site, cookie))
        assert response.status == 200
        preview = await response.json()
        assert preview["restart_required"] and preview["changed_fields"] == ["device.name"]
        assert preview["current_jobs"] == "unchanged_until_explicit_processor_restart"
        assert path.read_bytes() == previous
        response = await client.post(ROOT + "/profiles/aikey/apply", json={**payload,
            "preview_revision": preview["resulting_revision"]}, headers=headers(site, cookie))
        assert response.status == 200
        applied = await response.json()
        assert applied["revision"] == preview["resulting_revision"]
        assert json.loads(path.read_text())["device"]["mac"] == json.loads(previous)["device"]["mac"]
        assert site.aiport.path.read_bytes() == port
        response = await client.post(ROOT + "/profiles/aikey/rollback", json={
            "revision": applied["revision"], "target_revision": revision}, headers=headers(site, cookie))
        assert response.status == 200 and path.read_bytes() == previous
        assert site.aiport.path.read_bytes() == port
    finally:
        await client.close()
        await server.close()


async def test_port_policy_api_preserves_pairings_and_key_and_rolls_back_exactly(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        key_before, port_before = path.read_bytes(), site.aiport.path.read_bytes()
        revision = site.aiport.snapshot().revision
        patch = {"live_pool_detector": {"threshold": .76}}
        payload = {"revision": revision, "patch": patch}
        response = await client.post(ROOT + "/profiles/aiport/preview", json=payload,
                                     headers=headers(site, cookie))
        assert response.status == 200
        preview = await response.json()
        assert preview["changed_fields"] == ["live_pool_detector.threshold"]
        assert site.aiport.path.read_bytes() == port_before
        response = await client.post(ROOT + "/profiles/aiport/apply", json={**payload,
            "preview_revision": preview["resulting_revision"]}, headers=headers(site, cookie))
        assert response.status == 200
        applied = await response.json()
        assert applied["revision"] == site.aiport.snapshot().revision
        assert path.read_bytes() == key_before
        assert json.loads(site.aiport.path.read_text())["paired_streams"] == json.loads(port_before)["paired_streams"]
        response = await client.post(ROOT + "/profiles/aiport/rollback", json={
            "revision": applied["revision"], "target_revision": revision}, headers=headers(site, cookie))
        assert response.status == 200
        assert site.aiport.path.read_bytes() == port_before and path.read_bytes() == key_before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("bad", ["csrf", "origin", "revision", "preview_revision", "non_ascii_preview", "secret", "identity", "unknown_field", "duplicate_json"])
async def test_invalid_apply_is_refused_without_changes_or_echo(tmp_path, bad):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        before = path.read_bytes()
        original = site.aikey.snapshot().revision
        payload = {"revision": original, "patch": {"device": {"name": "Fictional changed"}}}
        preview = site.aikey.preview(original, payload["patch"])
        payload["preview_revision"] = preview.resulting_revision
        h = headers(site, cookie)
        if bad == "csrf":
            h["X-CSRF-Token"] = "invalid"
        elif bad == "origin":
            h["Origin"] = "https://untrusted.invalid"
        elif bad == "revision":
            payload["revision"] = "0" * 64
        elif bad == "preview_revision":
            payload["preview_revision"] = "0" * 64
        elif bad == "non_ascii_preview":
            payload["preview_revision"] = "ä" * 64
        elif bad == "secret":
            payload["patch"] = {"inference": {"api_key": "fictional-secret-canary"}}
        elif bad == "identity":
            payload["patch"] = {"device": {"mac": "020000000077"}}
        elif bad == "unknown_field":
            payload["retry_automatically"] = True
        if bad == "duplicate_json":
            h["Content-Type"] = "application/json"
            response = await client.post(ROOT + "/profiles/aikey/apply",
                data='{"revision":"fictional-secret-canary","revision":"duplicate"}', headers=h)
        else:
            response = await client.post(ROOT + "/profiles/aikey/apply", json=payload, headers=h)
        assert response.status in {400, 403, 409}
        assert path.read_bytes() == before
        assert "fictional-secret-canary" not in await response.text()
        assert not site.aikey.history_dir.exists()
    finally:
        await client.close()
        await server.close()


async def test_concurrent_api_applies_have_one_winner_and_no_overwrite(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        revision = site.aikey.snapshot().revision
        payloads = []
        for name in ("Fictional first", "Fictional second"):
            patch = {"device": {"name": name}}
            payloads.append({"revision": revision, "patch": patch,
                             "preview_revision": site.aikey.preview(revision, patch).resulting_revision})
        responses = await asyncio.gather(*[client.post(ROOT + "/profiles/aikey/apply", json=p,
            headers=headers(site, cookie)) for p in payloads])
        assert sorted(r.status for r in responses) == [200, 409]
        saved = json.loads(path.read_text())["device"]["name"]
        winner = (await next(r for r in responses if r.status == 200).json())["revision"]
        assert saved in {"Fictional first", "Fictional second"}
        assert site.aikey.snapshot().revision == winner
    finally:
        await client.close()
        await server.close()


async def test_acknowledgement_error_does_not_claim_rollback_or_replay(tmp_path, monkeypatch):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        before = site.aikey.snapshot().revision
        patch = {"device": {"name": "Fictional committed"}}
        preview = site.aikey.preview(before, patch)
        real, calls = site.aikey.apply, []
        def uncertain(*args):
            calls.append(True)
            real(*args)
            raise ConfigurationStoreError("fictional-secret-canary")
        monkeypatch.setattr(site.aikey, "apply", uncertain)
        response = await client.post(ROOT + "/profiles/aikey/apply", json={"revision": before,
            "patch": patch, "preview_revision": preview.resulting_revision}, headers=headers(site, cookie))
        assert response.status == 503
        body = await response.json()
        assert body["error"] == "outcome_uncertain" and body["automatic_retry"] is False
        assert len(calls) == 1 and site.aikey.snapshot().revision == preview.resulting_revision
        assert json.loads(path.read_text())["device"]["name"] == "Fictional committed"
        assert "fictional-secret-canary" not in json.dumps(body)
    finally:
        await client.close()
        await server.close()
