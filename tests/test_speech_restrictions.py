"""Saved audio scope can shrink without keys, live work or new permission grants."""

import json
import re

from multidict import MultiDict
import pytest
from yarl import URL

from aikey.admin_audit import AuditLog
from aikey.config_store import ConfigurationStore, ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from aikey.control_site import _COOKIE
from test_control_site_secrets import PASSWORD, SECRET, assert_private_page
from test_role_models import key_store, digest
from test_speech_provider_control import configured_site, selection

CAMERAS = ["private-camera-one", "private-camera-two"]


def with_two_cameras(path):
    raw = json.loads(path.read_text())
    raw["speech_to_text"]["camera_ids"] = CAMERAS.copy()
    path.write_text(json.dumps(raw) + "\n")
    return raw


def test_restriction_preview_and_apply_keep_every_other_field(tmp_path):
    store, path = key_store(tmp_path)
    original = with_two_cameras(path)
    before = path.read_bytes()
    revision = store.snapshot().revision
    preview = store.preview_speech_restriction(revision, CAMERAS[1:])
    assert path.read_bytes() == before and not store.history_dir.exists()
    assert preview.changed_fields == ("speech_to_text.camera_ids",)
    saved = store.restrict_speech(revision, CAMERAS[1:])
    assert saved.revision == preview.resulting_revision
    after = json.loads(path.read_text())
    assert after["speech_to_text"]["camera_ids"] == CAMERAS[1:]
    after["speech_to_text"]["camera_ids"] = original["speech_to_text"]["camera_ids"]
    assert after == original


def test_retaining_the_same_set_is_an_order_independent_no_op(tmp_path):
    store, path = key_store(tmp_path)
    with_two_cameras(path)
    original = path.read_bytes()
    revision = store.snapshot().revision
    preview = store.preview_speech_restriction(revision, CAMERAS[::-1])
    assert preview.changed_fields == () and not preview.restart_required
    assert store.restrict_speech(revision, CAMERAS[::-1]).revision == revision
    assert path.read_bytes() == original and not store.history_dir.exists()


def test_disabling_all_saved_speech_keeps_keys_history_and_other_roles(tmp_path):
    store, path = key_store(tmp_path)
    original = with_two_cameras(path)
    private_key = path.parent / "speech-key-retained"
    private_key.write_text(SECRET)
    private_key.chmod(0o600)
    original["speech_to_text"]["api_key_file"] = str(private_key)
    path.write_text(json.dumps(original))
    revision = store.snapshot().revision
    store.restrict_speech(revision, [])
    expected = dict(original)
    expected.pop("speech_to_text")
    assert json.loads(path.read_text()) == expected
    assert private_key.read_text() == SECRET
    assert json.loads((store.history_dir / (revision + ".json")).read_text()) == original


@pytest.mark.parametrize("retained", [["new-camera"], [CAMERAS[0], CAMERAS[0]], None, "camera", [{}], [True], [""]])
def test_expansion_and_malformed_scope_never_change_or_archive_configuration(tmp_path, retained):
    store, path = key_store(tmp_path)
    with_two_cameras(path)
    before = digest(tmp_path)
    for method in (store.preview_speech_restriction, store.restrict_speech):
        with pytest.raises(UnsafeConfigurationChange):
            method(store.snapshot().revision, retained)
    assert digest(tmp_path) == before


def test_concurrent_save_between_scope_read_and_apply_does_not_get_overwritten(tmp_path, monkeypatch):
    store, path = key_store(tmp_path)
    with_two_cameras(path)
    revision = store.snapshot().revision
    original_apply = store.apply

    def racing_apply(expected, patch):
        ConfigurationStore(path).apply(expected, {"inference": {"model": "synthetic-concurrent"}})
        return original_apply(expected, patch)

    monkeypatch.setattr(store, "apply", racing_apply)
    with pytest.raises(RevisionConflict):
        store.restrict_speech(revision, CAMERAS[:1])
    after = json.loads(path.read_text())
    assert after["inference"]["model"] == "synthetic-concurrent"
    assert after["speech_to_text"]["camera_ids"] == CAMERAS


