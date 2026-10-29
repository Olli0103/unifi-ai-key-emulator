"""Speech changes stay role-scoped; no audio or provider request is used."""

import json
from pathlib import Path

import pytest

from aikey.config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from test_control_site_secrets import signed_in, SECRET, assert_private_page
from test_role_models import key_store, digest


def selection(**changes):
    return {"provider": "openai-compatible", "model": "synthetic-whisper-b",
            "base_url": "http://127.0.0.1:8188/v1", **changes}


def test_speech_preview_apply_and_exact_undo_preserve_other_roles_and_camera_permissions(tmp_path):
    store, path = key_store(tmp_path)
    original = path.read_bytes()
    before = json.loads(original)
    revision = store.snapshot().revision
    preview = store.preview_speech(revision, selection())
    assert path.read_bytes() == original and not store.history_dir.exists()
    assert preview.changed_fields == ("speech_to_text.base_url", "speech_to_text.model")
    saved = store.apply_speech(revision, selection())
    assert saved.revision == preview.resulting_revision
    after = json.loads(path.read_text())
    for field in ("model", "base_url"):
        after["speech_to_text"][field] = before["speech_to_text"][field]
    assert after == before
    store.rollback_provider(saved.revision)
    assert path.read_bytes() == original


def test_key_retention_is_exact_role_and_endpoint_only(tmp_path):
    store, path = key_store(tmp_path)
    raw = json.loads(path.read_text())
    old_key = tmp_path / "state" / "speech-key-old"
    old_key.write_text(SECRET)
    raw["speech_to_text"]["api_key_file"] = str(old_key)
    path.write_text(json.dumps(raw))
    same = selection(base_url=raw["speech_to_text"]["base_url"])
    store.apply_speech(store.snapshot().revision, same)
    assert json.loads(path.read_text())["speech_to_text"]["api_key_file"] == str(old_key)
    saved = store.apply_speech(store.snapshot().revision, selection())
    assert "api_key_file" not in json.loads(path.read_text())["speech_to_text"]
    assert json.loads(path.read_text())["inference"] == raw["inference"]
    old_key.unlink()
    assert store.provider_rollback_status()["reason"] == "prior_key_missing"
    before = path.read_bytes()
    with pytest.raises(ConfigurationStoreError, match="prior provider key"):
        store.rollback_provider(saved.revision)
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"camera_ids": ["other"]}, {"max_audio_ms": 1000}, {"api_key": SECRET},
    {"api_key_file": "/outside/key"}, {"provider": "anthropic"},
    {"base_url": "http://public.example/v1"},
    {"provider": "openai", "base_url": "https://api.openai.com/v1"},
    {"language": "invalid"},
    {"local_network": "false"}, {"model": "invalid model"},
])
def test_rejected_settings_never_archive_or_change_config(tmp_path, change):
    store, path = key_store(tmp_path)
    before = digest(tmp_path)
    for method in (store.preview_speech, store.apply_speech):
        with pytest.raises(UnsafeConfigurationChange):
            method(store.snapshot().revision, selection(**change))
    assert digest(tmp_path) == before and not store.history_dir.exists()


def test_stale_revision_and_missing_camera_opt_in_refuse(tmp_path):
    store, path = key_store(tmp_path)
    before = path.read_bytes()
    with pytest.raises(RevisionConflict):
        store.apply_speech("0" * 64, selection())
    assert path.read_bytes() == before
    raw = json.loads(before)
    raw.pop("speech_to_text")
    path.write_text(json.dumps(raw))
    with pytest.raises(UnsafeConfigurationChange, match="camera opt-in"):
        store.apply_speech(store.snapshot().revision, selection())


async def configured_site(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    data = json.loads(path.read_text())
    data["speech_to_text"] = {"provider": "openai-compatible", "model": "synthetic-whisper-a",
                              "base_url": "http://127.0.0.1:8178/v1",
                              "camera_ids": ["synthetic-camera"], "max_audio_ms": 60000}
    path.write_text(json.dumps(data) + "\n")
    return site, server, client, cookie, path


def form(site, cookie, **changes):
    return {"csrf": site.security.csrf_token(cookie), "revision": site.aikey.snapshot().revision,
            "provider": "openai", "model": "synthetic-whisper-cloud",
            "base_url": "https://api.openai.com/v1", "api_key": SECRET,
            "allow_cloud_audio": "on", **changes}


async def test_ui_saves_separate_speech_key_never_echoes_it_and_undo_restores_bytes(tmp_path, caplog):
    site, server, client, cookie, path = await configured_site(tmp_path)
    try:
        original = path.read_bytes()
        before = json.loads(original)
        response = await client.post("/speech-provider", data=form(site, cookie),
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303 and response.headers["Location"] == "/?saved=1"
        raw = json.loads(path.read_text())
        speech = raw["speech_to_text"]
        assert speech["camera_ids"] == before["speech_to_text"]["camera_ids"]
        assert speech["max_audio_ms"] == 60000 and raw["inference"] == before["inference"]
        saved_key = path.parent / Path(speech["api_key_file"]).name
        assert saved_key.read_text().strip() == SECRET and saved_key.stat().st_mode & 0o777 == 0o600
        page = await client.get("/")
        body = await page.text()
        assert_private_page(page, body)
        assert "speech-key-" not in body and "synthetic-camera" not in body
        assert "may incur charges" in body and "Only already permitted cameras" in body
        assert SECRET not in caplog.text
        site.aikey.rollback_provider(site.aikey.snapshot().revision)
        assert path.read_bytes() == original
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("failure", ["csrf", "origin", "revision", "cloud_permission", "endpoint"])
async def test_failed_ui_changes_do_not_write_a_key_or_configuration(tmp_path, failure):
    site, server, client, cookie, path = await configured_site(tmp_path)
    try:
        before = path.read_bytes()
        fields, origin = form(site, cookie), site.origin
        if failure == "cloud_permission":
            fields.pop("allow_cloud_audio")
        elif failure == "origin":
            origin = "http://untrusted.invalid"
        elif failure == "endpoint":
            fields["base_url"] = "https://untrusted.invalid/v1"
        else:
            fields[failure] = "0" * 64
        response = await client.post("/speech-provider", data=fields,
                                     headers={"Origin": origin}, allow_redirects=False)
        assert response.status == (403 if failure in {"csrf", "origin"} else 200)
        assert SECRET not in await response.text()
        assert path.read_bytes() == before and not list(path.parent.glob("speech-key-*"))
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("after_commit", [False, True])
async def test_uncertain_replacement_never_deletes_a_live_speech_key(tmp_path, monkeypatch, after_commit):
    site, server, client, cookie, path = await configured_site(tmp_path)
    try:
        before = path.read_bytes()
        real = site.aikey.apply_speech

        def interrupted(*args):
            if after_commit:
                real(*args)
            raise ConfigurationStoreError("synthetic interrupted replacement")

        monkeypatch.setattr(site.aikey, "apply_speech", interrupted)
        response = await client.post("/speech-provider", data=form(site, cookie),
                                     headers={"Origin": site.origin}, allow_redirects=False)
        body = await response.text()
        assert "Reload settings before retrying" in body and SECRET not in body
        files = list(path.parent.glob("speech-key-*"))
        if after_commit:
            live = json.loads(path.read_text())["speech_to_text"]["api_key_file"]
            assert len(files) == 1 and files[0].name == Path(live).name
            assert files[0].read_text().strip() == SECRET
        else:
            assert not files and path.read_bytes() == before
    finally:
        await client.close()
        await server.close()
