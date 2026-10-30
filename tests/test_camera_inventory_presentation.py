"""Presentations share a bounded observation; confirmation always re-observes."""

import asyncio
from copy import deepcopy

import pytest

from aikey.camera_inventory import InventoryError
from test_camera_threshold_http import ready
from test_ocr_provider_http import form


def four_readonly_profiles(site):
    # Isolate the read budget with four logical profiles using one fictional
    # config. This is not a native pairing/lifecycle qualification.
    store = site.aiports["aiport"]
    for number in range(2, 5):
        site.aiports[f"aiport:fixture-{number}"] = store


@pytest.mark.parametrize("pages", [
    ("/camera-thresholds", "/plate-cameras"),
    ("/plate-cameras", "/camera-thresholds"),
])
async def test_two_four_profile_pages_use_one_complete_source_snapshot(tmp_path, pages):
    site, server, client, _, _, report = await ready(tmp_path)
    four_readonly_profiles(site)
    calls = []
    async def limited_source():
        calls.append(True)
        if len(calls) > 1:
            raise InventoryError("synthetic source quota exhausted")
        return deepcopy(report)
    site.inventory_loader = limited_source
    site._inventory_clock = lambda: 100
    try:
        for endpoint in pages:
            response = await client.get(endpoint)
            body = await response.text()
            assert response.status == 200
            assert body.count("action='" + endpoint + "/preview'") == 4
            assert "Editing unavailable" not in body
        assert calls == [True]
    finally:
        await client.close()
        await server.close()


async def test_failure_is_coherent_without_per_profile_or_concurrent_retries(tmp_path):
    site, server, client, _, _, report = await ready(tmp_path)
    four_readonly_profiles(site)
    calls, failing, clock = [], [True], [100]
    async def source():
        calls.append(True)
        if failing[0]:
            raise InventoryError("synthetic upstream rejection")
        return deepcopy(report)
    site.inventory_loader = source
    site._inventory_clock = lambda: clock[0]
    try:
        for endpoint in ("/camera-thresholds", "/plate-cameras"):
            body = await (await client.get(endpoint)).text()
            assert body.count("Editing unavailable") == 4
            assert "action='" + endpoint + "/preview'" not in body
        assert calls == [True]
        clock[0] += 3
        failing[0] = False
        body = await (await client.get("/camera-thresholds")).text()
        assert body.count("action='/camera-thresholds/preview'") == 4 and len(calls) == 2
    finally:
        await client.close()
        await server.close()


async def test_expired_or_invalid_observation_never_uses_prior_good_data(tmp_path):
    site, server, client, _, _, report = await ready(tmp_path)
    clock, calls = [100], []
    async def source():
        calls.append(True)
        return deepcopy(report)
    site.inventory_loader = source
    site._inventory_clock = lambda: clock[0]
    try:
        assert "action='/camera-thresholds/preview'" in await (await client.get("/camera-thresholds")).text()
        report["fetched_at"] = 1
        clock[0] += 3
        for endpoint in ("/camera-thresholds", "/plate-cameras"):
            body = await (await client.get(endpoint)).text()
            assert "Editing unavailable" in body and "action='" + endpoint + "/preview'" not in body
        assert len(calls) == 2
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("endpoint", ["/camera-thresholds", "/plate-cameras"])
async def test_post_preview_refetches_and_refuses_renamed_camera_despite_get_cache(tmp_path, endpoint):
    site, server, client, _, _, report = await ready(tmp_path)
    calls = []
    async def source():
        calls.append(True)
        return deepcopy(report)
    site.inventory_loader = source
    site._inventory_clock = lambda: 100
    try:
        body = await (await client.get(endpoint)).text()
        fields = form(body, endpoint + "/preview")
        report["cameras"][0]["name"] = "Fictional renamed camera"
        before = site.aiports["aiport"].path.read_bytes()
        response = await client.post(endpoint + "/preview", data=fields, headers={"Origin": site.origin})
        assert response.status == 409 and len(calls) == 2
        assert site.aiports["aiport"].path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


async def test_api_gets_share_snapshot_without_changing_remove_only_permission_source(tmp_path):
    site, server, client, _, _, report = await ready(tmp_path)
    calls = []
    async def source():
        calls.append(True)
        return deepcopy(report)
    site.inventory_loader = source
    site._inventory_clock = lambda: 100
    try:
        for endpoint in ("/camera-thresholds", "/plate-cameras", "/api/admin/v1/cameras"):
            response = await client.get(endpoint)
            assert response.status == 200
            await response.read()
        assert calls == [True]
        # The legacy remove-only view intentionally has a less strict name-only
        # source contract so revocation remains available after schema changes.
        response = await client.get("/aiport-permissions")
        assert response.status == 200
        await response.read()
        assert calls == [True, True]
    finally:
        await client.close()
        await server.close()


async def test_concurrent_pages_coalesce_one_inflight_fetch(tmp_path):
    site, server, client, _, _, report = await ready(tmp_path)
    calls, entered, release = [], asyncio.Event(), asyncio.Event()
    async def source():
        calls.append(True)
        entered.set()
        await release.wait()
        return deepcopy(report)
    site.inventory_loader = source
    site._inventory_clock = lambda: 100
    try:
        first = asyncio.create_task(client.get("/camera-thresholds"))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(client.get("/plate-cameras"))
        release.set()
        responses = await asyncio.gather(first, second)
        assert all(r.status == 200 for r in responses)
        for r in responses:
            assert "Editing unavailable" not in await r.text()
        assert calls == [True]
    finally:
        release.set()
        await client.close()
        await server.close()


async def test_snapshot_mutation_does_not_poison_other_readers(tmp_path):
    site, server, client, _, _, report = await ready(tmp_path)
    site._inventory_clock = lambda: 100
    try:
        snapshot = await site.inventory_snapshot()
        snapshot["cameras"].clear()
        body = await (await client.get("/camera-thresholds")).text()
        assert "action='/camera-thresholds/preview'" in body
        assert report["cameras"]
    finally:
        await client.close()
        await server.close()


async def test_replacing_loader_cannot_reuse_old_source_observation(tmp_path):
    site, server, client, _, _, report = await ready(tmp_path)
    site._inventory_clock = lambda: 100
    try:
        body = await (await client.get("/camera-thresholds")).text()
        assert "action='/camera-thresholds/preview'" in body
        async def changed_source():
            raise InventoryError("synthetic new source unavailable")
        site.inventory_loader = changed_source
        body = await (await client.get("/plate-cameras")).text()
        assert "Editing unavailable" in body and "action='/plate-cameras/preview'" not in body
    finally:
        await client.close()
        await server.close()


async def test_cancelled_leader_does_not_poison_following_snapshot_request(tmp_path):
    site, server, client, _, _, report = await ready(tmp_path)
    entered, release, calls = asyncio.Event(), asyncio.Event(), []
    async def source():
        calls.append(True)
        entered.set()
        await release.wait()
        return deepcopy(report)
    site.inventory_loader = source
    site._inventory_clock = lambda: 100
    try:
        first = asyncio.create_task(site.inventory_snapshot())
        await asyncio.wait_for(entered.wait(), 2)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        value = await asyncio.wait_for(site.inventory_snapshot(), 2)
        assert value["cameras"] and len(calls) == 2
    finally:
        release.set()
        await client.close()
        await server.close()
