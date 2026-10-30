"""Saved Port recognition scope can shrink, never expand or revive via Undo."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import re
import threading
import time

from aiohttp.test_utils import TestClient
import aiohttp
from multidict import MultiDict
import pytest

from aikey import aiport_config_store as module
from aikey.admin_audit import AuditLog
from aikey.aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from test_control_site_secrets import PASSWORD
from test_aiport_config_store import CAMERAS, fixture, settings
from test_aiport_provider_rollback import digest
from test_control_site_secrets import signed_in, assert_private_page, SECRET


def roles(path):
    value = json.loads(path.read_text())
    value["live_speech_cameras"] = list(CAMERAS)
    value["live_face"] = {
        "cameras": list(CAMERAS),
        "detector_path": "/models/face.onnx",
        "detector_sha256": "b" * 64,
        "embedder_path": "/models/embed.onnx",
        "embedder_sha256": "c" * 64,
    }
    # Omit optional defaults: changing face scope must not insert sound defaults.
    value["live_sound"] = {
        "cameras": list(CAMERAS),
        "model_path": "/models/sound.onnx",
        "model_sha256": "d" * 64,
        "class_map_path": "/models/classes.json",
        "class_map_sha256": "e" * 64,
    }
    path.write_text(json.dumps(value) + "\n")
    return value


@pytest.mark.parametrize("feature", ["face", "sound"])
def test_only_selected_saved_camera_scope_changes_and_history_remains(tmp_path, feature):
    store, path, _ = fixture(tmp_path)
    original = roles(path)
    revision, cameras = store.recognition_permissions(feature)
    assert cameras == CAMERAS
    before = path.read_bytes()
    preview = store.preview_recognition_restriction(revision, feature, list(CAMERAS[1:]))
    assert path.read_bytes() == before and not store.history_dir.exists()
    assert preview.restart_required
    result = store.restrict_recognition(revision, feature, list(CAMERAS[1:]))
    assert result.revision == preview.resulting_revision
    expected = deepcopy(original)
    expected["live_" + feature]["cameras"] = list(CAMERAS[1:])
    assert json.loads(path.read_text()) == expected
    assert (store.history_dir / (revision + ".json")).read_bytes() == before


@pytest.mark.parametrize("feature", ["face", "sound"])
def test_disable_whole_role_preserves_other_scope_key_model_and_identity(tmp_path, feature):
    store, path, _ = fixture(tmp_path)
    original = roles(path)
    key = path.parent / "provider-key-retained"
    key.write_text(SECRET)
    revision = store.snapshot().revision
    store.restrict_recognition(revision, feature, [])
    expected = deepcopy(original)
    del expected["live_" + feature]
    assert json.loads(path.read_text()) == expected
    assert key.read_text() == SECRET
    assert store.recognition_permissions(feature)[1] is None
    with pytest.raises(AiPortConfigurationError, match="not_configured"):
        store.restrict_recognition(store.snapshot().revision, feature, [])


def test_same_set_reverse_order_is_exact_noop_and_keeps_saved_default_omissions(tmp_path):
    store, path, _ = fixture(tmp_path)
    roles(path)
    before = path.read_bytes()
    revision = store.snapshot().revision
    for feature in ("face", "sound"):
        preview = store.preview_recognition_restriction(revision, feature, list(reversed(CAMERAS)))
        assert not preview.restart_required and preview.resulting_revision == revision
        assert (
            store.restrict_recognition(revision, feature, list(reversed(CAMERAS))).revision
            == revision
        )
    assert path.read_bytes() == before and not store.history_dir.exists()


@pytest.mark.parametrize(
    "retained", [None, "camera", [True], [{}], ["unknown"], [CAMERAS[0], CAMERAS[0]], ["x" * 1024]]
)
def test_bad_or_expanded_scope_rejects_before_lock_or_history(tmp_path, retained):
    store, path, _ = fixture(tmp_path)
    roles(path)
    before = digest(tmp_path)
    for method in (store.preview_recognition_restriction, store.restrict_recognition):
        with pytest.raises(AiPortConfigurationError):
            method(store.snapshot().revision, "face", retained)
    assert digest(tmp_path) == before


def test_stale_revision_and_unknown_feature_leave_all_files_unchanged(tmp_path):
    store, path, _ = fixture(tmp_path)
    roles(path)
    before = digest(tmp_path)
    for revision in ("0" * 64, "é" * 64, None, 1):
        with pytest.raises(AiPortRevisionConflict):
            store.restrict_recognition(revision, "face", [])
    for feature in ("speech", "../face", None, []):
        with pytest.raises(AiPortConfigurationError):
            store.restrict_recognition(store.snapshot().revision, feature, [])
    assert digest(tmp_path) == before


def test_provider_undo_cannot_restore_removed_recognition_scope(tmp_path):
    store, path, _ = fixture(tmp_path)
    roles(path)
    (tmp_path / "openai-key").write_text(SECRET)
    store.apply(store.snapshot().revision, settings(tmp_path))
    store.restrict_recognition(store.snapshot().revision, "face", list(CAMERAS[1:]))
    assert not store.provider_rollback_status()["available"]
    before = path.read_bytes()
    with pytest.raises(AiPortConfigurationError):
        store.rollback_provider(store.snapshot().revision)
    assert path.read_bytes() == before
    store.apply(store.snapshot().revision, dict(settings(tmp_path), model="synthetic-later"))
    store.rollback_provider(store.snapshot().revision)
    assert json.loads(path.read_text())["live_face"]["cameras"] == list(CAMERAS[1:])


async def site_with_roles(tmp_path):
    site, server, client, cookie, _ = await signed_in(tmp_path)
    path = site.aiport.path
    roles(path)

    async def inventory():
        return {
            "schema": "aikey-camera-preflight/1",
            "cameras": [
                {"mac": CAMERAS[0], "name": "Synthetic <north>"},
                {"mac": CAMERAS[1], "name": "Synthetic south"},
            ],
        }

    site.inventory_loader = inventory
    return site, server, client, cookie, path


def form(page, feature="face", mode="restrict"):
    for body in page.split("<form method='post' action='/aiport-permissions'>")[1:]:
        body = body.split("</form>")[0]
        if f"name='feature' value='{feature}'" in body and f"name='mode' value='{mode}'" in body:
            fields = MultiDict(re.findall("name='([^']+)' value='([^']*)'", body))
            fields["confirm"] = "on"
            return fields
    raise AssertionError("form absent")


async def test_signed_in_restriction_uses_session_profile_feature_revision_tokens(tmp_path):
    site, server, client, _, path = await site_with_roles(tmp_path)
    before = json.loads(path.read_text())
    try:
        response = await client.get("/aiport-permissions")
        page = await response.text()
        assert_private_page(response, page)
        assert "Synthetic &lt;north&gt;" in page and "Saved settings only" in page
        assert (
            "must restart" in page and "Other detection and audio permissions are unchanged" in page
        )
        assert all(camera not in page for camera in CAMERAS)
        fields = form(page)
        keep = fields.getall("keep_camera")
        del fields["keep_camera"]
        fields.add("keep_camera", keep[1])
        response = await client.post(
            "/aiport-permissions",
            data=fields,
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 303
        after = json.loads(path.read_text())
        before["live_face"]["cameras"] = list(CAMERAS[1:])
        assert after == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "kind",
    [
        "csrf",
        "origin",
        "profile",
        "feature",
        "revision",
        "cross_feature",
        "raw_camera",
        "duplicate",
        "unknown_field",
        "no_confirm",
    ],
)
async def test_refused_http_requests_cannot_change_saved_scope(tmp_path, kind):
    site, server, client, _, path = await site_with_roles(tmp_path)
    try:
        page = await (await client.get("/aiport-permissions")).text()
        fields = form(page)
        headers = {"Origin": site.origin}
        if kind == "csrf":
            fields["csrf"] = "invalid"
        if kind == "origin":
            headers["Origin"] = "https://invalid.example"
        if kind == "profile":
            fields["profile"] = "aiport:missing"
        if kind == "feature":
            fields["feature"] = "speech"
        if kind == "revision":
            fields["revision"] = "0" * 64
        if kind == "cross_feature":
            fields["feature"] = "sound"
        if kind == "raw_camera":
            fields["keep_camera"] = CAMERAS[0]
        if kind == "duplicate":
            fields.add("keep_camera", fields.getall("keep_camera")[0])
        if kind == "unknown_field":
            fields["model"] = "not-allowed"
        if kind == "no_confirm":
            del fields["confirm"]
        before = digest(path.parent)
        response = await client.post(
            "/aiport-permissions", data=fields, headers=headers, allow_redirects=False
        )
        assert response.status in (200, 403)
        assert "not saved" in await response.text() or response.status == 403
        assert digest(path.parent) == before
    finally:
        await client.close()
        await server.close()


async def test_inventory_outage_still_allows_disabling_only_one_saved_role(tmp_path):
    site, server, client, _, path = await site_with_roles(tmp_path)

    async def unavailable():
        raise OSError("private-inventory-canary")

    site.inventory_loader = unavailable
    try:
        page = await (await client.get("/aiport-permissions")).text()
        assert "private-inventory-canary" not in page and "value='restrict'" not in page
        fields = form(page, "sound", "disable_all")
        response = await client.post(
            "/aiport-permissions",
            data=fields,
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 303
        after = json.loads(path.read_text())
        assert "live_sound" not in after and after["live_face"]["cameras"] == list(CAMERAS)
        assert after["live_speech_cameras"] == list(CAMERAS)
    finally:
        await client.close()
        await server.close()


async def test_post_commit_error_is_uncertain_not_a_false_no_change_or_retry(tmp_path, monkeypatch):
    site, server, client, _, path = await site_with_roles(tmp_path)
    real = module.atomic_private
    calls = []

    def committed_then_failed(target, content):
        real(target, content)
        if target == path:
            calls.append(target)
            raise OSError("private-commit-canary")

    try:
        fields = form(await (await client.get("/aiport-permissions")).text(), mode="disable_all")
        monkeypatch.setattr(module, "atomic_private", committed_then_failed)
        response = await client.post(
            "/aiport-permissions", data=fields, headers={"Origin": site.origin}
        )
        page = await response.text()
        assert "outcome uncertain" in page and "private-commit-canary" not in page
        assert "Nothing was changed" not in page and len(calls) == 1
        assert "live_face" not in json.loads(path.read_text())
    finally:
        await client.close()
        await server.close()


def test_overlapping_scope_saves_commit_one_and_refuse_the_stale_other(tmp_path, monkeypatch):
    store, path, _ = fixture(tmp_path)
    roles(path)
    revision = store.snapshot().revision
    barrier = threading.Barrier(2, timeout=3)
    original_lock = store._locked
    starts, ends = [], []

    @contextmanager
    def meet_before_lock():
        barrier.wait()
        with original_lock():
            yield

    monkeypatch.setattr(store, "_locked", meet_before_lock)

    def save(camera):
        starts.append(time.monotonic())
        try:
            store.restrict_recognition(revision, "face", [camera])
            return "saved"
        except AiPortRevisionConflict:
            return "conflict"
        finally:
            ends.append(time.monotonic())

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, camera) for camera in CAMERAS]
        outcomes = [future.result(timeout=5) for future in futures]
    assert sorted(outcomes) == ["conflict", "saved"]
    assert max(starts) < min(ends)
    assert len(json.loads(path.read_text())["live_face"]["cameras"]) == 1
    assert len(list(store.history_dir.glob("*.json"))) == 1
    assert not list(path.parent.glob(".*.check"))


async def test_cross_profile_token_cannot_apply_even_with_identical_revision(tmp_path):
    site, server, client, _, path = await site_with_roles(tmp_path)
    other_root = tmp_path / "other"
    other_root.mkdir(mode=0o700)
    other, other_path, _ = fixture(other_root)
    other_path.write_bytes(path.read_bytes())
    site.aiports["aiport:other"] = other
    try:
        page = await (await client.get("/aiport-permissions")).text()
        fields = form(page)
        fields["profile"] = "aiport:other"
        assert other.snapshot().revision == fields["revision"]
        before = digest(path.parent), digest(other_root)
        response = await client.post(
            "/aiport-permissions", data=fields, headers={"Origin": site.origin}
        )
        assert "not saved" in await response.text()
        assert (digest(path.parent), digest(other_root)) == before
    finally:
        await client.close()
        await server.close()


async def test_cross_session_selection_cannot_be_reused_with_a_valid_new_csrf(tmp_path):
    site, server, client, _, path = await site_with_roles(tmp_path)
    peer = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await peer.start_server()
    try:
        original = form(await (await client.get("/aiport-permissions")).text())
        await peer.post(
            "/login",
            data={"password": PASSWORD},
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        fresh = form(await (await peer.get("/aiport-permissions")).text())
        original["csrf"] = fresh["csrf"]
        original["keep_camera"] = original.getall("keep_camera")[0]
        before = digest(path.parent)
        response = await peer.post(
            "/aiport-permissions", data=original, headers={"Origin": site.origin}
        )
        assert "not saved" in await response.text()
        assert digest(path.parent) == before
    finally:
        await peer.close()
        await client.close()
        await server.close()


@pytest.mark.parametrize("boundary", ["expiry", "revocation"])
async def test_authority_lost_after_preview_cannot_start_apply(tmp_path, monkeypatch, boundary):
    site, server, client, cookie, path = await site_with_roles(tmp_path)
    original = site.aiport.preview_recognition_restriction

    def preview_then_lose_authority(*args):
        result = original(*args)
        if boundary == "revocation":
            assert site.security.revoke(cookie)
        else:
            expired = site.security._clock() + site.security.session_ttl + 1
            site.security._clock = lambda: expired
        return result

    try:
        fields = form(await (await client.get("/aiport-permissions")).text(), mode="disable_all")
        monkeypatch.setattr(
            site.aiport, "preview_recognition_restriction", preview_then_lose_authority
        )
        before = digest(path.parent)
        response = await client.post(
            "/aiport-permissions",
            data=fields,
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 403 and digest(path.parent) == before
    finally:
        await client.close()
        await server.close()


async def test_unsigned_reads_and_posts_refuse_before_profile_reads(tmp_path, monkeypatch):
    site, server, client, _, path = await site_with_roles(tmp_path)
    client.session.cookie_jar.clear()
    monkeypatch.setattr(
        site.aiport, "recognition_permissions", lambda _: pytest.fail("unexpected scope read")
    )
    try:
        assert (await client.get("/aiport-permissions", allow_redirects=False)).status == 303
        response = await client.post(
            "/aiport-permissions",
            data={"csrf": "invalid"},
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 403
    finally:
        await client.close()
        await server.close()


async def test_audit_records_fixed_codes_not_caller_profile_or_selection_canaries(tmp_path):
    site, server, client, _, path = await site_with_roles(tmp_path)
    site.audit = AuditLog(tmp_path / "audit.jsonl")
    try:
        page = await (await client.get("/aiport-permissions")).text()
        fields = form(page, mode="disable_all")
        fields["profile"] = "aiport:caller-canary"
        response = await client.post(
            "/aiport-permissions", data=fields, headers={"Origin": site.origin}
        )
        assert "caller-canary" not in await response.text()
        fields = form(page)
        fields["keep_camera"] = SECRET
        response = await client.post(
            "/aiport-permissions", data=fields, headers={"Origin": site.origin}
        )
        assert SECRET not in await response.text()
        fields = form(page, mode="disable_all")
        response = await client.post(
            "/aiport-permissions",
            data=fields,
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 303
        entries = site.audit.recent()
        assert [entry["result"] for entry in entries] == ["saved_face", "rejected", "rejected"]
        text = json.dumps(entries)
        for canary in ("caller-canary", SECRET, *CAMERAS, fields["csrf"], fields["revision"]):
            assert canary not in text
        assert "live_face" not in json.loads(path.read_text())
    finally:
        await client.close()
        await server.close()
