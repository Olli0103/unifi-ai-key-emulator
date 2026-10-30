"""Role-change consequences are reviewed without keys, inference or writes."""

from dataclasses import asdict
import json

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey.admin_security import AdminSecurity
from aikey.aiport_config_store import AiPortConfigurationStore, AiPortRevisionConflict
from aikey.config_store import RevisionConflict, UnsafeConfigurationChange
from aikey.control_site import ControlSite, _COOKIE
from test_aiport_provider_rollback import saved_twice
from test_control_site import fixture
from test_role_models import KEY, digest, key_store
from test_control_site_secrets import signed_in, SECRET


@pytest.mark.parametrize("role", ["caption", "speech", "detection"])
def test_preview_is_pure_and_matches_the_later_exact_commit(tmp_path, monkeypatch, role):
    if role == "detection":
        store, path, _, _ = saved_twice(tmp_path)
    else:
        store, path = key_store(tmp_path)
    revision = store.snapshot().revision
    before = digest(tmp_path)
    # Merely previewing may not archive/save or create an undo record.
    with monkeypatch.context() as patch:
        patch.setattr(store, "_archive", lambda *a: pytest.fail("preview wrote history"))
        preview = store.preview_role_model(revision, role, "synthetic-proposed-b")
    assert digest(tmp_path) == before
    assert preview.current_revision == revision
    assert preview.resulting_revision != revision and preview.restart_required is True
    assert preview.role == role and preview.proposed_model == "synthetic-proposed-b"
    assert preview.job_effect == "current_runtime_until_restart"
    assert preview.index_effect == "unchanged" and preview.capability_tested is False
    assert KEY not in json.dumps(asdict(preview)) and "api_key" not in json.dumps(asdict(preview))
    changed = store.apply_role_model(revision, role, preview.proposed_model)
    assert changed.revision == preview.resulting_revision
    assert path.is_file()


@pytest.mark.parametrize("role", ["caption", "speech", "detection"])
def test_noop_preview_reports_no_restart_or_changed_identity(tmp_path, role):
    if role == "detection":
        store, _, _, _ = saved_twice(tmp_path)
    else:
        store, _ = key_store(tmp_path)
    revision = store.snapshot().revision
    model = store.role_models()[role]["model"]
    before = digest(tmp_path)
    p = store.preview_role_model(revision, role, model)
    assert p.current_revision == p.resulting_revision == revision
    assert not p.restart_required and p.job_effect == "unchanged"
    assert p.index_effect == "unchanged" and not p.capability_tested
    assert digest(tmp_path) == before


@pytest.mark.parametrize("role,model", [("text_embedding", "other-e5"),
                                       ("search_image_text", "other-clip"),
                                       ("unknown", "x"), ("caption", "../../secret")])
def test_refused_preview_has_no_history_or_configuration_side_effect(tmp_path, role, model):
    store, _ = key_store(tmp_path)
    before = digest(tmp_path)
    with pytest.raises(UnsafeConfigurationChange):
        store.preview_role_model(store.snapshot().revision, role, model)
    assert digest(tmp_path) == before


@pytest.mark.parametrize("port", [False, True])
def test_preview_never_authorizes_overwriting_a_later_revision(tmp_path, port):
    if port:
        store, path, _, _ = saved_twice(tmp_path)
        role, error = "detection", AiPortRevisionConflict
    else:
        store, path = key_store(tmp_path)
        role, error = "speech", RevisionConflict
    revision = store.snapshot().revision
    store.preview_role_model(revision, role, "synthetic-proposed-b")
    store.apply_role_model(revision, role, "synthetic-intervening-c")
    before = path.read_bytes()
    for method in (store.preview_role_model, store.apply_role_model):
        with pytest.raises(error):
            method(revision, role, "synthetic-proposed-b")
        assert path.read_bytes() == before


@pytest.mark.parametrize("profile,role", [("aikey", "caption"), ("aikey", "speech"),
                                           ("aiport", "detection")])
