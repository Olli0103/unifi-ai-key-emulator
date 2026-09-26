"""A NAS deployment cannot change camera assignments or existing containers."""

import json
import time
from copy import deepcopy

import pytest

from aikey.aiport_nas_compose import build_nas_compose
from aikey.aiport_deployment import plan_ai_ports
from aikey.aiport_instance_state import provision_slot
from aikey.aiport_nas_reconcile import ReconcileError, reconcile, verify_inputs
from test_aiport_nas_compose import fixture


def inputs(tmp_path, selected_slot=2):
    plan, states, options = fixture(tmp_path)
    selected = {selected_slot: states[selected_slot]}
    manifest = build_nas_compose(plan, selected, **options)
    report = {"schema": "aikey-camera-preflight/1", "source": "local_protect_integration_api",
              "protect_version": "7.3.60", "processing_enabled": False,
              "fetched_at": int(time.time()),
              "cameras": [{"id": f"{number:024x}", "model": "UVC G4 Bullet",
                           "state": "CONNECTED", "processing_class": "legacy_ingress_needed",
                           "source_kind": "protect", "recording_resolution": "4K"}
                          for number in range(1, 4)]}
    return plan, selected, options, manifest, report


def test_fresh_inventory_and_exact_manifest_are_required(tmp_path):
    plan, states, options, manifest, report = inputs(tmp_path)
    assert verify_inputs(plan, manifest, report, states, options) == ["aiport_slot_2"]
    report["fetched_at"] -= 301
    with pytest.raises(ReconcileError, match="fresh"):
        verify_inputs(plan, manifest, report, states, options)
    report["fetched_at"] += 301
    report["cameras"].pop()
    with pytest.raises(ReconcileError, match="inventory"):
        verify_inputs(plan, manifest, report, states, options)


def test_paired_camera_class_change_preserves_verified_nas_slot(tmp_path):
    _, states, options, _, report = inputs(tmp_path)
    plan = plan_ai_ports(report, camera_scope="legacy-and-g3-g5",
                         device_ips=["192.168.10.135", "192.168.10.136"],
                         ai_key_ip="192.168.10.98")
    selected = {2: states[2]}
    manifest = build_nas_compose(plan, selected, **options)
    report["cameras"][0]["processing_class"] = "smart_event_candidate"
    assert verify_inputs(plan, manifest, report, selected, options) == ["aiport_slot_2"]
    malformed = deepcopy(plan)
    malformed["legacy_camera_count"] = True
    malformed["enhancement_camera_count"] = 2
    with pytest.raises(ReconcileError, match="inventory"):
        verify_inputs(malformed, manifest, report, selected, options)


def test_selected_slot_rejects_configured_camera_outside_fresh_assignment(tmp_path):
    plan, states, options, _, report = inputs(tmp_path)
    for number, row in enumerate(report["cameras"], start=1):
        row["mac"] = f"2A11000000{number:02X}"
    state = states[2]
    config_path = state / "config.json"
    config = json.loads(config_path.read_text())
    config["paired_stream"] = {"camera_mac": report["cameras"][0]["mac"],
                               "source_ip": options["controller_ip"],
                               "ffmpeg_path": "/usr/bin/ffmpeg"}
    config_path.write_text(json.dumps(config) + "\n")
    manifest = build_nas_compose(plan, states, **options)
    with pytest.raises(ReconcileError, match="outside its verified slot"):
        verify_inputs(plan, manifest, report, states, options)

    selected_id = plan["instances"][1]["camera_ids"][0]
    config["paired_stream"]["camera_mac"] = next(
        row["mac"] for row in report["cameras"] if row["id"] == selected_id)
    config_path.write_text(json.dumps(config) + "\n")
    assert verify_inputs(plan, manifest, report, states, options) == ["aiport_slot_2"]

    config["paired_stream"]["source_ip"] = "192.168.10.44"
    config_path.write_text(json.dumps(config) + "\n")
    with pytest.raises(ReconcileError, match="outside its verified slot"):
        verify_inputs(plan, manifest, report, states, options)


def test_fresh_inventory_permits_selected_slot_with_unaddressed_future_slot(tmp_path):
    plan, selected, options, _, report = inputs(tmp_path, selected_slot=1)
    plan["instances"][1]["host_ip"] = None
    plan["instances"][1]["apple_publish"] = None
    plan["ai_port_instances_without_address"] = 1
    manifest = build_nas_compose(plan, selected, **options)
    assert verify_inputs(plan, manifest, report, selected, options) == ["aiport_slot_1"]
    plan["ai_port_instances_without_address"] = 0
    with pytest.raises(ReconcileError, match="inventory"):
        verify_inputs(plan, manifest, report, selected, options)


