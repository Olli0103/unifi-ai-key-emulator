"""One-step, revision-checked rollback of a local AI Port provider save (#17).

Every slot keeps its own history and undo record next to its local config;
deployed NAS copies are never involved. All state is synthetic.
"""

import hashlib
import json
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey import aiport_config_store as store_module
from aikey.admin_security import AdminSecurity
from aikey.aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from aikey.control_site import ControlSite, _COOKIE
from test_aiport_config_store import fixture, settings
from test_control_site import fixture as site_fixture


def digest(root):
    value = hashlib.sha256()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        value.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return value.hexdigest()


def saved_twice(root):
    """Save provider settings with key A, then again with key B; returns the undo point."""
    root.mkdir(parents=True, exist_ok=True)
    store, path, _ = fixture(root)
    (root / "openai-key").write_text("synthetic-key-a\n")
    (root / "openai-key-b").write_text("synthetic-key-b\n")
    first = store.apply(store.snapshot().revision, settings(root))
    before_second = path.read_bytes()
    second = store.apply(first.revision, dict(settings(root), model="gpt-6-luna-2",
                                              api_key_file=str(root / "openai-key-b")))
    return store, path, before_second, second


def record(path):
    return path.parent / ".aiport-config-history" / f"provider-change-{path.name}.json"


def test_rollback_restores_the_exact_prior_local_config_and_touches_no_other_slot(tmp_path):
    store, path, before_second, second = saved_twice(tmp_path / "slot-2")
    other, other_path, _, _ = saved_twice(tmp_path / "slot-3")
    other_state = digest(tmp_path / "slot-3")
    status = store.provider_rollback_status()
    assert status == {"available": True, "reason": None, "revision": second.revision}
    assert "openai-key" not in json.dumps(status)
    restored = store.rollback_provider(second.revision)
    assert path.read_bytes() == before_second                       # byte-for-byte
    provider = json.loads(path.read_text())["live_pool_detector"]["provider_config"]
    assert provider["api_key_file"].endswith("/openai-key") and provider["model"] == "gpt-6-luna"
    assert restored.key_configured and restored.camera_count == 2   # identity and streams kept
    assert not record(path).exists()
    assert store.provider_rollback_status()["reason"] == "no_recorded_provider_change"
    assert digest(tmp_path / "slot-3") == other_state               # the other slot is untouched
    assert other.provider_rollback_status()["available"] is True


def test_a_stale_or_forged_revision_changes_nothing(tmp_path):
    store, path, _, second = saved_twice(tmp_path / "slot-2")
    state = digest(tmp_path / "slot-2")
    for revision in ("0" * 64, None, "short", 7):
        with pytest.raises(AiPortRevisionConflict):
            store.rollback_provider(revision)
    assert digest(tmp_path / "slot-2") == state


def test_a_missing_prior_key_reference_blocks_the_rollback(tmp_path):
    store, path, _, second = saved_twice(tmp_path / "slot-2")
    (tmp_path / "slot-2" / "openai-key").unlink()
    assert store.provider_rollback_status() == {"available": False, "reason": "prior_key_missing"}
    state = digest(tmp_path / "slot-2")
    with pytest.raises(AiPortConfigurationError, match="prior_key_missing"):
        store.rollback_provider(second.revision)
    assert digest(tmp_path / "slot-2") == state


def test_an_interrupted_write_changes_nothing_and_the_retry_converges(tmp_path, monkeypatch):
    store, path, before_second, second = saved_twice(tmp_path / "slot-2")
    saved, undo = path.read_bytes(), record(path).read_bytes()
    real = store_module.atomic_private

    def power_loss(target, content):
        if Path(target) == path:
            raise OSError("synthetic power loss before the rename")
        return real(target, content)
    monkeypatch.setattr(store_module, "atomic_private", power_loss)
    with pytest.raises(AiPortConfigurationError, match="write_uncertain"):
        store.rollback_provider(second.revision)
    assert path.read_bytes() == saved and record(path).read_bytes() == undo
    monkeypatch.setattr(store_module, "atomic_private", real)
    store.rollback_provider(second.revision)
    assert path.read_bytes() == before_second


