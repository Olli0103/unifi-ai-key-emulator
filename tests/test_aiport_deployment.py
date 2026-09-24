"""AI Port capacity and port plans use synthetic inventory only."""

import json
from fractions import Fraction

import pytest

from aikey import aiport_deployment
from aikey.aiport_deployment import AiPortPlanError, plan_ai_ports


def camera(number, *, model="UVC G3 Test Camera", source=None, resolution=None,
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
    assert all(Fraction(instance["reserved_capacity"]) <= 1
               for instance in plan["instances"])
    assert all(instance["source_kind"] == "protect" for instance in plan["instances"])
    assert plan["camera_pairing"] == "disabled"


def test_known_legacy_models_fit_four_ports_from_fresh_inventory_and_keep_existing_slots():
    models = ["UVC G3 Instant", "UVC G3 Instant", "UVC G5 Flex",
              "UVC G4 Instant", "UVC G4 Doorbell Pro", "UVC G4 Pro",
              "UVC G4 Bullet", "UVC G4 Bullet", "UVC G4 Dome"]
    rows = report(*(camera(index, model=model,
                           processing_class="smart_event_candidate")
                    for index, model in enumerate(models, 1)))
    fresh = plan_ai_ports(rows, camera_scope="legacy-and-g3-g5")
    assert fresh["selected_camera_count"] == 9
    assert fresh["ai_port_instances_required"] == 4
    assert all(item["resolution_unverified"] for item in fresh["instances"])
    previous = {
        "schema": "aikey-aiport-deployment-plan/2", "ai_key": {"host_ip": None},
        "instances": [
            {"slot": slot, "source_kind": "protect",
             "camera_ids": [f"{index:024x}" for index in indices],
             "host_ip": f"192.0.2.{10 + slot}"}
            for slot, indices in enumerate(((1, 2, 3), (4, 5), (6, 7), (8, 9)), 1)
        ],
    }
    reconciled = plan_ai_ports(rows, camera_scope="legacy-and-g3-g5",
                               previous_plan=previous)
    assert reconciled["ai_port_instances_required"] == 4
    assert [item["reserved_capacity"] for item in reconciled["instances"]] == [
        "9/10", "7/10", "1", "1"]
    assert [item["host_ip"] for item in reconciled["instances"]] == [
        f"192.0.2.{index}" for index in range(11, 15)]


def test_model_capacity_bound_requires_an_exact_name_and_missing_resolution():
    known = plan_ai_ports(report(camera(1, model="UVC G3 Instant")))
    unknown = plan_ai_ports(report(camera(1, model="UVC G3 Instant Variant")))
    declared = plan_ai_ports(report(camera(1, model="UVC G3 Instant",
                                           resolution="4K")))
    assert known["instances"][0]["reserved_capacity"] == "1/5"
    assert unknown["instances"][0]["reserved_capacity"] == "1/2"
    assert declared["instances"][0]["reserved_capacity"] == "1/2"
    assert all(plan["instances"][0]["resolution_unverified"]
               for plan in (known, unknown))
    assert declared["instances"][0]["resolution_unverified"] is False


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


def test_reconcile_preserves_existing_camera_slots_and_host_ips_on_addition():
    first = plan_ai_ports(report(camera(1), camera(2), camera(3)),
                          device_ips=["192.0.2.10", "192.0.2.12"],
                          ai_key_ip="192.0.2.11")
    updated = plan_ai_ports(report(camera(0), camera(1), camera(2), camera(3)),
                            previous_plan=first)
    assert [(item["slot"], item["host_ip"], item["camera_ids"])
            for item in updated["instances"]] == [
                (1, "192.0.2.10", [f"{1:024x}", f"{2:024x}"]),
                (2, "192.0.2.12", [f"{3:024x}", f"{0:024x}"]),
            ]
    assert updated["ai_key"]["host_ip"] == "192.0.2.11"
    assert plan_ai_ports(report(camera(0), camera(1), camera(2), camera(3)),
                         previous_plan=updated) == updated


def test_reconcile_adds_a_new_slot_without_repacking_or_inventing_an_address():
    first = plan_ai_ports(report(camera(1), camera(2)),
                          device_ips=["192.0.2.10"])
    updated = plan_ai_ports(report(*(camera(number) for number in range(1, 6))),
                            previous_plan=first)
    assert [item["camera_ids"] for item in updated["instances"]] == [
        [f"{1:024x}", f"{2:024x}"],
        [f"{3:024x}", f"{4:024x}"],
        [f"{5:024x}"],
    ]
    assert [item["host_ip"] for item in updated["instances"]] == [
        "192.0.2.10", None, None]
    assert updated["ai_port_instances_without_address"] == 2
    addressed = plan_ai_ports(report(*(camera(number) for number in range(1, 6))),
                              previous_plan=updated,
                              device_ips=["192.0.2.10", "192.0.2.12", "192.0.2.13"])
    assert [item["host_ip"] for item in addressed["instances"]] == [
        "192.0.2.10", "192.0.2.12", "192.0.2.13"]


@pytest.mark.parametrize("new_report", [
    report(camera(1)),
    report(camera(1), camera(2, processing_class="offline", state="DISCONNECTED")),
    report(camera(1), camera(2, model="Third-party camera", source="onvif")),
])
def test_reconcile_stops_when_a_previously_assigned_camera_changes(new_report):
    first = plan_ai_ports(report(camera(1), camera(2)))
    with pytest.raises(AiPortPlanError, match="previously assigned camera"):
        plan_ai_ports(new_report, previous_plan=first)


def test_reconcile_stops_when_existing_slot_exceeds_revised_capacity():
    first = plan_ai_ports(report(*(camera(number, resolution="HD") for number in range(1, 6))))
    with pytest.raises(AiPortPlanError, match="exceeds current camera capacity"):
        plan_ai_ports(report(*(camera(number, resolution="4K") for number in range(1, 6))),
                      previous_plan=first)


@pytest.mark.parametrize("changed", [
    {"device_ips": ["192.0.2.13"]},
    {"ai_key_ip": "192.0.2.14"},
    {"device_ips": ["192.0.2.10", "192.0.2.10"]},
    {"device_ips": ["192.0.2.10", "192.0.2.11"]},
])
def test_reconcile_rejects_address_changes_or_conflicts(changed):
    first = plan_ai_ports(report(camera(1), camera(2)),
                          device_ips=["192.0.2.10"], ai_key_ip="192.0.2.11")
    with pytest.raises(AiPortPlanError):
        plan_ai_ports(report(camera(1), camera(2), camera(3)),
                      previous_plan=first, **changed)


def test_reconcile_cli_reads_previous_plan_without_exposing_private_inventory(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    old = tmp_path / "old.json"
    inventory.write_text(json.dumps(report(camera(0), camera(1), camera(2))))
    old.write_text(json.dumps(plan_ai_ports(report(camera(1), camera(2)),
                                             device_ips=["192.0.2.10"])))
    assert aiport_deployment.main(["--inventory", str(inventory),
                                   "--previous-plan", str(old)]) == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["instances"][0]["camera_ids"] == [
        f"{1:024x}", f"{2:024x}"]
    assert updated["instances"][1]["camera_ids"] == [f"{0:024x}"]


def test_reconcile_preserves_separate_protect_and_onvif_slots():
    original = plan_ai_ports(report(camera(1),
                                    camera(2, model="Third-party", source="onvif")),
                             device_ips=["192.0.2.10", "192.0.2.12"])
    updated = plan_ai_ports(report(camera(1), camera(3),
                                   camera(2, model="Third-party", source="onvif"),
                                   camera(4, model="Third-party", source="onvif")),
                            previous_plan=original)
    assert [(item["source_kind"], item["host_ip"], item["camera_ids"])
            for item in updated["instances"]] == [
                ("protect", "192.0.2.10", [f"{1:024x}", f"{3:024x}"]),
                ("onvif", "192.0.2.12", [f"{2:024x}"]),
                ("onvif", None, [f"{4:024x}"]),
            ]


@pytest.mark.parametrize("mutation", [
    lambda plan: plan["instances"][0].update(slot=2),
    lambda plan: plan["instances"][0].update(camera_ids=[f"{1:024x}", f"{1:024x}"]),
    lambda plan: plan["instances"][0].update(host_ip=1234),
])
def test_reconcile_rejects_corrupt_previous_slot(mutation):
    original = plan_ai_ports(report(camera(1), camera(2)))
    mutation(original)
    with pytest.raises(AiPortPlanError):
        plan_ai_ports(report(camera(1), camera(2)), previous_plan=original)