def test_missing_resolution_on_untouched_mac_slot_does_not_block_nas_slot(tmp_path):
    old_plan, states, options, _, report = inputs(tmp_path)
    old_plan = plan_ai_ports(report, camera_scope="legacy-and-g3-g5",
                             device_ips=["192.168.10.135", "192.168.10.136"],
                             ai_key_ip="192.168.10.98")
    enriched = deepcopy(report)
    for row in enriched["cameras"][:2]:
        row["recording_resolution"] = "HD"
    enriched["cameras"].append({**enriched["cameras"][2],
                                "id": f"{4:024x}", "recording_resolution": None})
    plan = plan_ai_ports(enriched, previous_plan=old_plan,
                         camera_scope="legacy-and-g3-g5",
                         device_ips=["192.168.10.135", "192.168.10.136"],
                         ai_key_ip="192.168.10.98")
    assert plan["instances"][0]["reserved_capacity"] == "9/10"
    raw = deepcopy(enriched)
    for row in raw["cameras"]:
        row.pop("recording_resolution", None)
    # Protect may advertise AI Port-generated smart types on a formerly legacy
    # camera; its identity and slot assignment remain unchanged.
    raw["cameras"][0]["processing_class"] = "smart_event_candidate"

    selected = {2: states[2]}
    manifest = build_nas_compose(plan, selected, **options)
    assert verify_inputs(plan, manifest, raw, selected, options) == ["aiport_slot_2"]

    first_state = states[2].parent / "slot-1"
    provision_slot(plan, 1, first_state, controller_ip=options["controller_ip"],
                   controller_cert_file=states[2] / "controller-ca.pem",
                   controller_pin=options["controller_pin"])
    with pytest.raises(ReconcileError, match="Selected NAS slot"):
        verify_inputs(plan, build_nas_compose(plan, {1: first_state}, **options),
                      raw, {1: first_state}, options)
    raw["cameras"].pop()
    with pytest.raises(ReconcileError, match="inventory"):
        verify_inputs(plan, manifest, raw, selected, options)


@pytest.mark.parametrize("mutation", ["host_port", "extra_service", "changed_ip"])
def test_modified_manifest_is_rejected(tmp_path, mutation):
    plan, states, options, manifest, report = inputs(tmp_path)
    if mutation == "host_port":
        manifest["services"]["aiport_slot_2"]["ports"] = ["8443:443"]
    elif mutation == "extra_service":
        manifest["services"]["rogue"] = {}
    else:
        manifest["services"]["aiport_slot_2"]["networks"]["aiport_lan"]["ipv4_address"] = "192.168.10.200"
    with pytest.raises(ReconcileError, match="manifest"):
        verify_inputs(plan, manifest, report, states, options)


class FakeDocker:
    def __init__(self, manifest, rows="", fail_health=False, wrong_ip=False,
                 unsafe_runtime=None, adopted=False, control_connected=False,
                 unsafe_network=None):
        self.manifest = manifest
        self.rows = rows
        self.fail_health = fail_health
        self.wrong_ip = wrong_ip
        self.unsafe_runtime = unsafe_runtime
        self.adopted = adopted
        self.control_connected = control_connected
        self.unsafe_network = unsafe_network
        self.started = False
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        if argv[:3] == ["docker", "network", "inspect"]:
            expected = self.manifest["x-aikey-network-check"]
            value = {
                "Name": "wrong" if self.unsafe_network == "name" else expected["name"],
                "Driver": "bridge" if self.unsafe_network == "driver" else "macvlan",
                "Options": {"parent": ("wrong0" if self.unsafe_network == "parent"
                                       else expected["parent"])},
                "IPAM": {"Config": [{
                    "Subnet": ("192.168.11.0/24" if self.unsafe_network == "subnet"
                               else expected["subnet"]),
                    "Gateway": ("192.168.10.2" if self.unsafe_network == "gateway"
                                else expected["gateway"])}]},
            }
            return json.dumps(value)
        if "config" in argv:
            return ""
        if "ps" in argv:
            if self.started:
                return json.dumps({"Service": "aiport_slot_2", "State": "running",
                                   "ID": "a" * 12, "Publishers": []})
            return self.rows
        if "inspect" in argv:
            service = self.manifest["services"]["aiport_slot_2"]
            if ".Config.Image" in argv[3]:
                return json.dumps(service["image"])
            if ".Config.Cmd" in argv[3]:
                return json.dumps(["--config", "/state/config.json", "--port", "8443"]
                                  if self.unsafe_runtime == "wrong_port" else service["command"])
            if ".Config.User" in argv[3]:
                return json.dumps("0:0" if self.unsafe_runtime == "root" else service["user"])
            if ".HostConfig" in argv[3]:
                return json.dumps({
                    "ReadonlyRootfs": self.unsafe_runtime != "writable_root",
                    "Privileged": self.unsafe_runtime == "privileged",
                    "CapAdd": ["SYS_ADMIN"] if self.unsafe_runtime == "added_capability" else None,
                    "CapDrop": [] if self.unsafe_runtime == "caps" else service["cap_drop"],
                    "SecurityOpt": ([] if self.unsafe_runtime == "privileges"
                                    else service["security_opt"]),
                    "Sysctls": ({} if self.unsafe_runtime == "missing_sysctl"
                                else {key: str(value) for key, value in service["sysctls"].items()}),
                    "PublishAllPorts": self.unsafe_runtime == "publish_all",
                    "PortBindings": ({"443/tcp": [{"HostPort": "443"}]}
                                     if self.unsafe_runtime == "host_port" else {}),
                })
            if ".Mounts" in argv[3]:
                volume = service["volumes"][0]
                return json.dumps([{ "Type": "bind",
                    "Source": "/wrong/state" if self.unsafe_runtime == "state_mount"
                    else volume["source"],
                    "Destination": volume["target"], "RW": True}])
            network = service["networks"]["aiport_lan"]
            network_name = (self.manifest["networks"]["aiport_lan"].get("name")
                            or "local-aiport_aiport_lan")
            if self.unsafe_network == "attachment":
                network_name = "wrong"
            return json.dumps({network_name: {
                "IPAddress": "192.168.10.200" if self.wrong_ip else network["ipv4_address"],
                "MacAddress": network["mac_address"]}})
        if "exec" in argv:
            if self.fail_health:
                raise ReconcileError("health failed")
            return json.dumps({"service": "aiport-candidate", "adopted": self.adopted,
                               "control_connected": self.control_connected})
        if "up" in argv:
            self.started = True
            return ""
        if "stop" in argv:
            self.started = False
            return ""
        raise AssertionError(argv)


