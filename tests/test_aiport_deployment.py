"""AI Port capacity and port plans use synthetic inventory only."""

import json

import pytest

from aikey import aiport_deployment
from aikey.aiport_deployment import AiPortPlanError, plan_ai_ports


def camera(number, *, model="UVC G3 Instant", source=None, resolution=None,
           processing_class="legacy_ingress_needed", state="CONNECTED"):
    row = {"id": f"{number:024x}", "model": model, "state": state,
           "processing_class": processing_class}
    if source is not None:
        row["source_kind"] = source
    if resolution is not None:
        row["recording_resolution"] = resolution
    return row


def report(*cameras):
    return {"schema": "aikey-camera-preflight/1", "cameras": list(cameras)}


def test_two_unknown_resolution_protect_cameras_need_one_separate_ai_port():
    plan = plan_ai_ports(report(camera(1), camera(2),
                                camera(3, processing_class="smart_event_candidate")),
                         device_ips=["192.0.2.10"], ai_key_ip="192.0.2.11")
    assert plan["legacy_camera_count"] == 2
    assert plan["ai_port_instances_required"] == 1
    assert plan["instances"][0]["camera_ids"] == [f"{1:024x}", f"{2:024x}"]
    assert plan["instances"][0]["resolution_unverified"] is True
    assert plan["instances"][0]["apple_publish"] == "192.0.2.10:443:8443/tcp"
    assert plan["ai_key"] == {"host_ip": "192.0.2.11", "management_tcp": 8080}
    assert plan["host_discovery_udp"] == 10001
    assert plan["schema"] == "aikey-aiport-deployment-plan/2"
    assert plan["camera_stream_ports"] == {
        "protect_outbound_tcp": [7447], "onvif_outbound_tcp": []}
    assert plan["camera_pairing"] == "disabled"
    assert plan["instances"][0]["state"] == "planned_only"


@pytest.mark.parametrize(("resolution", "per_instance"),
                         [("HD", 5), ("2K", 3), ("4K", 2), (None, 2)])
def test_protect_capacity_stays_within_published_limits(resolution, per_instance):
    rows = [camera(i, resolution=resolution) for i in range(1, per_instance + 2)]
    plan = plan_ai_ports(report(*rows))
    assert plan["ai_port_instances_required"] == 2
    assert len(plan["instances"][0]["camera_ids"]) == per_instance
    assert plan["ai_port_instances_without_address"] == 2
    assert all(item["apple_publish"] is None for item in plan["instances"])


@pytest.mark.parametrize(("resolution", "per_instance"),
                         [("HD", 3), ("2K", 2), ("4K", 1), (None, 1)])
def test_onvif_capacity_stays_within_published_limits(resolution, per_instance):
    rows = [camera(i, model="Third-party camera", source="onvif", resolution=resolution)
            for i in range(1, per_instance + 2)]
    plan = plan_ai_ports(report(*rows))
    assert plan["ai_port_instances_required"] == 2


def test_onvif_and_protect_never_share_an_instance():
    plan = plan_ai_ports(report(camera(1), camera(2, model="Third-party camera", source="onvif")))
    assert plan["ai_port_instances_required"] == 2
    assert {item["source_kind"] for item in plan["instances"]} == {"protect", "onvif"}
    assert plan["camera_stream_ports"] == {
        "protect_outbound_tcp": [7447], "onvif_outbound_tcp": "needs_evidence"}


def test_no_legacy_cameras_expose_no_ai_port_listener():
    plan = plan_ai_ports(report(camera(1, processing_class="smart_event_candidate")))
    assert plan["ai_port_instances_required"] == 0
    assert plan["host_discovery_udp"] is None
    assert plan["controller_websocket_tcp"] is None
    assert plan["camera_stream_ports"] == {
        "protect_outbound_tcp": [], "onvif_outbound_tcp": []}


