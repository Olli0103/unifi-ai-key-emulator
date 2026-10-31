"""Fictional camera restriction, consent, freshness and uncertain-save boundaries."""

import asyncio
import hashlib
import json
import re
import threading
import time

import aiohttp
import pytest
from multidict import MultiDict
from yarl import URL

from aikey.caption_budget import CaptionBudget, HOUR_NS
from aikey.config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from test_caption_policy_control import configured, store
from test_control_site_secrets import PASSWORD, assert_private_page, signed_in
from test_speech_enrollment import fields


def scope(path):
    value = configured(path, unmetered=True)
    value["worker"]["continuous"]["camera_ids"] = ["b" * 24, "a" * 24]
    value["metadata"] = {"opaque": "PRIVATE-CANARY"}
    value["inference"].pop("max_output_tokens", None)
    path.write_text(json.dumps(value) + "\n")
    return value


async def setup(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    scope(path)
    rows = [
        {
            "id": c * 24,
            "mac": "02000000000" + str(i),
            "name": name,
            "model": "UVC G5 Flex",
            "state": "CONNECTED",
            "smart_detect_types": [],
            "audio_detect_types": [],
        }
        for i, (c, name) in enumerate(
            [("a", "Fictional office"), ("b", "Fictional garden"), ("c", "Unapproved camera")], 1
        )
    ]

    async def inventory():
        return {
            "schema": "aikey-camera-preflight/1",
            "protect_version": "7.3.74",
            "fetched_at": int(time.time()),
            "cameras": rows,
        }

    site.inventory_loader = inventory
    return site, server, client, cookie, path, rows


async def preview(site, client, cookie, *, disable=False):
    page = await (await client.get("/caption-cameras")).text()
    tokens = re.findall("name='keep_camera' value='([^']+)'", page)
    data = MultiDict(
        {
            "csrf": site.security.csrf_token(cookie),
            "revision": site.aikey.snapshot().revision,
            "mode": "disable_all" if disable else "restrict",
        }
    )
    if not disable:
        data.add("keep_camera", tokens[0])
    r = await client.post("/caption-cameras/preview", data=data, headers={"Origin": site.origin})
    assert r.status == 200, await r.text()
    return fields(await r.text()), await r.text()


def test_scope_exact_raw_restriction_then_disable_no_usage_refund_or_other_feature_change(tmp_path):
    s, p = store(tmp_path)
    raw = scope(p)
    budget = CaptionBudget(tmp_path / "budget", clock_ns=lambda: 20 * HOUR_NS)
    for i in range(12):
        budget.reserve(
            hashlib.sha256(str(i).encode()).hexdigest(),
            hashlib.sha256(("x" + str(i)).encode()).hexdigest(),
            "fake",
        )
    ledger = budget.path.read_bytes()
    old = p.read_bytes()
    rev = s.snapshot().revision
    view = s.preview_caption_restriction(rev, ["a" * 24])
    assert p.read_bytes() == old and not s.history_dir.exists()
    saved = s.restrict_captions(rev, ["a" * 24])
    assert saved.revision == view.resulting_revision
    expected = json.loads(json.dumps(raw))
    expected["worker"]["continuous"]["camera_ids"] = ["a" * 24]
    assert json.loads(p.read_bytes()) == expected
    assert (s.history_dir / (rev + ".json")).read_bytes() == old
    saved = s.restrict_captions(saved.revision, [])
    del expected["worker"]["continuous"]
    assert json.loads(p.read_bytes()) == expected
    assert not s.caption_scope_status()["configured"]
    assert budget.path.read_bytes() == ledger and budget.snapshot()["remaining_reservations"] == 0
    assert s.history_dir.stat().st_mode & 0o777 == 0o700
    assert all(f.stat().st_mode & 0o777 == 0o600 for f in s.history_dir.iterdir())
    assert not s.provider_rollback_status()["available"]


@pytest.mark.parametrize("retained", [["c" * 24], ["a" * 24, "a" * 24], [True], {}, None])
def test_unknown_or_malformed_camera_restriction_never_writes(tmp_path, retained):
    s, p = store(tmp_path)
    scope(p)
    before = p.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        s.restrict_captions(s.snapshot().revision, retained)
    assert p.read_bytes() == before and not s.history_dir.exists()


def test_order_noop_model_wide_and_stale_scope(tmp_path):
    s, p = store(tmp_path)
    scope(p)
    before = p.read_bytes()
    rev = s.snapshot().revision
    assert s.restrict_captions(rev, ["a" * 24, "b" * 24]).revision == rev
    assert p.read_bytes() == before and not s.history_dir.exists()
    with pytest.raises(RevisionConflict):
        s.restrict_captions("0" * 64, [])
    value = json.loads(before)
    del value["worker"]["continuous"]["camera_ids"]
    p.write_text(json.dumps(value))
    with pytest.raises(UnsafeConfigurationChange):
        s.restrict_captions(s.snapshot().revision, ["a" * 24])
    s.restrict_captions(s.snapshot().revision, [])
    assert "continuous" not in json.loads(p.read_bytes())["worker"]


async def test_named_preview_confirm_and_redacted_saved_json(tmp_path):
    site, server, client, cookie, p, _ = await setup(tmp_path)
    original = json.loads(p.read_bytes())
    ledger = p.parent / "caption-budget.json"
    ledger.write_bytes(b"PRIVATE-invalid-journal-must-not-be-read")
    port = site.aiport.path.read_bytes()
    try:
        r = await client.get("/caption-cameras")
        body = await r.text()
        assert_private_page(r, body)
        assert "Fictional garden" in body and "Unapproved camera" not in body
        assert all(c * 24 not in body for c in "abc") and "PRIVATE" not in body
        status = await (await client.get("/api/admin/v1/caption-cameras")).json()
        assert status["caption_scope"]["explicit_camera_count"] == 2
        assert "camera_ids" not in json.dumps(status) and "PRIVATE" not in json.dumps(status)
        before = p.read_bytes()
        grant, body = await preview(site, client, cookie)
        assert p.read_bytes() == before and "Nothing has been saved" in body
        assert "Fictional garden" in body and "Fictional office" not in body
        r = await client.post("/caption-cameras/apply", data=grant, headers={"Origin": site.origin})
        assert r.status == 409 and p.read_bytes() == before
        r = await client.post(
            "/caption-cameras/apply",
            data={**grant, "confirm": "yes"},
            headers={"Origin": site.origin},
        )
        assert r.status == 200
        expected = json.loads(json.dumps(original))
        expected["worker"]["continuous"]["camera_ids"] = ["b" * 24]
        assert json.loads(p.read_bytes()) == expected
        assert (
            await client.post(
                "/caption-cameras/apply",
                data={**grant, "confirm": "yes"},
                headers={"Origin": site.origin},
            )
        ).status == 409
        grant, _ = await preview(site, client, cookie, disable=True)
        assert (
            await client.post(
                "/caption-cameras/apply",
                data={**grant, "confirm": "yes"},
                headers={"Origin": site.origin},
            )
        ).status == 200
        del expected["worker"]["continuous"]
        assert json.loads(p.read_bytes()) == expected
        assert (
            ledger.read_bytes() == b"PRIVATE-invalid-journal-must-not-be-read"
            and site.aiport.path.read_bytes() == port
        )
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "change", ["stale_inventory", "renamed", "different_mac", "expired", "stale_revision"]
)
async def test_confirmation_refuses_changed_authority_names_or_revision(tmp_path, change):
    site, server, client, cookie, p, rows = await setup(tmp_path)
    try:
        grant, _ = await preview(site, client, cookie)
        if change == "renamed":
            rows[1]["name"] = "Renamed camera"
        elif change == "different_mac":
            rows[1]["mac"] = "020000009999"
        elif change == "expired":
            site.admin_api.caption_scope_control.clock = lambda: time.monotonic() + 601
        elif change == "stale_revision":
            p.write_bytes(p.read_bytes() + b"\n")
        else:

            async def stale():
                return {
                    "schema": "aikey-camera-preflight/1",
                    "protect_version": "7.3.74",
                    "fetched_at": int(time.time()) - 301,
                    "cameras": rows,
                }

            site.inventory_loader = stale
        before = p.read_bytes()
        r = await client.post(
            "/caption-cameras/apply",
            data={**grant, "confirm": "yes"},
            headers={"Origin": site.origin},
        )
        assert r.status == 409 and p.read_bytes() == before and not site.aikey.history_dir.exists()
    finally:
        await client.close()
        await server.close()


