"""Saved-only worker controls on fictional state; never model or process calls."""

import asyncio
import json
import threading

from multidict import MultiDict
import pytest

from aikey.config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from aikey.worker_limits import LIMITS, proposed_limits
from test_control_site_secrets import signed_in, assert_private_page
from test_role_models import key_store
from test_speech_enrollment import fields


def selected(**changes):
    return {**{k: v[1] for k, v in LIMITS.items()}, "inference_concurrency": 1, **changes}


def configured(path):
    raw = json.loads(path.read_bytes())
    raw.setdefault("worker", {}).update(max_concurrency=4, inference_concurrency=1)
    path.write_text(json.dumps(raw) + "\n")
    path.chmod(0o600)


async def preview(site, client, cookie, **changes):
    payload = {k: str(v) for k, v in selected(max_concurrency=4, max_queue=16, **changes).items()}
    payload.update(csrf=site.security.csrf_token(cookie), revision=site.aikey.snapshot().revision)
    r = await client.post("/worker-limits/preview", data=payload, headers={"Origin": site.origin})
    assert r.status == 200, await r.text()
    return fields(await r.text())


async def apply(site, client, grant, **changes):
    return await client.post(
        "/worker-limits/apply",
        data={**grant, "confirm": "yes", "acknowledge_load": "yes", **changes},
        headers={"Origin": site.origin},
    )


def test_saved_only_six_fields_preserve_every_other_raw_field_and_history(tmp_path):
    s, p = key_store(tmp_path)
    configured(p)
    original = p.read_bytes()
    raw = json.loads(original)
    raw["metadata"] = {"opaque": "PRIVATE-CANARY"}
    raw["worker"]["opaque_future_option"] = [1, 2, 3]
    p.write_text(json.dumps(raw))
    original = p.read_bytes()
    values = selected(
        max_concurrency=8,
        max_queue=32,
        inference_concurrency=2,
        timeout_s=300,
        caption_timeout_s=240,
        speech_timeout_s=600,
    )
    before = s.worker_limits_status()
    result = s.preview_worker_limits(before["revision"], values)
    assert p.read_bytes() == original
    assert set(result.changed_fields) == {
        "worker." + k for k in values if before["limits"][k] != values[k]
    }
    after = s.apply_worker_limits(before["revision"], values)
    assert after.revision == result.resulting_revision
    changed = json.loads(p.read_bytes())
    for k in values:
        if before["limits"][k] != values[k]:
            changed["worker"].pop(k)
            raw["worker"].pop(k, None)
    assert changed == raw
    assert (s.history_dir / (before["revision"] + ".json")).read_bytes() == original


def test_same_values_keep_original_bytes_and_omitted_defaults(tmp_path):
    s, p = key_store(tmp_path)
    configured(p)
    before = p.read_bytes()
    status = s.worker_limits_status()
    assert (
        s.apply_worker_limits(status["revision"], status["limits"]).revision == status["revision"]
    )
    assert p.read_bytes() == before
    assert not s.history_dir.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"max_queue": 0},
        {"max_queue": 1025},
        {"max_queue": True},
        {"max_concurrency": 33},
        {"inference_concurrency": None},
        {"inference_concurrency": 0},
        {"inference_concurrency": 2},
        {"timeout_s": 901},
        {"caption_timeout_s": 601},
        {"speech_timeout_s": 901},
        {"timeout_s": 1.0},
        {"timeout_s": "120"},
        {"drain_s": 600},
    ],
)
def test_invalid_or_non_allowlisted_options_never_write(tmp_path, changes):
    s, p = key_store(tmp_path)
    before = p.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        s.apply_worker_limits(s.snapshot().revision, selected(**changes))
    assert p.read_bytes() == before
    assert not s.history_dir.exists()


