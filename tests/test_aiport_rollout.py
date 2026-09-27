"""Rollout plans keep paired cameras and slot identities, and converge."""

import hashlib
import json
import ssl
from fractions import Fraction
from pathlib import Path

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
                ("pair_in_protect", "nas-slot-3", "Keller"),
                ("address_conflict", None, None)}                # .135 is the Mac slot's
    assert set(kinds(plan)) == expected
    none = plan_rollout(report(extra=("Keller",), models=heavy), current())
    assert kinds(none) == [("address_needed", None, "Keller")]


def test_stale_and_vanished_entries_are_removed_offline_cameras_kept():
    slots = current()
    slots["mac"] = slot("mac", "192.168.0.135", ["Flur", "Schlafzimmer", "Esszimmer", "Dach"],
                        {"Flur", "Schlafzimmer"})
    plan = plan_rollout(report(offline=("Schlafzimmer",)), slots)
    assert set(kinds(plan)) == {("remove_from_allowlist", "mac", "Esszimmer"),
                                ("remove_from_allowlist", "mac", "unknown camera"),
                                ("restart_mac_container", "mac", None)}   # edits are local copies
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


LIVE_MODELS = {   # the paired nine, by slot, with their live main-lens streams
    "mac": [("Flur", "UVC G3 Instant"), ("Schlafzimmer", "UVC G3 Instant"),
            ("Buero", "UVC G5 Flex")],
    "nas-slot-2": [("Esszimmer", "UVC G4 Instant"), ("Haustuer", "UVC G4 Doorbell Pro")],
    "nas-slot-3": [("Einfahrt", "UVC G4 Pro"), ("Giebel Vorn", "UVC G4 Bullet")],
    "nas-slot-4": [("Garage", "UVC G4 Dome"), ("Giebel hinten", "UVC G4 Bullet")],
}


def live_like(extra=(), points=None):
    rows, slots, index = [], {}, 0
    for label, members in LIVE_MODELS.items():
        macs = []
        for name, model in members:
            index += 1
            mac = f"2A1100AA{index:04X}"
            macs.append(mac)
            rows.append({"id": f"{index:024x}", "mac": mac, "name": name, "model": model,
                         "state": "CONNECTED", "processing_class": "smart_event_candidate"})
        config = {"paired_streams": [{"camera_mac": mac} for mac in macs]}
        health = {"pool_cameras": [{"policy_enabled": True,
                                    **({"stream_points": points[mac]} if points and mac in points
                                       else {})} for mac in macs]}
        slots[label] = {"target": "mac" if label == "mac" else "nas",
                        "host_ip": f"192.168.0.{135 + len(slots)}", **observe_slot(config, health)}
    for index, (name, model) in enumerate(extra, 50):
        rows.append({"id": f"{index:024x}", "mac": f"2A1100BB{index:04X}", "name": name,
                     "model": model, "state": "CONNECTED",
                     "processing_class": "smart_event_candidate"})
    return {"schema": "aikey-camera-preflight/1", "cameras": rows}, slots


def test_live_nine_camera_loads_follow_the_ingress_point_rule():
    inventory, slots = live_like()
    plan = plan_rollout(inventory, slots)
    assert plan["actions"] == []
    assert {label: str(slot["load"]) for label, slot in plan["slots"].items()} == {
        "mac": "9/10", "nas-slot-2": "7/10", "nas-slot-3": "1", "nas-slot-4": "1"}


def test_one_hypothetical_hd_camera_fits_the_doorbell_slot_without_a_fifth_ai_port():
    inventory, slots = live_like(extra=[("Neu", "UVC G3 Instant")])
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.0.139"])
    assert plan["new_slots"] == []                          # old estimate made a fifth
    assert ("add_to_allowlist", "nas-slot-2", "Neu") in kinds(plan)
    assert str(plan["slots"]["nas-slot-2"]["load"]) == "9/10"
    assert all(slot["load"] <= 1 for slot in plan["slots"].values())


