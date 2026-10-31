"""Synthetic saved-cost policy changes; no worker, provider or native operation."""

import asyncio
import hashlib
import json
import threading
import time

from multidict import MultiDict
import pytest
from yarl import URL

from aikey.caption_budget import CaptionBudget, HOUR_NS
from aikey.config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from aikey.control_site import _COOKIE
from test_control_site_secrets import signed_in, assert_private_page, PASSWORD, SECRET
from test_role_models import key_store
from test_speech_enrollment import fields


def configured(path, *, local=True, unmetered=False):
    value = json.loads(path.read_bytes())
    value["controller"]["protect_version"] = "7.3.74"
    worker = value.setdefault("worker", {})
    worker.pop("test_scope", None)
    worker.pop("test_scopes", None)
    worker.update(
        callback_mode="enabled",
        request_mp4_exports=True,
        continuous={
            "enabled": True,
            "api_key_file": "/state/PRIVATE-INVENTORY-KEY",
            "web_trust_file": "/state/PRIVATE-TRUST",
            "web_cert_file": "/state/PRIVATE-CERT",
            "refresh_seconds": 60,
            "camera_models": ["UVC G5 Flex"],
            "camera_ids": ["a" * 24],
        },
    )
    if unmetered:
        worker["continuous"]["unmetered"] = True
    if local:
        value["inference"] = {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "fictional-caption",
            "allow_insecure_http": True,
        }
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)
    return value


def store(tmp_path, **kwargs):
    s, p = key_store(tmp_path)
    configured(p, **kwargs)
    s.snapshot()  # A broken fixture must fail before a policy operation.
    return s, p


async def setup(tmp_path, **kwargs):
    site, server, client, cookie, path = await signed_in(tmp_path)
    configured(path, **kwargs)
    site.aikey.snapshot()
    return site, server, client, cookie, path


async def preview(site, client, cookie, mode="unmetered"):
    r = await client.post(
        "/caption-policy/preview",
        data={
            "csrf": site.security.csrf_token(cookie),
            "revision": site.aikey.snapshot().revision,
            "mode": mode,
        },
        headers={"Origin": site.origin},
    )
    assert r.status == 200, await r.text()
    return fields(await r.text())


def confirm(grant, *, bypass=True):
    return {**grant, "confirm": "yes", **({"acknowledge_bypass": "yes"} if bypass else {})}


def test_exact_one_flag_preserves_raw_omissions_scope_keys_history_and_reservations(tmp_path):
    s, p = store(tmp_path)
    raw = json.loads(p.read_bytes())
    raw["worker"]["continuous"]["camera_models"] = ["UVC G5 Flex"]
    raw["metadata"] = {"opaque": "PRIVATE-CANARY"}
    raw["inference"].pop("max_output_tokens", None)
    p.write_text(json.dumps(raw) + "\n")
    original = p.read_bytes()
    budget = CaptionBudget(tmp_path / "state", clock_ns=lambda: 20 * HOUR_NS)
    for i in range(12):
        budget.reserve(
            hashlib.sha256(str(i).encode()).hexdigest(),
            hashlib.sha256(("in" + str(i)).encode()).hexdigest(),
            "fictional-camera",
        )
    ledger = budget.path.read_bytes()
    before_files = {x for x in tmp_path.rglob("*")}
    current = s.snapshot().revision
    view = s.preview_caption_policy(current, "unmetered")
    assert view.changed_fields == ("worker.continuous.unmetered",) and view.restart_required
    assert p.read_bytes() == original and {x for x in tmp_path.rglob("*")} == before_files
    saved = s.apply_caption_policy(current, "unmetered")
    value = json.loads(p.read_bytes())
    assert value["worker"]["continuous"].pop("unmetered") is True and value == raw
    assert (s.history_dir / (current + ".json")).read_bytes() == original
    assert s.history_dir.stat().st_mode & 0o777 == 0o700
    assert all(x.stat().st_mode & 0o777 == 0o600 for x in s.history_dir.iterdir())
    assert not s.provider_rollback_status()["available"]
    metered = s.apply_caption_policy(saved.revision, "metered")
    assert "unmetered" not in json.loads(p.read_bytes())["worker"]["continuous"]
    assert budget.path.read_bytes() == ledger and budget.snapshot()["remaining_reservations"] == 0
    before = p.read_bytes()
    assert s.apply_caption_policy(metered.revision, "metered").revision == metered.revision
    assert p.read_bytes() == before


