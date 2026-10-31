"""Fictional initial-caption consent, exact stored bytes and no live effects."""

import asyncio
import hashlib
import json
import re
import threading

from multidict import MultiDict
import pytest

from aikey.caption_budget import CaptionBudget, HOUR_NS
from aikey.caption_setup import CaptionSetupBinding
from aikey.config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from test_caption_camera_enrollment import setup as existing_setup
from test_caption_policy_control import store
from test_speech_enrollment import fields


def initial(path):
    raw = json.loads(path.read_bytes())
    raw["worker"].pop("continuous", None)
    raw["controller"]["host"] = "192.168.1.2"
    path.write_text(json.dumps(raw) + "\n")
    return raw


def binding(**changes):
    values = dict(
        controller="192.168.1.2",
        api_key_file="/state/PRIVATE-INVENTORY-KEY",
        web_trust_file="/state/PRIVATE-TRUST",
        web_cert_file="/state/PRIVATE-CERT",
    )
    return CaptionSetupBinding(**(values | changes))


async def setup(tmp_path):
    site, server, client, cookie, path, rows = await existing_setup(tmp_path)
    initial(path)
    site.caption_setup_binding = binding()
    return site, server, client, cookie, path, rows


async def preview(site, client):
    r = await client.get("/caption-setup")
    page = await r.text()
    tokens = re.findall("name='camera' value='([^']+)'", page)
    assert r.status == 200 and tokens, page
    r = await client.post(
        "/caption-setup/preview",
        data={**fields(page), "camera": tokens[0]},
        headers={"Origin": site.origin},
    )
    assert r.status == 200, await r.text()
    return fields(await r.text()), await r.text()


def consent(grant):
    return {**grant, "confirm": "yes", "acknowledge_processing": "yes"}


def test_store_exact_initial_policy_full_budget_and_history_preserved(tmp_path):
    s, path = store(tmp_path)
    raw = initial(path)
    original = path.read_bytes()
    budget = CaptionBudget(tmp_path / "budget", clock_ns=lambda: 20 * HOUR_NS)
    for i in range(12):
        budget.reserve(
            hashlib.sha256(str(i).encode()).hexdigest(),
            hashlib.sha256(("input" + str(i)).encode()).hexdigest(),
            "fictional",
        )
    ledger = budget.path.read_bytes()
    rev = s.snapshot().revision
    cameras = [{"id": "b" * 24, "model": "Fictional B"}, {"id": "a" * 24, "model": "Fictional A"}]
    p = s.preview_caption_setup(rev, binding(), cameras)
    assert p.changed_fields == ("worker.continuous",) and p.restart_required
    assert path.read_bytes() == original and not s.history_dir.exists()
    saved = s.apply_caption_setup(rev, binding(), cameras)
    assert saved.revision == p.resulting_revision
    data = json.loads(path.read_bytes())
    policy = data["worker"].pop("continuous")
    assert data == raw
    assert policy == dict(
        enabled=True,
        api_key_file="/state/PRIVATE-INVENTORY-KEY",
        web_trust_file="/state/PRIVATE-TRUST",
        web_cert_file="/state/PRIVATE-CERT",
        refresh_seconds=60,
        camera_ids=["a" * 24, "b" * 24],
        camera_models=["Fictional A", "Fictional B"],
    )
    assert (s.history_dir / (rev + ".json")).read_bytes() == original
    assert budget.path.read_bytes() == ledger and budget.snapshot()["remaining_reservations"] == 0
    assert not s.provider_rollback_status()["available"]
    with pytest.raises(RevisionConflict):
        s.apply_caption_setup(rev, binding(), cameras)
    with pytest.raises(UnsafeConfigurationChange):
        s.apply_caption_setup(saved.revision, binding(), cameras)


@pytest.mark.parametrize(
    "cameras",
    [
        None,
        {},
        [],
        [True],
        [{"id": "a" * 24}],
        [{"id": "a" * 24, "model": "A", "unmetered": True}],
        [{"id": "a" * 24, "model": "A"}] * 2,
        [{"id": "A" * 24, "model": "A"}],
        [{"id": "a" * 24, "model": "\n"}],
        [{"id": "../outside", "model": "A"}],
        [{"id": f"{i:024x}", "model": "A"} for i in range(17)],
    ],
)
def test_bad_scope_has_no_history_or_replacement(tmp_path, cameras):
    s, path = store(tmp_path)
    initial(path)
    before = path.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        s.preview_caption_setup(s.snapshot().revision, binding(), cameras)
    assert path.read_bytes() == before and not s.history_dir.exists()


