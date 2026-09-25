"""Rollout plans keep paired cameras and slot identities, and converge."""

import hashlib
import json
import ssl
from fractions import Fraction

import pytest

from aikey.aiport_rollout import (
    RolloutError, apply_and_register, apply_rollout, eligible_cameras, local_changes,
    observe_slot, plan_rollout, public,
)
from aikey.tls import ensure_identity_certificate

CAM = {name: f"2A11000000{index:02X}" for index, name in enumerate(
    ("Flur", "Schlafzimmer", "Esszimmer", "Haustuer", "Keller", "Garten", "Dach"), 1)}


def report(*, extra=(), offline=(), models=None, drop=()):
    models = models or {}
    rows = []
    for index, (name, mac) in enumerate(CAM.items(), 1):
        if name in drop or name in ("Keller", "Garten", "Dach") and name not in extra:
            continue
        rows.append({"id": f"{index:024x}", "mac": mac, "name": name,
                     "model": models.get(name, "UVC G3 Instant"),
                     "state": "DISCONNECTED" if name in offline else "CONNECTED",
                     "processing_class": "smart_event_candidate"})
    rows.append({"id": f"{99:024x}", "mac": "2A11000000FF", "name": "G6",
                 "model": "UVC G6 Instant", "state": "CONNECTED",
                 "processing_class": "smart_event_candidate"})
    return {"schema": "aikey-camera-preflight/1", "cameras": rows}


def slot(target, host, allow, paired):
    return {"target": target, "host_ip": host,
            **observe_slot({"paired_streams": [{"camera_mac": CAM[n]} for n in allow]},
                           None if paired is None else {"pool_cameras": [
                               {"policy_enabled": n in paired} for n in allow]})}


def current():
    return {"mac": slot("mac", "192.168.0.135", ["Flur", "Schlafzimmer"], {"Flur", "Schlafzimmer"}),
            "nas-slot-2": slot("nas", "192.168.0.136", ["Esszimmer", "Haustuer"],
                               {"Esszimmer", "Haustuer"})}


def kinds(plan):
    return sorted((a["kind"], a.get("slot"), a.get("camera")) for a in plan["actions"])


def test_current_deployment_is_a_no_op():
    plan = plan_rollout(report(), current(), new_slot_addresses=["192.168.0.139"])
    assert plan["actions"] == [] and plan["new_slots"] == []
    assert not local_changes(plan)
    assert plan["slots"]["mac"]["load"] == Fraction(2, 5)
    again = plan_rollout(report(), current(), new_slot_addresses=["192.168.0.139"])
    assert again["revision"] == plan["revision"]
    text = json.dumps(public(plan))
    assert "2A11" not in text and "192.168" not in text and "Flur" in text


def test_new_camera_joins_a_slot_with_capacity_and_waits_for_pairing():
    plan = plan_rollout(report(extra=("Keller",)), current())
    assert ("add_to_allowlist", "mac", "Keller") in kinds(plan)
    assert ("pair_in_protect", "mac", "Keller") in kinds(plan)
    assert not any(a["kind"] in {"pair", "unpair", "remove_from_allowlist"}
                   for a in plan["actions"])
    # After the local allowlist edit, only the user's pairing remains.
    applied = current()
    applied["mac"] = slot("mac", "192.168.0.135", ["Flur", "Schlafzimmer", "Keller"],
                          {"Flur", "Schlafzimmer"})
    second = plan_rollout(report(extra=("Keller",)), applied)
    assert kinds(second) == [("pair_in_protect", "mac", "Keller")]
    assert not local_changes(second)
    # Once Protect pairs it, the plan is empty.
    applied["mac"] = slot("mac", "192.168.0.135", ["Flur", "Schlafzimmer", "Keller"],
                          {"Flur", "Schlafzimmer", "Keller"})
    assert plan_rollout(report(extra=("Keller",)), applied)["actions"] == []


def test_full_slots_get_a_new_slot_from_the_address_pool_only():
    heavy = {name: "UVC G4 Pro" for name in CAM}          # 4K: half a slot each
    plan = plan_rollout(report(extra=("Keller",), models=heavy), current(),
                        new_slot_addresses=["192.168.0.135", "192.168.0.139"])
    assert [(s["label"], s["host_ip"]) for s in plan["new_slots"]] == [
        ("nas-slot-3", "192.168.0.139")]                      # used address skipped
    expected = {("create_slot", "nas-slot-3", None), ("add_to_allowlist", "nas-slot-3", "Keller"),
                ("deploy_nas_service", "nas-slot-3", None), ("adopt_in_protect", "nas-slot-3", None),
                ("pair_in_protect", "nas-slot-3", "Keller")}
    assert set(kinds(plan)) == expected
    none = plan_rollout(report(extra=("Keller",), models=heavy), current())
    assert kinds(none) == [("address_needed", None, "Keller")]


def test_stale_and_vanished_entries_are_removed_offline_cameras_kept():
    slots = current()
    slots["mac"] = slot("mac", "192.168.0.135", ["Flur", "Schlafzimmer", "Esszimmer", "Dach"],
                        {"Flur", "Schlafzimmer"})
    plan = plan_rollout(report(offline=("Schlafzimmer",)), slots)
    assert set(kinds(plan)) == {("remove_from_allowlist", "mac", "Esszimmer"),
                                ("remove_from_allowlist", "mac", "unknown camera")}
    assert "Schlafzimmer" in public(plan)["slots"]["mac"]["keep"]