@pytest.mark.parametrize("mode", [None, True, {}, [], "", "off", "13", "paid", "unmetered "])
def test_bad_modes_never_archive_or_change_policy(tmp_path, mode):
    s, p = store(tmp_path)
    original = p.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        s.preview_caption_policy(s.snapshot().revision, mode)
    with pytest.raises(UnsafeConfigurationChange):
        s.apply_caption_policy(s.snapshot().revision, mode)
    assert p.read_bytes() == original and not s.history_dir.exists()


def test_remote_route_missing_continuous_and_ambiguous_source_refuse(tmp_path):
    s, p = store(tmp_path, local=False)
    assert s.caption_policy_status()["unmetered_available"] is False
    original = p.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        s.apply_caption_policy(s.snapshot().revision, "unmetered")
    assert p.read_bytes() == original and not s.history_dir.exists()
    value = json.loads(original)
    value["worker"].pop("continuous")
    p.write_text(json.dumps(value))
    assert s.caption_policy_status()["mode"] == "not_configured"
    with pytest.raises(UnsafeConfigurationChange):
        s.preview_caption_policy(s.snapshot().revision, "metered")
    configured(p)
    original = p.read_text()
    p.write_text(original.replace('"continuous": {', '"continuous": {}, "continuous": {'))
    rev = hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(UnsafeConfigurationChange):
        s.preview_caption_policy(rev, "metered")
    with pytest.raises(UnsafeConfigurationChange):
        s.caption_policy_status()


def test_stale_save_and_provider_undo_cannot_restore_a_cost_bypass(tmp_path):
    s, p = store(tmp_path, unmetered=True)
    initial = s.snapshot().revision
    changed = s.apply_role_model(initial, "caption", "fictional-changed-caption")
    assert s.provider_rollback_status()["available"]
    metered = s.apply_caption_policy(changed.revision, "metered")
    original = p.read_bytes()
    assert not s.provider_rollback_status()["available"]
    with pytest.raises(RevisionConflict):
        s.apply_caption_policy(changed.revision, "unmetered")
    assert p.read_bytes() == original and s.snapshot().revision == metered.revision