def test_explicit_g3_g5_scope_plans_onboard_smart_cameras_without_g6():
    rows = [camera(1), camera(2, model="UVC G3 Instant",
                             processing_class="smart_event_candidate")]
    rows += [camera(number, model="UVC G4 Bullet",
                    processing_class="smart_event_candidate")
             for number in range(3, 8)]
    rows += [camera(8, model="UVC G5 Flex",
                    processing_class="smart_event_candidate"),
             camera(9, model="UVC G6 Instant",
                    processing_class="smart_event_candidate"),
             camera(10, model="UVC G4 Instant",
                    processing_class="offline", state="DISCONNECTED")]
    plan = plan_ai_ports(report(*rows), camera_scope="legacy-and-g3-g5",
                         device_ips=["192.0.2.10"])
    assert plan["camera_scope"] == "legacy-and-g3-g5"
    assert plan["legacy_camera_count"] == 1
    assert plan["enhancement_camera_count"] == 7
    assert plan["selected_camera_count"] == 8
    assert plan["ai_port_instances_required"] == 4
    assert plan["ai_port_instances_without_address"] == 3
    assert {camera_id for instance in plan["instances"]
            for camera_id in instance["camera_ids"]} == {
                f"{number:024x}" for number in range(1, 9)}
    assert all(instance["reserved_capacity"] == "1"
               for instance in plan["instances"])
    assert all(instance["source_kind"] == "protect" for instance in plan["instances"])
    assert plan["camera_pairing"] == "disabled"


def test_g3_g5_scope_rejects_unknown_scope_and_does_not_select_similar_model():
    rows = report(camera(1, model="UVC G50 Unknown",
                         processing_class="smart_event_candidate"),
                  camera(2, model="UVC G4 Bullet", source="onvif",
                         processing_class="smart_event_candidate"))
    assert plan_ai_ports(rows, camera_scope="legacy-and-g3-g5")[
        "selected_camera_count"] == 0
    with pytest.raises(AiPortPlanError, match="Unknown camera scope"):
        plan_ai_ports(rows, camera_scope="all")


@pytest.mark.parametrize("rows", [
    [camera(1), camera(1)],
    [camera(1, model="Unknown")],
    [camera(1, resolution="8K")],
    [camera(1, state="DISCONNECTED")],
    [camera(1, processing_class="unexpected")],
])
def test_malformed_or_uncertain_legacy_inventory_fails_closed(rows):
    with pytest.raises(AiPortPlanError):
        plan_ai_ports(report(*rows))


@pytest.mark.parametrize(("ips", "key_ip"), [
    (["192.0.2.10", "192.0.2.10"], None),
    (["192.0.2.10"], "192.0.2.10"),
    (["0.0.0.0"], None),
    (["224.0.0.1"], None),
])
def test_ip_conflicts_and_non_unicast_addresses_are_rejected(ips, key_ip):
    with pytest.raises(AiPortPlanError):
        plan_ai_ports(report(camera(1)), device_ips=ips, ai_key_ip=key_ip)


def test_live_cli_fetches_inventory_before_planning(monkeypatch, tmp_path, capsys):
    async def fake_fetch(host, *, api_key_file, trust_file, cert_file):
        assert host == "192.0.2.1"
        assert [path.name for path in (api_key_file, trust_file, cert_file)] == ["key", "trust", "cert"]
        return report(camera(1), camera(2))

    monkeypatch.setattr(aiport_deployment, "fetch_inventory", fake_fetch)
    result = aiport_deployment.main([
        "--controller", "192.0.2.1", "--api-key-file", str(tmp_path / "key"),
        "--web-trust-file", str(tmp_path / "trust"),
        "--web-cert-file", str(tmp_path / "cert"),
        "--ai-key-ip", "192.0.2.11", "--ai-port-ip", "192.0.2.10",
    ])
    assert result == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["ai_port_instances_required"] == 1
    assert plan["instances"][0]["apple_publish"] == "192.0.2.10:443:8443/tcp"


def test_live_cli_requires_all_private_trust_files():
    with pytest.raises(SystemExit) as raised:
        aiport_deployment.main(["--controller", "192.0.2.1", "--api-key-file", "key"])
    assert raised.value.code == 2
