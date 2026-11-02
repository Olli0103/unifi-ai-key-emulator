"""Additional audio permission requires fresh named consent, never provider edits."""

import asyncio
import json
import re
from copy import deepcopy

import pytest

from aikey.config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from test_control_site_secrets import signed_in, assert_private_page
from test_role_models import key_store, digest
from test_speech_enrollment import inventory, fields


def ready(tmp_path):
    store, path = key_store(tmp_path)
    raw = json.loads(path.read_bytes())
    raw["controller"]["protect_version"] = "7.3.74"
    raw["speech_to_text"].update(language="de", local_network=True)
    path.write_text(json.dumps(raw) + "\n")
    return store, path


def test_store_preserves_all_raw_values_and_archives_exact_prior_bytes(tmp_path):
    store, path = ready(tmp_path)
    before = path.read_bytes()
    rev = store.snapshot().revision
    original = digest(tmp_path)
    preview = store.preview_speech_camera_additions(rev, ["c" * 24, "b" * 24])
    assert digest(tmp_path) == original
    assert preview.changed_fields == ("speech_to_text.camera_ids",)
    result = store.add_speech_cameras(rev, ["c" * 24, "b" * 24])
    expected = json.loads(before)
    expected["speech_to_text"]["camera_ids"] += ["b" * 24, "c" * 24]
    assert json.loads(path.read_bytes()) == expected
    assert result.revision == preview.resulting_revision
    assert (store.history_dir / (rev + ".json")).read_bytes() == before
    assert not store.provider_rollback_status()["available"]
    store.restrict_speech(result.revision, ["a" * 24])
    assert store.snapshot().configuration["speech_to_text"]["camera_ids"] == ["a" * 24]


@pytest.mark.parametrize("additions", [None, {}, (), [], ["a" * 24], ["b" * 24] * 2,
    [True], [None], ["A" * 24], ["x"], [{"id": "b" * 24}], [str(i).zfill(24) for i in range(32)]])
def test_invalid_additions_do_not_write(tmp_path, additions):
    store, _ = ready(tmp_path)
    before = digest(tmp_path)
    with pytest.raises(UnsafeConfigurationChange):
        store.add_speech_cameras(store.snapshot().revision, additions)
    assert digest(tmp_path) == before


@pytest.mark.parametrize("fault", ["stale", "missing", "legacy_id", "full"])
def test_no_initial_conversion_or_stale_regrant(tmp_path, fault):
    store, path = ready(tmp_path)
    raw = json.loads(path.read_bytes())
    if fault == "missing":
        raw.pop("speech_to_text")
    elif fault == "legacy_id":
        raw["speech_to_text"]["camera_ids"] = ["old-legacy-camera"]
    elif fault == "full":
        raw["speech_to_text"]["camera_ids"] = [f"{i:024x}" for i in range(32)]
    path.write_text(json.dumps(raw))
    rev = store.snapshot().revision
    before = digest(tmp_path)
    with pytest.raises((RevisionConflict, UnsafeConfigurationChange)):
        store.add_speech_cameras("0" * 64 if fault == "stale" else rev, ["b" * 24])
    assert digest(tmp_path) == before


