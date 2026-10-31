"""Synthetic scope additions, unchanged usage and fresh named one-use authority."""

import asyncio
import hashlib
import json
import re
import time

import pytest
from multidict import MultiDict

from aikey.caption_budget import CaptionBudget, HOUR_NS
from aikey.config_store import RevisionConflict, UnsafeConfigurationChange
from test_caption_scope_control import setup as restriction_setup, scope
from test_caption_policy_control import store
from test_speech_enrollment import fields


async def setup(tmp_path):
    site, server, client, cookie, path, rows = await restriction_setup(tmp_path)
    rows[2]["name"] = "Fictional new camera"
    rows[2]["smart_detect_types"] = ["person"]
    return site, server, client, cookie, path, rows


async def preview(site, client, cookie):
    r = await client.get("/caption-camera-enrollment")
    page = await r.text()
    tokens = re.findall("name='add_camera' value='([^']+)'", page)
    assert r.status == 200 and len(tokens) == 1, page
    r = await client.post(
        "/caption-camera-enrollment/preview",
        data={
            "csrf": site.security.csrf_token(cookie),
            "revision": site.aikey.snapshot().revision,
            "add_camera": tokens[0],
        },
        headers={"Origin": site.origin},
    )
    assert r.status == 200, await r.text()
    return fields(await r.text()), await r.text()


def consent(grant, *, bypass=True):
    return {
        **grant,
        "confirm": "yes",
        "acknowledge_processing": "yes",
        **({"acknowledge_bypass": "yes"} if bypass else {}),
    }


def test_store_appends_exactly_one_raw_field_and_preserves_full_budget(tmp_path):
    s, path = store(tmp_path)
    raw = scope(path)
    original = path.read_bytes()
    budget = CaptionBudget(tmp_path / "budget", clock_ns=lambda: 20 * HOUR_NS)
    for i in range(12):
        budget.reserve(
            hashlib.sha256(str(i).encode()).hexdigest(),
            hashlib.sha256(("input" + str(i)).encode()).hexdigest(),
            "fictional",
        )
    journal = budget.path.read_bytes()
    rev = s.snapshot().revision
    additions = [{"id": "c" * 24, "model": "UVC G5 Flex"}]
    view = s.preview_caption_enrollment(rev, additions)
    assert path.read_bytes() == original and not s.history_dir.exists()
    saved = s.enroll_caption_cameras(rev, additions)
    assert saved.revision == view.resulting_revision
    expected = json.loads(json.dumps(raw))
    expected["worker"]["continuous"]["camera_ids"] += ["c" * 24]
    assert json.loads(path.read_bytes()) == expected
    assert (s.history_dir / (rev + ".json")).read_bytes() == original
    assert budget.path.read_bytes() == journal
    assert budget.snapshot()["remaining_reservations"] == 0
    assert not s.provider_rollback_status()["available"]
    with pytest.raises(RevisionConflict):
        s.enroll_caption_cameras(rev, additions)
    with pytest.raises(UnsafeConfigurationChange):
        s.enroll_caption_cameras(saved.revision, additions)


@pytest.mark.parametrize(
    "additions",
    [
        None,
        {},
        [],
        [True],
        [{"id": "c" * 24}],
        [{"id": "c" * 24, "model": "other"}],
        [{"id": "a" * 24, "model": "UVC G5 Flex"}],
        [{"id": "../outside", "model": "UVC G5 Flex"}],
        [{"id": "c" * 24, "model": "UVC G5 Flex", "raw": "PRIVATE"}],
        [{"id": "c" * 24, "model": "UVC G5 Flex"}] * 2,
    ],
)
def test_invalid_additions_refused_before_history_or_write(tmp_path, additions):
    s, p = store(tmp_path)
    scope(p)
    original = p.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        s.enroll_caption_cameras(s.snapshot().revision, additions)
    assert p.read_bytes() == original and not s.history_dir.exists()


