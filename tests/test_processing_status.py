"""Owned HTTPS health, unknown/zero semantics, no private payload or write path."""

import asyncio
import json

import pytest

from aikey.admin_runtime import RuntimeObserver, _normalize
from test_admin_runtime_observation import key_health, target
from test_control_site_secrets import signed_in, assert_private_page


def key_processing():
    return {**key_health(), "worker": {"queued": 0, "active": 1, "pending": 1,
        "capacity": 20, "ledger": 31,
        "captions": {"admitted": 7, "exhausted": 2, "deferred_fair_share": 3},
        "speech": {"refused_long": 4, "clipped": 0},
        "deep": {"describe_tasks": 8, "described": 6, "crops_failed": 1},
        "reverification": {"confirmed": 2, "unchanged": 1},
        "inference_gate": {"capacity": 1, "active": 1, "waiting": 2, "on_demand_waiting": 1,
                           "captions_active": 1, "captions_waiting": 1, "caption_lane": 2},
        "failure_notes": {"scope": "current_process_publication_counts_not_current_job_outcomes",
            "capacity": 1024, "current_process": {"written": 4, "already_recorded": 2,
                "busy": 1, "capacity": 0, "unavailable": 0},
            "private": "CANARY-private-exception"},
        "private": "CANARY-private-caption"}}


def port_processing():
    return {"service": "aiport-candidate", "adopted": True, "control_connected": True,
        "active_streams": 1, "streams_with_decoded_frames": 1,
        "pool_inference": {"attempts": 10, "successes": 8, "dropped_frames": 3,
            "failed_cameras": 0, "api_failures": 2, "pending_cameras": 1,
            "model_load_failed": False, "closed": False, "api_request_cap": None,
            "api_provider_failures": 2, "api_backoff_skips": 5,
            "api_consecutive_provider_failures": 0, "last_api_error_code": "CANARY-provider-body"},
        "pool_cameras": [{"camera": "CANARY-native-id", "stream_active": True,
            "policy_enabled": True, "disabled": False, "pending": True,
            "attempts": 10, "successes": 8, "api_failures": 2,
            "api_requests_remaining": 0, "api_request_budget_healthy": False,
            "gate_history": ["CANARY-model-reply"]}],
        "private": "CANARY-private-url"}


async def test_processing_api_and_page_share_pinned_observations_without_private_payload(tmp_path):
    site, server, client, cookie, key = await signed_in(tmp_path)
    peers = []
    paths = [key, site.aiport.path]
    before = [p.read_bytes() for p in paths]
    files_before = set(tmp_path.rglob("*"))
    try:
        key_peer, key_calls, key_url = await target(key.parent, key_processing())
        peers.append(key_peer)
        port_peer, port_calls, port_url = await target(site.aiport.path.parent, port_processing())
        peers.append(port_peer)
        site.runtime_observer = RuntimeObserver(site._backup_profiles(), {"aikey": key_url, "aiport": port_url})
        # Existing certificate files are fixture setup, not page effects.
        files_before = set(tmp_path.rglob("*"))
        response = await client.get("/api/admin/v1/processing")
        assert response.status == 200
        data = await response.json()
        assert data["schema"] == "aikey-admin/1" and data["processing_schema"] == "aikey-processing-status/1"
        assert data["read_only"] is True
        rows = data["profiles"]
        assert [row["profile"] for row in rows] == ["aikey", "aiport"]
        assert rows[0]["runtime"]["queued"] == 0 and rows[0]["runtime"]["captions"]["admitted"] == 7
        assert rows[0]["runtime"]["failure_notes"]["current_process"]["written"] == 4
        assert rows[1]["runtime"]["inference"]["api_request_cap"] is None
        assert rows[1]["runtime"]["inference"]["api_request_cap_reported"] is True
        assert rows[1]["runtime"]["cameras"][0]["api_requests_remaining"] == 0
        assert rows[1]["runtime"]["cameras"][0]["api_request_budget_healthy"] is False
        assert "CANARY" not in json.dumps(data) and str(tmp_path) not in json.dumps(data)
        page = await client.get("/processing")
        html = await page.text()
        assert page.status == 200 and "Processing and budgets" in html
        assert_private_page(page, html)
        for required in ("Unknown is not zero", "not failed jobs", "12 new attempts",
                         "Not configured", "Runtime camera 1", "not saved inventory names",
                         "not event, object, paid-request or quality counts"):
            assert required in html
        assert "CANARY" not in html and str(tmp_path) not in html
        assert "<form" not in html and "<button" not in html and "localStorage" not in html
        assert [p.read_bytes() for p in paths] == before and set(tmp_path.rglob("*")) == files_before
        assert len(key_calls) == len(port_calls) == 1
        # Unsupported options/methods never start a new observation or mutation.
        assert (await client.get("/processing?url=https://example.com")).status == 400
        assert (await client.get("/api/admin/v1/processing?profile=missing")).status == 400
        assert (await client.post("/processing", data={"restart": "yes"})).status == 405
        assert (await client.post("/api/admin/v1/processing", json={"apply": True})).status == 405
        assert len(key_calls) == len(port_calls) == 1
        await client.post("/logout", data={"csrf": site.security.csrf_token(cookie)},
                          headers={"Origin": site.origin}, allow_redirects=False)
        assert (await client.get("/processing", allow_redirects=False)).status == 303
        assert (await client.get("/api/admin/v1/processing")).status == 401
    finally:
        for peer in peers:
            await peer.close()
        await client.close()
        await server.close()


