"""Revision-checked per-role model settings (#17). Synthetic configs only."""

import hashlib
import json
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey import clip, search
from aikey.admin_security import AdminSecurity
from aikey.aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from aikey.config_store import (FIXED_ROLE_MODELS, RevisionConflict, UnsafeConfigurationChange)
from aikey.control_site import ControlSite, _COOKIE
from test_aiport_provider_rollback import saved_twice
from test_config_store import store as make_store
from test_control_site import fixture

KEY = "-".join(("synthetic", "role", "provider", "credential"))


def key_store(tmp_path):
    store, path = make_store(tmp_path)
    (tmp_path / "state" / "provider-key").write_text(KEY + "\n")
    data = json.loads(path.read_text())
    data["speech_to_text"] = {"provider": "openai-compatible", "model": "synthetic-whisper-a",
                              "base_url": "http://127.0.0.1:8178/v1", "camera_ids": ["a" * 24]}
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)
    return store, path


def digest(root):
    value = hashlib.sha256()
    for p in sorted(x for x in Path(root).rglob("*") if x.is_file() and not x.name.endswith(".lock")):
        value.update(str(p.relative_to(root)).encode() + p.read_bytes())
    return value.hexdigest()


def test_roles_are_listed_without_writing_and_search_encoders_stay_pinned(tmp_path):
    store, path = key_store(tmp_path)
    before = path.read_bytes()
    roles = store.role_models()
    assert roles["caption"] == {"model": "synthetic-vision-model", "editable": True, "reason": None}
    assert roles["speech"] == {"model": "synthetic-whisper-a", "editable": True, "reason": None}
    assert FIXED_ROLE_MODELS == {"search_image_text": clip.MODEL, "text_embedding": search.MODEL}
    for role in FIXED_ROLE_MODELS:
        assert roles[role]["editable"] is False and roles[role]["reason"] == "index_migration_required"
    assert path.read_bytes() == before
    # Saving the current model is a no-op: same revision, no undo record.
    revision = store.snapshot().revision
    assert store.apply_role_model(revision, "speech", "synthetic-whisper-a").revision == revision
    assert store.provider_rollback_status()["reason"] == "no_recorded_provider_change"


def test_a_model_change_touches_only_that_role_and_can_be_undone_exactly(tmp_path):
    store, path = key_store(tmp_path)
    original, before = path.read_bytes(), json.loads(path.read_text())
    after = store.apply_role_model(store.snapshot().revision, "speech", "synthetic-whisper-b")
    changed = json.loads(path.read_text())
    assert changed["speech_to_text"]["model"] == "synthetic-whisper-b"
    changed["speech_to_text"]["model"] = before["speech_to_text"]["model"]
    assert changed == before                                      # nothing else moved, key included
    assert store.provider_rollback_status()["available"] is True
    store.rollback_provider(after.revision)
    assert path.read_bytes() == original


@pytest.mark.parametrize("role,model,error", [
    ("search_image_text", "clip-ViT-B-32", "role_not_editable"),
    ("text_embedding", "other-e5", "role_not_editable"),
    ("vision", "x", "unknown_role"),
    ("speech", "", "invalid_model_id"),
    ("speech", "two words", "invalid_model_id"),
    ("speech", "../../etc/passwd", "invalid_model_id"),
    ("speech", "m" * 129, "invalid_model_id"),
    ("speech", None, "invalid_model_id"),
])
def test_wrong_roles_and_invalid_models_change_nothing(tmp_path, role, model, error):
    store, path = key_store(tmp_path)
    before = digest(tmp_path)
    with pytest.raises(UnsafeConfigurationChange, match=error):
        store.apply_role_model(store.snapshot().revision, role, model)
    assert digest(tmp_path) == before


def test_a_stale_revision_changes_nothing(tmp_path):
    store, path = key_store(tmp_path)
    before = digest(tmp_path)
    for revision in ("0" * 64, None, "short"):
        with pytest.raises(RevisionConflict):
            store.apply_role_model(revision, "speech", "synthetic-whisper-b")
    assert digest(tmp_path) == before


