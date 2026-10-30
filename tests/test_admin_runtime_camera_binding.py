"""Join runtime policy ordinals only when the versioned saved binding matches."""

import json

import pytest

from aikey.aiport_camera_engine import CameraPolicyEngine
from test_aiport_camera_engine import FIRST, SECOND, policy
from test_admin_runtime_observation import target
from test_control_site_secrets import signed_in


def test_binding_revision_is_role_order_and_identity_specific_without_raw_mac():
    from aikey.camera_bindings import binding_revision
    a = binding_revision("020000000099", [FIRST, SECOND])
    assert len(a) == 64 and a != binding_revision("020000000099", [SECOND, FIRST])
    assert a != binding_revision("020000000098", [FIRST, SECOND])
    assert a == binding_revision("02:00:00:00:00:99", [FIRST.lower(), SECOND.lower()])
    assert FIRST not in a and SECOND not in a


def test_zone_count_observation_separates_no_policy_from_policy_without_zones():
    engine = CameraPolicyEngine([FIRST, SECOND])
    engine.replace_policy(FIRST, policy(FIRST, zone=True))
    one, two = engine.camera_snapshot(now=1)
    assert one["policy_smart_zones"] == 1 and one["policy_exclude_zones"] == 0
    assert two["policy_smart_zones"] is None
    engine.replace_policy(SECOND, policy(SECOND))
    one, two = engine.camera_snapshot(now=2)
    assert two["policy_smart_zones"] == 0 and one["policy_smart_zones"] == 1


@pytest.mark.parametrize("matches", [True, False])
async def test_private_camera_mac_is_joined_only_after_runtime_order_agrees(tmp_path, matches):
    from aikey.admin_runtime import RuntimeObserver
    from aikey.camera_bindings import binding_revision, SCHEMA
    site, server, client, cookie, key = await signed_in(tmp_path)
    runtime_server = None
    try:
        config = json.loads(site.aiport.path.read_text())
        cameras = [r["camera_mac"] for r in config["paired_streams"]]
        declared = cameras if matches else list(reversed(cameras))
        value = {"service": "aiport-candidate", "adopted": True, "control_connected": True,
            "pool_binding_schema": SCHEMA, "pool_binding_revision": binding_revision(config["mac"], declared),
            "pool_cameras": [{"policy_enabled": True, "policy_enabled_types": ["person"],
                              "policy_smart_zones": n, "policy_exclude_zones": 0,
                              "stream_active": True, "stream_points": 2,
                              "stream_geometry": {"width":1920,"height":1080,"fps":30},
                              "stream_restart_attempts":0,"stream_restart_successes":0}
                             for n in (1, 0)]}
        runtime_server, calls, url = await target(site.aiport.path.parent, value)
        site.runtime_observer = RuntimeObserver(site._backup_profiles(), {"aiport": url})
        response = await client.get('/api/admin/v1/profiles/aiport/runtime')
        assert response.status == 200
        data = await response.json()
        assert data["saved_order_matches_reported_runtime_order"] is matches
        assert len(data["runtime"]["cameras"]) == 2
        for index, item in enumerate(data["runtime"]["cameras"]):
            assert item["policy_smart_zones"] == (1, 0)[index]
            assert item["stream_active"] is True and item["stream_points"] == 2
            assert item["stream_geometry"] == {"width":1920,"height":1080,"fps":30}
            if matches:
                assert item["saved_camera_mac"] == cameras[index]
            else:
                assert "saved_camera_mac" not in item
        assert len(calls) == 1
    finally:
        if runtime_server is not None:
            await runtime_server.close()
        await client.close()
        await server.close()


@pytest.mark.parametrize("geometry,points", [
    ({"width": True,"height":1080,"fps":30},2),
    ({"width":1920,"height":1080,"fps":float('inf')},2),
    ({"width":1920,"height":1080,"fps":30,"uri":"fictional-private-alias"},2),
    ({"width":1920,"height":1080,"fps":30},5),
])
def test_invalid_or_contradictory_stream_measurements_are_not_published(geometry, points):
    from aikey.admin_runtime import _normalize
    with pytest.raises(ValueError):
        _normalize("aiport", {"service":"aiport-candidate","adopted":True,
            "pool_cameras":[{"stream_geometry":geometry,"stream_points":points}]})