@pytest.mark.parametrize(
    "mutation", ["configured", "host", "version", "callbacks", "exports", "test_scope"]
)
def test_initial_prerequisites_never_converted(tmp_path, mutation):
    s, path = store(tmp_path)
    raw = initial(path)
    if mutation == "configured":
        from test_caption_policy_control import configured

        raw = configured(path)
        raw["controller"]["host"] = "192.168.1.2"
    elif mutation == "host":
        raw["controller"]["host"] = "192.168.1.3"
    elif mutation == "version":
        raw["controller"]["protect_version"] = "99.0.0"
    elif mutation == "callbacks":
        raw["worker"]["callback_mode"] = "disabled"
    elif mutation == "exports":
        raw["worker"]["request_mp4_exports"] = False
    else:
        # Pure prerequisite rejection: shape need not be accepted by validate_config.
        from aikey.caption_setup import prerequisites

        raw["worker"]["test_scopes"] = []
        with pytest.raises(ValueError):
            prerequisites(raw, binding())
        return
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    assert s.caption_setup_status(binding())["available"] is False
    with pytest.raises(UnsafeConfigurationChange):
        s.apply_caption_setup(s.snapshot().revision, binding(), [{"id": "a" * 24, "model": "A"}])
    assert path.read_bytes() == before and not s.history_dir.exists()


@pytest.mark.parametrize("host", ["127.0.0.1", "8.8.8.8", "localhost", "192.168.1.2\n", "::1"])
def test_setup_binding_requires_exact_private_ipv4(host):
    with pytest.raises(ValueError):
        CaptionSetupBinding(host, "/a", "/b", "/c")


@pytest.mark.parametrize("path", ["relative", "/a/../b", "/a\n", "", "/" + "a" * 1024])
def test_binding_fixed_path_validation(path):
    with pytest.raises(ValueError):
        CaptionSetupBinding("192.168.1.2", path, "/b", "/c")


async def test_named_unchecked_review_exact_apply_and_one_use(tmp_path):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        before, port = path.read_bytes(), site.aiport.path.read_bytes()
        grant, page = await preview(site, client)
        assert " checked" not in page and "12 caption reservations" in page
        assert "Fictional new camera" in page and "local-only" in page
        assert all(
            s not in page for s in [rows[2]["id"], rows[2]["mac"], "PRIVATE-", "acknowledge_bypass"]
        )
        assert path.read_bytes() == before
        r = await client.post(
            "/caption-setup/apply", data=consent(grant), headers={"Origin": site.origin}
        )
        assert r.status == 200 and "Initial captions saved" in await r.text()
        policy = json.loads(path.read_bytes())["worker"]["continuous"]
        assert policy["camera_ids"] == ["c" * 24] and "unmetered" not in policy
        assert site.aiport.path.read_bytes() == port
        saved = path.read_bytes()
        assert (
            await client.post(
                "/caption-setup/apply", data=consent(grant), headers={"Origin": site.origin}
            )
        ).status == 409
        assert path.read_bytes() == saved
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "name",
        "model",
        "id",
        "mac",
        "offline",
        "legacy",
        "ambiguous",
        "version",
        "stale",
        "host",
        "binding",
        "loader",
    ],
)
async def test_fresh_identity_and_authority_bound_at_confirmation(tmp_path, mutation):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        grant, _ = await preview(site, client)
        if mutation in {"name", "model", "id", "mac"}:
            rows[2][mutation] = {
                "name": "Other",
                "model": "Other",
                "id": "d" * 24,
                "mac": "020000000009",
            }[mutation]
        elif mutation == "offline":
            rows[2]["state"] = "DISCONNECTED"
        elif mutation == "legacy":
            rows[2]["smart_detect_types"] = []
        elif mutation == "ambiguous":
            rows[0]["name"] = rows[2]["name"].upper()
        elif mutation == "binding":
            site.caption_setup_binding = binding()
        elif mutation == "host":
            raw = json.loads(path.read_bytes())
            raw["controller"]["host"] = "192.168.1.3"
            path.write_text(json.dumps(raw))
        else:
            old = site.inventory_loader

            async def changed():
                report = await old()
                if mutation == "version":
                    report["protect_version"] = "7.3.60"
                if mutation == "stale":
                    report["fetched_at"] -= 301
                return report

            site.inventory_loader = changed
        before = path.read_bytes()
        assert (
            await client.post(
                "/caption-setup/apply", data=consent(grant), headers={"Origin": site.origin}
            )
        ).status == 409
        assert path.read_bytes() == before and not site.aikey.history_dir.exists()
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "no_processing",
        "no_confirm",
        "unknown",
        "duplicate",
        "expired",
        "logout",
        "csrf",
        "origin",
        "foreign",
    ],
)
async def test_consent_session_and_form_boundaries(tmp_path, mutation):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        grant, _ = await preview(site, client)
        data = MultiDict(consent(grant))
        headers = {"Origin": site.origin}
        if mutation == "no_processing":
            data.pop("acknowledge_processing")
        elif mutation == "no_confirm":
            data.pop("confirm")
        elif mutation == "unknown":
            data["api_key_file"] = "/outside"
        elif mutation == "duplicate":
            data.add("grant", data["grant"])
        elif mutation == "expired":
            site.admin_api.caption_setup_control.clock = lambda: float("inf")
        elif mutation == "logout":
            site.security.revoke(cookie)
        elif mutation == "csrf":
            data["csrf"] = "bad"
        elif mutation == "origin":
            headers["Origin"] = "https://foreign.invalid"
        else:
            site.admin_api.caption_setup_control.grants[grant["grant"]]["session"] = "0" * 64
        before = path.read_bytes()
        r = await client.post("/caption-setup/apply", data=data, headers=headers)
        assert r.status in {403, 409} and path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