async def setup(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    raw = json.loads(path.read_bytes())
    raw["controller"]["protect_version"] = "7.3.74"
    raw["speech_to_text"] = {"provider": "openai-compatible", "model": "fixture-whisper",
        "base_url": "http://127.0.0.1:8178/v1", "camera_ids": ["b" * 24], "language": "de"}
    path.write_text(json.dumps(raw) + "\n")
    report = inventory()
    reads = []
    async def read():
        reads.append(True)
        return deepcopy(report)
    site.inventory_loader = read
    return site, server, client, cookie, path, report, reads


async def preview(site, client):
    response = await client.get("/speech-camera-enrollment")
    page = await response.text()
    tokens = re.findall(r"name='add_camera' value='([^']+)'", page)
    response = await client.post("/speech-camera-enrollment/preview",
        data={**fields(page), "add_camera": tokens[0]}, headers={"Origin": site.origin})
    return response, await response.text(), tokens


def confirmation(page):
    return {**fields(page), "confirm": "yes", "acknowledge_audio": "yes", "acknowledge_cost": "yes"}


async def test_explicit_saved_addition_privacy_status_and_existing_remove_only(tmp_path):
    site, server, client, cookie, path, report, reads = await setup(tmp_path)
    try:
        before = path.read_bytes()
        page = await (await client.get("/speech-camera-enrollment")).text()
        assert "Fictional hall &lt;not markup&gt;" in page
        assert "Fictional offline" not in page and "Fictional no speech" not in page
        assert "Fictional outside" not in page  # already saved
        assert "checked" not in page and "a" * 24 not in page and "02000000F001" not in page
        response, review, _ = await preview(site, client)
        assert response.status == 200 and "http://127.0.0.1:8178/v1" in review
        assert "fixture-whisper" in review
        assert_private_page(response, review)
        assert path.read_bytes() == before
        payload = confirmation(review)
        response = await client.post("/speech-camera-enrollment/apply", data=payload,
            headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303 and response.headers["Location"] == "/speech-permissions"
        expected = json.loads(before)
        expected["speech_to_text"]["camera_ids"] += ["a" * 24]
        assert json.loads(path.read_bytes()) == expected
        saved = path.read_bytes()
        assert (await client.post("/speech-camera-enrollment/apply", data=payload,
            headers={"Origin": site.origin})).status == 409
        assert path.read_bytes() == saved
        status_response = await client.get("/api/admin/v1/speech-camera-enrollment")
        status = await status_response.text()
        assert '"saved_camera_count":2' in status.replace(" ", "")
        assert all(v not in status for v in ("a" * 24, "b" * 24, "8178", "fixture-whisper"))
        assert len(reads) >= 3
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("fault", ["name", "duplicate_name", "offline", "capability", "stale_inventory",
    "version", "duplicate_mac", "revision", "provider", "expiry", "logout", "cross_session",
    "no_audio", "no_cost", "no_confirm", "extra", "repeated"])
async def test_confirmation_refuses_changed_context_without_writes(tmp_path, fault):
    from multidict import MultiDict
    site, server, client, cookie, path, report, reads = await setup(tmp_path)
    try:
        response, review, _ = await preview(site, client)
        assert response.status == 200
        payload = confirmation(review)
        if fault == "name":
            report["cameras"][0]["name"] = "Changed microphone location"
        elif fault == "duplicate_name":
            report["cameras"][1]["name"] = report["cameras"][0]["name"]
        elif fault == "offline":
            report["cameras"][0]["state"] = "DISCONNECTED"
        elif fault == "capability":
            report["cameras"][0]["audio_detect_types"] = []
        elif fault == "stale_inventory":
            report["fetched_at"] -= 301
        elif fault == "version":
            report["protect_version"] = "7.3.70"
        elif fault == "duplicate_mac":
            report["cameras"][1]["mac"] = report["cameras"][0]["mac"]
        elif fault in {"revision", "provider"}:
            raw = json.loads(path.read_bytes())
            raw["speech_to_text"]["model"] = "later-model"
            if fault == "provider":
                raw["speech_to_text"]["base_url"] = "http://127.0.0.1:8179/v1"
            path.write_text(json.dumps(raw))
        elif fault == "expiry":
            site.admin_api.speech_camera_enrollment.clock = lambda: float("inf")
        elif fault == "logout":
            await client.post("/logout", data={"csrf": site.security.csrf_token(cookie)},
                headers={"Origin": site.origin}, allow_redirects=False)
        elif fault == "cross_session":
            from test_control_site_secrets import PASSWORD
            from aikey.control_site import _COOKIE
            from yarl import URL
            await client.post("/login", data={"password": PASSWORD}, headers={"Origin": site.origin})
            new_cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
            payload["csrf"] = site.security.csrf_token(new_cookie)
        elif fault.startswith("no_"):
            payload.pop({"no_audio": "acknowledge_audio", "no_cost": "acknowledge_cost",
                         "no_confirm": "confirm"}[fault])
        elif fault == "extra":
            payload["camera_ids"] = "a" * 24
        elif fault == "repeated":
            payload = MultiDict(payload)
            payload.add("acknowledge_audio", "yes")
        before = digest(path.parent)
        response = await client.post("/speech-camera-enrollment/apply", data=payload,
            headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status in {403, 409}
        assert digest(path.parent) == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("operation", ["preview", "apply"])
@pytest.mark.parametrize("fault", ["csrf", "origin", "query"])
async def test_authorization_and_exact_request_contract(tmp_path, operation, fault):
    site, server, client, cookie, path, report, reads = await setup(tmp_path)
    try:
        response, review, tokens = await preview(site, client)
        data = confirmation(review) if operation == "apply" else {
            "csrf": site.security.csrf_token(cookie), "revision": site.aikey.snapshot().revision,
            "add_camera": tokens[0]}
        if fault == "csrf":
            data["csrf"] = "bad"
        before = digest(path.parent)
        response = await client.post("/speech-camera-enrollment/" + operation + ("?x=1" if fault == "query" else ""),
            data=data, headers={"Origin": "http://foreign.invalid" if fault == "origin" else site.origin})
        assert response.status in {403, 409}
        assert digest(path.parent) == before
    finally:
        await client.close()
        await server.close()


async def test_concurrent_confirmation_commits_once_and_uncertainty_is_not_retried(tmp_path, monkeypatch):
    site, server, client, cookie, path, report, reads = await setup(tmp_path)
    try:
        _, review, _ = await preview(site, client)
        original = site.aikey.add_speech_cameras
        calls = []
        def lost_ack(*args):
            calls.append(True)
            original(*args)
            raise ConfigurationStoreError("fictional lost acknowledgement")
        monkeypatch.setattr(site.aikey, "add_speech_cameras", lost_ack)
        payload = confirmation(review)
        responses = await asyncio.gather(*[client.post("/speech-camera-enrollment/apply", data=payload,
            headers={"Origin": site.origin}, allow_redirects=False) for _ in range(2)])
        assert sorted(r.status for r in responses) == [409, 503]
        assert len(calls) == 1
        assert json.loads(path.read_bytes())["speech_to_text"]["camera_ids"] == ["b" * 24, "a" * 24]
        saved = digest(path.parent)
        assert (await client.post("/speech-camera-enrollment/apply", data=payload,
            headers={"Origin": site.origin})).status == 409
        assert digest(path.parent) == saved
    finally:
        await client.close()
        await server.close()


def test_provider_undo_cannot_regrant_old_scope_and_later_provider_undo_retains_additions(tmp_path):
    store, path = ready(tmp_path)
    store.apply_role_model(store.snapshot().revision, "speech", "first-provider-change")
    old = store.snapshot().revision
    store.add_speech_cameras(old, ["b" * 24])
    assert not store.provider_rollback_status()["available"]
    with pytest.raises((ConfigurationStoreError, UnsafeConfigurationChange)):
        store.rollback_provider(store.snapshot().revision)
    result = store.apply_role_model(store.snapshot().revision, "speech", "second-provider-change")
    assert store.provider_rollback_status()["available"]
    store.rollback_provider(result.revision)
    assert store.snapshot().configuration["speech_to_text"]["camera_ids"] == ["a" * 24, "b" * 24]


def test_separate_store_writers_use_one_revision(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from aikey.config_store import ConfigurationStore
    store, path = ready(tmp_path)
    revision = store.snapshot().revision
    def add(camera):
        try:
            ConfigurationStore(path).add_speech_cameras(revision, [camera])
            return "saved"
        except RevisionConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(add, ["b" * 24, "c" * 24]))
    assert sorted(results) == ["conflict", "saved"]
    assert len(store.snapshot().configuration["speech_to_text"]["camera_ids"]) == 2


async def test_fixed_audit_codes_and_no_identifier_or_provider_echo(tmp_path):
    from aikey.admin_audit import AuditLog
    site, server, client, cookie, path, report, reads = await setup(tmp_path)
    try:
        site.audit = AuditLog(tmp_path / "audit.jsonl")
        _, review, _ = await preview(site, client)
        response = await client.post("/speech-camera-enrollment/apply", data=confirmation(review),
            headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        audit = site.audit.path.read_text()
        assert all(v not in audit for v in ("a" * 24, "8178", "Fictional", "fixture-whisper", "speech-key"))
        assert [r["action"] for r in site.audit.recent()] == ["speech_camera_add", "speech_camera_add"]
        assert site.audit.failures == 0
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("stage", ["status", "inventory", "preview"])
async def test_logout_during_await_never_grants_audio_authority(tmp_path, monkeypatch, stage):
    site, server, client, cookie, path, report, reads = await setup(tmp_path)
    try:
        _, review, _ = await preview(site, client)
        if stage == "inventory":
            original = site.inventory_loader
            async def revoked():
                value = await original()
                site.security.revoke(cookie)
                return value
            site.inventory_loader = revoked
        else:
            name = "speech_camera_enrollment_status" if stage == "status" else "preview_speech_camera_additions"
            original = getattr(site.aikey, name)
            def revoked(*args):
                value = original(*args)
                site.security.revoke(cookie)
                return value
            monkeypatch.setattr(site.aikey, name, revoked)
        before = digest(path.parent)
        response = await client.post("/speech-camera-enrollment/apply", data=confirmation(review),
            headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 403
        assert digest(path.parent) == before
    finally:
        await client.close()
        await server.close()
