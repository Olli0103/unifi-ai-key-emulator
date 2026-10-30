"""Real loopback forms, no provider/media calls and no live configuration."""

import asyncio
from html.parser import HTMLParser
import json
import threading

import aiohttp
import pytest

from test_control_site_secrets import PASSWORD, SECRET, signed_in, assert_private_page


class Forms(HTMLParser):
    def __init__(self, action):
        super().__init__()
        self.action, self.active, self.fields = action, False, {}
        self.select = None

    def handle_starttag(self, tag, attributes):
        a = dict(attributes)
        if tag == "form":
            self.active = a.get("action") == self.action
        if not self.active:
            return
        if tag == "input" and a.get("name"):
            if a.get("type") not in {"checkbox", "password"} or "checked" in a:
                self.fields[a["name"]] = a.get("value", "")
        if tag == "select":
            self.select = a.get("name")
        if tag == "option" and self.select and (self.select not in self.fields or "selected" in a):
            self.fields[self.select] = a["value"]

    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None
        if tag == "form":
            self.active = False


def form(body, action):
    parser = Forms(action)
    parser.feed(body)
    return parser.fields


async def configured(tmp_path):
    site, server, client, cookie, key = await signed_in(tmp_path)
    store = site.aiports["aiport"]
    raw = json.loads(store.path.read_text())
    raw["live_pool_detector"] = {
        "inference_backend": "vision_api", "provider_config": {
            "provider": "ollama", "model": "synthetic-detector", "base_url": "http://127.0.0.1:11434",
            "api_key_file": "/state/detection-key", "max_output_tokens": 256},
        "threshold": 0.8, "smart_types": ["person", "vehicle"], "max_requests_per_hour": 24,
        "plate_cameras": [raw["paired_streams"][0]["camera_mac"]]}
    store.path.write_text(json.dumps(raw) + "\n")
    (store.path.parent / "detection-key").write_text("synthetic-detector-only-key")
    (store.path.parent / "detection-key").chmod(0o600)
    return site, server, client, cookie, key


async def preview(client, site, *, replace=False):
    body = await (await client.get("/ocr-provider")).text()
    fields = form(body, "/ocr-provider/preview")
    fields.update(model="synthetic-ocr", base_url="http://127.0.0.1:11435")
    if replace:
        fields["replace_key"] = "yes"
    response = await client.post("/ocr-provider/preview", data=fields, headers={"Origin": site.origin})
    assert response.status == 200
    body = await response.text()
    assert_private_page(response, body)
    assert "api_key_file" not in body and "/state/ocr-key" not in body
    confirm = form(body, "/ocr-provider/apply")
    confirm["confirm"] = "yes"
    if replace:
        confirm["api_key"] = SECRET
    return confirm


