"""Failed acknowledgements cannot prove that a model change/undo did not commit."""

import json

import pytest

from aikey.admin_audit import AuditLog
from aikey.config_store import ConfigurationStoreError, RevisionConflict
from aikey.aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from test_control_site_secrets import signed_in, provider_form, SECRET


@pytest.mark.parametrize("profile", ["aikey", "aiport"])
@pytest.mark.parametrize("operation", ["model", "rollback"])
@pytest.mark.parametrize("failure", ["os", "store"])
async def test_committed_model_or_undo_is_reported_uncertain_without_retry_or_key_deletion(
        tmp_path, monkeypatch, profile, operation, failure):
    site, server, client, cookie, _ = await signed_in(tmp_path)
    try:
        store = site.aikey if profile == "aikey" else site.aiports["aiport"]
        original = store.path.read_bytes()
        fields = provider_form(site, cookie, profile=profile, revision=store.snapshot().revision)
        if profile == "aiport":
            fields.update(threshold="0.8", smart_types="person", max_requests_per_hour="")
        response = await client.post("/provider", data=fields,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        configured = json.loads(store.path.read_text())
        expected = json.loads(original) if operation == "rollback" else configured
        role = "caption" if profile == "aikey" else "detection"
        if operation == "model":
            selected = (expected["inference"] if profile == "aikey"
                        else expected["live_pool_detector"]["provider_config"])
            selected["model"] = "synthetic-next-model"
        keys = {p.name: p.read_bytes() for p in store.path.parent.glob("provider-key-*")}
        assert keys
        site.audit = AuditLog(tmp_path / "audit.jsonl")
        method = "apply_role_model" if operation == "model" else "rollback_provider"
        real = getattr(store, method)
        error = OSError if failure == "os" else (
            ConfigurationStoreError if profile == "aikey" else AiPortConfigurationError)
        calls = []

        def fail_after_commit(*args):
            calls.append(1)
            real(*args)
            raise error("synthetic-private-disk-detail")

        monkeypatch.setattr(store, method, fail_after_commit)
        data = {"csrf": site.security.csrf_token(cookie), "profile": profile,
                "revision": store.snapshot().revision}
        if operation == "model":
            data.update(role=role, model="synthetic-next-model")
        endpoint = "/role-model" if operation == "model" else "/provider/rollback"
        response = await client.post(endpoint, data=data,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        body = await response.text()
        # Read-back establishes a real persisted change despite the failed ack.
        assert json.loads(store.path.read_text()) == expected
        assert {p.name: p.read_bytes() for p in store.path.parent.glob("provider-key-*")} == keys
        assert calls == [1]
        assert response.status == 200 and "outcome uncertain" in body
        assert "Nothing was changed" not in body and "Reload settings" in body
        assert SECRET not in body and "synthetic-private-disk-detail" not in body
        record = site.audit.recent()[0]
        assert record["result"] == "uncertain" and record["profile"] == profile
        assert SECRET not in site.audit.path.read_text()
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("operation", ["model", "rollback"])
@pytest.mark.parametrize("profile", ["aikey", "aiport"])
async def test_proven_conflict_stays_a_refusal_and_preserves_bytes(
        tmp_path, monkeypatch, profile, operation):
    site, server, client, cookie, _ = await signed_in(tmp_path)
    try:
        store = site.aikey if profile == "aikey" else site.aiports["aiport"]
        method = "apply_role_model" if operation == "model" else "rollback_provider"
        error = RevisionConflict if profile == "aikey" else AiPortRevisionConflict
        before = store.path.read_bytes()

        def conflict(*args):
            raise error("synthetic conflict")

        monkeypatch.setattr(store, method, conflict)
        data = {"csrf": site.security.csrf_token(cookie), "profile": profile,
                "revision": store.snapshot().revision, "role": "caption", "model": "next-model"}
        endpoint = "/role-model" if operation == "model" else "/provider/rollback"
        response = await client.post(endpoint, data=data,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        body = await response.text()
        assert ("Model not saved" if operation == "model" else "Rollback not applied") in body
        assert "outcome uncertain" not in body and store.path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("operation", ["model", "rollback"])
@pytest.mark.parametrize("profile", ["aikey", "aiport"])
async def test_unclassified_io_failure_before_write_is_not_claimed_as_a_commit(
        tmp_path, monkeypatch, profile, operation):
    site, server, client, cookie, _ = await signed_in(tmp_path)
    try:
        store = site.aikey if profile == "aikey" else site.aiports["aiport"]
        before = store.path.read_bytes()
        method = "apply_role_model" if operation == "model" else "rollback_provider"

        def unavailable(*args):
            raise OSError("synthetic-private-read-failure")

        monkeypatch.setattr(store, method, unavailable)
        data = {"csrf": site.security.csrf_token(cookie), "profile": profile,
                "revision": store.snapshot().revision, "role": "caption", "model": "next-model"}
        response = await client.post(
            "/role-model" if operation == "model" else "/provider/rollback", data=data,
            headers={"Origin": site.origin}, allow_redirects=False)
        body = await response.text()
        assert "outcome uncertain" in body and "Nothing was changed" not in body
        assert "synthetic-private-read-failure" not in body
        assert store.path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("profile", ["aikey", "aiport"])
async def test_later_model_change_survives_an_earlier_uncertain_ack(tmp_path, monkeypatch, profile):
    site, server, client, cookie, _ = await signed_in(tmp_path)
    try:
        store = site.aikey if profile == "aikey" else site.aiports["aiport"]
        fields = provider_form(site, cookie, profile=profile, revision=store.snapshot().revision)
        if profile == "aiport":
            fields.update(threshold="0.8", smart_types="person", max_requests_per_hour="")
        assert (await client.post("/provider", data=fields,
                                  headers={"Origin": site.origin}, allow_redirects=False)).status == 303
        role = "caption" if profile == "aikey" else "detection"
        real = store.apply_role_model
        calls = []

        def changed_then_superseded(*args):
            calls.append(1)
            real(*args)
            real(store.snapshot().revision, role, "synthetic-later-model")
            raise OSError("synthetic-private-ack-failure")

        monkeypatch.setattr(store, "apply_role_model", changed_then_superseded)
        response = await client.post("/role-model", data={
            "csrf": site.security.csrf_token(cookie), "profile": profile,
            "revision": store.snapshot().revision, "role": role, "model": "synthetic-first-model",
        }, headers={"Origin": site.origin}, allow_redirects=False)
        assert "outcome uncertain" in await response.text()
        assert calls == [1]
        assert store.role_models()[role]["model"] == "synthetic-later-model"
    finally:
        await client.close()
        await server.close()