async def test_missing_inventory_disables_individual_edit_but_explicit_full_disable_still_works(
    tmp_path,
):
    site, server, client, cookie, p, _ = await setup(tmp_path)
    site.inventory_loader = None
    try:
        page = await (await client.get("/caption-cameras")).text()
        assert "name='keep_camera'" not in page and "value='disable_all'" in page
        grant, _ = await preview(site, client, cookie, disable=True)
        assert (
            await client.post(
                "/caption-cameras/apply",
                data={**grant, "confirm": "yes"},
                headers={"Origin": site.origin},
            )
        ).status == 200
        assert "continuous" not in json.loads(p.read_bytes())["worker"]
    finally:
        await client.close()
        await server.close()


async def test_postcommit_lost_ack_stays_uncertain_and_cannot_replay(tmp_path, monkeypatch):
    site, server, client, cookie, p, _ = await setup(tmp_path)
    original = site.aikey.restrict_captions
    calls = []

    def uncertain(*args):
        calls.append(True)
        original(*args)
        raise ConfigurationStoreError("PRIVATE-provider-details")

    monkeypatch.setattr(site.aikey, "restrict_captions", uncertain)
    try:
        grant, _ = await preview(site, client, cookie, disable=True)
        data = {**grant, "confirm": "yes"}
        r = await client.post("/caption-cameras/apply", data=data, headers={"Origin": site.origin})
        body = await r.text()
        assert r.status == 503 and "uncertain" in body and "PRIVATE" not in body
        assert "continuous" not in json.loads(p.read_bytes())["worker"]
        assert (
            await client.post("/caption-cameras/apply", data=data, headers={"Origin": site.origin})
        ).status == 409
        assert calls == [True]
    finally:
        await client.close()
        await server.close()