def test_stale_revision_and_gate_invariant(tmp_path):
    s, p = key_store(tmp_path)
    first = s.snapshot().revision
    s.apply_worker_limits(first, selected())
    before = p.read_bytes()
    with pytest.raises(RevisionConflict):
        s.apply_worker_limits(first, selected(max_queue=32))
    assert p.read_bytes() == before
    with pytest.raises(ValueError):
        proposed_limits({k: v for k, v in selected().items() if k != "max_queue"})


async def test_page_preview_confirm_and_replay(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    configured(path)
    before = path.read_bytes()
    try:
        r = await client.get("/worker-limits")
        body = await r.text()
        assert r.status == 200
        assert_private_page(r, body)
        assert "Saved settings only" in body and "repair a stalled backend" in body
        r = await client.get("/api/admin/v1/worker-limits")
        value = await r.json()
        assert value["saved_only"] and value["limits"]["max_concurrency"] == 4
        grant = await preview(site, client, cookie)
        assert path.read_bytes() == before
        r = await apply(site, client, grant)
        assert r.status == 200 and "running AI Key is unchanged" in await r.text()
        assert json.loads(path.read_bytes())["worker"]["max_queue"] == 16
        saved = path.read_bytes()
        assert (await apply(site, client, grant)).status == 409
        assert path.read_bytes() == saved
    finally:
        await client.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_load",
        "bad_confirm",
        "duplicate",
        "extra",
        "query",
        "stale",
        "expired",
        "logout",
        "cross_session",
    ],
)
async def test_confirmation_refusals_preserve_bytes(tmp_path, mutation):
    site, server, client, cookie, path = await signed_in(tmp_path)
    configured(path)
    try:
        grant = await preview(site, client, cookie)
        data = MultiDict({**grant, "confirm": "yes", "acknowledge_load": "yes"})
        route = "/worker-limits/apply"
        if mutation == "missing_load":
            del data["acknowledge_load"]
        if mutation == "bad_confirm":
            data["confirm"] = "no"
        if mutation == "duplicate":
            data.add("grant", grant["grant"])
        if mutation == "extra":
            data["timeout_s"] = "900"
        if mutation == "query":
            route += "?x=1"
        if mutation == "stale":
            raw = json.loads(path.read_bytes())
            raw["metadata"] = {"later": True}
            path.write_text(json.dumps(raw))
        if mutation == "expired":
            site.admin_api.worker_limits_control.clock = lambda: float("inf")
        if mutation in {"logout", "cross_session"}:
            await client.post(
                "/logout",
                data={"csrf": site.security.csrf_token(cookie)},
                headers={"Origin": site.origin},
            )
            if mutation == "cross_session":
                from test_control_site_secrets import PASSWORD

                await client.post(
                    "/login", data={"password": PASSWORD}, headers={"Origin": site.origin}
                )
        before = path.read_bytes()
        r = await client.post(route, data=data, headers={"Origin": site.origin})
        assert r.status in {403, 409}
        assert path.read_bytes() == before
    finally:
        await client.close()


async def test_post_commit_ack_failure_is_uncertain_not_retry(tmp_path, monkeypatch):
    site, server, client, cookie, path = await signed_in(tmp_path)
    configured(path)
    real = site.aikey.apply_worker_limits
    calls = []

    def lost_ack(*args):
        calls.append(True)
        real(*args)
        raise ConfigurationStoreError("private failure detail")

    try:
        grant = await preview(site, client, cookie)
        monkeypatch.setattr(site.aikey, "apply_worker_limits", lost_ack)
        r = await apply(site, client, grant)
        body = await r.text()
        assert (
            r.status == 503
            and "may have succeeded" in body
            and "private failure detail" not in body
        )
        assert json.loads(path.read_bytes())["worker"]["max_queue"] == 16
        assert (await apply(site, client, grant)).status == 409
        assert len(calls) == 1
    finally:
        await client.close()


