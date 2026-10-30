"""Remove saved Port speech presence without changing other audio roles."""

from copy import deepcopy
import json

import pytest

from test_aiport_config_store import CAMERAS, fixture, settings
from test_aiport_permission_restrictions import form, roles, site_with_roles
from test_aiport_provider_rollback import digest
from test_control_site_secrets import assert_private_page, SECRET
from aikey.aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from aikey.admin_audit import AuditLog
from aikey import aiport_config_store as store_module
from test_control_site_secrets import PASSWORD
from aikey.control_site import _COOKIE
from yarl import URL


@pytest.mark.parametrize("retained", [list(CAMERAS[1:]), []])
def test_speech_restriction_preserves_other_roles_and_removes_only_orphan_cap(tmp_path, retained):
    store, path, _ = fixture(tmp_path)
    original = roles(path)
    if retained:
        original["live_sound"]["cameras"] = retained
    else:
        del original["live_sound"]
    original["live_speech_max_events_per_hour"] = 17
    path.write_text(json.dumps(original) + "\n")
    before = path.read_bytes()
    revision, cameras = store.recognition_permissions("speech")
    assert cameras == CAMERAS
    preview = store.preview_recognition_restriction(revision, "speech", retained)
    assert path.read_bytes() == before and not store.history_dir.exists()
    result = store.restrict_recognition(revision, "speech", retained)
    expected = deepcopy(original)
    if retained:
        expected["live_speech_cameras"] = retained
    else:
        del expected["live_speech_cameras"]
        del expected["live_speech_max_events_per_hour"]
    assert json.loads(path.read_text()) == expected
    assert result.revision == preview.resulting_revision
    assert (store.history_dir / (revision + ".json")).read_bytes() == before