def test_dry_run_does_not_mutate_and_apply_starts_once(tmp_path):
    _, states, _, manifest, _ = inputs(tmp_path)
    path = tmp_path / "compose.json"
    fake = FakeDocker(manifest)
    dry = reconcile(path, manifest, states, run=fake)
    assert dry["would_start"] == ["aiport_slot_2"]
    assert not any("up" in call or "stop" in call for call in fake.calls)
    fake.calls.clear()
    applied = reconcile(path, manifest, states, apply=True, run=fake,
                        pause=lambda _: None)
    assert applied["started"] == ["aiport_slot_2"]
    assert applied["readiness"] == {"aiport_slot_2": "awaiting_adoption"}
    assert sum("up" in call for call in fake.calls) == 1
    assert "--no-recreate" in next(call for call in fake.calls if "up" in call)
    assert "--pull" in next(call for call in fake.calls if "up" in call)


def test_external_macvlan_is_verified_before_start(tmp_path):
    plan, states, options, _, report = inputs(tmp_path)
    options["external_network"] = "caddy_lan"
    selected = {2: states[2]}
    manifest = build_nas_compose(plan, selected, **options)
    assert verify_inputs(plan, manifest, report, selected, options) == ["aiport_slot_2"]
    fake = FakeDocker(manifest)
    outcome = reconcile(tmp_path / "compose.json", manifest, selected,
                        apply=True, run=fake, pause=lambda _: None)
    assert outcome["started"] == ["aiport_slot_2"]
    assert sum(call[:3] == ["docker", "network", "inspect"] for call in fake.calls) == 1


@pytest.mark.parametrize("unsafe_network", [
    "name", "driver", "parent", "subnet", "gateway", "attachment",
])
def test_wrong_external_macvlan_or_container_attachment_blocks_apply(
        tmp_path, unsafe_network):
    plan, states, options, _, _ = inputs(tmp_path)
    manifest = build_nas_compose(plan, {2: states[2]},
                                 **(options | {"external_network": "caddy_lan"}))
    row = {"Service": "aiport_slot_2", "State": "created",
           "ID": "a" * 12, "Publishers": []}
    fake = FakeDocker(manifest, json.dumps(row), unsafe_network=unsafe_network)
    with pytest.raises(ReconcileError):
        reconcile(tmp_path / "compose.json", manifest, {2: states[2]},
                  apply=True, run=fake)
    assert not any("up" in call or "stop" in call for call in fake.calls)


def test_existing_running_container_is_preserved(tmp_path):
    _, states, _, manifest, _ = inputs(tmp_path)
    row = {"Service": "aiport_slot_2", "State": "running",
           "ID": "a" * 12, "Publishers": []}
    fake = FakeDocker(manifest, json.dumps(row))
    outcome = reconcile(tmp_path / "compose.json", manifest, states,
                        apply=True, run=fake)
    assert outcome["preserved"] == ["aiport_slot_2"]
    assert outcome["started"] == []
    assert not any("up" in call or "stop" in call for call in fake.calls)