def test_old_provider_undo_cannot_silently_regrant_removed_audio_scope(tmp_path):
    store, path = key_store(tmp_path)
    with_two_cameras(path)
    store.apply_speech(store.snapshot().revision, selection())
    store.restrict_speech(store.snapshot().revision, CAMERAS[:1])
    before = path.read_bytes()
    assert not store.provider_rollback_status()["available"]
    with pytest.raises(ConfigurationStoreError):
        store.rollback_provider(store.snapshot().revision)
    assert path.read_bytes() == before
    store.apply_speech(store.snapshot().revision, selection(model="synthetic-later-provider"))
    store.rollback_provider(store.snapshot().revision)
    assert json.loads(path.read_text())["speech_to_text"]["camera_ids"] == CAMERAS[:1]


async def permission_site(tmp_path):
    site, server, client, cookie, path = await configured_site(tmp_path)
    with_two_cameras(path)

    async def inventory():
        return {"schema": "aikey-camera-preflight/1", "cameras": [
            {"id": CAMERAS[0], "name": "Kitchen <not markup>"},
            {"id": CAMERAS[1], "name": "Hall & stairs"}]}
    site.inventory_loader = inventory
    return site, server, client, cookie, path


def fields(site, cookie, tokens=(), **changes):
    data = MultiDict({"csrf": site.security.csrf_token(cookie), "revision": site.aikey.snapshot().revision,
                      "mode": "restrict", "confirm": "on", **changes})
    for token in tokens:
        data.add("keep_camera", token)
    return data


async def page_tokens(client):
    response = await client.get("/speech-permissions")
    assert response.status == 200
    body = await response.text()
    assert_private_page(response, body)
    return body, re.findall(r"name='keep_camera' value='([a-f0-9]{64})'", body)


