"""Downloaded counters are not private config, identifiers, logs or media."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from aikey.admin_runtime import RuntimeObserver
from aikey.diagnostic_export import FILENAME, SCHEMA, _profile
from test_admin_runtime_observation import target
from test_control_site_secrets import assert_private_page, signed_in
from test_processing_status import key_processing, port_processing


@pytest.mark.parametrize("kind", ["aikey", "aiport"])
async def test_actual_pinned_peers_export_only_allowlisted_data_and_never_forward_cookies(
    tmp_path, kind
):
    site, server, client, cookie, key = await signed_in(tmp_path)
    peer = None
    try:
        directory = key.parent if kind == "aikey" else site.aiport.path.parent
        response = key_processing() if kind == "aikey" else port_processing()
        peer, calls, url = await target(directory, response)
        profile = "aikey" if kind == "aikey" else "aiport"
        site.runtime_observer = RuntimeObserver(site._backup_profiles(), {profile: url})
        paths = [key, site.aiport.path]
        before = [p.read_bytes() for p in paths]
        files = set(tmp_path.rglob("*"))
        page = await client.get("/diagnostics")
        text = await page.text()
        assert page.status == 200 and "Download redacted JSON" in text and not calls
        assert_private_page(page, text)
        r = await client.get("/api/admin/v1/diagnostics")
        body = await r.read()
        value = json.loads(body)
        assert r.status == 200 and r.content_type == "application/json"
        assert r.headers["Content-Disposition"] == f'attachment; filename="{FILENAME}"'
        assert (
            r.headers["Cache-Control"] == "no-store"
            and r.headers["Referrer-Policy"] == "no-referrer"
        )
        assert "default-src 'none'" in r.headers["Content-Security-Policy"]
        assert value["schema"] == SCHEMA and value["profile_counts"] == {"aikey": 1, "aiport": 1}
        observed = next(v for v in value["profiles"] if v["kind"] == kind)
        assert observed["ordinal"] == 1 and observed["observation"] == "runtime_health"
        assert observed["tls_ca_and_leaf_pin_verified"] is True
        if kind == "aikey":
            assert observed["runtime"]["queued"] == 0
            assert observed["runtime"]["captions"]["admitted"] == 7
            assert observed["runtime"]["caption_policy_mode"] == "metered"
        else:
            assert observed["runtime"]["active_streams"] == 1
            assert observed["runtime"]["inference"]["api_failures"] == 2
        for private in [
            b"CANARY",
            b"fictional-private",
            str(tmp_path).encode(),
            cookie.encode(),
            b"runtime_configuration_revision",
            b'"camera":',
            b'"cameras":',
            b"observed_at",
            b"api_request_cap",
        ]:
            assert private not in body
        assert len(body) < 65536 and [p.read_bytes() for p in paths] == before
        assert set(tmp_path.rglob("*")) == files and len(calls) == 1
        assert all(k.lower() not in {"authorization", "cookie", "x-token"} for k in calls[0])
    finally:
        if peer is not None:
            await peer.close()
        await client.close()
        await server.close()


def test_second_projection_omits_every_unselected_string_and_keeps_unknown_distinct_from_zero():
    private = "PRIVATE-MAC-IP-NAME-MODEL-URL-SECRET-PATH-ERROR-MEDIA-REVISION"
    data = {
        "observation": "runtime_health",
        "tls_ca_and_leaf_pin_verified": True,
        "runtime_configuration_revision": private,
        "profile": private,
        "observed_at": private,
        "runtime": {
            "adopted": False,
            "queued": 0,
            "private": private,
            "captions": {"admitted": 0, "private": private},
            "caption_policy": {"mode": "unmetered", "scope": "wrong-scope-" + private},
        },
    }
    row = _profile("aikey", 1, data)
    assert private not in json.dumps(row)
    assert row["runtime"]["queued"] == 0 and row["runtime"]["pending"] is None
    assert row["runtime"]["adopted"] is False and row["runtime"]["connected"] is None
    assert row["runtime"]["caption_policy_mode"] is None
    assert set(row) == {"kind", "ordinal", "observation", "tls_ca_and_leaf_pin_verified", "runtime"}


@pytest.mark.parametrize("bad", [True, -1, 2**63, 1.5, "PRIVATE", [], {}, float("nan")])
def test_malformed_counter_never_leaks_or_becomes_zero(bad):
    result = _profile(
        "aikey",
        1,
        {
            "observation": "runtime_health",
            "tls_ca_and_leaf_pin_verified": True,
            "runtime": {"active": bad, "private": "PRIVATE"},
        },
    )
    assert result["observation"] == "unavailable" and result["runtime"] is None
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"observation": "PRIVATE"},
        {
            "observation": "runtime_health",
            "tls_ca_and_leaf_pin_verified": False,
            "runtime": {"active": 0},
        },
        {
            "observation": "runtime_health",
            "tls_ca_and_leaf_pin_verified": True,
            "runtime": {"adopted": "PRIVATE"},
        },
    ],
)
def test_invalid_or_unpinned_observation_cannot_claim_healthy_runtime(value):
    row = _profile("aikey", 1, value)
    assert row["observation"] == "unavailable" and row["runtime"] is None
    assert row["tls_ca_and_leaf_pin_verified"] is None and "PRIVATE" not in json.dumps(row)


async def test_query_methods_and_anonymous_requests_never_collect_and_expiry_is_rechecked(tmp_path):
    site, server, client, cookie, _ = await signed_in(tmp_path)
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def read(profile):
        calls.append(profile)
        entered.set()
        await release.wait()
        return {"observation": "not_configured", "runtime": None}

    site.runtime_observer = SimpleNamespace(read=read)
    try:
        assert (await client.get("/api/admin/v1/diagnostics?url=PRIVATE")).status == 400
        assert (await client.get("/diagnostics?path=PRIVATE")).status == 400
        assert (
            await client.post("/api/admin/v1/diagnostics", json={"restart": True})
        ).status == 405
        assert (await client.head("/api/admin/v1/diagnostics")).status == 405
        assert not calls
        pending = asyncio.create_task(client.get("/api/admin/v1/diagnostics"))
        await asyncio.wait_for(entered.wait(), 1)
        assert (
            await client.post(
                "/logout",
                data={"csrf": site.security.csrf_token(cookie)},
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
        ).status == 303
        release.set()
        response = await pending
        assert response.status == 401 and "Content-Disposition" not in response.headers
        assert (await client.get("/diagnostics", allow_redirects=False)).status == 303
        assert (await client.get("/api/admin/v1/diagnostics")).status == 401
        assert not site.admin_api.diagnostic_export.collecting
    finally:
        release.set()
        await client.close()
        await server.close()


async def test_one_collection_deadline_busy_response_and_retry_leave_no_orphan_tasks(tmp_path):
    site, server, client, _, _ = await signed_in(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls, alive = [], set()

    async def read(profile):
        calls.append(profile)
        alive.add(profile)
        entered.set()
        try:
            await release.wait()
            return {"observation": "not_configured", "runtime": None}
        finally:
            alive.discard(profile)

    site.runtime_observer = SimpleNamespace(read=read)
    site.admin_api.diagnostic_export.deadline_seconds = 0.1
    try:
        first = asyncio.create_task(client.get("/api/admin/v1/diagnostics"))
        await asyncio.wait_for(entered.wait(), 1)
        busy = await client.get("/api/admin/v1/diagnostics")
        assert busy.status == 429 and busy.headers["Retry-After"] == "2"
        assert len(calls) == 2
        timed_out = await first
        assert timed_out.status == 503 and not alive
        assert "Content-Disposition" not in timed_out.headers
        assert not site.admin_api.diagnostic_export.collecting
        release.set()
        fresh = await client.get("/api/admin/v1/diagnostics")
        value = await fresh.json()
        assert fresh.status == 200 and all(
            r["observation"] == "not_configured" for r in value["profiles"]
        )
        assert len(calls) == 4 and not alive
    finally:
        release.set()
        await client.close()
        await server.close()


async def test_direct_cancelled_collector_releases_its_slot_and_children(tmp_path):
    site, server, client, _, _ = await signed_in(tmp_path)
    started = asyncio.Event()
    alive = set()

    async def read(profile):
        alive.add(profile)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            alive.discard(profile)

    exporter = site.admin_api.diagnostic_export
    site.runtime_observer = SimpleNamespace(read=read)
    exporter.api.authenticated = lambda _: True
    task = asyncio.create_task(exporter.download(SimpleNamespace(query={})))
    try:
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not alive and not exporter.collecting
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.close()
        await server.close()


async def test_profile_count_bound_is_fail_closed_before_any_read(tmp_path):
    site, server, client, _, _ = await signed_in(tmp_path)
    calls = []

    async def read(profile):
        calls.append(profile)
        return {"observation": "not_configured", "runtime": None}

    site.runtime_observer = SimpleNamespace(read=read)
    site._backup_profiles = lambda: {"PRIVATE-" + str(i): ("aiport", tmp_path) for i in range(33)}
    try:
        r = await client.get("/api/admin/v1/diagnostics")
        assert r.status == 503 and not calls and "PRIVATE" not in await r.text()
        site._backup_profiles = lambda: {
            "PRIVATE-" + str(i): ("aiport", tmp_path) for i in range(32)
        }
        r = await client.get("/api/admin/v1/diagnostics")
        value = await r.json()
        assert r.status == 200 and len(value["profiles"]) == 32 and len(await r.read()) <= 65536
        assert [p["ordinal"] for p in value["profiles"]] == list(range(1, 33))
        assert "PRIVATE" not in json.dumps(value)
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("kind", ["PRIVATE", [], None, 1])
async def test_invalid_profile_shape_is_refused_before_any_transport(tmp_path, kind):
    site, server, client, _, _ = await signed_in(tmp_path)
    calls = []

    async def read(profile):
        calls.append(profile)
        return {"observation": "not_configured", "runtime": None}

    site.runtime_observer = SimpleNamespace(read=read)
    site._backup_profiles = lambda: {"PRIVATE-PROFILE": (kind, tmp_path)}
    try:
        r = await client.get("/api/admin/v1/diagnostics")
        assert r.status == 503 and not calls and "PRIVATE" not in await r.text()
        assert not site.admin_api.diagnostic_export.collecting
    finally:
        await client.close()
        await server.close()


async def test_one_reader_failure_cancels_and_drains_siblings_before_releasing_collection(tmp_path):
    site, server, client, _, _ = await signed_in(tmp_path)
    entered, settled = asyncio.Event(), asyncio.Event()

    async def read(profile):
        if profile == "aikey":
            await entered.wait()
            raise OSError("PRIVATE-reader-failure")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()

    site.runtime_observer = SimpleNamespace(read=read)
    try:
        r = await client.get("/api/admin/v1/diagnostics")
        assert r.status == 503 and settled.is_set()
        assert "PRIVATE" not in await r.text() and "Content-Disposition" not in r.headers
        assert not site.admin_api.diagnostic_export.collecting
    finally:
        await client.close()
        await server.close()