def test_a_hypothetical_five_point_camera_still_needs_a_new_slot():
    inventory, slots = live_like(extra=[("Neu", "UVC G4 Bullet")])
    plan = plan_rollout(inventory, slots)
    assert kinds(plan) == [("address_needed", None, "Neu")]  # 7+5 > 10 everywhere
    unknown, slots = live_like(extra=[("Neu", "UVC G5 Future")])   # unknown G5: 5 points
    assert kinds(plan_rollout(unknown, slots)) == [("address_needed", None, "Neu")]


def test_observed_stream_points_override_a_model_estimate():
    inventory, _ = live_like()
    doorbell = next(row["mac"] for row in inventory["cameras"] if row["name"] == "Haustuer")
    esszimmer = next(row["mac"] for row in inventory["cameras"] if row["name"] == "Esszimmer")
    # e.g. Protect switched Esszimmer to a 2K stream: 3 points instead of 5.
    inventory, slots = live_like(extra=[("Neu", "UVC G4 Bullet")],
                                 points={esszimmer: 3, doorbell: 2})
    plan = plan_rollout(inventory, slots)
    assert ("add_to_allowlist", "nas-slot-2", "Neu") in kinds(plan)
    assert str(plan["slots"]["nas-slot-2"]["load"]) == "1"


# The live NAS project's shape (synthetic addresses), one service per slot.
COMPOSE = """name: local-aiport-nas
services:
  aiport_slot_2:
    image: local-aiport:nas-amd64-held-shadow-r22-20260926
    build:
      context: /home/olli/aiport-deployment/build-motion-tuned
    user: "1000:10"
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    tmpfs: ["/tmp:rw,nosuid,noexec,size=64m,mode=1777"]
    dns: [192.168.10.1]
    sysctls:
      net.ipv4.ip_unprivileged_port_start: 0
    restart: unless-stopped
    stop_grace_period: 20s
    command: ["--config", "/state/config.json", "--port", "443"]
    volumes:
      - type: bind
        source: /home/olli/aiport-deployment/slot-2
        target: /state
        bind:
          create_host_path: false
    networks:
      caddy_lan:
        ipv4_address: 192.168.10.21
        mac_address: 02:02:a5:a0:8e:2d
networks:
  caddy_lan:
    external: true
"""


LIVE_IMAGE = "local-aiport:nas-amd64-held-shadow-r22-20260926"


def test_a_compose_service_is_appended_verbatim_and_removed_exactly():
    from aikey.aiport_compose_slots import (
        ComposeSlotError, add_slot_service, remove_slot_service, slot_service)
    options = {"template": "aiport_slot_2", "service": "aiport_slot_5",
               "source": "/home/olli/aiport-deployment/slot-5",
               "ipv4": "192.168.10.30", "mac": "02AABBCCDD05"}
    block = slot_service(COMPOSE, **options)
    added = add_slot_service(COMPOSE, **options)
    assert added.startswith(COMPOSE.split("networks:\n  caddy_lan:\n    external")[0])
    assert block in added and added.count("aiport_slot_2:") == 1
    assert "ipv4_address: 192.168.10.30" in block and "mac_address: 02:aa:bb:cc:dd:05" in block
    assert add_slot_service(added, **options) == added                   # idempotent
    for clash in ({"ipv4": "192.168.10.21"}, {"mac": "0202A5A08E2D"},
                  {"source": "/home/olli/aiport-deployment/slot-2"}):
        with pytest.raises(ComposeSlotError):
            add_slot_service(COMPOSE, **{**options, "service": "aiport_slot_6", **clash})
    assert remove_slot_service(added, block=block) == COMPOSE             # exact inverse


def _compose_settings(text=COMPOSE):
    return {"text": text, "template": "aiport_slot_2", "live_image": LIVE_IMAGE,
            "state_parent": "/home/olli/aiport-deployment", "service_prefix": "aiport_slot_"}


def test_the_live_nine_camera_plan_with_compose_is_a_no_op():
    inventory, slots = live_like()
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.0.139"],
                        compose=_compose_settings())
    assert plan["actions"] == [] and plan["new_slots"] == [] and not local_changes(plan)