@pytest.mark.parametrize("route", ["/processing", "/api/admin/v1/processing"])
async def test_no_peer_and_unavailable_are_unknown_not_zero_or_offline(tmp_path, route):
    site, server, client, cookie, key = await signed_in(tmp_path)
    try:
        result = await client.get(route)
        assert result.status == 200
        if route == "/processing":
            assert "No runtime target configured. Status unknown." in await result.text()
        else:
            assert all(row["runtime"] is None and row["observation"] == "not_configured"
                       for row in (await result.json())["profiles"])
        async def missing(_):
            return RuntimeObserver.unavailable()
        site.runtime_observer.read = missing
        result = await client.get(route)
        if route == "/processing":
            assert "Runtime observation unavailable. Status unknown" in await result.text()
        else:
            assert all(row["runtime"] is None and row["observation"] == "unavailable"
                       for row in (await result.json())["profiles"])
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("route", ["/processing", "/api/admin/v1/processing"])
async def test_revoked_session_during_parallel_observation_receives_no_counters(tmp_path, route):
    site, server, client, cookie, key = await signed_in(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(_):
        entered.set()
        await release.wait()
        return RuntimeObserver.unavailable()
    site.runtime_observer.read = blocked
    try:
        pending = asyncio.create_task(client.get(route, allow_redirects=False))
        await asyncio.wait_for(entered.wait(), 2)
        await client.post("/logout", data={"csrf": site.security.csrf_token(cookie)},
                          headers={"Origin": site.origin}, allow_redirects=False)
        release.set()
        response = await pending
        assert response.status == (303 if route == "/processing" else 401)
        assert "counter_scope" not in await response.text()
    finally:
        release.set()
        await client.close()
        await server.close()


@pytest.mark.parametrize("group,field", [("speech", "clipped"), ("deep", "described"),
    ("inference_gate", "waiting"), ("reverification", "confirmed"), ("captions", "admitted")])
@pytest.mark.parametrize("bad", [True, -1, 1.2, "CANARY-private", 2**63])
def test_key_counter_validation_does_not_coerce_invalid_data(group, field, bad):
    value = key_processing()
    value["worker"][group][field] = bad
    with pytest.raises(ValueError):
        _normalize("aikey", value)


@pytest.mark.parametrize("case", ["scope", "capacity_bool", "capacity", "counts_null", "counts_type", "counter"])
def test_failure_note_publication_never_turns_into_current_failure_count(case):
    value = key_processing()
    notes = value["worker"]["failure_notes"]
    if case == "scope":
        notes["scope"] = "failed_jobs"
    elif case == "capacity_bool":
        notes["capacity"] = True
    elif case == "capacity":
        notes["capacity"] = 2048
    elif case == "counts_null":
        notes["current_process"] = None
    elif case == "counts_type":
        notes["current_process"] = []
    else:
        notes["current_process"]["written"] = True
    with pytest.raises(ValueError):
        _normalize("aikey", value)


@pytest.mark.parametrize("group,field,bad", [("pool_inference", "api_request_cap", True),
    ("pool_inference", "attempts", -1), ("pool_inference", "closed", 1),
    ("pool_inference", "api_failures", "CANARY-private"),
    ("pool_cameras", "api_requests_remaining", 1.2),
    ("pool_cameras", "api_request_budget_healthy", "yes")])
def test_port_counters_and_budget_flags_are_strict(group, field, bad):
    value = port_processing()
    (value[group][0] if group == "pool_cameras" else value[group])[field] = bad
    with pytest.raises(ValueError):
        _normalize("aiport", value)


def test_legacy_namespaces_remain_unknown_and_unknown_cap_is_not_reported_uncapped():
    key = _normalize("aikey", key_health())
    assert key["failure_notes"] is None and key["speech"] is None and key["inference_gate"] is None
    port = port_processing()
    port["pool_inference"].pop("api_request_cap")
    normalized = _normalize("aiport", port)
    assert normalized["inference"]["api_request_cap_reported"] is False
    assert normalized["inference"]["api_request_cap"] is None


async def test_corrupt_health_keeps_other_profile_available_and_never_echoes_private_error(tmp_path):
    site, server, client, cookie, key = await signed_in(tmp_path)
    peers = []
    try:
        bad = key_processing()
        bad["worker"]["failure_notes"]["current_process"]["written"] = "CANARY-private"
        for path, value in [(key.parent, bad), (site.aiport.path.parent, port_processing())]:
            peer, _, url = await target(path, value)
            peers.append((peer, url))
        site.runtime_observer = RuntimeObserver(site._backup_profiles(),
            {"aikey": peers[0][1], "aiport": peers[1][1]})
        result = await (await client.get("/api/admin/v1/processing")).json()
        assert result["profiles"][0]["runtime"] is None
        assert result["profiles"][1]["runtime"]["active_streams"] == 1
        assert "CANARY" not in json.dumps(result)
    finally:
        for peer, _ in peers:
            await peer.close()
        await client.close()
        await server.close()