async def test_two_simultaneous_confirmations_admit_only_one_save(tmp_path, monkeypatch):
    site, server, client, cookie, p, _ = await setup(tmp_path)
    original = site.aikey.preview_caption_restriction
    entered, release = threading.Event(), threading.Event()

    def delayed(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    try:
        grant, _ = await preview(site, client, cookie, disable=True)
        monkeypatch.setattr(site.aikey, "preview_caption_restriction", delayed)
        data = {**grant, "confirm": "yes"}
        first = asyncio.create_task(
            client.post("/caption-cameras/apply", data=data, headers={"Origin": site.origin})
        )
        assert await asyncio.to_thread(entered.wait, 2)
        second = asyncio.create_task(
            client.post("/caption-cameras/apply", data=data, headers={"Origin": site.origin})
        )
        await asyncio.sleep(0.02)
        release.set()
        r1, r2 = await asyncio.gather(first, second)
        assert sorted([r1.status, r2.status]) == [200, 409]
        assert "continuous" not in json.loads(p.read_bytes())["worker"]
    finally:
        release.set()
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "case",
    [
        "origin",
        "csrf",
        "query",
        "duplicate_mode",
        "unknown_field",
        "empty_selection",
        "unknown_camera",
        "duplicate_camera",
        "disable_with_camera",
    ],
)
async def test_preview_invalid_admission_preserves_files_and_grants(tmp_path, case):
    site, server, client, cookie, p, _ = await setup(tmp_path)
    before = p.read_bytes()
    try:
        page = await (await client.get("/caption-cameras")).text()
        token = re.findall("name='keep_camera' value='([^']+)'", page)[0]
        data = MultiDict(
            {
                "csrf": site.security.csrf_token(cookie),
                "revision": site.aikey.snapshot().revision,
                "mode": "restrict",
                "keep_camera": token,
            }
        )
        origin, query = site.origin, ""
        if case == "origin":
            origin = "https://untrusted.example"
        elif case == "csrf":
            data["csrf"] = "invalid"
        elif case == "query":
            query = "?unexpected=1"
        elif case == "duplicate_mode":
            data.add("mode", "disable_all")
        elif case == "unknown_field":
            data["provider"] = "PRIVATE-leak-attempt"
        elif case == "empty_selection":
            del data["keep_camera"]
        elif case == "unknown_camera":
            data["keep_camera"] = "0" * 64
        elif case == "duplicate_camera":
            data.add("keep_camera", token)
        else:
            data["mode"] = "disable_all"
        r = await client.post(
            "/caption-cameras/preview" + query, data=data, headers={"Origin": origin}
        )
        assert r.status == (403 if case in {"origin", "csrf"} else 409)
        assert "PRIVATE" not in await r.text()
        assert p.read_bytes() == before and not site.aikey.history_dir.exists()
        assert not site.admin_api.caption_scope_control.grants
    finally:
        await client.close()
        await server.close()