def test_unobserved_slot_is_never_changed():
    slots = current()
    slots["nas-slot-2"] = slot("nas", "192.168.0.136", ["Esszimmer", "Haustuer"], None)
    plan = plan_rollout(report(), slots)
    assert kinds(plan) == [("slot_unobserved", "nas-slot-2", None)]
    assert not local_changes(plan)


def test_eligibility_skips_other_models_and_rejects_bad_reports():
    assert "2A11000000FF" not in eligible_cameras(report())
    with pytest.raises(RolloutError):
        eligible_cameras({"schema": "other", "cameras": []})


def _provisioned(tmp_path):
    controller = tmp_path / "controller"
    controller.mkdir(mode=0o700)
    cert, _ = ensure_identity_certificate(controller, "2A1100F0A55E")
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    root = tmp_path / "slots"
    root.mkdir(mode=0o700)
    template = root / "mac"
    template.mkdir(mode=0o700)
    (template / "controller-ca.pem").write_bytes(cert.read_bytes())
    (template / "controller-ca.pem").chmod(0o600)
    (template / "config.json").write_text(json.dumps({
        "controller_ip": "192.168.10.1", "controller_pin": pin, "device_ip": "192.168.10.20",
        "firmware_version": "5.1.12", "mac": "02AABBCCDDEE",
        "paired_streams": [{"camera_mac": CAM["Flur"], "source_ip": "192.168.10.1",
                            "ffmpeg_path": "/usr/bin/ffmpeg"},
                           {"camera_mac": CAM["Schlafzimmer"], "source_ip": "192.168.10.1",
                            "ffmpeg_path": "/usr/bin/ffmpeg"}],
        "live_pool_detector": {"inference_backend": "vision_api", "threshold": 0.8}}))
    (template / "config.json").chmod(0o600)
    return root, template


def test_apply_edits_allowlists_and_provisions_a_registered_new_slot(tmp_path):
    root, template = _provisioned(tmp_path)
    heavy = {name: "UVC G4 Pro" for name in CAM}
    slots = {"mac": slot("mac", "192.168.10.20", ["Flur", "Schlafzimmer"],
                         {"Flur", "Schlafzimmer"})}
    plan = plan_rollout(report(extra=("Keller",), models=heavy, drop=("Esszimmer", "Haustuer")), slots,
                        new_slot_addresses=["192.168.10.30"])
    rollout_path = tmp_path / "rollout.json"
    rollout = {"schema": "aikey-aiport-rollout/1",
               "slots": [{"label": "mac", "target": "mac", "state_dir": str(template),
                          "health_port": 8443}],
               "new_slots": {"target": "nas", "state_parent": str(root),
                             "addresses": ["192.168.10.30"], "health_port": 443}}
    changed = apply_and_register(rollout_path, rollout, plan)
    assert changed == ["nas-slot-1"]
    new_config = json.loads((root / "nas-slot-1" / "config.json").read_text())
    assert new_config["device_ip"] == "192.168.10.30" and new_config["mac"].startswith("02")
    assert [s["camera_mac"] for s in new_config["paired_streams"]] == [CAM["Keller"]]
    assert new_config["live_pool_detector"] == {"inference_backend": "vision_api",
                                                "threshold": 0.8}
    assert "max_requests_per_hour" not in new_config["live_pool_detector"]
    assert [s["label"] for s in json.loads(rollout_path.read_text())["slots"]] == [
        "mac", "nas-slot-1"]
    identity = (root / "nas-slot-1" / "device.key").read_bytes()
    # The registered but not yet running slot is unobserved: nothing new is planned.
    slots["nas-slot-1"] = {"target": "nas", "host_ip": "192.168.10.30",
                           **observe_slot(new_config, None)}
    again = plan_rollout(report(extra=("Keller",), models=heavy, drop=("Esszimmer", "Haustuer")), slots,
                         new_slot_addresses=["192.168.10.30"])
    assert not local_changes(again) and again["new_slots"] == []
    assert apply_rollout(again, {"mac": template, "nas-slot-1": root / "nas-slot-1"},
                         new_slot_parent=root, template_label="mac") == []
    assert (root / "nas-slot-1" / "device.key").read_bytes() == identity   # never rotated


def test_apply_removes_stale_entries_with_a_backup(tmp_path):
    root, template = _provisioned(tmp_path)
    slots = {"mac": slot("mac", "192.168.10.20", ["Flur", "Schlafzimmer"], {"Flur"}),
             "nas-slot-2": slot("nas", "192.168.10.21", ["Schlafzimmer"], {"Schlafzimmer"})}
    plan = plan_rollout(report(), slots)
    other = root / "nas-slot-2"
    other.mkdir(mode=0o700)
    (other / "config.json").write_text(json.dumps({
        "controller_ip": "192.168.10.1",
        "paired_streams": [{"camera_mac": CAM["Schlafzimmer"]}]}))
    assert apply_rollout(plan, {"mac": template, "nas-slot-2": other},
                         new_slot_parent=root, template_label="mac") == ["mac"]
    saved = [s["camera_mac"] for s in json.loads(
        (template / "config.json").read_text())["paired_streams"]]
    assert CAM["Flur"] in saved and CAM["Schlafzimmer"] not in saved   # stale entry removed
    assert saved[1:] == [CAM["Esszimmer"], CAM["Haustuer"]] or saved[1:] == [
        CAM["Haustuer"], CAM["Esszimmer"]]                             # unassigned, now reserved
    assert (template / "config.json.before-rollout").exists()