def test_a_later_allowlist_edit_or_a_forged_record_never_rolls_back_streams(tmp_path):
    store, path, _, second = saved_twice(tmp_path / "slot-2")
    # A rollout edit of the paired streams changes the revision: the undo is withdrawn.
    data = json.loads(path.read_text())
    data["paired_streams"][1]["camera_mac"] = "2A1122334499"
    path.write_text(json.dumps(data) + "\n")
    path.chmod(0o600)
    assert store.provider_rollback_status()["reason"] == "configuration_changed_since"
    with pytest.raises(AiPortRevisionConflict):
        store.rollback_provider(store.snapshot().revision)
    # A record forged to point at an archive with other streams is refused too.
    current = path.read_bytes()
    forged = json.loads(current)
    forged["paired_streams"] = forged["paired_streams"][:1]
    forged["live_pool_detector"]["provider_config"]["model"] = "other"
    history = path.parent / ".aiport-config-history"
    forged_bytes = (json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n").encode()
    forged_revision = hashlib.sha256(forged_bytes).hexdigest()
    (history / f"{forged_revision}.json").write_bytes(forged_bytes)
    (history / f"{forged_revision}.json").chmod(0o600)
    record(path).write_text(json.dumps({"schema": 1, "from": forged_revision,
                                        "to": hashlib.sha256(current).hexdigest()}))
    assert store.provider_rollback_status()["reason"] == "identity_or_streams_differ"
    with pytest.raises(AiPortConfigurationError, match="identity_or_streams"):
        store.rollback_provider(hashlib.sha256(current).hexdigest())
    assert path.read_bytes() == current


def test_two_configs_in_one_directory_keep_separate_undo_records(tmp_path):
    store, path, _ = fixture(tmp_path)
    (tmp_path / "openai-key").write_text("synthetic-key-a\n")
    twin_path = tmp_path / "config-b.json"
    twin_path.write_bytes(path.read_bytes())
    twin_path.chmod(0o600)
    twin = store_module.AiPortConfigurationStore(twin_path)
    store.apply(store.snapshot().revision, settings(tmp_path))
    assert store.provider_rollback_status()["available"] is True
    assert twin.provider_rollback_status()["reason"] == "no_recorded_provider_change"


async def test_the_control_site_rolls_back_only_the_chosen_ai_port(tmp_path):
    key_config, mac_port = site_fixture(tmp_path / "mac")
    _, nas_port = site_fixture(tmp_path / "nas")
    password = "synthetic-admin-passphrase"
    site = ControlSite(key_config, mac_port, signing_key=b"n" * 32,
                       password_record=AdminSecurity.create_password_record(password), port=8765,
                       aiport_instances={"nas-2": nas_port})
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"n" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        await client.post("/login", data={"password": password}, headers={"Origin": site.origin},
                          allow_redirects=False)
        cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        csrf = site.security.csrf_token(cookie)
        nas_before, mac_before = nas_port.read_bytes(), mac_port.read_bytes()
        store = site.aiports["aiport:nas-2"]
        saved = await client.post("/provider", data={
            "csrf": csrf, "profile": "aiport:nas-2", "revision": store.snapshot().revision,
            "provider": "ollama", "model": "synthetic-vision", "base_url": "http://127.0.0.1:11434",
            "max_output_tokens": "128", "threshold": "0.8", "smart_types": "person",
            "max_events_per_hour": "12", "max_requests_per_hour": "24",
        }, headers={"Origin": site.origin}, allow_redirects=False)
        assert saved.status == 303
        page = await (await client.get("/")).text()
        assert "Roll back the last AI Port nas-2 provider change" in page
        assert "Roll back the last this AI Port provider change" not in page   # only the saved one
        revision = store.snapshot().revision
        wrong = await client.post("/provider/rollback", data={
            "csrf": csrf, "profile": "aiport:other", "revision": revision},
            headers={"Origin": site.origin}, allow_redirects=False)
        assert wrong.status == 400
        done = await client.post("/provider/rollback", data={
            "csrf": csrf, "profile": "aiport:nas-2", "revision": revision},
            headers={"Origin": site.origin}, allow_redirects=False)
        assert done.status == 303 and done.headers["Location"] == "/?rolledback=1"
        assert nas_port.read_bytes() == nas_before and mac_port.read_bytes() == mac_before
        notice = await (await client.get("/?rolledback=1")).text()
        assert "deployed NAS copies are not changed" in notice
    finally:
        await client.close()
        await server.close()