@pytest.mark.parametrize("mode", ["missing", "model_wide", "full"])
def test_initial_or_model_wide_or_full_scope_is_not_converted(tmp_path, mode):
    s, p = store(tmp_path)
    raw = scope(p)
    if mode == "missing":
        del raw["worker"]["continuous"]
    elif mode == "model_wide":
        del raw["worker"]["continuous"]["camera_ids"]
    else:
        raw["worker"]["continuous"]["camera_ids"] = [f"{i:024x}" for i in range(16)]
    p.write_text(json.dumps(raw))
    original = p.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        s.enroll_caption_cameras(s.snapshot().revision, [{"id": "c" * 24, "model": "UVC G5 Flex"}])
    assert p.read_bytes() == original and not s.history_dir.exists()


async def test_named_unchecked_preview_and_separate_bypass_consent(tmp_path):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        original = path.read_bytes()
        page = await (await client.get("/caption-camera-enrollment")).text()
        assert "Fictional new camera" in page and " checked" not in page
        assert all(v not in page for v in ["c" * 24, rows[2]["mac"], "PRIVATE-CANARY"])
        grant, reviewed = await preview(site, client, cookie)
        assert "acknowledge_bypass" in reviewed and "no rolling caption" in reviewed
        assert " checked" not in reviewed and "Nothing saved" in reviewed
        r = await client.post(
            "/caption-camera-enrollment/apply",
            data=consent(grant, bypass=False),
            headers={"Origin": site.origin},
        )
        assert r.status == 409 and path.read_bytes() == original
        r = await client.post(
            "/caption-camera-enrollment/apply", data=consent(grant), headers={"Origin": site.origin}
        )
        assert r.status == 200 and "saved" in await r.text()
        saved = path.read_bytes()
        assert json.loads(saved)["worker"]["continuous"]["camera_ids"] == [
            "b" * 24,
            "a" * 24,
            "c" * 24,
        ]
        r = await client.post(
            "/caption-camera-enrollment/apply", data=consent(grant), headers={"Origin": site.origin}
        )
        assert r.status == 409 and path.read_bytes() == saved
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "mutation", ["name", "model", "mac", "id", "offline", "legacy", "ambiguous", "version", "stale"]
)
async def test_changed_or_ineligible_fresh_inventory_cannot_confirm(tmp_path, mutation):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        grant, _ = await preview(site, client, cookie)
        original = path.read_bytes()
        if mutation == "name":
            rows[2]["name"] = "Different name"
        elif mutation == "model":
            rows[2]["model"] = "Different model"
        elif mutation == "mac":
            rows[2]["mac"] = "020000000009"
        elif mutation == "id":
            rows[2]["id"] = "d" * 24
        elif mutation == "offline":
            rows[2]["state"] = "DISCONNECTED"
        elif mutation == "legacy":
            rows[2]["smart_detect_types"] = []
        elif mutation == "ambiguous":
            rows[0]["name"] = rows[2]["name"].upper()
        else:

            async def inventory():
                return {
                    "schema": "aikey-camera-preflight/1",
                    "protect_version": "7.3.70" if mutation == "version" else "7.3.74",
                    "fetched_at": int(time.time()) - (600 if mutation == "stale" else 0),
                    "cameras": rows,
                }

            site.inventory_loader = inventory
        r = await client.post(
            "/caption-camera-enrollment/apply", data=consent(grant), headers={"Origin": site.origin}
        )
        assert (
            r.status == 409
            and path.read_bytes() == original
            and not site.aikey.history_dir.exists()
        )
    finally:
        await client.close()
        await server.close()


async def test_metered_confirmation_preserves_mode_and_no_bypass_form(tmp_path):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        raw = json.loads(path.read_bytes())
        del raw["worker"]["continuous"]["unmetered"]
        path.write_text(json.dumps(raw))
        grant, page = await preview(site, client, cookie)
        assert "acknowledge_bypass" not in page and "share the existing" in page
        r = await client.post(
            "/caption-camera-enrollment/apply", data=consent(grant), headers={"Origin": site.origin}
        )
        assert r.status == 409
        r = await client.post(
            "/caption-camera-enrollment/apply",
            data=consent(grant, bypass=False),
            headers={"Origin": site.origin},
        )
        assert r.status == 200
        assert "unmetered" not in json.loads(path.read_bytes())["worker"]["continuous"]
    finally:
        await client.close()
        await server.close()