async def test_preview_save_key_separation_and_exact_undo(tmp_path, caplog):
    site, server, client, _, key = await configured(tmp_path)
    try:
        store = site.aiports["aiport"]
        before, key_before = store.path.read_bytes(), key.read_bytes()
        fields = await preview(client, site, replace=True)
        assert store.path.read_bytes() == before and not list(store.path.parent.glob("ocr-key-*"))
        response = await client.post("/ocr-provider/apply", data=fields,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303 and response.headers["Location"] == "/ocr-provider?saved=1"
        raw = json.loads(store.path.read_text())
        ocr = raw["live_pool_detector"].pop("ocr_provider_config")
        assert raw == json.loads(before) and key.read_bytes() == key_before
        assert ocr["model"] == "synthetic-ocr" and ocr["base_url"].endswith("11435")
        secret = store.path.parent / ocr["api_key_file"].split("/")[-1]
        assert secret.read_text() == SECRET + "\n" and secret.stat().st_mode & 0o777 == 0o600
        for path in ["/", "/ocr-provider", "/model-registry"]:
            page = await client.get(path)
            body = await page.text()
            assert_private_page(page, body)
            assert secret.name not in body and "synthetic-detector-only-key" not in body
        assert SECRET not in caplog.text
        page = await (await client.get("/ocr-provider")).text()
        undo = form(page, "/provider/rollback")
        response = await client.post("/provider/rollback", data=undo,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303 and store.path.read_bytes() == before
        assert secret.read_text() == SECRET + "\n"  # retained for private recovery, not auto-pruned
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("fault", ["model", "base_url", "profile", "draft", "signature", "expired",
                                   "revision", "confirm", "duplicate", "extra", "origin", "csrf"])
async def test_changed_unconfirmed_or_stale_form_never_writes_a_key(tmp_path, monkeypatch, fault):
    site, server, client, _, _ = await configured(tmp_path)
    try:
        store = site.aiports["aiport"]
        before = store.path.read_bytes()
        fields = await preview(client, site, replace=True)
        headers = {"Origin": site.origin}
        if fault in {"model", "base_url", "profile", "draft", "signature", "revision"}:
            fields[fault] += "x"
        elif fault == "confirm":
            del fields["confirm"]
        elif fault == "duplicate":
            fields = list(fields.items()) + [("model", "synthetic-other")]
        elif fault == "extra":
            fields["plate_cameras"] = "2A1122334456"
        elif fault == "origin":
            headers["Origin"] = "https://foreign.invalid"
        elif fault == "csrf":
            fields["csrf"] = "wrong"
        else:
            import aikey.ocr_provider_control as module
            original = module.time.time
            monkeypatch.setattr(module.time, "time", lambda: original() + 601)
        response = await client.post("/ocr-provider/apply", data=fields, headers=headers,
                                     allow_redirects=False)
        assert response.status in {403, 409}
        assert SECRET not in await response.text()
        assert store.path.read_bytes() == before and not list(store.path.parent.glob("ocr-key-*"))
    finally:
        await client.close()
        await server.close()


async def test_current_permission_removal_invalidates_preview_and_cannot_be_undone(tmp_path):
    site, server, client, _, _ = await configured(tmp_path)
    try:
        store = site.aiports["aiport"]
        fields = await preview(client, site, replace=True)
        raw = json.loads(store.path.read_text())
        del raw["live_pool_detector"]["plate_cameras"]
        store.path.write_text(json.dumps(raw) + "\n")
        before = store.path.read_bytes()
        response = await client.post("/ocr-provider/apply", data=fields, headers={"Origin": site.origin})
        assert response.status == 409 and store.path.read_bytes() == before
        body = await (await client.get("/ocr-provider")).text()
        assert "action='/ocr-provider/preview'" not in body
        assert "existing plate-camera permission" in body
        assert not list(store.path.parent.glob("ocr-key-*"))
    finally:
        await client.close()
        await server.close()


async def test_parallel_confirmations_cannot_overwrite_the_first_key(tmp_path, monkeypatch):
    site, server, client, _, _ = await configured(tmp_path)
    release, entered = threading.Event(), threading.Event()
    task = None
    try:
        store = site.aiports["aiport"]
        fields = await preview(client, site, replace=True)
        original = store.apply_ocr_provider

        def held(*args):
            entered.set()
            assert release.wait(5)
            return original(*args)
        monkeypatch.setattr(store, "apply_ocr_provider", held)
        task = asyncio.create_task(client.post("/ocr-provider/apply", data=fields,
                                  headers={"Origin": site.origin}, allow_redirects=False))
        assert await asyncio.to_thread(entered.wait, 3)
        other = await client.post("/ocr-provider/apply", data={**fields, "api_key": "synthetic-second-key"},
                                   headers={"Origin": site.origin}, allow_redirects=False)
        assert other.status == 409
        [path] = list(store.path.parent.glob("ocr-key-*"))
        assert path.read_text() == SECRET + "\n"
        release.set()
        assert (await task).status == 303
        assert path.read_text() == SECRET + "\n"
        replay = await client.post("/ocr-provider/apply", data=fields, headers={"Origin": site.origin})
        assert replay.status == 409 and path.read_text() == SECRET + "\n"
    finally:
        release.set()
        if task is not None:
            await task
        await client.close()
        await server.close()


async def test_post_commit_failure_retains_key_and_reports_uncertainty(tmp_path, monkeypatch):
    site, server, client, _, _ = await configured(tmp_path)
    try:
        store = site.aiports["aiport"]
        fields = await preview(client, site, replace=True)
        original = store.apply_ocr_provider

        def lost(*args):
            original(*args)
            raise OSError("private-must-not-be-echoed")
        monkeypatch.setattr(store, "apply_ocr_provider", lost)
        response = await client.post("/ocr-provider/apply", data=fields, headers={"Origin": site.origin})
        body = await response.text()
        assert response.status == 503 and "outcome uncertain" in body
        assert SECRET not in body and "private-must-not-be-echoed" not in body
        [path] = list(store.path.parent.glob("ocr-key-*"))
        assert path.read_text() == SECRET + "\n"
        assert json.loads(store.path.read_text())["live_pool_detector"]["ocr_provider_config"]["api_key_file"].endswith(path.name)
    finally:
        await client.close()
        await server.close()


async def test_new_login_cannot_reuse_an_old_confirmation_even_with_current_csrf(tmp_path):
    site, server, client, _, _ = await configured(tmp_path)
    try:
        store = site.aiports["aiport"]
        fields = await preview(client, site, replace=True)
        before = store.path.read_bytes()
        client.session.cookie_jar.clear()
        response = await client.post("/login", data={"password": PASSWORD},
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        from yarl import URL
        current = client.session.cookie_jar.filter_cookies(URL(site.origin))[site.cookie_name].value
        fields["csrf"] = site.security.csrf_token(current)
        response = await client.post("/ocr-provider/apply", data=fields, headers={"Origin": site.origin})
        assert response.status == 409 and store.path.read_bytes() == before
        assert not list(store.path.parent.glob("ocr-key-*"))
    finally:
        await client.close()
        await server.close()


async def test_cloud_ocr_requires_its_own_key_not_the_detector_reference(tmp_path):
    site, server, client, _, _ = await configured(tmp_path)
    try:
        body = await (await client.get("/ocr-provider")).text()
        fields = form(body, "/ocr-provider/preview")
        fields.update(provider="openai", base_url="https://api.openai.com/v1", allow_remote="yes")
        store = site.aiports["aiport"]
        before = store.path.read_bytes()
        response = await client.post("/ocr-provider/preview", data=fields, headers={"Origin": site.origin})
        assert response.status == 409
        fields["replace_key"] = "yes"
        response = await client.post("/ocr-provider/preview", data=fields, headers={"Origin": site.origin})
        assert response.status == 200
        body = await response.text()
        assert "https://api.openai.com/v1" in body and "New separate OCR API key" in body
        assert "synthetic-detector-only-key" not in body
        confirm = form(body, "/ocr-provider/apply")
        confirm["confirm"] = "yes"
        response = await client.post("/ocr-provider/apply", data=confirm, headers={"Origin": site.origin})
        assert response.status == 409 and store.path.read_bytes() == before
        assert not list(store.path.parent.glob("ocr-key-*"))
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("key_value", ["a" * 4096, "é" * 2048, "fictional\0credential"],
                         ids=["ascii_file_overflow", "utf8_file_overflow", "nul_control"])
async def test_unreadable_or_control_character_key_refuses_before_file_creation(tmp_path, key_value):
    site, server, client, _, _ = await configured(tmp_path)
    try:
        store = site.aiports["aiport"]
        fields = await preview(client, site, replace=True)
        fields["api_key"] = key_value
        before = store.path.read_bytes()
        multipart = aiohttp.FormData()
        for name, value in fields.items():
            multipart.add_field(name, value, content_type="text/plain; charset=utf-8")
        response = await client.post("/ocr-provider/apply", data=multipart,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 409 and store.path.read_bytes() == before
        assert not list(store.path.parent.glob("ocr-key-*"))
    finally:
        await client.close()
        await server.close()


async def test_maximum_readable_credential_file_succeeds(tmp_path):
    site, server, client, _, _ = await configured(tmp_path)
    try:
        store = site.aiports["aiport"]
        fields = await preview(client, site, replace=True)
        fields["api_key"] = "a" * 4095
        response = await client.post("/ocr-provider/apply", data=fields,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        [key] = list(store.path.parent.glob("ocr-key-*"))
        from aikey.secret_files import read_secret
        assert key.stat().st_size == 4096 and read_secret(key) == fields["api_key"]
    finally:
        await client.close()
        await server.close()