def _one_new_nas_slot(tmp_path, *, model="UVC G4 Pro"):
    root, template = _provisioned(tmp_path)
    heavy = {name: model for name in CAM}
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text(COMPOSE)
    compose_path.chmod(0o600)
    rollout_path = tmp_path / "rollout.json"
    rollout = {"schema": "aikey-aiport-rollout/1",
               "slots": [{"label": "mac", "target": "mac", "state_dir": str(template),
                          "health_port": 8443}],
               "new_slots": {"target": "nas", "state_parent": str(root),
                             "addresses": ["192.168.10.30"], "health_port": 443},
               "compose": {"path": str(compose_path), "template": "aiport_slot_2",
                           "state_parent": "/home/olli/aiport-deployment",
                           "live_image": LIVE_IMAGE, "health_timeout_seconds": 600}}
    slots = {"mac": slot("mac", "192.168.10.20", ["Flur", "Schlafzimmer"],
                         {"Flur", "Schlafzimmer"})}
    inventory = report(extra=("Keller",), models=heavy, drop=("Esszimmer", "Haustuer"))
    return root, compose_path, rollout_path, rollout, slots, inventory


def test_an_extra_camera_plans_and_applies_one_reviewed_nas_service(tmp_path):
    root, compose_path, rollout_path, rollout, slots, inventory = _one_new_nas_slot(tmp_path)
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                        compose=_compose_settings())
    new, = plan["new_slots"]
    action, = [a for a in plan["actions"] if a["kind"] == "compose_add_service"]
    assert action["automated"] and action["service"] == "aiport_slot_1"
    assert action["ipv4_address"] == "192.168.10.30"
    assert action["bind_source"] == "/home/olli/aiport-deployment/slot-1"
    assert action["block"] in public(plan)["actions"][[a["kind"] for a in public(plan)[
        "actions"]].index("compose_add_service")]["block"]               # shown before apply
    manual = {a["kind"] for a in plan["actions"] if not a["automated"]}
    assert manual == {"upload_slot_state", "redeploy_nas_project", "verify_slot_health",
                      "adopt_in_protect", "pair_in_protect"}
    assert not any(a["kind"] in {"pair", "unpair"} for a in plan["actions"])
    # The dry run is stable (deterministic MAC), so its revision can gate apply.
    again = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                         compose=_compose_settings())
    assert again["revision"] == plan["revision"]
    apply_and_register(rollout_path, rollout, plan)
    text = compose_path.read_text()
    assert text.startswith(COMPOSE.split("networks:\n  caddy_lan:\n    external")[0])
    assert action["block"] in text
    identity = json.loads((root / "nas-slot-1" / "config.json").read_text())
    assert identity["mac"] == new["mac"] and identity["device_ip"] == "192.168.10.30"
    registered = json.loads(rollout_path.read_text())["slots"][-1]
    assert registered["label"] == "nas-slot-1" and registered["pending"]["service"] == "aiport_slot_1"
    assert (compose_path.parent / "compose.yaml.before-nas-slot-1").read_text() == COMPOSE


def test_repeated_apply_changes_nothing_more(tmp_path):
    root, compose_path, rollout_path, rollout, slots, inventory = _one_new_nas_slot(tmp_path)
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                        compose=_compose_settings())
    apply_and_register(rollout_path, rollout, plan)
    after = compose_path.read_text()
    # Registered, not yet running: unobserved, so nothing new is planned.
    new_config = json.loads((root / "nas-slot-1" / "config.json").read_text())
    slots["nas-slot-1"] = {"target": "nas", "host_ip": "192.168.10.30",
                           "mac": new_config["mac"], **observe_slot(new_config, None)}
    second = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                          compose=_compose_settings(after))
    assert not local_changes(second) and second["new_slots"] == []
    # Re-applying the already applied plan changes nothing: the identity is
    # verified unchanged and the Compose service is already present (#45).
    identity = (root / "nas-slot-1" / "device.key").read_bytes()
    assert apply_rollout(plan, {"mac": Path(rollout["slots"][0]["state_dir"])},
                         new_slot_parent=root, template_label="mac",
                         compose_path=compose_path) == []
    assert compose_path.read_text() == after
    assert (root / "nas-slot-1" / "device.key").read_bytes() == identity