async def test_signed_in_speech_control_uses_opaque_labels_and_explicit_confirmation(tmp_path):
    site, server, client, _, path = await site_with_roles(tmp_path)
    before = json.loads(path.read_text())
    before["live_sound"]["cameras"] = list(CAMERAS[1:])
    path.write_text(json.dumps(before) + "\n")
    try:
        response = await client.get("/aiport-permissions")
        page = await response.text()
        assert_private_page(response, page)
        assert "Speech presence" in page and "must restart" in page
        assert "Sound classification requires" in page and "saved sound permission first" in page
        assert all(camera not in page for camera in CAMERAS)
        fields = form(page, feature="speech")
        keep = fields.getall("keep_camera")
        del fields["keep_camera"]
        fields.add("keep_camera", keep[1])
        response = await client.post("/aiport-permissions", data=fields,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        before["live_speech_cameras"] = list(CAMERAS[1:])
        assert json.loads(path.read_text()) == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("retained", [list(CAMERAS[1:]), []])
def test_sound_dependency_refuses_before_history_or_lock_without_changing_any_role(tmp_path, retained):
    store, path, _ = fixture(tmp_path)
    roles(path)
    before = path.read_bytes()
    revision = store.snapshot().revision
    for method in (store.preview_recognition_restriction, store.restrict_recognition):
        with pytest.raises(AiPortConfigurationError, match="^aiport_audio_required_by_sound$"):
            method(revision, "speech", retained)
    assert path.read_bytes() == before and not store.lock_path.exists() and not store.history_dir.exists()


async def test_audio_dependency_refusal_shows_actionable_fixed_hint_without_camera_ids(tmp_path):
    site, server, client, _, path = await site_with_roles(tmp_path)
    try:
        page = await (await client.get("/aiport-permissions")).text()
        fields = form(page, feature="speech", mode="disable_all")
        before = path.read_bytes()
        response = await client.post("/aiport-permissions", data=fields, headers={"Origin": site.origin})
        body = await response.text()
        assert "Remove saved sound permissions" in body
        assert "No other role was changed automatically" in body
        assert all(camera not in body for camera in CAMERAS)
        assert path.read_bytes() == before and not site.aiport.history_dir.exists()
    finally:
        await client.close()
        await server.close()


def speech_without_sound(path):
    value = roles(path)
    del value["live_sound"]
    path.write_text(json.dumps(value) + "\n")
    return value


def test_noop_keeps_exact_raw_ids_and_defaults_and_partial_restriction_keeps_original_order(tmp_path):
    store, path, _ = fixture(tmp_path)
    value = speech_without_sound(path)
    value["live_speech_cameras"] = ["2a:11:22:33:44:55", "2a:11:22:33:44:56"]
    path.write_text(json.dumps(value, indent=2) + "\n")
    before = path.read_bytes()
    revision = store.snapshot().revision
    result = store.restrict_recognition(revision, "speech", list(reversed(CAMERAS)))
    assert result.revision == revision and path.read_bytes() == before
    assert not store.history_dir.exists()
    store.restrict_recognition(revision, "speech", list(CAMERAS[1:]))
    value["live_speech_cameras"] = ["2a:11:22:33:44:56"]
    assert json.loads(path.read_text()) == value


@pytest.mark.parametrize("retained", [None, "camera", [True], [{}], ["unknown"], [CAMERAS[0]] * 2])
def test_speech_input_cannot_expand_or_malformed_scope_and_creates_no_lock(tmp_path, retained):
    store, path, _ = fixture(tmp_path)
    speech_without_sound(path)
    before = digest(tmp_path)
    revision = store.snapshot().revision
    for method in (store.preview_recognition_restriction, store.restrict_recognition):
        with pytest.raises(AiPortConfigurationError, match="remove_only"):
            method(revision, "speech", retained)
    assert digest(tmp_path) == before


def test_missing_speech_cannot_be_enabled_and_stale_revision_never_writes(tmp_path):
    store, path, _ = fixture(tmp_path)
    before = digest(tmp_path)
    assert store.recognition_permissions("speech")[1] is None
    with pytest.raises(AiPortConfigurationError, match="not_configured"):
        store.restrict_recognition(store.snapshot().revision, "speech", list(CAMERAS))
    assert digest(tmp_path) == before
    speech_without_sound(path)
    before = digest(tmp_path)
    with pytest.raises(AiPortRevisionConflict):
        store.restrict_recognition("0" * 64, "speech", [])
    assert digest(tmp_path) == before


@pytest.mark.parametrize("retained", [list(CAMERAS[1:]), []])
def test_provider_undo_cannot_regrant_audio_and_later_undo_keeps_reduced_scope(tmp_path, retained):
    store, path, _ = fixture(tmp_path)
    speech_without_sound(path)
    (tmp_path / "openai-key").write_text(SECRET)
    (tmp_path / "openai-key").chmod(0o600)
    store.apply(store.snapshot().revision, settings(tmp_path))
    store.restrict_recognition(store.snapshot().revision, "speech", retained)
    assert not store.provider_rollback_status()["available"]
    with pytest.raises(AiPortConfigurationError):
        store.rollback_provider(store.snapshot().revision)
    store.apply(store.snapshot().revision, dict(settings(tmp_path), model="synthetic-later"))
    store.rollback_provider(store.snapshot().revision)
    assert json.loads(path.read_text()).get("live_speech_cameras", []) == retained


@pytest.mark.parametrize("kind", ["csrf", "origin", "revision", "cross_feature", "raw_camera", "unknown_field"])
async def test_speech_refusals_do_not_hide_behind_sound_dependency(tmp_path, kind):
    site, server, client, _, path = await site_with_roles(tmp_path)
    speech_without_sound(path)
    try:
        page = await (await client.get("/aiport-permissions")).text()
        fields = form(page, feature="speech")
        keep = fields.getall("keep_camera")[1]
        del fields["keep_camera"]
        fields["keep_camera"] = keep
        headers = {"Origin": site.origin}
        if kind == "csrf":
            fields["csrf"] = "invalid"
        elif kind == "origin":
            headers["Origin"] = "https://invalid.example"
        elif kind == "revision":
            fields["revision"] = "0" * 64
        elif kind == "cross_feature":
            fields["keep_camera"] = form(page, feature="face").getall("keep_camera")[1]
        elif kind == "raw_camera":
            fields["keep_camera"] = CAMERAS[1]
        else:
            fields["model"] = "not-allowed"
        before = digest(path.parent)
        response = await client.post("/aiport-permissions", data=fields, headers=headers,
                                     allow_redirects=False)
        assert response.status == (403 if kind in {"csrf", "origin"} else 200)
        assert digest(path.parent) == before
    finally:
        await client.close()
        await server.close()


async def test_old_speech_tokens_cannot_be_rebound_to_a_new_valid_session(tmp_path):
    site, server, client, old_cookie, path = await site_with_roles(tmp_path)
    speech_without_sound(path)
    try:
        page = await (await client.get("/aiport-permissions")).text()
        fields = form(page, feature="speech")
        keep = fields.getall("keep_camera")[1]
        del fields["keep_camera"]
        fields["keep_camera"] = keep
        client.session.cookie_jar.clear()
        assert (await client.post("/login", data={"password": PASSWORD},
                                  headers={"Origin": site.origin}, allow_redirects=False)).status == 303
        new_cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        assert new_cookie != old_cookie
        fields["csrf"] = site.security.csrf_token(new_cookie)
        before = digest(path.parent)
        response = await client.post("/aiport-permissions", data=fields, headers={"Origin": site.origin})
        assert "not saved" in await response.text()
        assert digest(path.parent) == before
    finally:
        await client.close()
        await server.close()


async def test_speech_commit_failure_is_uncertain_and_never_silently_retried(tmp_path, monkeypatch):
    site, server, client, _, path = await site_with_roles(tmp_path)
    speech_without_sound(path)
    calls = []
    real = store_module.atomic_private

    def commit_then_fail(target, content):
        real(target, content)
        if target == path:
            calls.append(target)
            raise OSError("private-speech-commit-canary")

    try:
        fields = form(await (await client.get("/aiport-permissions")).text(),
                      feature="speech", mode="disable_all")
        monkeypatch.setattr(store_module, "atomic_private", commit_then_fail)
        response = await client.post("/aiport-permissions", data=fields, headers={"Origin": site.origin})
        body = await response.text()
        assert "outcome uncertain" in body and "private-speech-commit-canary" not in body
        assert "Nothing was changed" not in body and len(calls) == 1
        assert "live_speech_cameras" not in json.loads(path.read_text())
        response = await client.post("/aiport-permissions", data=fields, headers={"Origin": site.origin})
        assert "not saved" in await response.text() and len(calls) == 1
    finally:
        await client.close()
        await server.close()


async def test_whole_audio_disable_with_unavailable_inventory_has_no_fake_live_revoke_and_fixed_audit(tmp_path):
    site, server, client, _, path = await site_with_roles(tmp_path)
    speech_without_sound(path)
    site.inventory_loader = None
    site.audit = AuditLog(tmp_path / "speech-audit.jsonl")
    try:
        page = await (await client.get("/aiport-permissions")).text()
        assert "Unambiguous camera names are unavailable" in page
        fields = form(page, feature="speech", mode="disable_all")
        response = await client.post("/aiport-permissions", data=fields,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        assert "live_speech_cameras" not in json.loads(path.read_text())
        entries = site.audit.recent()
        assert [entry["result"] for entry in entries] == ["saved_speech"]
        encoded = json.dumps(entries)
        assert all(canary not in encoded for canary in [*CAMERAS, SECRET, fields["csrf"], fields["revision"]])
        result = await (await client.get("/aiport-permissions")).text()
        assert "Not configured in saved settings. Running status is not checked" in result
    finally:
        await client.close()
        await server.close()