def test_an_ai_port_detection_change_stays_in_its_slot(tmp_path):
    slot2, path2, _, _ = saved_twice(tmp_path / "slot-2")
    slot3, path3, _, _ = saved_twice(tmp_path / "slot-3")
    other = digest(tmp_path / "slot-3")
    before = json.loads(path2.read_text())
    assert slot2.role_models() == {"detection": {"model": "gpt-6-luna-2", "editable": True, "reason": None}}
    slot2.apply_role_model(slot2.snapshot().revision, "detection", "synthetic-detector-c")
    changed = json.loads(path2.read_text())
    assert changed["live_pool_detector"]["provider_config"]["model"] == "synthetic-detector-c"
    changed["live_pool_detector"]["provider_config"]["model"] = before["live_pool_detector"]["provider_config"]["model"]
    assert changed == before and digest(tmp_path / "slot-3") == other
    with pytest.raises(AiPortConfigurationError, match="unknown_role"):
        slot2.apply_role_model(slot2.snapshot().revision, "caption", "x")
    with pytest.raises(AiPortConfigurationError, match="invalid_model_id"):
        slot2.apply_role_model(slot2.snapshot().revision, "detection", "bad model")
    with pytest.raises(AiPortRevisionConflict):
        slot2.apply_role_model("0" * 64, "detection", "synthetic-detector-d")


async def test_the_control_site_edits_one_role_without_exposing_secrets(tmp_path):
    key_config, port_config = fixture(tmp_path)
    data = json.loads(key_config.read_text())
    data["speech_to_text"] = {"provider": "openai-compatible", "model": "synthetic-whisper-a",
                              "base_url": "http://127.0.0.1:8178/v1", "camera_ids": ["a" * 24]}
    key_config.write_text(json.dumps(data) + "\n")
    key_config.chmod(0o600)
    password = "synthetic-admin-passphrase"
    audit = tmp_path / "audit.jsonl"
    site = ControlSite(key_config, port_config, signing_key=b"r" * 32,
                       password_record=AdminSecurity.create_password_record(password), port=8765,
                       audit_log=audit)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"r" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    origin = {"Origin": site.origin}
    try:
        await client.post("/login", data={"password": password}, headers=origin, allow_redirects=False)
        cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        csrf = site.security.csrf_token(cookie)
        page = await (await client.get("/")).text()
        section = page.split("Models by role")[1].split("</section>")[0]
        assert "synthetic-whisper-a" in section and "Pinned by the search index" in section
        assert "name='role' value='search_image_text'" not in section         # no form for pinned roles
        assert "api_key" not in section and "provider-key" not in section
        port_before = port_config.read_bytes()
        form = {"csrf": csrf, "profile": "aikey", "role": "speech",
                "revision": site.aikey.snapshot().revision, "model": "synthetic-whisper-b"}
        assert (await client.post("/role-model", data={**form, "csrf": "forged"}, headers=origin)).status == 403
        wrong = await client.post("/role-model", data={**form, "role": "text_embedding"}, headers=origin)
        assert "Model not saved" in await wrong.text()
        stale = await client.post("/role-model", data={**form, "revision": "0" * 64}, headers=origin)
        assert "Model not saved" in await stale.text()
        saved = await client.post("/role-model", data=form, headers=origin, allow_redirects=False)
        assert saved.status == 303
        assert json.loads(key_config.read_text())["speech_to_text"]["model"] == "synthetic-whisper-b"
        assert port_config.read_bytes() == port_before                      # other profile untouched
        after = await (await client.get("/")).text()
        assert "Roll back the last AI Key provider change" in after
        entries = [(e["action"], e["result"]) for e in site.audit.recent(10) if e["action"] == "role_model"]
        assert entries == [("role_model", "saved"), ("role_model", "rejected"),
                           ("role_model", "rejected"), ("role_model", "forbidden")]
        assert "synthetic-whisper" not in audit.read_text()                 # audit keeps codes only
    finally:
        await client.close()
        await server.close()