def test_a_new_slot_directory_that_differs_from_the_plan_is_refused(tmp_path):
    root, compose_path, rollout_path, rollout, slots, inventory = _one_new_nas_slot(tmp_path)
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                        compose=_compose_settings())
    apply_and_register(rollout_path, rollout, plan)
    path = root / "nas-slot-1" / "config.json"
    config = json.loads(path.read_text())
    config["paired_streams"].append({"camera_mac": CAM["Garten"], "source_ip": "192.168.10.1",
                                     "ffmpeg_path": "/usr/bin/ffmpeg"})
    path.write_text(json.dumps(config))
    edited = path.read_bytes()
    with pytest.raises(RolloutError, match="differs from the reviewed plan"):
        apply_rollout(plan, {"mac": Path(rollout["slots"][0]["state_dir"])},
                      new_slot_parent=root, template_label="mac", compose_path=compose_path)
    assert path.read_bytes() == edited


def test_a_new_slot_silent_past_its_deadline_is_rolled_back(tmp_path):
    from aikey.aiport_rollout import verify_new_slots
    root, compose_path, rollout_path, rollout, slots, inventory = _one_new_nas_slot(tmp_path)
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                        compose=_compose_settings())
    apply_and_register(rollout_path, rollout, plan)
    deadline = rollout["slots"][-1]["pending"]["deadline"]
    silent = lambda *_args: None                                     # noqa: E731
    assert verify_new_slots(rollout_path, rollout, health=silent,
                            now=deadline - 1) == {"nas-slot-1": "waiting"}
    assert verify_new_slots(rollout_path, rollout, health=silent,
                            now=deadline + 1) == {"nas-slot-1": "rolled_back"}
    assert compose_path.read_text() == COMPOSE                       # exactly removed
    assert [s["label"] for s in json.loads(rollout_path.read_text())["slots"]] == ["mac"]
    assert (root / "nas-slot-1" / "device.key").exists()             # identity kept


def test_a_new_slot_that_answers_health_is_kept(tmp_path):
    from aikey.aiport_rollout import verify_new_slots
    _root, compose_path, rollout_path, rollout, slots, inventory = _one_new_nas_slot(tmp_path)
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                        compose=_compose_settings())
    apply_and_register(rollout_path, rollout, plan)
    applied = compose_path.read_text()
    healthy = lambda *_args: {"control_connected": False}              # noqa: E731
    assert verify_new_slots(rollout_path, rollout, health=healthy) == {"nas-slot-1": "healthy"}
    assert "pending" not in json.loads(rollout_path.read_text())["slots"][-1]
    assert compose_path.read_text() == applied


def test_a_new_slot_needed_only_by_a_fallback_estimate_is_not_created(tmp_path):
    # An unknown model gets the 5-point fallback; at the 2-point minimum it
    # would fit the existing slot, so the plan asks for review instead.
    root, compose_path, rollout_path, rollout, slots, _inventory = _one_new_nas_slot(tmp_path)
    inventory, live_slots = live_like(extra=[("Neu", "UVC G5 Future")])
    plan = plan_rollout(inventory, live_slots, new_slot_addresses=["192.168.0.139"],
                        compose=_compose_settings())
    new, = plan["new_slots"]
    assert new["capacity_basis"] == "unverified"
    assert ("capacity_unverified", new["label"], "Neu") in kinds(plan)
    assert not local_changes(plan)
    allowed = plan_rollout(inventory, live_slots, new_slot_addresses=["192.168.0.139"],
                           compose=_compose_settings(), allow_estimated_capacity=True)
    assert local_changes(allowed)
    # An evidenced model (G4 Bullet, 5 points) is not second-guessed.
    known, live_slots = live_like(extra=[("Neu", "UVC G4 Bullet")])
    evidenced = plan_rollout(known, live_slots, new_slot_addresses=["192.168.0.139"],
                             compose=_compose_settings())
    assert evidenced["new_slots"][0]["capacity_basis"] == "evidenced" and local_changes(evidenced)


# --- #45 safety gates: stale Compose, addresses, deploy steps, freshness, rollback ---

def test_the_live_nine_camera_plan_with_reserved_addresses_is_still_a_no_op():
    inventory, slots = live_like()
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.0.139"],
                        reserved_addresses=["192.168.0.98", "192.168.0.110", "192.168.0.1"],
                        compose=_compose_settings())
    assert plan["actions"] == [] and plan["new_slots"] == [] and not local_changes(plan)