async def test_full_http_flow_saved_only_json_redacted_no_scope_or_budget_write(tmp_path, caplog):
    site, server, client, cookie, p = await setup(tmp_path)
    ledger = p.parent / "caption-budget.json"
    ledger.write_text("PRIVATE-invalid-journal-that-must-not-be-read-or-repaired")
    original = json.loads(p.read_bytes())
    port = site.aiport.path.read_bytes()
    try:
        page = await client.get("/caption-policy")
        body = await page.text()
        assert (
            page.status == 200
            and "Choose a policy" in body
            and "value='unmetered' selected" not in body
        )
        assert_private_page(page, body)
        status = await (await client.get("/api/admin/v1/caption-policy")).json()
        assert status["observation"] == "saved_policy_not_running_authority"
        assert status["caption_policy"]["mode"] == "metered"
        assert "PRIVATE" not in json.dumps(status) and str(tmp_path) not in json.dumps(status)
        before = p.read_bytes()
        grant = await preview(site, client, cookie)
        assert p.read_bytes() == before
        assert len(site.admin_api.caption_policy_control.grants) == 1
        assert cookie not in json.dumps(site.admin_api.caption_policy_control.grants)
        missing = await client.post(
            "/caption-policy/apply",
            data=confirm(grant, bypass=False),
            headers={"Origin": site.origin},
        )
        assert missing.status == 409 and p.read_bytes() == before
        saved = await client.post(
            "/caption-policy/apply", data=confirm(grant), headers={"Origin": site.origin}
        )
        assert saved.status == 200 and "Caption policy saved" in await saved.text()
        assert_private_page(saved, await saved.text())
        changed = json.loads(p.read_bytes())
        assert changed["worker"]["continuous"].pop("unmetered") is True and changed == original
        assert ledger.read_text() == "PRIVATE-invalid-journal-that-must-not-be-read-or-repaired"
        assert site.aiport.path.read_bytes() == port
        assert "PRIVATE" not in caplog.text and SECRET not in caplog.text
        assert (
            await client.post(
                "/caption-policy/apply", data=confirm(grant), headers={"Origin": site.origin}
            )
        ).status == 409
        # Going back to metered also requires its own reviewed one-use grant.
        back = await preview(site, client, cookie, "metered")
        assert (
            await client.post(
                "/caption-policy/apply",
                data=confirm(back, bypass=False),
                headers={"Origin": site.origin},
            )
        ).status == 200
        assert "unmetered" not in json.loads(p.read_bytes())["worker"]["continuous"]
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "case",
    [
        "unknown",
        "duplicate",
        "wrong_mode",
        "bad_revision",
        "bad_origin",
        "bad_csrf",
        "query",
        "file_field",
    ],
)
async def test_preview_invalid_inputs_have_no_write_or_grant(tmp_path, case):
    site, server, client, cookie, p = await setup(tmp_path)
    try:
        before = p.read_bytes()
        data = MultiDict(
            {
                "csrf": site.security.csrf_token(cookie),
                "revision": site.aikey.snapshot().revision,
                "mode": "unmetered",
            }
        )
        origin, suffix = site.origin, ""
        if case == "unknown":
            data.add("api_key", SECRET)
        elif case == "duplicate":
            data.add("mode", "metered")
        elif case == "wrong_mode":
            data["mode"] = "off"
        elif case == "bad_revision":
            data["revision"] = "bad"
        elif case == "bad_origin":
            origin = "https://unapproved.invalid"
        elif case == "bad_csrf":
            data["csrf"] = "bad"
        elif case == "query":
            suffix = "?apply=1"
        elif case == "file_field":
            import aiohttp

            form = aiohttp.FormData()
            for k, v in data.items():
                form.add_field(k, v)
            form.add_field("extra", b"untrusted", filename="not-a-policy.txt")
            data = form
        r = await client.post(
            "/caption-policy/preview" + suffix, data=data, headers={"Origin": origin}
        )
        assert r.status in {403, 409}
        assert SECRET not in await r.text() and p.read_bytes() == before
        assert (
            not site.admin_api.caption_policy_control.grants and not site.aikey.history_dir.exists()
        )
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "case",
    [
        "unknown",
        "duplicate",
        "stale",
        "expired",
        "other_session",
        "missing_confirm",
        "missing_bypass",
        "bad_csrf",
        "query",
        "file_grant",
    ],
)
async def test_confirmation_requires_current_owned_grant_and_explicit_bypass(tmp_path, case):
    site, server, client, cookie, p = await setup(tmp_path)
    try:
        grant = await preview(site, client, cookie)
        data = MultiDict(confirm(grant))
        origin, suffix = site.origin, ""
        if case == "unknown":
            data.add("mode", "metered")
        elif case == "duplicate":
            data.add("grant", grant["grant"])
        elif case == "stale":
            raw = json.loads(p.read_bytes())
            raw["device"]["name"] = "changed-by-another-editor"
            p.write_text(json.dumps(raw))
        elif case == "expired":
            site.admin_api.caption_policy_control.clock = lambda: time.monotonic() + 601
        elif case == "other_session":
            await client.post(
                "/login",
                data={"password": PASSWORD},
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
            other = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
            data["csrf"] = site.security.csrf_token(other)
        elif case == "missing_confirm":
            data.pop("confirm")
        elif case == "missing_bypass":
            data.pop("acknowledge_bypass")
        elif case == "bad_csrf":
            data["csrf"] = "bad"
        elif case == "query":
            suffix = "?apply=1"
        elif case == "file_grant":
            import aiohttp

            form = aiohttp.FormData()
            for k, v in data.items():
                if k != "grant":
                    form.add_field(k, v)
            form.add_field("grant", b"untrusted", filename="not-a-grant.txt")
            data = form
        before = p.read_bytes()
        r = await client.post(
            "/caption-policy/apply" + suffix, data=data, headers={"Origin": origin}
        )
        assert r.status in {403, 409} and p.read_bytes() == before
        assert not site.aikey.history_dir.exists()
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "stage,loss",
    [("preview", "logout"), ("preview", "expiry"), ("apply", "logout"), ("apply", "expiry")],
)
async def test_authority_lost_during_file_preview_never_admits_a_save(
    tmp_path, monkeypatch, stage, loss
):
    site, server, client, cookie, p = await setup(tmp_path)
    release, entered = threading.Event(), threading.Event()
    try:
        if stage == "apply":
            grant = await preview(site, client, cookie)
            data = confirm(grant)
        else:
            data = {
                "csrf": site.security.csrf_token(cookie),
                "revision": site.aikey.snapshot().revision,
                "mode": "unmetered",
            }
        original = site.aikey.preview_caption_policy

        def blocked(*args):
            entered.set()
            assert release.wait(5)
            return original(*args)

        monkeypatch.setattr(site.aikey, "preview_caption_policy", blocked)
        before = p.read_bytes()
        pending = asyncio.create_task(
            client.post("/caption-policy/" + stage, data=data, headers={"Origin": site.origin})
        )
        for _ in range(1000):
            if entered.is_set():
                break
            await asyncio.sleep(0.001)
        assert entered.is_set()
        if loss == "logout":
            site.security.revoke(cookie)
        else:
            site.security._clock = lambda: time.time() + 86400
        release.set()
        assert (await pending).status == 403
        assert p.read_bytes() == before and not site.aikey.history_dir.exists()
        assert not site.admin_api.operations
    finally:
        release.set()
        await client.close()
        await server.close()


