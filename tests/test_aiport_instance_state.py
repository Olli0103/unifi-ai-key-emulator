"""Planned AI Port slots receive private identities without rotating old ones."""

import hashlib
import json
import ssl

import pytest

from aikey.aiport_deployment import plan_ai_ports
from aikey.aiport_instance_state import InstanceStateError, provision_slot
from aikey.tls import ensure_identity_certificate


def fixture(tmp_path):
    controller = tmp_path / "controller"
    controller.mkdir(mode=0o700)
    cert, _ = ensure_identity_certificate(controller, "2A1100F0A55E")
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    plan = plan_ai_ports({"schema": "aikey-camera-preflight/1", "cameras": [
        {"id": f"{1:024x}", "model": "UVC G3 Instant", "state": "CONNECTED",
         "processing_class": "legacy_ingress_needed"},
        {"id": f"{2:024x}", "model": "UVC G3 Instant", "state": "CONNECTED",
         "processing_class": "legacy_ingress_needed"}]},
        device_ips=["192.168.10.20"], ai_key_ip="192.168.10.21")
    root = tmp_path / "states"
    root.mkdir(mode=0o700)
    return plan, cert, pin, root


def test_provision_creates_stable_private_identity_and_never_rotates_it(tmp_path):
    plan, cert, pin, root = fixture(tmp_path)
    state = root / "slot-1"
    first = provision_slot(plan, 1, state, controller_ip="192.168.10.1",
                           controller_cert_file=cert, controller_pin=pin)
    assert first == {"slot": 1, "state": "created", "host_ip": "192.168.10.20"}
    assert state.stat().st_mode & 0o777 == 0o700
    assert all((state / name).stat().st_mode & 0o777 == 0o600 for name in (
        "identity.json", "config.json", "device.crt", "device.key",
        "controller-ca.pem"))
    config = json.loads((state / "config.json").read_text())
    identity = json.loads((state / "identity.json").read_text())
    assert config["mac"].startswith("02")
    assert len(identity["certificate_sha256"]) == 64
    assert config["device_ip"] == "192.168.10.20"
    assert config["controller_pin"] == pin
    previous_key = (state / "device.key").read_bytes()
    second = provision_slot(plan, 1, state, controller_ip="192.168.10.1",
                            controller_cert_file=cert, controller_pin=pin)
    assert second["state"] == "verified_existing"
    assert (state / "device.key").read_bytes() == previous_key


@pytest.mark.parametrize("change", [
    "unaddressed", "wrong_pin", "same_controller_ip", "same_ai_key_ip",
])
def test_invalid_plan_or_controller_trust_creates_no_state(tmp_path, change):
    plan, cert, pin, root = fixture(tmp_path)
    controller_ip = "192.168.10.1"
    if change == "unaddressed":
        plan["instances"][0]["host_ip"] = None
    elif change == "wrong_pin":
        pin = "a" * 64
    elif change == "same_controller_ip":
        controller_ip = "192.168.10.20"
    else:
        plan["ai_key"]["host_ip"] = "192.168.10.20"
    state = root / "slot-1"
    with pytest.raises(InstanceStateError):
        provision_slot(plan, 1, state, controller_ip=controller_ip,
                       controller_cert_file=cert, controller_pin=pin)
    assert not state.exists()


def test_changed_address_or_incomplete_state_never_overwrites_existing_identity(tmp_path):
    plan, cert, pin, root = fixture(tmp_path)
    state = root / "slot-1"
    provision_slot(plan, 1, state, controller_ip="192.168.10.1",
                   controller_cert_file=cert, controller_pin=pin)
    previous_key = (state / "device.key").read_bytes()
    plan["instances"][0]["host_ip"] = "192.168.10.22"
    with pytest.raises(InstanceStateError, match="differs"):
        provision_slot(plan, 1, state, controller_ip="192.168.10.1",
                       controller_cert_file=cert, controller_pin=pin)
    assert (state / "device.key").read_bytes() == previous_key
    (state / "config.json").unlink()
    plan["instances"][0]["host_ip"] = "192.168.10.20"
    with pytest.raises(InstanceStateError, match="incomplete"):
        provision_slot(plan, 1, state, controller_ip="192.168.10.1",
                       controller_cert_file=cert, controller_pin=pin)
    assert (state / "device.key").read_bytes() == previous_key


def test_world_readable_or_symlinked_existing_state_is_rejected(tmp_path):
    plan, cert, pin, root = fixture(tmp_path)
    state = root / "slot-1"
    provision_slot(plan, 1, state, controller_ip="192.168.10.1",
                   controller_cert_file=cert, controller_pin=pin)
    state.chmod(0o755)
    with pytest.raises(InstanceStateError, match="private"):
        provision_slot(plan, 1, state, controller_ip="192.168.10.1",
                       controller_cert_file=cert, controller_pin=pin)
    link = root / "linked"
    link.symlink_to(state, target_is_directory=True)
    with pytest.raises(InstanceStateError, match="symlink"):
        provision_slot(plan, 1, link, controller_ip="192.168.10.1",
                       controller_cert_file=cert, controller_pin=pin)


def test_new_identity_requires_a_private_parent(tmp_path):
    plan, cert, pin, root = fixture(tmp_path)
    root.chmod(0o755)
    with pytest.raises(InstanceStateError, match="private parent"):
        provision_slot(plan, 1, root / "slot-1", controller_ip="192.168.10.1",
                       controller_cert_file=cert, controller_pin=pin)
    assert not (root / "slot-1").exists()


def test_provisioned_certificate_fingerprint_cannot_change_silently(tmp_path):
    plan, cert, pin, root = fixture(tmp_path)
    state = root / "slot-1"
    provision_slot(plan, 1, state, controller_ip="192.168.10.1",
                   controller_cert_file=cert, controller_pin=pin)
    identity = json.loads((state / "identity.json").read_text())
    identity["certificate_sha256"] = "0" * 64
    (state / "identity.json").write_text(json.dumps(identity))
    with pytest.raises(InstanceStateError, match="differs"):
        provision_slot(plan, 1, state, controller_ip="192.168.10.1",
                       controller_cert_file=cert, controller_pin=pin)
