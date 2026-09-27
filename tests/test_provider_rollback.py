"""One-step, revision-checked rollback of an AI Key provider change (#17). Synthetic state."""

import json
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey import config_store as store_module
from aikey.admin_security import AdminSecurity
from aikey.config_store import ConfigurationStoreError, RevisionConflict
from aikey.control_site import ControlSite, _COOKIE
from test_config_store import store as make_store
from test_control_site import fixture

OLD_KEY = "-".join(("synthetic", "old", "provider", "credential"))
NEW_KEY = "-".join(("synthetic", "new", "provider", "credential"))


def changed_provider(tmp_path):
    """An OpenAI config with key A, then a saved change to a new model and key B."""
    config_store, config_path = make_store(tmp_path)
    (tmp_path / "state" / "provider-key").write_text(OLD_KEY + "\n")
    original = config_path.read_bytes()
    new_key = tmp_path / "state" / "provider-key-new"
    new_key.write_text(NEW_KEY + "\n")
    before = config_store.snapshot()
    after = config_store.apply_inference(before.revision, {
        "provider": "openai", "model": "synthetic-vision-model-2",
        "base_url": "https://api.openai.com/v1", "allow_remote": True,
        "api_key_file": str(new_key)})
    return config_store, config_path, original, after


def journal(config_path):
    return config_path.parent / ".aikey-config-history" / "provider-change.json"


def test_rollback_restores_the_exact_prior_settings_and_key_reference(tmp_path):
    config_store, config_path, original, after = changed_provider(tmp_path)
    status = config_store.provider_rollback_status()
    assert status == {"available": True, "reason": None, "revision": after.revision}
    assert OLD_KEY not in json.dumps(status) and "provider-key" not in json.dumps(status)
    restored = config_store.rollback_provider(after.revision)
    assert config_path.read_bytes() == original                        # byte-for-byte
    assert json.loads(config_path.read_text())["inference"]["api_key_file"].endswith("/provider-key")
    assert restored.configuration["inference"]["api_key_file"] == {"configured": True, "write_only": True}
    assert not journal(config_path).exists()                            # one step only
    assert config_store.provider_rollback_status()["reason"] == "no_recorded_provider_change"
    with pytest.raises(ConfigurationStoreError, match="No provider change"):
        config_store.rollback_provider(restored.revision)


def test_a_stale_or_forged_revision_changes_nothing(tmp_path):
    config_store, config_path, _, after = changed_provider(tmp_path)
    saved, record = config_path.read_bytes(), journal(config_path).read_bytes()
    for revision in ("0" * 64, None, "short"):
        with pytest.raises(RevisionConflict):
            config_store.rollback_provider(revision)
    assert config_path.read_bytes() == saved and journal(config_path).read_bytes() == record


def test_a_later_unrelated_change_withdraws_the_undo(tmp_path):
    config_store, config_path, _, after = changed_provider(tmp_path)
    later = config_store.apply(after.revision, {"device": {"name": "Synthetic processor"}})
    assert config_store.provider_rollback_status() == {
        "available": False, "reason": "configuration_changed_since"}
    saved = config_path.read_bytes()
    with pytest.raises(RevisionConflict):
        config_store.rollback_provider(later.revision)                  # would discard the rename
    assert config_path.read_bytes() == saved


def test_a_missing_prior_key_blocks_the_rollback(tmp_path):
    config_store, config_path, _, after = changed_provider(tmp_path)
    (tmp_path / "state" / "provider-key").unlink()
    assert config_store.provider_rollback_status() == {"available": False, "reason": "prior_key_missing"}
    saved, record = config_path.read_bytes(), journal(config_path).read_bytes()
    with pytest.raises(ConfigurationStoreError, match="no longer stored"):
        config_store.rollback_provider(after.revision)
    assert config_path.read_bytes() == saved and journal(config_path).read_bytes() == record