async def test_two_concurrent_confirmations_admit_only_one_filesystem_save(tmp_path, monkeypatch):
    site, server, client, cookie, p = await setup(tmp_path)
    release, both = threading.Event(), threading.Event()
    try:
        grant = await preview(site, client, cookie)
        original_preview = site.aikey.preview_caption_policy
        original_apply = site.aikey.apply_caption_policy
        reads, writes = [], []

        def blocked(*args):
            reads.append(1)
            if len(reads) == 2:
                both.set()
            assert release.wait(5)
            return original_preview(*args)

        def counted(*args):
            writes.append(1)
            return original_apply(*args)

        monkeypatch.setattr(site.aikey, "preview_caption_policy", blocked)
        monkeypatch.setattr(site.aikey, "apply_caption_policy", counted)
        requests = [
            asyncio.create_task(
                client.post(
                    "/caption-policy/apply", data=confirm(grant), headers={"Origin": site.origin}
                )
            )
            for _ in range(2)
        ]
        for _ in range(1000):
            if both.is_set():
                break
            await asyncio.sleep(0.001)
        assert both.is_set()
        release.set()
        responses = await asyncio.gather(*requests)
        assert sorted(r.status for r in responses) == [200, 409] and writes == [1]
        assert json.loads(p.read_bytes())["worker"]["continuous"]["unmetered"] is True
    finally:
        release.set()
        await client.close()
        await server.close()