def test_a_reserved_address_never_becomes_a_new_slot():
    heavy = {name: "UVC G4 Pro" for name in CAM}
    plan = plan_rollout(report(extra=("Keller",), models=heavy), current(),
                        new_slot_addresses=["192.168.0.98"], reserved_addresses=["192.168.0.98"])
    assert plan["new_slots"] == []
    assert set(kinds(plan)) == {("address_conflict", None, None),
                                ("address_needed", None, "Keller")}


@pytest.mark.parametrize("change,reason", [
    ({"live_image": None}, "live_image_unset"),
    ({"live_image": "local-aiport:nas-amd64-ai-port-plates-r25-20260926"}, "template_image_differs"),
])
def test_a_stale_compose_copy_never_clones_a_service(tmp_path, change, reason):
    root, compose_path, rollout_path, rollout, slots, inventory = _one_new_nas_slot(tmp_path)
    settings = {**_compose_settings(), **change}
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"], compose=settings)
    stale, = [a for a in plan["actions"] if a["kind"] == "compose_copy_stale"]
    assert stale["reason"] == reason and not stale["automated"]
    assert not any(a["automated"] for a in plan["actions"]
                   if a["kind"] in {"create_slot", "compose_add_service"})
    rollout["compose"]["live_image"] = change["live_image"]
    assert apply_and_register(rollout_path, rollout, plan) == []
    assert compose_path.read_text() == COMPOSE and not (root / "nas-slot-1").exists()


def test_mixed_images_in_the_compose_copy_are_stale():
    from aikey.aiport_compose_slots import add_slot_service
    from aikey.aiport_rollout import _compose_staleness
    two = add_slot_service(COMPOSE, template="aiport_slot_2", service="aiport_slot_3",
                           source="/home/olli/aiport-deployment/slot-3",
                           ipv4="192.168.10.31", mac="02AABBCCDD03")
    assert _compose_staleness(_compose_settings(two)) is None            # all on the live image
    older = two.replace(LIVE_IMAGE, "local-aiport:older", 2).replace(
        "local-aiport:older", LIVE_IMAGE, 1)                                # slot 3 left behind
    assert _compose_staleness(_compose_settings(older)) == "mixed_images"


def test_existing_slot_edits_list_the_upload_and_restart_steps():
    slots = current()
    slots["nas-slot-2"] = slot("nas", "192.168.0.136", ["Esszimmer", "Haustuer", "Dach"],
                               {"Esszimmer", "Haustuer"})
    plan = plan_rollout(report(extra=("Keller",)), slots)
    manual = {(a["kind"], a.get("slot")) for a in plan["actions"] if not a["automated"]}
    assert {("upload_slot_config", "nas-slot-2"), ("restart_nas_slot", "nas-slot-2")} <= manual
    assert ("pair_in_protect", plan["actions"][0].get("slot")) in manual or any(
        a["kind"] == "pair_in_protect" for a in plan["actions"])


def test_hypothetical_add_then_remove_keeps_every_other_identity(tmp_path):
    root, template = _provisioned(tmp_path)
    before = (template / "config.json").read_text()
    identity = json.loads(before)["mac"], json.loads(before)["device_ip"]
    slots = {"mac": slot("mac", "192.168.10.20", ["Flur", "Schlafzimmer"],
                         {"Flur", "Schlafzimmer"})}
    add = plan_rollout(report(extra=("Keller",), drop=("Esszimmer", "Haustuer")), slots)
    assert [(a["kind"], a.get("camera")) for a in add["actions"] if a["automated"]] == [
        ("add_to_allowlist", "Keller")]
    assert apply_rollout(add, {"mac": template}, new_slot_parent=root,
                         template_label="mac") == ["mac"]
    added = json.loads((template / "config.json").read_text())
    assert [s["camera_mac"] for s in added["paired_streams"]][-1] == CAM["Keller"]
    # Later the camera is removed from Protect, which then stops sending its policy.
    slots = {"mac": slot("mac", "192.168.10.20", ["Flur", "Schlafzimmer", "Keller"],
                         {"Flur", "Schlafzimmer"})}
    gone = plan_rollout(report(drop=("Esszimmer", "Haustuer")), slots)
    assert [(a["kind"], a.get("camera")) for a in gone["actions"] if a["automated"]] == [
        ("remove_from_allowlist", "unknown camera")]
    apply_rollout(gone, {"mac": template}, new_slot_parent=root, template_label="mac")
    final = json.loads((template / "config.json").read_text())
    assert [s["camera_mac"] for s in final["paired_streams"]] == [CAM["Flur"], CAM["Schlafzimmer"]]
    assert (final["mac"], final["device_ip"]) == identity                 # identity untouched
    assert (template / "config.json.before-rollout").read_text() == before