def test_an_interrupted_write_leaves_the_current_config_and_the_undo_intact(tmp_path, monkeypatch):
    config_store, config_path, original, after = changed_provider(tmp_path)
    saved, record = config_path.read_bytes(), journal(config_path).read_bytes()
    real = store_module.atomic_private

    def power_loss(path, content):
        if Path(path) == config_path:
            raise OSError("synthetic power loss before the rename")
        return real(path, content)
    monkeypatch.setattr(store_module, "atomic_private", power_loss)
    with pytest.raises(ConfigurationStoreError, match="outcome is uncertain"):
        config_store.rollback_provider(after.revision)
    assert config_path.read_bytes() == saved and journal(config_path).read_bytes() == record
    monkeypatch.setattr(store_module, "atomic_private", real)
    config_store.rollback_provider(after.revision)                      # the retry converges
    assert config_path.read_bytes() == original


def test_a_failed_undo_record_keeps_the_save_and_offers_no_undo(tmp_path, monkeypatch):
    config_store, config_path = make_store(tmp_path)
    (tmp_path / "state" / "provider-key").write_text(OLD_KEY + "\n")
    real = store_module.atomic_private

    def no_journal(path, content):
        if Path(path).name == "provider-change.json":
            raise OSError("synthetic disk full")
        return real(path, content)
    monkeypatch.setattr(store_module, "atomic_private", no_journal)
    before = config_store.snapshot()
    after = config_store.apply_inference(before.revision, {
        "provider": "openai", "model": "synthetic-vision-model-3",
        "base_url": "https://api.openai.com/v1", "allow_remote": True})
    assert after.revision != before.revision                            # the change is saved
    assert config_store.provider_rollback_status()["available"] is False


async def test_the_control_site_offers_a_revision_checked_rollback_without_secrets(tmp_path):
    key_config, port_config = fixture(tmp_path)
    data = json.loads(key_config.read_text())
    data["runtime"]["state_dir"] = "/state"
    key_config.write_text(json.dumps(data) + "\n")
    password = "synthetic-admin-passphrase"
    site = ControlSite(key_config, port_config, signing_key=b"u" * 32,
                       password_record=AdminSecurity.create_password_record(password),
                       port=8765, aiport_runtime_state_dir=Path("/state"))
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"u" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        await client.post("/login", data={"password": password}, headers={"Origin": site.origin},
                          allow_redirects=False)
        cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        csrf = site.security.csrf_token(cookie)
        assert "/provider/rollback" not in await (await client.get("/")).text()   # nothing to undo
        original = key_config.read_bytes()
        save = await client.post("/provider", data={
            "csrf": csrf, "profile": "aikey", "revision": site.aikey.snapshot().revision,
            "provider": "openai", "model": "synthetic-vision", "base_url": "https://api.openai.com/v1",
            "allow_remote": "on", "max_output_tokens": "256", "api_key": NEW_KEY,
        }, headers={"Origin": site.origin}, allow_redirects=False)
        assert save.status == 303
        page = await (await client.get("/")).text()
        assert "Roll back the last AI Key provider change" in page
        assert NEW_KEY not in page and "provider-key-" not in page
        current = site.aikey.snapshot().revision
        # Without a session token, and with a stale revision: refused, nothing changes.
        assert (await client.post("/provider/rollback", data={"revision": current},
                                  headers={"Origin": site.origin})).status == 403
        stale = await client.post("/provider/rollback", data={"csrf": csrf, "revision": "0" * 64},
                                  headers={"Origin": site.origin}, allow_redirects=False)
        assert "Rollback not applied" in await stale.text()
        assert site.aikey.snapshot().revision == current
        done = await client.post("/provider/rollback", data={"csrf": csrf, "revision": current},
                                 headers={"Origin": site.origin}, allow_redirects=False)
        assert done.status == 303 and done.headers["Location"] == "/?rolledback=1"
        assert key_config.read_bytes() == original
        after = await (await client.get("/?rolledback=1")).text()
        assert "previous AI Key provider settings were restored" in after
        assert "/provider/rollback" not in after and NEW_KEY not in after
    finally:
        await client.close()
        await server.close()