async def test_admitted_save_survives_waiter_cancellation_and_cleanup_drains_it(
    tmp_path, monkeypatch
):
    site, server, client, cookie, p = await setup(tmp_path)
    release, entered = threading.Event(), threading.Event()
    try:
        grant = await preview(site, client, cookie)
        original = site.aikey.apply_caption_policy
        calls = []

        def blocked(*args):
            calls.append(1)
            entered.set()
            assert release.wait(5)
            return original(*args)

        monkeypatch.setattr(site.aikey, "apply_caption_policy", blocked)
        pending = asyncio.create_task(
            client.post(
                "/caption-policy/apply", data=confirm(grant), headers={"Origin": site.origin}
            )
        )
        for _ in range(1000):
            if entered.is_set():
                break
            await asyncio.sleep(0.001)
        assert entered.is_set() and len(site.admin_api.operations) == 1
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        drain = asyncio.create_task(site.admin_api.drain(None))
        await asyncio.sleep(0.01)
        assert not drain.done()
        release.set()
        await drain
        assert (
            calls == [1] and json.loads(p.read_bytes())["worker"]["continuous"]["unmetered"] is True
        )
    finally:
        release.set()
        await client.close()
        await server.close()


async def test_full_registry_keeps_existing_valid_grant_and_uses_monotonic_expiry(tmp_path):
    site, server, client, cookie, p = await setup(tmp_path)
    try:
        first = await preview(site, client, cookie)
        for _ in range(31):
            await preview(site, client, cookie)
        r = await client.post(
            "/caption-policy/preview",
            data={
                "csrf": first["csrf"],
                "revision": site.aikey.snapshot().revision,
                "mode": "unmetered",
            },
            headers={"Origin": site.origin},
        )
        assert r.status == 409 and len(site.admin_api.caption_policy_control.grants) == 32
        assert (
            await client.post(
                "/caption-policy/apply", data=confirm(first), headers={"Origin": site.origin}
            )
        ).status == 200
    finally:
        await client.close()
        await server.close()


async def test_uncertain_ack_preserves_saved_policy_and_never_retries(tmp_path, monkeypatch):
    site, server, client, cookie, p = await setup(tmp_path)
    try:
        grant = await preview(site, client, cookie)
        original = site.aikey.apply_caption_policy
        calls = []

        def committed_then_lost(*args):
            calls.append(1)
            original(*args)
            raise ConfigurationStoreError("PRIVATE-uncertain-details")

        monkeypatch.setattr(site.aikey, "apply_caption_policy", committed_then_lost)
        r = await client.post(
            "/caption-policy/apply", data=confirm(grant), headers={"Origin": site.origin}
        )
        assert r.status == 503 and "outcome uncertain" in await r.text()
        assert "PRIVATE" not in await r.text() and calls == [1]
        assert json.loads(p.read_bytes())["worker"]["continuous"]["unmetered"] is True
        assert (
            await client.post(
                "/caption-policy/apply", data=confirm(grant), headers={"Origin": site.origin}
            )
        ).status == 409
        assert calls == [1]
    finally:
        await client.close()
        await server.close()


async def test_post_publication_validation_failure_is_uncertain_not_a_prewrite_refusal(
    tmp_path, monkeypatch
):
    site, server, client, cookie, p = await setup(tmp_path)
    try:
        grant = await preview(site, client, cookie)
        original = site.aikey._replace

        def published_then_read_failed(content):
            original(content)

            def failed_read(*args):
                raise UnsafeConfigurationChange("PRIVATE-post-publication-validation")

            monkeypatch.setattr(site.aikey, "_read", failed_read)

        monkeypatch.setattr(site.aikey, "_replace", published_then_read_failed)
        r = await client.post(
            "/caption-policy/apply", data=confirm(grant), headers={"Origin": site.origin}
        )
        body = await r.text()
        assert r.status == 503 and "outcome uncertain" in body and "PRIVATE" not in body
        assert "This request changed no settings" not in body
        assert json.loads(p.read_bytes())["worker"]["continuous"]["unmetered"] is True
        assert (
            await client.post(
                "/caption-policy/apply", data=confirm(grant), headers={"Origin": site.origin}
            )
        ).status == 409
    finally:
        await client.close()
        await server.close()
