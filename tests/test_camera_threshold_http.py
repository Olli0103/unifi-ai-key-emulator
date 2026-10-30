"""Session-bound normal loopback forms; no live cameras or provider calls."""

import asyncio
from copy import deepcopy
import json
import threading
import time

from multidict import MultiDict
import pytest

from test_ocr_provider_http import configured, form
from test_control_site_secrets import assert_private_page


def inventory(store):
    raw = json.loads(store.path.read_text())
    return {"schema": "aikey-camera-preflight/1", "protect_version": "7.3.74",
        "fetched_at": int(time.time()), "cameras": [
            {"id": f"{i + 1:024x}", "mac": stream["camera_mac"], "name": "Fictional room " + str(i + 1),
             "model": "Fictional camera", "state": "CONNECTED", "smart_detect_types": ["person"],
             "audio_detect_types": []} for i, stream in enumerate(raw["paired_streams"])]}


async def ready(tmp_path):
    site, server, client, cookie, key = await configured(tmp_path)
    report = inventory(site.aiports["aiport"])
    async def load():
        return deepcopy(report)
    site.inventory_loader = load
    return site, server, client, cookie, key, report


async def preview(site, client):
    response = await client.get("/camera-thresholds")
    body = await response.text()
    assert_private_page(response, body)
    fields = form(body, "/camera-thresholds/preview")
    token = next(k for k in fields if k.startswith("gate_"))
    fields[token] = "0.375"
    response = await client.post("/camera-thresholds/preview", data=fields, headers={"Origin": site.origin})
    assert response.status == 200
    body = await response.text()
    assert "0.375 (override)" in body
    assert_private_page(response, body)
    confirm = form(body, "/camera-thresholds/apply")
    confirm["confirm"] = "yes"
    return confirm


async def test_preview_apply_and_exact_undo_are_camera_only(tmp_path, caplog):
    site, server, client, _, key, _ = await ready(tmp_path)
    try:
        store = site.aiports["aiport"]
        before, key_before = store.path.read_bytes(), key.read_bytes()
        fields = await preview(site, client)
        assert store.path.read_bytes() == before and not store.history_dir.exists()
        body = await (await client.get("/camera-thresholds")).text()
        assert all(c not in body for c in store.configured_camera_macs())
        response = await client.post("/camera-thresholds/apply", data=fields,
                                    headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        raw = json.loads(store.path.read_bytes())
        gates = raw["live_pool_detector"].pop("camera_thresholds")
        assert list(gates.values()) == [.375] and raw == json.loads(before)
        body = await (await client.get(response.headers["Location"])).text()
        undo = form(body, "/camera-thresholds/undo")
        undo["confirm"] = "yes"
        response = await client.post("/camera-thresholds/undo", data=undo,
                                    headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        assert store.path.read_bytes() == before and key.read_bytes() == key_before
        assert "0.375" not in caplog.text and "Fictional room" not in caplog.text
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("fault", ["gate", "signature", "revision", "expires", "confirm", "extra",
    "profile", "duplicate", "csrf", "origin", "renamed_camera", "new_id", "stale_inventory", "expired_session"])
async def test_modified_or_expired_confirmation_never_saves(tmp_path, fault):
    site, server, client, cookie, _, report = await ready(tmp_path)
    try:
        store = site.aiports["aiport"]
        fields = await preview(site, client)
        if fault == "gate":
            fields[next(k for k in fields if k.startswith("gate_"))] = "0.1"
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
        response = await client.post("/camera-thresholds/apply", data=fields,
            headers={"Origin": "https://foreign.invalid" if fault == "origin" else site.origin},
            allow_redirects=False)
        assert response.status in {403, 409}
        assert store.path.read_bytes() == before and not store.history_dir.exists()
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("fault", ["missing", "stale", "version", "duplicate_mac", "duplicate_name", "mac_name"])
async def test_inventory_uncertainty_disables_named_editing(tmp_path, fault):
    site, server, client, _, _, report = await ready(tmp_path)
    try:
        if fault == "missing":
            report["cameras"] = []
        elif fault == "stale":
            report["fetched_at"] = 1
        elif fault == "version":
            report["protect_version"] = "unvalidated"
        elif fault == "duplicate_mac":
            row = deepcopy(report["cameras"][0])
            row["id"] = "f" * 24
            report["cameras"].append(row)
        elif fault == "duplicate_name":
            raw = json.loads(site.aiports["aiport"].path.read_text())
            raw["paired_streams"].append({**raw["paired_streams"][0], "camera_mac": "02000000F0C4"})
            site.aiports["aiport"].path.write_text(json.dumps(raw) + "\n")
            row = deepcopy(report["cameras"][0])
            row.update(id="f" * 24, mac="02000000F0C4")
            report["cameras"].append(row)
        else:
            report["cameras"][0]["name"] = report["cameras"][0]["mac"]
        response = await client.get("/camera-thresholds")
        body = await response.text()
        assert response.status == 200 and "Editing unavailable" in body
        assert "action='/camera-thresholds/preview'" not in body
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("value", ["0", "-0.1", "1.01", "nan", "inf", "x", "1" * 33])
async def test_bad_numeric_preview_does_not_write(tmp_path, value):
    site, server, client, _, _, _ = await ready(tmp_path)
    try:
        store = site.aiports["aiport"]
        body = await (await client.get("/camera-thresholds")).text()
        fields = form(body, "/camera-thresholds/preview")
        fields[next(k for k in fields if k.startswith("gate_"))] = value
        before = store.path.read_bytes()
        response = await client.post("/camera-thresholds/preview", data=fields, headers={"Origin": site.origin})
        assert response.status == 409 and store.path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


async def test_admitted_save_drains_after_waiter_cancellation(tmp_path, monkeypatch):
    site, server, client, _, _, _ = await ready(tmp_path)
    entered, release = threading.Event(), threading.Event()
    store = site.aiports["aiport"]
    original = store.apply_camera_thresholds
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(store, "apply_camera_thresholds", slow)
    try:
        fields = await preview(site, client)
        task = asyncio.create_task(client.post("/camera-thresholds/apply", data=fields,
                                headers={"Origin": site.origin}, allow_redirects=False))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        release.set()
        await site.admin_api.drain(None)
        assert list(json.loads(store.path.read_text())["live_pool_detector"]["camera_thresholds"].values()) == [.375]
    finally:
        release.set()
        await client.close()
        await server.close()


async def test_lost_save_acknowledgement_is_uncertain_without_automatic_retry(tmp_path, monkeypatch):
    site, server, client, _, _, _ = await ready(tmp_path)
    store = site.aiports["aiport"]
    original, calls = store.apply_camera_thresholds, []
    def lost(*args):
        calls.append(True)
        original(*args)
        raise OSError("fictional lost acknowledgement")
    monkeypatch.setattr(store, "apply_camera_thresholds", lost)
    try:
        fields = await preview(site, client)
        response = await client.post("/camera-thresholds/apply", data=fields,
                                    headers={"Origin": site.origin}, allow_redirects=False)
        body = await response.text()
        assert response.status == 503 and "outcome uncertain" in body and calls == [True]
        assert "fictional lost" not in body
        assert list(json.loads(store.path.read_text())["live_pool_detector"]["camera_thresholds"].values()) == [.375]
    finally:
        await client.close()
        await server.close()
