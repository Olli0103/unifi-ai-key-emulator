"""Explicit single-use plate-crop consent, fictional inventory and private routes."""

import asyncio
import json
import re
import threading

from multidict import MultiDict
import pytest

from test_camera_threshold_http import ready
from test_ocr_provider_http import form
from test_control_site_secrets import assert_private_page


async def preview(site, client, *, selected=True):
    response = await client.get("/plate-cameras")
    body = await response.text()
    assert_private_page(response, body)
    assert "plate-data permission" in body and "Vehicle detection is not enabled" not in body
    fields = form(body, "/plate-cameras/preview")
    # The fixture's one existing plate permission is checked in the rendered form.
    token = re.search(r"name='(gate_[0-9a-f]{64})'", body).group(1)
    if selected:
        fields[token] = "yes"
    else:
        fields.pop(token, None)
    response = await client.post("/plate-cameras/preview", data=fields, headers={"Origin": site.origin})
    body = await response.text()
    assert response.status == 200 and "Confirm plate permissions" in body
    assert "http://127.0.0.1:11434" in body and "synthetic-detector" in body
    assert_private_page(response, body)
    fields = form(body, "/plate-cameras/apply")
    fields["confirm"] = "yes"
    return fields


async def test_disable_last_plate_camera_discloses_and_removes_ocr_route_not_key(tmp_path):
    site, server, client, _, key, _ = await ready(tmp_path)
    try:
        store = site.aiports["aiport"]
        raw = json.loads(store.path.read_text())
        raw["live_pool_detector"]["ocr_provider_config"] = {
            "provider": "ollama", "model": "synthetic-detector", "base_url": "http://127.0.0.1:11434",
            "api_key_file": "/state/ocr-retained-key"}
        store.path.write_text(json.dumps(raw) + "\n")
        retained = store.path.parent / "ocr-retained-key"
        retained.write_text("fictional-existing-ocr-key")
        retained.chmod(0o600)
        before, key_bytes = store.path.read_bytes(), key.read_bytes()
        body = await (await client.get("/plate-cameras")).text()
        fields = form(body, "/plate-cameras/preview")
        for k in list(fields):
            if k.startswith("gate_"):
                fields.pop(k)
        response = await client.post("/plate-cameras/preview", data=fields, headers={"Origin": site.origin})
        body = await response.text()
        assert "separate saved OCR assignment will also be removed" in body
        fields = form(body, "/plate-cameras/apply")
        fields["confirm"] = "yes"
        assert store.path.read_bytes() == before
        response = await client.post("/plate-cameras/apply", data=fields,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        changed = json.loads(store.path.read_text())
        expected = json.loads(before)
        expected["live_pool_detector"].pop("plate_cameras")
        expected["live_pool_detector"].pop("ocr_provider_config")
        assert changed == expected and key.read_bytes() == key_bytes
        assert retained.read_text() == "fictional-existing-ocr-key"
        body = await (await client.get(response.headers["Location"])).text()
        assert "Plate permissions saved; running service unchanged." in body
        assert "fictional-existing-ocr-key" not in body and "ocr-retained-key" not in body
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("fault", ["selection", "signature", "revision", "expires", "confirm", "extra",
    "profile", "duplicate", "csrf", "origin", "renamed_camera", "new_id", "stale_inventory", "expired_session"])
async def test_changed_expired_or_unsigned_confirmation_never_saves(tmp_path, fault):
    site, server, client, cookie, _, report = await ready(tmp_path)
    try:
        store = site.aiports["aiport"]
        fields = await preview(site, client)
        if fault == "selection":
            fields.pop(next(k for k in fields if k.startswith("gate_")))
        elif fault == "signature":
            fields["signature"] = "0" * 64
        elif fault == "revision":
            fields["revision"] = "0" * 64
        elif fault == "expires":
            fields["expires"] = "1"
        elif fault == "confirm":
            fields.pop("confirm")
        elif fault == "extra":
            fields["allow_remote"] = "yes"
        elif fault == "profile":
            fields["profile"] = "aikey"
        elif fault == "duplicate":
            fields = MultiDict(fields)
            fields.add("revision", fields["revision"])
        elif fault == "csrf":
            fields["csrf"] = "wrong"
        elif fault == "renamed_camera":
            report["cameras"][0]["name"] = "Another room"
        elif fault == "new_id":
            report["cameras"][0]["id"] = "f" * 24
        elif fault == "stale_inventory":
            report["fetched_at"] = 1
        elif fault == "expired_session":
            site.security.revoke(cookie)
        before = store.path.read_bytes()
        response = await client.post("/plate-cameras/apply", data=fields,
            headers={"Origin": "https://foreign.invalid" if fault == "origin" else site.origin},
            allow_redirects=False)
        assert response.status in {403, 409} and store.path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


async def test_prior_grant_cannot_replay_after_exact_byte_revocation(tmp_path):
    site, server, client, _, _, _ = await ready(tmp_path)
    try:
        store = site.aiports["aiport"]
        raw = json.loads(store.path.read_text())
        raw["live_pool_detector"].pop("plate_cameras")
        store.path.write_text(json.dumps(raw) + "\n")
        original = store.path.read_bytes()
        fields = await preview(site, client, selected=True)
        response = await client.post("/plate-cameras/apply", data=fields,
                                    headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        # Model an explicit external restoration to the same bytes/revision (ABA).
        store.path.write_bytes(original)
        response = await client.post("/plate-cameras/apply", data=fields,
                                    headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 409 and store.path.read_bytes() == original
    finally:
        await client.close()
        await server.close()


async def test_two_open_previews_cannot_both_admit_even_on_noop(tmp_path):
    site, server, client, _, _, _ = await ready(tmp_path)
    try:
        first = await preview(site, client)
        second = await preview(site, client)
        assert first["draft"] != second["draft"] and first["signature"] != second["signature"]
        a = await client.post("/plate-cameras/apply", data=second, headers={"Origin": site.origin}, allow_redirects=False)
        b = await client.post("/plate-cameras/apply", data=first, headers={"Origin": site.origin}, allow_redirects=False)
        assert a.status == 303 and b.status == 409
    finally:
        await client.close()
        await server.close()


async def test_lost_acknowledgement_is_uncertain_and_confirmation_consumed(tmp_path, monkeypatch):
    site, server, client, _, _, _ = await ready(tmp_path)
    store = site.aiports["aiport"]
    original, calls = store.apply_plate_cameras, []
    def lost(*args):
        calls.append(True)
        original(*args)
        raise OSError("fictional lost acknowledgement")
    monkeypatch.setattr(store, "apply_plate_cameras", lost)
    try:
        fields = await preview(site, client, selected=False)
        response = await client.post("/plate-cameras/apply", data=fields, headers={"Origin": site.origin}, allow_redirects=False)
        body = await response.text()
        assert response.status == 503 and "outcome uncertain" in body and calls == [True]
        assert "fictional lost" not in body and "plate_cameras" not in json.loads(store.path.read_text())["live_pool_detector"]
        response = await client.post("/plate-cameras/apply", data=fields, headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 409 and calls == [True]
    finally:
        await client.close()
        await server.close()


async def test_admitted_permission_save_drains_after_waiter_cancellation(tmp_path, monkeypatch):
    site, server, client, _, _, _ = await ready(tmp_path)
    entered, release = threading.Event(), threading.Event()
    store = site.aiports["aiport"]
    original = store.apply_plate_cameras
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(store, "apply_plate_cameras", slow)
    try:
        fields = await preview(site, client, selected=False)
        task = asyncio.create_task(client.post("/plate-cameras/apply", data=fields,
                                headers={"Origin": site.origin}, allow_redirects=False))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        release.set()
        await site.admin_api.drain(None)
        assert "plate_cameras" not in json.loads(store.path.read_text())["live_pool_detector"]
    finally:
        release.set()
        await client.close()
        await server.close()


async def test_plate_permission_does_not_silently_enable_vehicle_class(tmp_path):
    site, server, client, _, _, _ = await ready(tmp_path)
    try:
        store = site.aiports["aiport"]
        raw = json.loads(store.path.read_text())
        raw["live_pool_detector"]["smart_types"] = ["person"]
        store.path.write_text(json.dumps(raw) + "\n")
        before = store.path.read_bytes()
        body = await (await client.get("/plate-cameras")).text()
        assert "Vehicle detection is not enabled" in body and store.path.read_bytes() == before
    finally:
        await client.close()
        await server.close()
