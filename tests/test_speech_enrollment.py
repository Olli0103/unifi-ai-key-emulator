"""Initial explicit audio permission: no implicit scope, secret echo or live call."""

import asyncio
import html
import json
from pathlib import Path
import re
import time

import pytest

from aikey.config_store import UnsafeConfigurationChange
from test_control_site_secrets import signed_in, SECRET, assert_private_page
from test_role_models import key_store


def inventory():
    return {
        "schema": "aikey-camera-preflight/1",
        "protect_version": "7.3.74",
        "fetched_at": int(time.time()),
        "cameras": [
            {
                "id": character * 24,
                "mac": "02000000F00" + str(index),
                "name": name,
                "model": "Fictional camera",
                "state": state,
                "smart_detect_types": [],
                "audio_detect_types": audio,
            }
            for index, (character, name, state, audio) in enumerate(
                [
                    ("a", "Fictional hall <not markup>", "CONNECTED", ["alrmSpeak"]),
                    ("b", "Fictional outside", "CONNECTED", ["alrmSpeak"]),
                    ("c", "Fictional offline", "DISCONNECTED", ["alrmSpeak"]),
                    ("d", "Fictional no speech", "CONNECTED", []),
                ],
                1,
            )
        ],
    }


async def setup(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    calls = []

    async def read():
        calls.append(True)
        return inventory()

    site.inventory_loader = read
    return site, server, client, cookie, path, calls


def fields(page):
    return {
        name: html.unescape(value)
        for name, value in re.findall(r"<input type='hidden' name='([^']+)' value='([^']*)'>", page)
    }


async def preview(site, client, cookie, **changes):
    response = await client.get("/speech-enrollment")
    page = await response.text()
    tokens = re.findall(r"name='camera' value='([^']+)'", page)
    payload = {
        **fields(page),
        "provider": "openai-compatible",
        "base_url": "http://127.0.0.1:8178/v1",
        "model": "fictional-whisper",
        "language": "de",
        "camera": tokens[0],
        **changes,
    }
    response = await client.post(
        "/speech-enrollment/preview", data=payload, headers={"Origin": site.origin}
    )
    return response, tokens, payload


def confirmation(page, **changes):
    return {**fields(page), "api_key": "", "confirm": "yes", "acknowledge_cost": "yes", **changes}


def test_store_initial_only_exact_role_and_no_provider_undo_regrant(tmp_path):
    store, path = key_store(tmp_path)
    raw = json.loads(path.read_text())
    raw.pop("speech_to_text")
    path.write_text(json.dumps(raw))
    original = path.read_bytes()
    selection = {
        "provider": "openai-compatible",
        "model": "fixture-whisper",
        "base_url": "http://127.0.0.1:8178/v1",
    }
    revision = store.snapshot().revision
    p = store.preview_speech_enrollment(revision, selection, ["a" * 24])
    assert path.read_bytes() == original and not store.history_dir.exists()
    assert p.changed_fields == ("speech_to_text",)
    saved = store.enroll_speech(revision, selection, ["a" * 24])
    data = json.loads(path.read_text())
    assert data.pop("speech_to_text") == dict(selection, camera_ids=["a" * 24])
    assert data == json.loads(original)
    assert not store.provider_rollback_status()["available"]
    with pytest.raises(UnsafeConfigurationChange, match="already configured"):
        store.enroll_speech(saved.revision, selection, ["b" * 24])
    store.restrict_speech(saved.revision, [])
    assert "speech_to_text" not in store.snapshot().configuration


@pytest.mark.parametrize(
    "cameras", [[], ["a" * 24] * 2, ["x"], [True], ["A" * 24], ["a" * 24] * 33]
)
def test_store_rejects_bad_initial_scope_before_history_or_write(tmp_path, cameras):
    store, path = key_store(tmp_path)
    raw = json.loads(path.read_text())
    raw.pop("speech_to_text")
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        store.enroll_speech(
            store.snapshot().revision,
            {
                "provider": "openai-compatible",
                "model": "fixture",
                "base_url": "http://127.0.0.1/v1",
            },
            cameras,
        )
    assert path.read_bytes() == before and not store.history_dir.exists()


async def test_full_http_preview_enrollment_and_removal_without_other_profile_or_provider_effect(
    tmp_path, caplog
):
    site, server, client, cookie, path, calls = await setup(tmp_path)
    before, port_before = path.read_bytes(), site.aiport.path.read_bytes()
    try:
        response = await client.get("/speech-enrollment")
        page = await response.text()
        assert_private_page(response, page)
        assert "checked" not in page and page.count("name='camera'") == 2
        assert "Fictional offline" not in page and "Fictional no speech" not in page
        assert "&lt;not markup&gt;" in page and "<not markup>" not in page
        assert "a" * 24 not in page and "02000000F001" not in page
        result, tokens, _ = await preview(site, client, cookie)
        confirmation_page = await result.text()
        assert result.status == 200 and "Fictional hall" in confirmation_page
        assert "Fictional outside" not in confirmation_page
        assert SECRET not in confirmation_page and "speech-key-" not in confirmation_page
        assert path.read_bytes() == before and not site.aikey.history_dir.exists()
        assert not list(path.parent.glob("speech-key-*"))
        result = await client.post(
            "/speech-enrollment/apply",
            data=confirmation(confirmation_page),
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert result.status == 303
        data = json.loads(path.read_text())
        speech = data.pop("speech_to_text")
        assert speech["camera_ids"] == ["a" * 24] and speech["language"] == "de"
        assert data == json.loads(before) and site.aiport.path.read_bytes() == port_before
        assert len(calls) == 3  # Cached GET; fresh preview + apply observations.
        after = path.read_bytes()
        assert (
            await client.post(
                "/speech-enrollment/apply",
                data=confirmation(confirmation_page),
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
        ).status == 409
        assert path.read_bytes() == after
        existing_page = await (await client.get("/speech-enrollment")).text()
        assert "<form" not in existing_page and "Speech is already configured" in existing_page
        site.aikey.restrict_speech(site.aikey.snapshot().revision, [])
        assert "speech_to_text" not in site.aikey.snapshot().configuration
        assert SECRET not in caplog.text
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("provider", ["openai", "openai-compatible"])
async def test_confirmation_stores_new_private_key_only_server_side(tmp_path, provider, caplog):
    site, server, client, cookie, path, calls = await setup(tmp_path)
    try:
        response, _, _ = await preview(
            site,
            client,
            cookie,
            provider=provider,
            base_url="https://api.openai.com/v1"
            if provider == "openai"
            else "http://127.0.0.1:8178/v1",
        )
        page = await response.text()
        assert response.status == 200
        response = await client.post(
            "/speech-enrollment/apply",
            data=confirmation(page, api_key=SECRET),
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 303 and SECRET not in await response.text()
        value = json.loads(path.read_text())["speech_to_text"]
        key = path.parent / Path(value["api_key_file"]).name
        assert key.read_text().strip() == SECRET and key.stat().st_mode & 0o777 == 0o600
        settings = await (await client.get("/")).text()
        assert (
            SECRET not in settings and "speech-key-" not in settings and SECRET not in caplog.text
        )
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "kind",
    [
        "other_session",
        "expired",
        "wrong_csrf",
        "wrong_origin",
        "no_consent",
        "no_cost_ack",
        "tamper",
        "stale_revision",
        "camera_removed",
        "camera_renamed",
        "missing_cloud_key",
    ],
)
async def test_invalid_confirmation_has_no_key_or_config_effect(tmp_path, kind):
    site, server, client, cookie, path, calls = await setup(tmp_path)
    try:
        changes = (
            {"provider": "openai", "base_url": "https://api.openai.com/v1"}
            if kind == "missing_cloud_key"
            else {}
        )
        result, _, _ = await preview(site, client, cookie, **changes)
        payload = confirmation(
            await result.text(), api_key="" if kind == "missing_cloud_key" else SECRET
        )
        origin = site.origin
        if kind == "other_session":
            grant = site.admin_api.speech_enrollment.grants[payload["grant"]]
            grant["session"] = "different-session"
        elif kind == "expired":
            site.admin_api.speech_enrollment.clock = lambda: time.monotonic() + 1000
        elif kind == "wrong_csrf":
            payload["csrf"] = "wrong"
        elif kind == "wrong_origin":
            origin = "https://outside.invalid"
        elif kind == "no_consent":
            payload["confirm"] = "no"
        elif kind == "no_cost_ack":
            payload["acknowledge_cost"] = "no"
        elif kind == "tamper":
            payload["base_url"] = "https://outside.invalid/v1"
        elif kind == "stale_revision":
            path.write_bytes(path.read_bytes() + b"\n")
        elif kind.startswith("camera_"):

            async def changed():
                value = inventory()
                if kind == "camera_removed":
                    value["cameras"].pop(0)
                else:
                    value["cameras"][0]["name"] = "Changed microphone location"
                return value

            site.inventory_loader = changed
        before = path.read_bytes()
        result = await client.post(
            "/speech-enrollment/apply",
            data=payload,
            headers={"Origin": origin},
            allow_redirects=False,
        )
        assert result.status == (403 if kind in {"wrong_csrf", "wrong_origin"} else 409)
        assert path.read_bytes() == before and not list(path.parent.glob("speech-key-*"))
        assert SECRET not in await result.text()
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("stage", ["expiry", "logout"])
async def test_authority_lost_during_fresh_inventory_cannot_create_key(tmp_path, stage):
    site, server, client, cookie, path, calls = await setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    try:
        result, _, _ = await preview(site, client, cookie)
        payload = confirmation(await result.text(), api_key=SECRET)
        before = path.read_bytes()

        async def blocked():
            entered.set()
            await release.wait()
            return inventory()

        site.inventory_loader = blocked
        task = asyncio.create_task(
            client.post(
                "/speech-enrollment/apply",
                data=payload,
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        if stage == "logout":
            await client.post(
                "/logout",
                data={"csrf": site.security.csrf_token(cookie)},
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
        else:
            site.admin_api.speech_enrollment.clock = lambda: time.monotonic() + 1000
        release.set()
        result = await task
        assert result.status == (403 if stage == "logout" else 409)
        assert path.read_bytes() == before and not list(path.parent.glob("speech-key-*"))
    finally:
        release.set()
        await client.close()
        await server.close()


async def test_concurrent_confirmations_admit_one_and_uncertain_save_retains_key(
    tmp_path, monkeypatch
):
    site, server, client, cookie, path, calls = await setup(tmp_path)
    try:
        result, _, _ = await preview(site, client, cookie)
        payload = confirmation(await result.text(), api_key=SECRET)
        actual = site.aikey.enroll_speech
        commits = []

        def fail_ack(*args):
            commits.append(True)
            actual(*args)
            raise OSError("PRIVATE-CANARY")

        monkeypatch.setattr(site.aikey, "enroll_speech", fail_ack)
        entered = []
        both_entered, release = asyncio.Event(), asyncio.Event()

        async def overlap():
            entered.append(True)
            if len(entered) == 2:
                both_entered.set()
            await release.wait()
            return inventory()

        site.inventory_loader = overlap
        tasks = [
            asyncio.create_task(
                client.post(
                    "/speech-enrollment/apply",
                    data=payload,
                    headers={"Origin": site.origin},
                    allow_redirects=False,
                )
            )
            for _ in range(2)
        ]
        await asyncio.wait_for(both_entered.wait(), 2)
        assert all(not task.done() for task in tasks)
        release.set()
        results = await asyncio.gather(*tasks)
        assert sorted(r.status for r in results) == [409, 503] and commits == [True]
        raw = json.loads(path.read_text())
        key = path.parent / Path(raw["speech_to_text"]["api_key_file"]).name
        assert key.read_text().strip() == SECRET
        assert "outcome uncertain" in await next(r for r in results if r.status == 503).text()
        bodies = await asyncio.gather(*(r.text() for r in results))
        assert all("PRIVATE-CANARY" not in body for body in bodies)
    finally:
        await client.close()
        await server.close()


async def test_null_role_is_initial_setup_and_preview_capacity_does_not_evict_valid_grant(tmp_path):
    site, server, client, cookie, path, calls = await setup(tmp_path)
    try:
        raw = json.loads(path.read_text())
        raw["speech_to_text"] = None
        path.write_text(json.dumps(raw))
        response, _, payload = await preview(site, client, cookie)
        assert response.status == 200
        valid = confirmation(await response.text())
        control = site.admin_api.speech_enrollment
        existing = control.grants[valid["grant"]]
        for index in range(31):
            control.grants[f"fictional-{index}"] = dict(existing)
        before = path.read_bytes()
        response = await client.post(
            "/speech-enrollment/preview", data=payload, headers={"Origin": site.origin}
        )
        assert response.status == 409 and path.read_bytes() == before and len(control.grants) == 32
        response = await client.post(
            "/speech-enrollment/apply",
            data=valid,
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 303
        assert json.loads(path.read_text())["speech_to_text"]["camera_ids"] == ["a" * 24]
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "case", ["missing_inventory", "duplicate_name", "duplicate_mac", "stale", "no_speech"]
)
async def test_unusable_inventory_offers_no_initial_scope_and_preserves_state(tmp_path, case):
    site, server, client, cookie, path, calls = await setup(tmp_path)
    try:

        async def read():
            value = inventory()
            if case == "duplicate_name":
                value["cameras"][1]["name"] = value["cameras"][0]["name"]
            elif case == "duplicate_mac":
                value["cameras"][1]["mac"] = value["cameras"][0]["mac"]
            elif case == "stale":
                value["fetched_at"] -= 301
            elif case == "no_speech":
                for row in value["cameras"]:
                    row["audio_detect_types"] = []
            return value

        site.inventory_loader = None if case == "missing_inventory" else read
        before = path.read_bytes()
        response = await client.get("/speech-enrollment")
        page = await response.text()
        assert response.status == 200 and "<form" not in page
        assert path.read_bytes() == before and not site.admin_api.speech_enrollment.grants
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_camera",
        "raw_camera",
        "unknown_field",
        "duplicate_provider",
        "public_destination",
        "unknown_camera",
        "no_camera",
    ],
)
async def test_preview_rejects_ambiguous_or_unapproved_inputs_before_writes(tmp_path, case):
    site, server, client, cookie, path, calls = await setup(tmp_path)
    try:
        page = await (await client.get("/speech-enrollment")).text()
        token = re.findall(r"name='camera' value='([^']+)'", page)[0]
        payload = {
            **fields(page),
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:8178/v1",
            "model": "fixture",
            "language": "",
            "camera": token,
        }
        if case == "raw_camera":
            payload["camera"] = "a" * 24
        elif case == "unknown_field":
            payload["api_key"] = SECRET
        elif case == "public_destination":
            payload["base_url"] = "https://outside.invalid/v1"
        elif case == "unknown_camera":
            payload["camera"] = "f" * 64
        elif case == "no_camera":
            payload.pop("camera")
        data = list(payload.items())
        if case == "duplicate_camera":
            data.append(("camera", token))
        elif case == "duplicate_provider":
            data.append(("provider", "openai"))
        before = path.read_bytes()
        result = await client.post(
            "/speech-enrollment/preview", data=data, headers={"Origin": site.origin}
        )
        assert result.status == 409 and path.read_bytes() == before
        assert (
            not list(path.parent.glob("speech-key-*"))
            and not site.admin_api.speech_enrollment.grants
        )
    finally:
        await client.close()
        await server.close()