async def test_logout_during_awaited_preview_does_not_mint_confirmation(tmp_path, monkeypatch):
    site, server, client, cookie, p, _ = await setup(tmp_path)
    original = site.aikey.preview_caption_restriction
    entered, release = threading.Event(), threading.Event()

    def delayed(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(site.aikey, "preview_caption_restriction", delayed)
    before = p.read_bytes()
    task = None
    try:
        data = {
            "csrf": site.security.csrf_token(cookie),
            "revision": site.aikey.snapshot().revision,
            "mode": "disable_all",
        }
        task = asyncio.create_task(
            client.post("/caption-cameras/preview", data=data, headers={"Origin": site.origin})
        )
        assert await asyncio.to_thread(entered.wait, 2)
        assert (
            await client.post(
                "/logout",
                data={"csrf": data["csrf"]},
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
        ).status == 303
        release.set()
        assert (await task).status == 403
        assert not site.admin_api.caption_scope_control.grants and p.read_bytes() == before
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await client.close()
        await server.close()


def test_post_publication_validation_failure_is_uncertain_not_prewrite_refusal(
    tmp_path, monkeypatch
):
    s, p = store(tmp_path)
    scope(p)
    read, replace = s._read, s._replace
    committed = False

    def publish(*args):
        nonlocal committed
        replace(*args)
        committed = True

    def failed_ack(path):
        if committed:
            raise UnsafeConfigurationChange("PRIVATE-after-publication")
        return read(path)

    revision = s.snapshot().revision
    monkeypatch.setattr(s, "_replace", publish)
    monkeypatch.setattr(s, "_read", failed_ack)
    with pytest.raises(ConfigurationStoreError) as caught:
        s.restrict_captions(revision, [])
    assert not isinstance(caught.value, UnsafeConfigurationChange)
    assert "continuous" not in json.loads(p.read_bytes())["worker"]


async def test_foreign_session_cannot_consume_another_sessions_confirmation(tmp_path):
    site, server, client, cookie, p, _ = await setup(tmp_path)
    other = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    try:
        grant, _ = await preview(site, client, cookie, disable=True)
        r = await other.post(
            site.origin + "/login",
            data={"password": PASSWORD},
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert r.status == 303
        await r.read()
        second = other.cookie_jar.filter_cookies(URL(site.origin))[site.cookie_name].value
        before = p.read_bytes()
        r = await other.post(
            site.origin + "/caption-cameras/apply",
            data={**grant, "confirm": "yes", "csrf": site.security.csrf_token(second)},
            headers={"Origin": site.origin},
        )
        assert r.status == 409 and p.read_bytes() == before
        assert grant["grant"] in site.admin_api.caption_scope_control.grants
        assert (
            await client.post(
                "/caption-cameras/apply",
                data={**grant, "confirm": "yes"},
                headers={"Origin": site.origin},
            )
        ).status == 200
    finally:
        await other.close()
        await client.close()
        await server.close()


async def test_cancelled_waiter_does_not_cancel_admitted_restriction_and_cleanup_drains(
    tmp_path, monkeypatch
):
    site, server, client, cookie, p, _ = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = site.aikey.restrict_captions
    calls = []

    def delayed(*args):
        calls.append(True)
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(site.aikey, "restrict_captions", delayed)
    task = drain = None
    try:
        grant, _ = await preview(site, client, cookie, disable=True)
        task = asyncio.create_task(
            client.post(
                "/caption-cameras/apply",
                data={**grant, "confirm": "yes"},
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
        assert "continuous" not in json.loads(p.read_bytes())["worker"] and calls == [True]
        assert not site.admin_api.caption_scope_control.grants
    finally:
        release.set()
        for pending in [task, drain]:
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
        await client.close()
        await server.close()


async def test_registry_bound_preserves_valid_grants_and_no_implicit_no_policy_conversion(tmp_path):
    site, server, client, cookie, p, _ = await setup(tmp_path)
    before = p.read_bytes()
    try:
        data = {
            "csrf": site.security.csrf_token(cookie),
            "revision": site.aikey.snapshot().revision,
            "mode": "disable_all",
        }
        first = None
        for _ in range(32):
            r = await client.post(
                "/caption-cameras/preview", data=data, headers={"Origin": site.origin}
            )
            assert r.status == 200
            first = first or fields(await r.text())
            await r.read()
        r = await client.post(
            "/caption-cameras/preview", data=data, headers={"Origin": site.origin}
        )
        assert r.status == 409 and p.read_bytes() == before
        assert len(site.admin_api.caption_scope_control.grants) == 32
        assert first["grant"] in site.admin_api.caption_scope_control.grants
        assert (
            await client.post(
                "/caption-cameras/apply",
                data={**first, "confirm": "yes"},
                headers={"Origin": site.origin},
            )
        ).status == 200
        page = await (await client.get("/caption-cameras")).text()
        assert "No continuous-caption policy" in page and "<form" not in page
        r = await client.post(
            "/caption-cameras/preview",
            data={**data, "revision": site.aikey.snapshot().revision},
            headers={"Origin": site.origin},
        )
        assert r.status == 409 and "continuous" not in json.loads(p.read_bytes())["worker"]
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("case", ["duplicate_name", "duplicate_id", "missing_saved_camera"])
async def test_ambiguous_inventory_never_names_or_restricts_the_wrong_camera(tmp_path, case):
    site, server, client, cookie, p, rows = await setup(tmp_path)
    before = p.read_bytes()
    try:
        if case == "duplicate_name":
            rows[2]["name"] = rows[0]["name"].upper()
        elif case == "duplicate_id":
            rows[2]["id"] = rows[0]["id"]
        else:
            rows.pop(1)
        page = await (await client.get("/caption-cameras")).text()
        assert "name='keep_camera'" not in page and "value='disable_all'" in page
        assert p.read_bytes() == before
        grant, _ = await preview(site, client, cookie, disable=True)
        assert (
            await client.post(
                "/caption-cameras/apply",
                data={**grant, "confirm": "yes"},
                headers={"Origin": site.origin},
            )
        ).status == 200
        assert "continuous" not in json.loads(p.read_bytes())["worker"]
    finally:
        await client.close()
        await server.close()