async def test_shared_operation_is_drained_after_waiter_cancellation(tmp_path, monkeypatch):
    site, server, client, cookie, path = await signed_in(tmp_path)
    configured(path)
    started, release = threading.Event(), threading.Event()
    real = site.aikey.apply_worker_limits

    def blocked(*args):
        started.set()
        assert release.wait(5)
        return real(*args)

    try:
        grant = await preview(site, client, cookie)
        monkeypatch.setattr(site.aikey, "apply_worker_limits", blocked)
        task = asyncio.create_task(apply(site, client, grant))
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.005)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert site.admin_api.operations
        release.set()
        await site.admin_api.drain(site.app())
        assert not site.admin_api.operations
        assert json.loads(path.read_bytes())["worker"]["max_queue"] == 16
    finally:
        release.set()
        await client.close()


def test_editor_defaults_and_proposals_match_actual_worker_constructor(tmp_path):
    from types import SimpleNamespace
    from aikey.worker import JobProcessor
    from test_worker import configuration

    config = configuration(SimpleNamespace(origin="http://127.0.0.1:1"))
    config["worker"] = {"legacy_profile": "protect-7.2.105"}
    worker = JobProcessor(config, tmp_path / "defaults")
    assert worker._queue.maxsize == LIMITS["max_queue"][1]
    assert worker.concurrency == LIMITS["max_concurrency"][1]
    assert worker.timeout_s == LIMITS["timeout_s"][1]
    assert worker.caption_timeout_s == LIMITS["caption_timeout_s"][1]
    assert worker.speech_timeout_s == LIMITS["speech_timeout_s"][1]
    assert worker._inference_gate is None
    choices = selected(
        max_concurrency=8,
        inference_concurrency=2,
        max_queue=32,
        timeout_s=500,
        caption_timeout_s=400,
        speech_timeout_s=600,
    )
    config["worker"].update(choices)
    worker = JobProcessor(config, tmp_path / "selected")
    assert worker._inference_gate.capacity == 2 and worker.concurrency == 8
    assert (
        worker._queue.maxsize,
        worker.timeout_s,
        worker.caption_timeout_s,
        worker.speech_timeout_s,
    ) == (32, 500, 400, 600)


async def test_grant_capacity_and_expiry_during_await(tmp_path, monkeypatch):
    site, server, client, cookie, path = await signed_in(tmp_path)
    configured(path)
    try:
        control = site.admin_api.worker_limits_control
        grant = await preview(site, client, cookie)
        real = site.aikey.preview_worker_limits

        def expire(*args):
            result = real(*args)
            control.clock = lambda: float("inf")
            return result

        monkeypatch.setattr(site.aikey, "preview_worker_limits", expire)
        before = path.read_bytes()
        assert (await apply(site, client, grant)).status == 409
        assert path.read_bytes() == before
    finally:
        await client.close()


async def test_two_simultaneous_confirmations_commit_only_once(tmp_path, monkeypatch):
    site, server, client, cookie, path = await signed_in(tmp_path)
    configured(path)
    real = site.aikey.apply_worker_limits
    calls = []

    def recorded(*args):
        calls.append(True)
        return real(*args)

    try:
        grant = await preview(site, client, cookie)
        monkeypatch.setattr(site.aikey, "apply_worker_limits", recorded)
        results = await asyncio.gather(apply(site, client, grant), apply(site, client, grant))
        assert sorted(r.status for r in results) == [200, 409]
        assert calls == [True]
    finally:
        await client.close()


async def test_runtime_wide_mutation_limit_is_shared(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    configured(path)
    try:
        now = site._mutation_clock()
        site._mutation_times.extend([now] * 60)
        before = path.read_bytes()
        r = await client.post(
            "/worker-limits/preview",
            data={"csrf": site.security.csrf_token(cookie)},
            headers={"Origin": site.origin},
        )
        assert r.status == 429 and path.read_bytes() == before
        assert not site.admin_api.worker_limits_control.grants
        assert (await client.get("/worker-limits")).status == 200
    finally:
        await client.close()