async def test_aggregate_json_and_anonymous_refusal(tmp_path):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        r = await client.get("/api/admin/v1/caption-camera-enrollment")
        body = await r.text()
        assert r.status == 200
        v = json.loads(body)["caption_enrollment"]
        assert v["explicit_camera_count"] == 2 and v["existing_cost_mode"] == "unmetered"
        assert all(
            s not in body for s in ["b" * 24, "c" * 24, rows[2]["mac"], str(path), "PRIVATE"]
        )
        r = await client.get("/api/admin/v1/caption-camera-enrollment?extra=yes")
        assert r.status == 400
        client.session.cookie_jar.clear()
        r = await client.get("/api/admin/v1/caption-camera-enrollment")
        assert r.status == 401
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "bad",
    [
        "no_confirm",
        "no_processing",
        "duplicate_confirm",
        "duplicate_bypass",
        "extra",
        "expired",
        "revision",
    ],
)
async def test_refusals_preserve_files_and_do_not_enroll(tmp_path, bad):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        grant, _ = await preview(site, client, cookie)
        data = MultiDict(consent(grant))
        if bad == "no_confirm":
            del data["confirm"]
        elif bad == "no_processing":
            del data["acknowledge_processing"]
        elif bad == "duplicate_confirm":
            data.add("confirm", "yes")
        elif bad == "duplicate_bypass":
            data.add("acknowledge_bypass", "yes")
        elif bad == "extra":
            data["raw_camera_id"] = "c" * 24
        elif bad == "expired":
            site.admin_api.caption_camera_enrollment.clock = lambda: 10**15
        else:
            raw = json.loads(path.read_bytes())
            raw["metadata"]["other"] = "later"
            path.write_text(json.dumps(raw))
        original = path.read_bytes()
        r = await client.post(
            "/caption-camera-enrollment/apply", data=data, headers={"Origin": site.origin}
        )
        assert (
            r.status == 409
            and path.read_bytes() == original
            and not site.aikey.history_dir.exists()
        )
    finally:
        await client.close()
        await server.close()


