"""A NAS deployment cannot change camera assignments or existing containers."""

import json
import time

import pytest

from aikey.aiport_nas_compose import build_nas_compose
from aikey.aiport_nas_reconcile import ReconcileError, reconcile, verify_inputs
from test_aiport_nas_compose import fixture


def inputs(tmp_path):
    plan, states, options = fixture(tmp_path)
    selected = {2: states[2]}
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
                 unsafe_runtime=None):
        self.manifest = manifest
        self.rows = rows
        self.fail_health = fail_health
        self.wrong_ip = wrong_ip
        self.unsafe_runtime = unsafe_runtime
        self.started = False
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
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
                })
            if ".Mounts" in argv[3]:
                volume = service["volumes"][0]
                return json.dumps([{ "Type": "bind",
                    "Source": "/wrong/state" if self.unsafe_runtime == "state_mount"
                    else volume["source"],
                    "Destination": volume["target"], "RW": True}])
            network = service["networks"]["aiport_lan"]
            return json.dumps({"local-aiport_aiport_lan": {
                "IPAddress": "192.168.10.200" if self.wrong_ip else network["ipv4_address"],
                "MacAddress": network["mac_address"]}})
        if "exec" in argv:
            if self.fail_health:
                raise ReconcileError("health failed")
            return json.dumps({"service": "aiport-candidate", "adopted": False,
                               "control_connected": False})
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
    assert sum("up" in call for call in fake.calls) == 1
    assert "--no-recreate" in next(call for call in fake.calls if "up" in call)
    assert "--pull" in next(call for call in fake.calls if "up" in call)


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
    "privileges", "state_mount",
])
def test_existing_slot_with_weakened_isolation_is_rejected(tmp_path, unsafe_runtime):
    _, states, _, manifest, _ = inputs(tmp_path)
    row = {"Service": "aiport_slot_2", "State": "running",
           "ID": "a" * 12, "Publishers": []}
    fake = FakeDocker(manifest, json.dumps(row), unsafe_runtime=unsafe_runtime)
    with pytest.raises(ReconcileError, match="user, isolation or state mount"):
        reconcile(tmp_path / "compose.json", manifest, states, apply=True, run=fake)
    assert not any("up" in call or "stop" in call for call in fake.calls)