def test_rollback_restores_allowlists_and_removes_only_pending_slots(tmp_path):
    from aikey.aiport_rollout import rollback
    root, compose_path, rollout_path, rollout, slots, inventory = _one_new_nas_slot(tmp_path)
    template = Path(rollout["slots"][0]["state_dir"])
    original = (template / "config.json").read_bytes()
    # an allowlist edit on the existing slot, then a pending new NAS slot
    edit = plan_rollout(report(extra=("Keller",), drop=("Esszimmer", "Haustuer")),
                        {"mac": slot("mac", "192.168.10.20", ["Flur", "Schlafzimmer"],
                                     {"Flur", "Schlafzimmer"})})
    apply_rollout(edit, {"mac": template}, new_slot_parent=root, template_label="mac")
    assert (template / "config.json").read_bytes() != original
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                        compose=_compose_settings())
    apply_and_register(rollout_path, rollout, plan)
    assert compose_path.read_text() != COMPOSE
    result = rollback(rollout_path, rollout)
    assert result == {"mac": "allowlist_restored", "nas-slot-1": "pending_slot_removed"}
    assert (template / "config.json").read_bytes() == original           # exact bytes
    assert compose_path.read_text() == COMPOSE                           # exact inverse
    assert [s["label"] for s in json.loads(rollout_path.read_text())["slots"]] == ["mac"]
    assert (root / "nas-slot-1" / "device.key").exists()                 # identity kept for retry
    assert rollback(rollout_path, rollout) == {}                         # idempotent


def test_a_healthy_registered_slot_is_never_rolled_back(tmp_path):
    from aikey.aiport_rollout import rollback, verify_new_slots
    root, compose_path, rollout_path, rollout, slots, inventory = _one_new_nas_slot(tmp_path)
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                        compose=_compose_settings())
    apply_and_register(rollout_path, rollout, plan)
    verify_new_slots(rollout_path, rollout, health=lambda *_a: {"adopted": True})
    after = compose_path.read_text()
    assert rollback(rollout_path, rollout) == {}
    assert compose_path.read_text() == after
    assert [s["label"] for s in rollout["slots"]] == ["mac", "nas-slot-1"]


def test_apply_refuses_a_stale_inventory():
    from aikey.aiport_rollout import MAX_INVENTORY_AGE_S, check_inventory_fresh
    check_inventory_fresh({"fetched_at": 1_000_000}, now=1_000_000 + MAX_INVENTORY_AGE_S)
    for report_value in ({"fetched_at": 1_000_000}, {}, {"fetched_at": "x"}):
        with pytest.raises(RolloutError):
            check_inventory_fresh(report_value, now=1_000_000 + MAX_INVENTORY_AGE_S + 1)


def test_config_drift_names_only_the_differing_keys():
    from aikey.aiport_rollout import config_drift
    base = {"mac": "X", "live_pool_detector": {"threshold": 0.3, "smart_types": ["person"]}}
    assert config_drift(base, json.loads(json.dumps(base))) == []
    deployed = json.loads(json.dumps(base))
    deployed["live_pool_detector"]["plate_cameras"] = ["2A1122334455"]
    assert config_drift(base, deployed) == ["live_pool_detector.plate_cameras"]
    deployed["live_pool_detector"]["threshold"] = 0.4
    assert config_drift(base, deployed) == ["live_pool_detector.plate_cameras",
                                            "live_pool_detector.threshold"]
    assert config_drift({"a": None}, {}) == ["a"]            # a missing key is not a null value