async def test_two_concurrent_confirmations_commit_once(tmp_path, monkeypatch):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    calls = []
    original = site.aikey.enroll_caption_cameras

    def tracked(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(site.aikey, "enroll_caption_cameras", tracked)
    try:
        grant, _ = await preview(site, client, cookie)

        async def send():
            r = await client.post(
                "/caption-camera-enrollment/apply",
                data=consent(grant),
                headers={"Origin": site.origin},
            )
            await r.read()
            return r.status

        assert sorted(await asyncio.gather(send(), send())) == [200, 409]
        assert calls == [True]
        assert (
            json.loads(path.read_bytes())["worker"]["continuous"]["camera_ids"].count("c" * 24) == 1
        )
    finally:
        await client.close()
        await server.close()


async def test_committed_ack_loss_is_uncertain_and_never_automatically_retried(
    tmp_path, monkeypatch
):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    original = site.aikey.enroll_caption_cameras
    calls = []

    def lost(*args):
        calls.append(True)
        original(*args)
        raise OSError("PRIVATE CANARY")

    monkeypatch.setattr(site.aikey, "enroll_caption_cameras", lost)
    try:
        grant, _ = await preview(site, client, cookie)
        r = await client.post(
            "/caption-camera-enrollment/apply", data=consent(grant), headers={"Origin": site.origin}
        )
        body = await r.text()
        assert r.status == 503 and "PRIVATE" not in body and "may have succeeded" in body
        saved = path.read_bytes()
        assert "c" * 24 in json.loads(saved)["worker"]["continuous"]["camera_ids"]
        r = await client.post(
            "/caption-camera-enrollment/apply", data=consent(grant), headers={"Origin": site.origin}
        )
        assert r.status == 409 and path.read_bytes() == saved and calls == [True]
    finally:
        await client.close()
        await server.close()


async def test_grant_bound_and_no_silent_eviction(tmp_path):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        page = await (await client.get("/caption-camera-enrollment")).text()
        [token] = re.findall("name='add_camera' value='([^']+)'", page)
        data = {
            "csrf": site.security.csrf_token(cookie),
            "revision": site.aikey.snapshot().revision,
            "add_camera": token,
        }
        first = None
        for _ in range(32):
            r = await client.post(
                "/caption-camera-enrollment/preview", data=data, headers={"Origin": site.origin}
            )
            assert r.status == 200
            body = await r.text()
            first = first or fields(body)
        original = path.read_bytes()
        r = await client.post(
            "/caption-camera-enrollment/preview", data=data, headers={"Origin": site.origin}
        )
        assert r.status == 409 and path.read_bytes() == original
        assert len(site.admin_api.caption_camera_enrollment.grants) == 32
        assert first["grant"] in site.admin_api.caption_camera_enrollment.grants
        r = await client.post(
            "/caption-camera-enrollment/apply", data=consent(first), headers={"Origin": site.origin}
        )
        assert r.status == 200
    finally:
        await client.close()
        await server.close()


async def test_logged_out_preview_cannot_mint_grant(tmp_path):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = site.inventory_loader

    async def delayed():
        entered.set()
        await release.wait()
        return await original()

    task = None
    try:
        page = await (await client.get("/caption-camera-enrollment")).text()
        [token] = re.findall("name='add_camera' value='([^']+)'", page)
        site.inventory_loader = delayed
        data = {
            "csrf": site.security.csrf_token(cookie),
            "revision": site.aikey.snapshot().revision,
            "add_camera": token,
        }
        task = asyncio.create_task(
            client.post(
                "/caption-camera-enrollment/preview", data=data, headers={"Origin": site.origin}
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        r = await client.post(
            "/logout",
            data={"csrf": data["csrf"]},
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert r.status == 303
        release.set()
        assert (await task).status == 403
        assert not site.admin_api.caption_camera_enrollment.grants
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await client.close()
        await server.close()


async def test_cancelled_waiter_does_not_cancel_admitted_addition(tmp_path, monkeypatch):
    import threading

    site, server, client, cookie, path, rows = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = site.aikey.enroll_caption_cameras
    calls = []

    def delayed(*args):
        calls.append(True)
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(site.aikey, "enroll_caption_cameras", delayed)
    task = drain = None
    try:
        grant, _ = await preview(site, client, cookie)
        task = asyncio.create_task(
            client.post(
                "/caption-camera-enrollment/apply",
                data=consent(grant),
                headers={"Origin": site.origin},
            )
        )
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        drain = asyncio.create_task(site.admin_api.drain(None))
        await asyncio.sleep(0.02)
        assert not drain.done()
        release.set()
        await asyncio.wait_for(drain, 2)
        assert json.loads(path.read_bytes())["worker"]["continuous"]["camera_ids"] == [
            "b" * 24,
            "a" * 24,
            "c" * 24,
        ]
        assert calls == [True] and not site.admin_api.caption_camera_enrollment.grants
    finally:
        release.set()
        await asyncio.gather(*[t for t in [task, drain] if t is not None], return_exceptions=True)
        await client.close()
        await server.close()


async def test_foreign_session_cannot_consume_reviewed_grant(tmp_path):
    import aiohttp
    from yarl import URL
    from test_control_site_secrets import PASSWORD

    site, server, client, cookie, path, rows = await setup(tmp_path)
    other = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    try:
        grant, _ = await preview(site, client, cookie)
        r = await other.post(
            site.origin + "/login",
            data={"password": PASSWORD},
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert r.status == 303
        await r.read()
        second = other.cookie_jar.filter_cookies(URL(site.origin))[site.cookie_name].value
        original = path.read_bytes()
        r = await other.post(
            site.origin + "/caption-camera-enrollment/apply",
            data={**consent(grant), "csrf": site.security.csrf_token(second)},
            headers={"Origin": site.origin},
        )
        assert r.status == 409 and path.read_bytes() == original
        assert grant["grant"] in site.admin_api.caption_camera_enrollment.grants
        r = await client.post(
            "/caption-camera-enrollment/apply", data=consent(grant), headers={"Origin": site.origin}
        )
        assert r.status == 200
    finally:
        await other.close()
        await client.close()
        await server.close()
