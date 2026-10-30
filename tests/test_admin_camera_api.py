"""Read-only inventory cannot silently become a pairing or permission grant."""

import json
import time

import pytest

from aikey.camera_inventory import parse_cameras
from test_camera_inventory import camera
from test_control_site_secrets import signed_in


async def test_camera_api_reuses_validated_inventory_and_labels_saved_bindings(tmp_path):
    site, server, client, cookie, key = await signed_in(tmp_path)
    try:
        port = json.loads(site.aiport.path.read_text())
        row = camera(1)
        row["mac"] = port["paired_streams"][0]["camera_mac"]
        report = {"schema": "aikey-camera-preflight/1", "protect_version": "7.3.74",
                  "fetched_at": int(time.time()), "cameras": [c.public() for c in parse_cameras([row])],
                  "private_key": "fictional-private-credential"}
        calls = []
        async def inventory():
            calls.append(True)
            return report
        site.inventory_loader = inventory
        before = key.read_bytes(), site.aiport.path.read_bytes()
        response = await client.get('/api/admin/v1/cameras')
        assert response.status == 200
        data = await response.json()
        assert data["processing_enabled_by_this_read"] is False
        assert data["cameras"][0]["saved_profiles"] == ["aiport"]
        assert data["cameras"][0]["binding_observation"] == "saved_configuration_not_native_pairing_proof"
        assert data["onvif_ingest"] == "not_implemented"
        assert "fictional-private-credential" not in json.dumps(data)
        assert before == (key.read_bytes(), site.aiport.path.read_bytes())
        assert len(calls) == 1
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("bad", ["camera", "version", "stale"])
async def test_unknown_or_missing_inventory_is_not_an_empty_success(tmp_path, bad):
    site, server, client, cookie, key = await signed_in(tmp_path)
    try:
        response = await client.get('/api/admin/v1/cameras')
        assert response.status == 503 and (await response.json())["error"] == "inventory_unavailable"
        async def invalid():
            return {"schema": "aikey-camera-preflight/1",
                    "protect_version": "unverified" if bad == "version" else "7.3.74",
                    "fetched_at": int(time.time()) - (3600 if bad == "stale" else 0),
                    "cameras": [{"id": "fictional-private-id"}] if bad == "camera" else []}
        site.inventory_loader = invalid
        response = await client.get('/api/admin/v1/cameras')
        assert response.status == 503
        assert "fictional-private-id" not in await response.text()
    finally:
        await client.close()
        await server.close()


async def test_signed_out_or_caller_target_never_reaches_inventory(tmp_path):
    site, server, client, cookie, key = await signed_in(tmp_path)
    calls = []
    async def forbidden():
        calls.append(True)
        raise AssertionError("no inventory read")
    site.inventory_loader = forbidden
    try:
        assert (await client.get('/api/admin/v1/cameras?url=https://private.invalid')).status == 400
        await client.post('/logout', data={"csrf": site.security.csrf_token(cookie)},
                          headers={"Origin": site.origin}, allow_redirects=False)
        assert (await client.get('/api/admin/v1/cameras')).status == 401
        assert not calls
    finally:
        await client.close()
        await server.close()