def test_adopted_new_slot_must_reconnect_or_it_is_stopped(tmp_path):
    _, states, _, manifest, _ = inputs(tmp_path)
    fake = FakeDocker(manifest, adopted=True)
    with pytest.raises(ReconcileError, match="disconnected from Protect"):
        reconcile(tmp_path / "compose.json", manifest, states,
                  apply=True, run=fake, pause=lambda _: None)
    assert sum("exec" in call for call in fake.calls) == 16
    assert [call[-1] for call in fake.calls if "stop" in call] == ["aiport_slot_2"]


def test_existing_disconnected_slot_blocks_new_apply_without_stopping_it(tmp_path):
    _, states, _, manifest, _ = inputs(tmp_path)
    row = {"Service": "aiport_slot_2", "State": "running",
           "ID": "a" * 12, "Publishers": []}
    fake = FakeDocker(manifest, json.dumps(row), adopted=True)
    dry = reconcile(tmp_path / "compose.json", manifest, states, run=fake)
    assert dry["readiness"] == {"aiport_slot_2": "controller_disconnected"}
    with pytest.raises(ReconcileError, match="no new slots"):
        reconcile(tmp_path / "compose.json", manifest, states, apply=True, run=fake)
    assert not any("up" in call or "stop" in call for call in fake.calls)


def test_connected_adopted_slot_reports_ready(tmp_path):
    _, states, _, manifest, _ = inputs(tmp_path)
    fake = FakeDocker(manifest, adopted=True, control_connected=True)
    applied = reconcile(tmp_path / "compose.json", manifest, states,
                        apply=True, run=fake, pause=lambda _: None)
    assert applied["readiness"] == {"aiport_slot_2": "connected"}


def test_failed_new_container_stops_only_attempted_service(tmp_path):
    _, states, _, manifest, _ = inputs(tmp_path)
    fake = FakeDocker(manifest, fail_health=True)
    with pytest.raises(ReconcileError):
        reconcile(tmp_path / "compose.json", manifest, states,
                  apply=True, run=fake, pause=lambda _: None)
    assert [call[-1] for call in fake.calls if "stop" in call] == ["aiport_slot_2"]


@pytest.mark.parametrize("rows", [
    '{"Service":"rogue","State":"running","Publishers":[]}',
    '{"Service":"aiport_slot_2","State":"running","Publishers":[{"PublishedPort":443}]}',
    '{"Service":"aiport_slot_2","State":"restarting","Publishers":[]}',
])
def test_unexpected_docker_state_is_rejected_before_mutation(tmp_path, rows):
    _, states, _, manifest, _ = inputs(tmp_path)
    fake = FakeDocker(manifest, rows)
    with pytest.raises(ReconcileError):
        reconcile(tmp_path / "compose.json", manifest, states,
                  apply=True, run=fake)
    assert not any("up" in call or "stop" in call for call in fake.calls)


def test_running_slot_with_wrong_network_address_is_rejected(tmp_path):
    _, states, _, manifest, _ = inputs(tmp_path)
    row = {"Service": "aiport_slot_2", "State": "running",
           "ID": "a" * 12, "Publishers": []}
    fake = FakeDocker(manifest, json.dumps(row), wrong_ip=True)
    with pytest.raises(ReconcileError, match="IP or MAC"):
        reconcile(tmp_path / "compose.json", manifest, states, apply=True, run=fake)
    assert not any("up" in call or "stop" in call for call in fake.calls)


@pytest.mark.parametrize("unsafe_runtime", [
    "root", "writable_root", "privileged", "added_capability", "caps",
    "privileges", "state_mount", "missing_sysctl", "publish_all", "host_port",
])
def test_existing_slot_with_weakened_isolation_is_rejected(tmp_path, unsafe_runtime):
    _, states, _, manifest, _ = inputs(tmp_path)
    row = {"Service": "aiport_slot_2", "State": "running",
           "ID": "a" * 12, "Publishers": []}
    fake = FakeDocker(manifest, json.dumps(row), unsafe_runtime=unsafe_runtime)
    with pytest.raises(ReconcileError, match="user, isolation or state mount"):
        reconcile(tmp_path / "compose.json", manifest, states, apply=True, run=fake)
    assert not any("up" in call or "stop" in call for call in fake.calls)


def test_existing_slot_with_wrong_port_command_is_rejected_before_start(tmp_path):
    _, states, _, manifest, _ = inputs(tmp_path)
    row = {"Service": "aiport_slot_2", "State": "exited",
           "ID": "a" * 12, "Publishers": []}
    fake = FakeDocker(manifest, json.dumps(row), unsafe_runtime="wrong_port")
    with pytest.raises(ReconcileError, match="command"):
        reconcile(tmp_path / "compose.json", manifest, states, apply=True, run=fake)
    assert not any("up" in call or "stop" in call for call in fake.calls)
