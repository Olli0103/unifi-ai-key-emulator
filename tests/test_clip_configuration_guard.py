"""CLIP index identity permits unrelated administration, never encoder migration."""

import copy
import json
import socket

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey import clip
from aikey.admin_security import AdminSecurity
from aikey.config import defaults
from aikey.config_store import ConfigurationStore, UnsafeConfigurationChange
from aikey.control_site import ControlSite, _COOKIE
from aikey.embedding_profile import _with_fingerprint, ensure_embedding_profile


def saved_clip(tmp_path, *, enabled=True, revision=True):
    state = tmp_path / "state"
    raw = defaults(state, "020000000001")
    raw["controller"]["host"] = "127.0.0.1"
    raw["inference"].update(provider="ollama", base_url="http://127.0.0.1:11434",
                            model="synthetic-caption-a")
    raw["speech_to_text"] = {"provider": "openai-compatible", "model": "synthetic-speech-a",
                              "base_url": "http://127.0.0.1:8178/v1", "camera_ids": ["a" * 24]}
    raw["search"].update(profile=clip.PROFILE, enabled=enabled)
    raw["find_anything"] = {"clip_server": "http://127.0.0.1:8179/"}
    identity = {"profile": clip.PROFILE, "model": clip.MODEL, "dimensions": clip.DIMENSIONS,
                "backend": "local-clip-onnx", "source": "http://127.0.0.1:8179"}
    if revision:
        identity["revision"] = "a" * 64
    ensure_embedding_profile(state, identity)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw, indent=2) + "\n")
    path.chmod(0o600)
    return ConfigurationStore(path), path, state / "search-profile.json"


@pytest.fixture
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("admin profile comparison must not contact an encoder")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(clip.ClipClient, "_client", forbidden)


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("revision", [True, False])
def test_noop_preview_keeps_existing_clip_profile_and_all_bytes(tmp_path, offline, enabled, revision):
    store, path, profile = saved_clip(tmp_path, enabled=enabled, revision=revision)
    before, pinned = path.read_bytes(), profile.read_bytes()
    preview = store.preview(store.snapshot().revision, {})
    assert preview.current_revision == preview.resulting_revision
    assert not preview.changed_fields and not preview.restart_required
    assert path.read_bytes() == before and profile.read_bytes() == pinned
    assert not store.history_dir.exists()


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("revision", [True, False])
def test_unrelated_save_and_exact_rollback_preserve_encoder(tmp_path, offline, enabled, revision):
    store, path, profile = saved_clip(tmp_path, enabled=enabled, revision=revision)
    before, pinned = path.read_bytes(), profile.read_bytes()
    initial = store.snapshot().revision
    changed = store.apply(initial, {"device": {"name": "Synthetic new display name"}})
    assert changed.revision != initial and profile.read_bytes() == pinned
    store.rollback(changed.revision, initial)
    assert path.read_bytes() == before and profile.read_bytes() == pinned


@pytest.mark.parametrize("role", ["caption", "speech"])
def test_role_model_save_and_provider_undo_preserve_clip(tmp_path, offline, role):
    store, path, profile = saved_clip(tmp_path)
    before, pinned = path.read_bytes(), profile.read_bytes()
    changed = store.apply_role_model(store.snapshot().revision, role, "synthetic-model-b")
    assert store.provider_rollback_status()["available"]
    store.rollback_provider(changed.revision)
    assert path.read_bytes() == before and profile.read_bytes() == pinned


@pytest.mark.parametrize("patch", [
    {"find_anything": {"clip_server": "http://127.0.0.1:8180"}},
    {"search": {"profile": "e5-session-v1"}},
    {"embeddings": {"base_url": "http://127.0.0.1:8181/v1"}},
    {"embeddings": {"revision": "b" * 64}},
    {"embeddings": {"backend": "disabled"}},
])
@pytest.mark.parametrize("method", ["preview", "apply"])
def test_encoder_changes_are_still_blocked_without_history_or_profile_writes(
        tmp_path, offline, patch, method):
    store, path, profile = saved_clip(tmp_path)
    before, pinned = path.read_bytes(), profile.read_bytes()
    with pytest.raises(UnsafeConfigurationChange, match="search-index migration"):
        getattr(store, method)(store.snapshot().revision, copy.deepcopy(patch))
    assert path.read_bytes() == before and profile.read_bytes() == pinned
    assert not store.history_dir.exists()


@pytest.mark.parametrize("mutate", [
    {"model": "clip-other"}, {"dimensions": 384}, {"backend": "other"},
    {"revision": None}, {"revision": "short"}, {"revision": ["a" * 64]},
    {"extra": True},
])
def test_recomputed_fingerprint_does_not_make_an_invalid_clip_identity_usable(tmp_path, offline, mutate):
    store, path, profile = saved_clip(tmp_path)
    identity = json.loads(profile.read_bytes())
    identity.pop("fingerprint")
    identity.update(mutate)
    profile.write_text(json.dumps(_with_fingerprint(identity)))
    before, pinned = path.read_bytes(), profile.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        store.preview(store.snapshot().revision, {})
    assert path.read_bytes() == before and profile.read_bytes() == pinned


def test_corrupt_clip_fingerprint_is_not_bypassed(tmp_path, offline):
    store, path, profile = saved_clip(tmp_path)
    value = json.loads(profile.read_bytes())
    value["fingerprint"] = "0" * 64
    profile.write_text(json.dumps(value))
    before = profile.read_bytes()
    with pytest.raises(UnsafeConfigurationChange, match="profile is invalid"):
        store.preview(store.snapshot().revision, {})
    assert profile.read_bytes() == before


async def test_authenticated_provider_save_and_undo_with_pinned_clip(tmp_path):
    store, path, profile = saved_clip(tmp_path)
    before, pinned = path.read_bytes(), profile.read_bytes()
    password = "synthetic-admin-passphrase"
    site = ControlSite(path, None, signing_key=b"c" * 32,
                       password_record=AdminSecurity.create_password_record(password), port=8765)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"c" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    headers = {"Origin": site.origin}
    try:
        await client.post("/login", data={"password": password}, headers=headers, allow_redirects=False)
        cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        csrf = site.security.csrf_token(cookie)
        form = {"csrf": csrf, "profile": "aikey", "revision": site.aikey.snapshot().revision,
                "provider": "ollama", "model": "synthetic-caption-b", "api_key": "",
                "base_url": "http://127.0.0.1:11434", "max_output_tokens": "128"}
        assert (await client.post("/provider", data={**form, "csrf": "wrong"}, headers=headers)).status == 403
        assert path.read_bytes() == before and profile.read_bytes() == pinned
        response = await client.post("/provider", data=form, headers=headers, allow_redirects=False)
        assert response.status == 303
        assert json.loads(path.read_bytes())["inference"]["model"] == "synthetic-caption-b"
        assert profile.read_bytes() == pinned
        response = await client.post("/provider/rollback", data={"csrf": csrf, "profile": "aikey",
                                     "revision": site.aikey.snapshot().revision},
                                     headers=headers, allow_redirects=False)
        assert response.status == 303
        assert path.read_bytes() == before and profile.read_bytes() == pinned
    finally:
        await client.close()
        await server.close()