def test_a_drifted_slot_is_never_edited_or_uploaded_automatically():
    slots = current()
    slots["mac"]["config_drift"] = ["live_pool_detector.plate_cameras"]
    plan = plan_rollout(report(extra=("Keller",)), slots)
    drift = [a for a in plan["actions"] if a["kind"] == "slot_config_drift"]
    assert drift == [{"kind": "slot_config_drift", "slot": "mac",
                      "keys": ["live_pool_detector.plate_cameras"], "automated": False}]
    add = next(a for a in plan["actions"] if a["kind"] == "add_to_allowlist")
    assert add["slot"] == "mac" and add["automated"] is False
    assert not local_changes(plan)
    # Without drift the same camera is added locally as before.
    clean = plan_rollout(report(extra=("Keller",)), current())
    assert next(a for a in clean["actions"] if a["kind"] == "add_to_allowlist")["automated"] is True


def test_a_drifted_slot_without_edits_only_reports_the_drift():
    slots = current()
    slots["nas-slot-2"]["config_drift"] = ["live_pool_detector.plate_cameras"]
    plan = plan_rollout(report(), slots)
    assert kinds(plan) == [("slot_config_drift", "nas-slot-2", None)]
    assert not local_changes(plan)
    assert "plate_cameras" in json.dumps(public(plan)) and "2A11" not in json.dumps(public(plan))


def test_a_drifted_slot_keeps_stale_removals_manual():
    slots = current()
    slots["mac"] = slot("mac", "192.168.0.135", ["Flur", "Schlafzimmer", "Dach"],
                        {"Flur", "Schlafzimmer"})
    slots["mac"]["config_drift"] = ["live_pool_detector.plate_cameras"]
    plan = plan_rollout(report(), slots)
    removal = [a for a in plan["actions"] if a["kind"] == "remove_from_allowlist"]
    assert removal and all(a["automated"] is False for a in removal)


def test_rollout_config_accepts_only_a_string_deployed_config(tmp_path):
    from aikey.aiport_rollout import RolloutError, load_rollout_config
    base = {"schema": "aikey-aiport-rollout/1", "new_slots": {"target": "nas"},
            "slots": [{"label": "nas-slot-3", "target": "nas", "health_port": 443,
                       "state_dir": str(tmp_path), "deployed_config": str(tmp_path / "c.json")}]}
    path = tmp_path / "rollout.json"
    path.write_text(json.dumps(base))
    assert load_rollout_config(path)["slots"][0]["deployed_config"].endswith("c.json")
    base["slots"][0]["deployed_config"] = 5
    path.write_text(json.dumps(base))
    with pytest.raises(RolloutError):
        load_rollout_config(path)


def _nas_template(slots, *, known=True, drift=()):
    """Make the plan's template a NAS slot with a known (or unknown) deployed copy."""
    slots = dict(slots)
    template = dict(slots["mac"], target="nas")
    if known:
        template["deployed_config_known"] = True
    if drift:
        template["config_drift"] = list(drift)
    slots["nas-template"] = template
    del slots["mac"]
    return slots


def test_the_live_nine_camera_plan_stays_a_no_op_with_a_verified_nas_template():
    inventory, slots = live_like()
    for label in slots:
        slots[label]["deployed_config_known"] = slots[label]["target"] == "nas"
    template = next(label for label in slots if slots[label]["target"] == "nas")
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.0.139"],
                        compose=_compose_settings(), template_label=template)
    assert plan["actions"] == [] and not local_changes(plan)


def test_a_new_slot_is_automated_only_from_a_verified_template(tmp_path):
    root, compose_path, rollout_path, rollout, slots, inventory = _one_new_nas_slot(tmp_path)
    verified = plan_rollout(inventory, _nas_template(slots), new_slot_addresses=["192.168.10.30"],
                            compose=_compose_settings(), template_label="nas-template")
    create = next(a for a in verified["actions"] if a["kind"] == "create_slot")
    assert create["automated"] is True
    for kwargs, reason in (({"drift": ["live_pool_detector.threshold"]}, "template_config_drift"),
                           ({"known": False}, "template_deployed_config_unset")):
        plan = plan_rollout(inventory, _nas_template(slots, **kwargs),
                            new_slot_addresses=["192.168.10.30"], compose=_compose_settings(),
                            template_label="nas-template")
        problem = [a for a in plan["actions"] if a["kind"] == "template_config_unverified"]
        assert problem and problem[0]["reason"] == reason
        assert all(not a["automated"] for a in plan["actions"]
                   if a["kind"] in {"create_slot", "compose_add_service", "add_to_allowlist"})
        assert not local_changes(plan)
        assert apply_rollout(plan, {"nas-template": Path(rollout["slots"][0]["state_dir"])},
                             new_slot_parent=root, template_label="nas-template",
                             compose_path=compose_path) == []
        assert compose_path.read_text() == COMPOSE and not (root / "nas-slot-1").exists()


