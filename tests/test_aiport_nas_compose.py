"""NAS Compose generation preserves verified per-slot identity and address."""

import hashlib
import json
import os
import ssl

import pytest

from aikey.aiport_deployment import plan_ai_ports
from aikey.aiport_instance_state import provision_slot
from aikey.aiport_nas_compose import NasComposeError, build_nas_compose, main
from aikey.tls import ensure_identity_certificate


def fixture(tmp_path):
    controller = tmp_path / "controller"
    controller.mkdir(mode=0o700)
    cert, _ = ensure_identity_certificate(controller, "2A1100F0A55E")
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    cameras = [{"id": f"{number:024x}", "model": "UVC G4 Bullet",
                "state": "CONNECTED", "processing_class": "legacy_ingress_needed",
                "source_kind": "protect", "recording_resolution": "4K"}
               for number in range(1, 4)]
    plan = plan_ai_ports({"schema": "aikey-camera-preflight/1", "cameras": cameras},
                         device_ips=["192.168.10.135", "192.168.10.136"],
                         ai_key_ip="192.168.10.98")
    root = tmp_path / "states"
    root.mkdir(mode=0o700)
    states = {}
    for slot in (1, 2):
        state = root / f"slot-{slot}"
        provision_slot(plan, slot, state, controller_ip="192.168.10.1",
                       controller_cert_file=cert, controller_pin=pin)
        states[slot] = state
    options = {"controller_ip": "192.168.10.1", "controller_pin": pin,
               "nas_ip": "192.168.10.110", "subnet": "192.168.10.0/24",
               "gateway": "192.168.10.1", "parent": "bond0.10",
               "image": "local-aiport:candidate", "uid": os.getuid(), "gid": os.getgid()}
    return plan, states, options


def test_nas_compose_has_one_fixed_identity_and_port_per_selected_slot(tmp_path):
    plan, states, options = fixture(tmp_path)
    compose = build_nas_compose(plan, states, **options)
    assert set(compose["services"]) == {"aiport_slot_1", "aiport_slot_2"}
    assert compose["networks"]["aiport_lan"]["driver"] == "macvlan"
    assert compose["networks"]["aiport_lan"]["driver_opts"] == {"parent": "bond0.10"}
    for slot, address in ((1, "192.168.10.135"), (2, "192.168.10.136")):
        service = compose["services"][f"aiport_slot_{slot}"]
        config = json.loads((states[slot] / "config.json").read_text())
        assert service["networks"]["aiport_lan"]["ipv4_address"] == address
        assert service["networks"]["aiport_lan"]["mac_address"].replace(":", "").upper() == config["mac"]
        assert service["command"] == ["--config", "/state/config.json", "--port", "443"]
        assert service["user"] == f"{os.getuid()}:{os.getgid()}"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["sysctls"] == {"net.ipv4.ip_unprivileged_port_start": 0}
        assert service["volumes"][0]["bind"] == {"create_host_path": False}
        assert "ports" not in service


def test_existing_mac_slot_can_be_excluded_during_nas_migration(tmp_path):
    plan, states, options = fixture(tmp_path)
    compose = build_nas_compose(plan, {2: states[2]}, **options)
    assert set(compose["services"]) == {"aiport_slot_2"}
    assert compose["services"]["aiport_slot_2"]["networks"]["aiport_lan"]["ipv4_address"] == "192.168.10.136"


def test_existing_macvlan_can_be_reused_with_an_explicit_network_contract(tmp_path):
    plan, states, options = fixture(tmp_path)
    compose = build_nas_compose(plan, {2: states[2]},
                                **(options | {"external_network": "caddy_lan"}))
    assert compose["networks"] == {"aiport_lan": {
        "external": True, "name": "caddy_lan"}}
    assert compose["x-aikey-network-check"] == {
        "name": "caddy_lan", "driver": "macvlan", "parent": "bond0.10",
        "subnet": "192.168.10.0/24", "gateway": "192.168.10.1"}
    assert compose["services"]["aiport_slot_2"]["networks"]["aiport_lan"][
        "ipv4_address"] == "192.168.10.136"


@pytest.mark.parametrize("name", ["", "bad/name", "bad name", ".hidden"])
def test_existing_network_requires_a_safe_explicit_name(tmp_path, name):
    plan, states, options = fixture(tmp_path)
    with pytest.raises(NasComposeError, match="valid name"):
        build_nas_compose(plan, {2: states[2]},
                          **(options | {"external_network": name}))


def test_selected_slot_can_start_before_other_planned_slots_have_addresses(tmp_path):
    plan, states, options = fixture(tmp_path)
    plan["instances"][1]["host_ip"] = None
    plan["instances"][1]["apple_publish"] = None
    plan["ai_port_instances_without_address"] = 1
    compose = build_nas_compose(plan, {1: states[1]}, **options)
    assert set(compose["services"]) == {"aiport_slot_1"}
    assert compose["services"]["aiport_slot_1"]["networks"]["aiport_lan"]["ipv4_address"] == "192.168.10.135"


def test_unselected_but_addressed_slot_still_cannot_conflict(tmp_path):
    plan, states, options = fixture(tmp_path)
    plan["instances"][1]["host_ip"] = "192.168.10.135"
    with pytest.raises(NasComposeError, match="distinct and reserved"):
        build_nas_compose(plan, {1: states[1]}, **options)


@pytest.mark.parametrize("change", [
    "unaddressed", "nas_conflict", "controller_conflict", "wrong_pin",
    "public_state", "missing_state", "wrong_owner", "wrong_subnet", "root_user",
])
def test_unsafe_network_or_identity_is_rejected(tmp_path, change):
    plan, states, options = fixture(tmp_path)
    selected = {2: states[2]}
    if change == "unaddressed":
        plan["instances"][1]["host_ip"] = None
    elif change == "nas_conflict":
        options["nas_ip"] = "192.168.10.136"
    elif change == "controller_conflict":
        options["controller_ip"] = "192.168.10.136"
    elif change == "wrong_pin":
        options["controller_pin"] = "0" * 64
    elif change == "public_state":
        states[2].chmod(0o755)
    elif change == "missing_state":
        (states[2] / "device.key").unlink()
    elif change == "wrong_owner":
        options["uid"] = os.getuid() + 1
    elif change == "wrong_subnet":
        options["subnet"] = "192.168.11.0/24"
    else:
        options["uid"] = 0
    with pytest.raises(NasComposeError):
        build_nas_compose(plan, selected, **options)


def test_manifest_output_is_private_and_idempotent(tmp_path):
    plan, states, options = fixture(tmp_path)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan))
    plan_file.chmod(0o600)
    output = tmp_path / "compose.json"
    argv = ["--plan", str(plan_file), "--slot-state", f"2={states[2]}",
            "--controller-ip", options["controller_ip"],
            "--controller-pin", options["controller_pin"],
            "--nas-ip", options["nas_ip"], "--subnet", options["subnet"],
            "--gateway", options["gateway"], "--parent", options["parent"],
            "--image", options["image"], "--uid", str(options["uid"]),
            "--gid", str(options["gid"]), "--output", str(output)]
    assert main(argv) == 0
    initial = output.read_bytes()
    assert output.stat().st_mode & 0o777 == 0o600
    assert main(argv) == 0
    assert output.read_bytes() == initial
    output.write_text("different\n")
    with pytest.raises(SystemExit):
        main(argv)
    assert output.read_text() == "different\n"