async def test_authenticated_http_preview_then_revision_checked_confirmation(tmp_path, profile, role):
    key, port = fixture(tmp_path)
    raw = json.loads(key.read_text())
    raw["inference"]["model"] = "synthetic-caption-a"
    raw["speech_to_text"] = {"provider": "openai-compatible", "model": "synthetic-speech-a",
                             "base_url": "http://127.0.0.1:8178/v1", "camera_ids": ["a" * 24]}
    secret_path = key.parent / "preview-secret"
    secret_path.write_text(KEY)
    secret_path.chmod(0o600)
    raw["inference"]["api_key_file"] = str(secret_path)
    key.write_text(json.dumps(raw) + "\n")
    port_store = AiPortConfigurationStore(port)
    port_store.apply(port_store.snapshot().revision, {
        "provider": "openai-compatible", "model": "synthetic-detection-a",
        "base_url": "http://127.0.0.1:8179/v1", "allow_remote": False,
        "allow_insecure_http": False, "max_output_tokens": 256,
        "threshold": 0.3, "smart_types": ["person"], "max_requests_per_hour": 12})
    password = "synthetic-preview-admin-password"
    site = ControlSite(key, port, signing_key=b"p" * 32,
                       password_record=AdminSecurity.create_password_record(password),
                       port=8765, audit_log=tmp_path / "audit.jsonl")
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = str(server.make_url("")).rstrip("/")
    site.security = AdminSecurity(b"p" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    headers = {"Origin": site.origin}
    try:
        assert (await client.post("/role-model-preview", data={}, headers=headers,
                                  allow_redirects=False)).status == 403
        await client.post("/login", data={"password": password}, headers=headers,
                          allow_redirects=False)
        cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        csrf = site.security.csrf_token(cookie)
        store = site.aikey if profile == "aikey" else site.aiports[profile]
        form = {"csrf": csrf, "profile": profile, "role": role,
                "revision": store.snapshot().revision, "model": "synthetic-proposed-b"}
        original = key.read_bytes(), port.read_bytes()
        noop = await client.post("/role-model-preview", data={
            **form, "model": store.role_models()[role]["model"]}, headers=headers)
        assert "does not check whether prior changes still need activation" in await noop.text()
        assert (key.read_bytes(), port.read_bytes()) == original
        response = await client.post("/role-model-preview", data=form, headers=headers)
        page = await response.text()
        assert response.status == 200 and "Preview model change" in page
        assert "Nothing has been saved" in page and "Current jobs keep their current runtime" in page
        assert "Search index unchanged" in page and "Model capability has not been tested" in page
        assert "action='/role-model'" in page and "Confirm model change" in page
        assert KEY not in page and password not in page
        assert (key.read_bytes(), port.read_bytes()) == original
        assert (await client.post("/role-model-preview", data={**form, "csrf": "wrong"},
                                  headers=headers)).status == 403
        assert (await client.post("/role-model-preview", data=form,
                                  headers={"Origin": "https://untrusted.invalid"})).status == 403
        saved = await client.post("/role-model", data=form, headers=headers, allow_redirects=False)
        other_path, other_bytes = (port, original[1]) if profile == "aikey" else (key, original[0])
        assert saved.status == 303 and other_path.read_bytes() == other_bytes
        stale = await client.post("/role-model-preview", data=form, headers=headers)
        assert "Model preview unavailable" in await stale.text()
        assert "synthetic-proposed-b" not in (tmp_path / "audit.jsonl").read_text()
    finally:
        await client.close()
        await server.close()


async def test_revocation_during_preview_returns_no_confirmation(tmp_path, monkeypatch):
    site, server, client, cookie, path = await signed_in(tmp_path)
    original = path.read_bytes()
    try:
        real = site.aikey.preview_role_model

        def revoked(*args):
            result = real(*args)
            site.security.revoke(cookie)
            return result

        monkeypatch.setattr(site.aikey, "preview_role_model", revoked)
        response = await client.post("/role-model-preview", data={
            "csrf": site.security.csrf_token(cookie), "profile": "aikey", "role": "caption",
            "revision": site.aikey.snapshot().revision, "model": "synthetic-proposed-b"},
            headers={"Origin": site.origin})
        assert response.status == 403 and "Confirm model change" not in await response.text()
        assert path.read_bytes() == original
    finally:
        await client.close()
        await server.close()


async def test_preview_read_failure_is_redacted_and_never_applied(tmp_path, monkeypatch):
    site, server, client, cookie, path = await signed_in(tmp_path)
    original = path.read_bytes()

    def unavailable(*args):
        raise OSError(SECRET)

    try:
        monkeypatch.setattr(site.aikey, "preview_role_model", unavailable)
        monkeypatch.setattr(site.aikey, "apply_role_model", lambda *args: pytest.fail("preview applied"))
        response = await client.post("/role-model-preview", data={
            "csrf": site.security.csrf_token(cookie), "profile": "aikey", "role": "caption",
            "revision": site.aikey.snapshot().revision, "model": "synthetic-proposed-b"},
            headers={"Origin": site.origin})
        body = await response.text()
        assert response.status == 200 and "Model preview unavailable" in body
        assert SECRET not in body and "Confirm model change" not in body
        assert path.read_bytes() == original
    finally:
        await client.close()
        await server.close()