async def test_grants_bounded_concurrent_apply_one_write_and_uncertain_ack(tmp_path, monkeypatch):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        grant, _ = await preview(site, client)
        control = site.admin_api.caption_setup_control
        control.grants.update({f"{i:064x}": {**control.grants[grant["grant"]]} for i in range(31)})
        page = await (await client.get("/caption-setup")).text()
        token = re.search("name='camera' value='([^']+)'", page)[1]
        r = await client.post(
            "/caption-setup/preview",
            data={**fields(page), "camera": token},
            headers={"Origin": site.origin},
        )
        assert r.status == 409 and len(control.grants) == 32
        original = site.aikey.apply_caption_setup
        calls = []

        def lost(*args):
            calls.append(True)
            original(*args)
            raise ConfigurationStoreError("PRIVATE-CANARY")

        monkeypatch.setattr(site.aikey, "apply_caption_setup", lost)
        replies = await asyncio.gather(
            *(
                client.post(
                    "/caption-setup/apply", data=consent(grant), headers={"Origin": site.origin}
                )
                for _ in range(2)
            )
        )
        assert sorted(r.status for r in replies) == [409, 503] and calls == [True]
        texts = [await r.text() for r in replies]
        assert "PRIVATE-CANARY" not in "".join(texts) and any(
            "may have succeeded" in t for t in texts
        )
        assert "continuous" in json.loads(path.read_bytes())["worker"]
    finally:
        await client.close()
        await server.close()


async def test_admitted_save_is_drained_after_handler_cancellation(tmp_path, monkeypatch):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    released, entered = threading.Event(), threading.Event()
    try:
        grant, _ = await preview(site, client)
        original = site.aikey.apply_caption_setup

        def slow(*args):
            entered.set()
            assert released.wait(5)
            return original(*args)

        monkeypatch.setattr(site.aikey, "apply_caption_setup", slow)
        task = asyncio.create_task(
            site.admin_api.file_operation(
                site.admin_api.caption_setup_control._commit,
                site.admin_api.caption_setup_control.grants[grant["grant"]],
            )
        )
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert site.admin_api.operations
        released.set()
        await site.admin_api.drain(None)
        assert (
            not site.admin_api.operations
            and "continuous" in json.loads(path.read_bytes())["worker"]
        )
    finally:
        released.set()
        await client.close()
        await server.close()


@pytest.mark.parametrize("mutation", ["missing_binding", "existing_policy", "missing_model"])
async def test_unavailable_initial_setup_does_not_fetch_inventory(tmp_path, mutation):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        if mutation == "missing_binding":
            site.caption_setup_binding = None
        else:
            raw = json.loads(path.read_bytes())
            if mutation == "existing_policy":
                from aikey.caption_setup import initial_policy

                raw["worker"]["continuous"] = initial_policy(
                    raw, binding(), [{"id": "c" * 24, "model": "A"}]
                )
            else:
                raw["inference"]["model"] = ""
            path.write_text(json.dumps(raw))

        async def forbidden():
            pytest.fail("No inventory fetch without initial setup prerequisites")

        site.inventory_loader = forbidden
        before = path.read_bytes()
        response = await client.get("/caption-setup")
        page = await response.text()
        assert response.status == 200 and "Initial setup is unavailable" in page
        assert "action='/caption-setup/preview'" not in page and "PRIVATE-" not in page
        assert path.read_bytes() == before and not site.aikey.history_dir.exists()
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("mutation", ["version", "stale", "duplicate_id", "duplicate_mac"])
async def test_same_loader_rejects_changed_inventory_contract(tmp_path, mutation):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        report = await site.inventory_loader()

        async def fixed_loader():
            return report

        site.inventory_loader = fixed_loader
        grant, _ = await preview(site, client)
        if mutation == "version":
            report["protect_version"] = "7.3.60"
        elif mutation == "stale":
            report["fetched_at"] -= 301
        elif mutation == "duplicate_id":
            rows[0]["id"] = rows[2]["id"]
        else:
            rows[0]["mac"] = rows[2]["mac"]
        before = path.read_bytes()
        r = await client.post(
            "/caption-setup/apply", data=consent(grant), headers={"Origin": site.origin}
        )
        assert r.status == 409 and path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("mutation", ["logout", "expiry"])
async def test_awaited_preview_cannot_revive_authority(tmp_path, monkeypatch, mutation):
    site, server, client, cookie, path, rows = await setup(tmp_path)
    try:
        grant, _ = await preview(site, client)
        original = site.aikey.preview_caption_setup

        def changed(*args):
            result = original(*args)
            if mutation == "logout":
                site.security.revoke(cookie)
            else:
                site.admin_api.caption_setup_control.clock = lambda: float("inf")
            return result

        monkeypatch.setattr(site.aikey, "preview_caption_setup", changed)
        before = path.read_bytes()
        r = await client.post(
            "/caption-setup/apply", data=consent(grant), headers={"Origin": site.origin}
        )
        assert r.status in {403, 409} and path.read_bytes() == before
    finally:
        await client.close()
        await server.close()