async def test_named_form_has_no_camera_ids_or_keys_and_removes_only_selected_permission(tmp_path):
    site, server, client, cookie, path = await permission_site(tmp_path)
    site.audit = AuditLog(tmp_path / "audit.jsonl")
    try:
        original = json.loads(path.read_text())
        body, tokens = await page_tokens(client)
        assert len(tokens) == 2
        assert "Kitchen &lt;not markup&gt;" in body and "Hall &amp; stairs" in body
        assert "Saved settings only" in body and "until you restart" in body
        assert all(camera not in body for camera in CAMERAS) and SECRET not in body
        response = await client.post("/speech-permissions", data=fields(site, cookie, tokens[1:]),
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        after = json.loads(path.read_text())
        assert after["speech_to_text"]["camera_ids"] == CAMERAS[1:]
        after["speech_to_text"]["camera_ids"] = CAMERAS
        assert after == original
        audit = (tmp_path / "audit.jsonl").read_text()
        assert json.loads(audit)["action"] == "speech_permissions"
        assert all(camera not in audit for camera in CAMERAS) and SECRET not in audit
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("bad", ["unknown", "duplicate", "stale", "no_confirmation", "extra_field", "ambiguous_mode", "contradictory_all"])
async def test_bad_selection_cannot_change_saved_permissions(tmp_path, bad):
    site, server, client, cookie, path = await permission_site(tmp_path)
    try:
        _body, tokens = await page_tokens(client)
        data = fields(site, cookie, tokens[:1])
        if bad == "unknown":
            data["keep_camera"] = "f" * 64
        elif bad == "duplicate":
            data.add("keep_camera", tokens[0])
        elif bad == "stale":
            data["revision"] = "0" * 64
        elif bad == "no_confirmation":
            del data["confirm"]
        elif bad == "extra_field":
            data["api_key"] = SECRET
        elif bad == "ambiguous_mode":
            data.add("mode", "disable_all")
        else:
            data["mode"] = "disable_all"
        before = digest(tmp_path)
        response = await client.post("/speech-permissions", data=data, headers={"Origin": site.origin})
        assert response.status == 200 and "not saved" in await response.text()
        assert digest(tmp_path) == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("bad", ["csrf", "origin", "expired"])
async def test_session_origin_and_csrf_are_required_before_scope_access(tmp_path, bad):
    site, server, client, cookie, path = await permission_site(tmp_path)
    try:
        _body, tokens = await page_tokens(client)
        data = fields(site, cookie, tokens[:1])
        headers = {"Origin": site.origin}
        if bad == "csrf":
            data["csrf"] = "wrong"
        elif bad == "origin":
            headers["Origin"] = "https://cross-origin.invalid"
        else:
            site.security._clock = lambda: 10**12
        before = path.read_bytes()
        assert (await client.post("/speech-permissions", data=data, headers=headers)).status == 403
        assert path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


async def test_inventory_outage_does_not_block_disabling_all_saved_audio(tmp_path):
    site, server, client, cookie, path = await permission_site(tmp_path)
    async def outage():
        raise RuntimeError("private inventory outage canary")
    site.inventory_loader = outage
    try:
        body, tokens = await page_tokens(client)
        assert not tokens and "names are unavailable" in body and "private inventory outage canary" not in body
        response = await client.post("/speech-permissions", data=fields(site, cookie, mode="disable_all"),
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303 and "speech_to_text" not in json.loads(path.read_text())
        body, tokens = await page_tokens(client)
        assert not tokens and "not configured in the saved settings" in body
        assert "Running-worker status is not verified" in body
    finally:
        await client.close()
        await server.close()


async def test_official_speech_default_destination_is_shown_without_a_key_value(tmp_path):
    site, server, client, cookie, path = await permission_site(tmp_path)
    try:
        raw = json.loads(path.read_text())
        raw["speech_to_text"]["provider"] = "openai"
        raw["speech_to_text"].pop("base_url")
        path.write_text(json.dumps(raw))
        body, _tokens = await page_tokens(client)
        assert "https://api.openai.com/v1" in body
        assert "No audio is sent by this page" in body and SECRET not in body
    finally:
        await client.close()
        await server.close()


async def test_signed_out_cannot_read_or_change_saved_audio_permissions(tmp_path):
    site, server, client, cookie, path = await permission_site(tmp_path)
    try:
        _body, tokens = await page_tokens(client)
        data = fields(site, cookie, tokens[:1])
        before = path.read_bytes()
        client.session.cookie_jar.clear()
        response = await client.get("/speech-permissions", allow_redirects=False)
        assert response.status == 303 and response.headers["Location"] == "/login"
        assert (await client.post("/speech-permissions", data=data,
                                 headers={"Origin": site.origin})).status == 403
        assert path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


async def test_selection_tokens_are_bound_to_the_authenticated_session(tmp_path):
    site, server, client, cookie, path = await permission_site(tmp_path)
    try:
        _body, old_tokens = await page_tokens(client)
        await client.post("/login", data={"password": PASSWORD},
                          headers={"Origin": site.origin}, allow_redirects=False)
        new_cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        assert new_cookie != cookie
        original = path.read_bytes()
        response = await client.post("/speech-permissions", data=fields(site, new_cookie, old_tokens[:1]),
                                     headers={"Origin": site.origin})
        assert response.status == 200 and "not saved" in await response.text()
        assert path.read_bytes() == original
    finally:
        await client.close()
        await server.close()


async def test_uncertain_after_apply_is_not_reported_as_a_confirmed_rejection(tmp_path, monkeypatch):
    site, server, client, cookie, path = await permission_site(tmp_path)
    try:
        _body, tokens = await page_tokens(client)
        real = site.aikey.restrict_speech
        def uncertain(*args):
            real(*args)
            raise OSError("private failure after committed permissions")
        monkeypatch.setattr(site.aikey, "restrict_speech", uncertain)
        response = await client.post("/speech-permissions", data=fields(site, cookie, tokens[:1]),
                                     headers={"Origin": site.origin})
        body = await response.text()
        assert response.status == 200 and "outcome uncertain" in body
        assert "private failure" not in body and SECRET not in body
        assert json.loads(path.read_text())["speech_to_text"]["camera_ids"] == CAMERAS[:1]
    finally:
        await client.close()
        await server.close()