def test_a_cloned_detector_keeps_only_the_new_slots_plate_cameras():
    from aikey.aiport_rollout import _cloned_detector
    detector = {"threshold": 0.8, "plate_cameras": [CAM["Flur"], CAM["Keller"]]}
    assert _cloned_detector(detector, [CAM["Keller"]]) == {"threshold": 0.8,
                                                            "plate_cameras": [CAM["Keller"]]}
    assert _cloned_detector(detector, [CAM["Garten"]]) == {"threshold": 0.8}
    assert detector["plate_cameras"] == [CAM["Flur"], CAM["Keller"]]      # template unchanged


def test_a_failed_step_undoes_this_runs_allowlist_edit_and_registers_nothing(tmp_path, monkeypatch):
    root, compose_path, rollout_path, rollout, slots, inventory = _one_new_nas_slot(tmp_path)
    template = Path(rollout["slots"][0]["state_dir"])
    config = json.loads((template / "config.json").read_text())
    config["paired_streams"].append({"camera_mac": CAM["Dach"], "source_ip": "192.168.10.1",
                                     "ffmpeg_path": "/usr/bin/ffmpeg"})     # a stale entry
    (template / "config.json").write_text(json.dumps(config))
    slots["mac"] = slot("mac", "192.168.10.20", ["Flur", "Schlafzimmer", "Dach"],
                        {"Flur", "Schlafzimmer"})
    rollout_path.write_text(json.dumps(rollout))
    plan = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                        compose=_compose_settings())
    assert {a["kind"] for a in plan["actions"] if a["automated"]} >= {
        "remove_from_allowlist", "create_slot", "compose_add_service"}
    before = (template / "config.json").read_bytes()
    registered = rollout_path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("disk full while writing the Compose file")
    monkeypatch.setattr("aikey.aiport_rollout.add_slot_service", fail)
    with pytest.raises(OSError):
        apply_and_register(rollout_path, rollout, plan)
    assert (template / "config.json").read_bytes() == before              # allowlist edit undone
    assert not (template / "config.json.before-rollout").exists()
    assert compose_path.read_text() == COMPOSE
    assert rollout_path.read_bytes() == registered and len(rollout["slots"]) == 1
    # The provisioned identity stays unregistered; a reviewed retry reuses it unchanged.
    identity = (root / "nas-slot-1" / "device.key").read_bytes()
    monkeypatch.undo()
    apply_and_register(rollout_path, rollout, plan)
    assert (root / "nas-slot-1" / "device.key").read_bytes() == identity
    assert [s["label"] for s in rollout["slots"]] == ["mac", "nas-slot-1"]
    assert CAM["Dach"] not in (template / "config.json").read_text()


def test_applying_fresh_plans_twice_converges(tmp_path):
    root, compose_path, rollout_path, rollout, slots, inventory = _one_new_nas_slot(tmp_path)
    first = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                         compose=_compose_settings())
    apply_and_register(rollout_path, rollout, first)
    new_config = json.loads((root / "nas-slot-1" / "config.json").read_text())
    slots["nas-slot-1"] = {"target": "nas", "host_ip": "192.168.10.30", "mac": new_config["mac"],
                           **observe_slot(new_config, None)}
    for _ in range(2):
        again = plan_rollout(inventory, slots, new_slot_addresses=["192.168.10.30"],
                             compose=_compose_settings(compose_path.read_text()))
        assert not local_changes(again) and again["new_slots"] == []
        assert apply_and_register(rollout_path, rollout, again) == []
    assert len(rollout["slots"]) == 2
