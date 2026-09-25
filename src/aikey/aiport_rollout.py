"""Idempotent dry-run/apply of AI Port slots from Protect's camera inventory.

Inputs are live and need no Protect administrator session:

* the camera preflight report (integration API): which cameras are eligible
  (connected legacy or G3-G5 Protect cameras) and their capacity weight;
* each deployed slot's pinned ``/healthz``: Protect only sends a camera's
  smart policy and stream to the AI Port it paired the camera with, so a
  camera with a policy or stream on a slot is paired to that slot.

The rollout never moves a paired camera, never renames or re-addresses a slot
and never pairs, unpairs, adopts or restarts anything. It only changes local
files: allowlist entries (``paired_streams``) of existing slots, and the
identity of a new slot through :func:`provision_slot`. Everything that needs
Protect or the NAS is returned as a user action. A second run after applying
reports no local change.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from fractions import Fraction
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl

from .aiport_deployment import (
    _CAPACITY, _PROTECT_MODEL_MAX_PIXELS, AiPortPlanError, stream_capacity_points,
)
from .aiport_ingest import IngressError, normalize_mac
from .aiport_instance_state import InstanceStateError, provision_slot
from .config import atomic_private


SCHEMA = "aikey-aiport-rollout/1"
_G3_G5_MODEL = re.compile(r"UVC G[345](?:\s|\Z)")
_LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
_MAX_CAMERAS_PER_SLOT = 5   # AiPortIngressPool accepts at most five streams
_TARGETS = frozenset({"mac", "nas"})


class RolloutError(ValueError):
    """Fixed error text without camera or network identities."""


def _mac(value: object) -> str:
    try:
        return normalize_mac(value)
    except (IngressError, TypeError) as exc:
        raise RolloutError("invalid device identity") from exc


def _weight(row: dict) -> Fraction:
    maximum = _PROTECT_MODEL_MAX_PIXELS.get(row.get("model"))
    if maximum is not None:
        return Fraction(stream_capacity_points(*maximum), 10)
    if row.get("processing_class") == "legacy_ingress_needed":
        return _CAPACITY["protect"][None]
    return Fraction(1, 2)


def eligible_cameras(report: dict) -> dict[str, dict]:
    """Protect cameras an AI Port may serve, keyed by MAC (offline included)."""
    if (not isinstance(report, dict) or report.get("schema") != "aikey-camera-preflight/1"
            or not isinstance(report.get("cameras"), list) or len(report["cameras"]) > 256):
        raise RolloutError("A camera preflight v1 report is required")
    result = {}
    for row in report["cameras"]:
        if not isinstance(row, dict) or row.get("source_kind") not in (None, "protect"):
            continue
        model = row.get("model")
        legacy = row.get("processing_class") == "legacy_ingress_needed"
        g3_g5 = isinstance(model, str) and _G3_G5_MODEL.match(model) is not None
        if not (legacy or g3_g5):
            continue
        result[_mac(row.get("mac"))] = {
            "name": row["name"] if isinstance(row.get("name"), str) else "unnamed",
            "online": row.get("state") == "CONNECTED", "weight": _weight(row)}
    return result


def read_slot_health(state_dir: Path, port: int, *, timeout: float = 5.0) -> dict | None:
    """Fetch one slot's /healthz, pinned to its own device certificate."""
    try:
        config = json.loads((state_dir / "config.json").read_text())
        expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(
            (state_dir / "device.crt").read_text())).hexdigest()
        host = config["device_ip"]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE   # the pin below is the trust check
        with context.wrap_socket(socket.create_connection((host, port), timeout=timeout),
                                 server_hostname=host) as conn:
            if hashlib.sha256(conn.getpeercert(binary_form=True)).hexdigest() != expected:
                return None
            conn.settimeout(timeout)
            conn.sendall(f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                         "Connection: close\r\n\r\n".encode())
            raw = b""
            while len(raw) < 4 * 1024 * 1024 and (chunk := conn.recv(262144)):
                raw += chunk
        return json.loads(raw.split(b"\r\n\r\n", 1)[1])
    except (OSError, ValueError, KeyError, IndexError, ssl.SSLError):
        return None


def observe_slot(config: dict, health: dict | None) -> dict:
    """Allowlist of one slot and, if reachable, what Protect paired to it."""
    if not isinstance(config, dict) or not isinstance(config.get("paired_streams"), list):
        raise RolloutError("Slot config needs paired_streams")
    allowlist = [_mac(stream.get("camera_mac")) for stream in config["paired_streams"]]
    paired = None
    if isinstance(health, dict) and isinstance(health.get("pool_cameras"), list):
        rows = health["pool_cameras"]
        if len(rows) == len(allowlist):
            paired = {mac for mac, row in zip(allowlist, rows)
                      if isinstance(row, dict)
                      and (row.get("policy_enabled") is True or row.get("stream_active") is True)}
    return {"allowlist": allowlist, "paired": paired}


def plan_rollout(report: dict, slots: dict[str, dict], *,
                 new_slot_addresses: list[str] = (), new_slot_target: str = "nas",
                 new_slot_prefix: str = "nas-slot-") -> dict:
    """Diff slots against eligible cameras; ``slots[label]`` holds
    ``target``, ``host_ip`` and the :func:`observe_slot` result."""
    if new_slot_target not in _TARGETS or not _LABEL.fullmatch(new_slot_prefix + "1"):
        raise RolloutError("Invalid new-slot policy")
    eligible = eligible_cameras(report)
    for label, slot in slots.items():
        if not _LABEL.fullmatch(label) or slot.get("target") not in _TARGETS:
            raise RolloutError("Invalid slot label or target")
    paired_where = {}
    for label, slot in sorted(slots.items()):
        for mac in slot["paired"] or ():
            paired_where.setdefault(mac, label)
    def name(mac: str) -> str:
        return eligible[mac]["name"] if mac in eligible else "unknown camera"

    def weight(mac: str) -> Fraction:
        return eligible[mac]["weight"] if mac in eligible else Fraction(1, 2)

    result: dict[str, dict] = {}
    actions: list[dict] = []
    assigned: set[str] = set()
    for label, slot in sorted(slots.items()):
        keep, remove, awaiting, unknown = [], [], [], []
        for mac in slot["allowlist"]:
            owner = paired_where.get(mac)
            if slot["paired"] is None:
                unknown.append(mac)          # unobserved slot: change nothing
            elif owner == label:
                keep.append(mac)
            elif owner is not None:
                remove.append(mac)           # Protect paired it to another slot
            elif mac in eligible and eligible[mac]["online"]:
                awaiting.append(mac)         # reserved here, not yet paired
            elif mac in eligible:
                keep.append(mac)             # offline: keep, never churn
            else:
                remove.append(mac)           # gone from Protect or out of scope
        members = keep + awaiting + unknown
        assigned.update(members)
        result[label] = {"target": slot["target"], "host_ip": slot.get("host_ip"),
                         "keep": keep + unknown, "add": [], "remove": remove,
                         "awaiting_pairing": awaiting, "observed": slot["paired"] is not None,
                         "load": sum((weight(mac) for mac in members), Fraction())}
        for mac in remove:
            actions.append({"kind": "remove_from_allowlist", "slot": label,
                            "camera": name(mac), "_mac": mac, "automated": True})
        for mac in awaiting:
            actions.append({"kind": "pair_in_protect", "slot": label,
                            "camera": name(mac), "automated": False})
        if slot["paired"] is None:
            actions.append({"kind": "slot_unobserved", "slot": label, "automated": False})
        if result[label]["load"] > 1:
            actions.append({"kind": "capacity_estimate_exceeded", "slot": label,
                            "automated": False})

    used_addresses = {slot.get("host_ip") for slot in slots.values()}
    pool = [address for address in new_slot_addresses if address not in used_addresses]
    numbers = [int(label[len(new_slot_prefix):]) for label in slots
               if label.startswith(new_slot_prefix) and label[len(new_slot_prefix):].isdigit()]
    next_number = max(numbers, default=0) + 1
    new_slots: list[dict] = []
    pending = sorted((mac for mac, camera in eligible.items()
                      if camera["online"] and mac not in assigned and mac not in paired_where),
                     key=lambda mac: (-weight(mac), name(mac)))
    for mac in pending:
        target = next((label for label in sorted(result)
                       if result[label]["observed"]
                       and result[label]["load"] + weight(mac) <= 1
                       and len(result[label]["keep"]) + len(result[label]["awaiting_pairing"])
                       + len(result[label]["add"]) < _MAX_CAMERAS_PER_SLOT), None)
        if target is None:
            slot = next((item for item in new_slots
                         if item["load"] + weight(mac) <= 1
                         and len(item["add"]) < _MAX_CAMERAS_PER_SLOT), None)
            if slot is None:
                if not pool:
                    actions.append({"kind": "address_needed", "camera": name(mac),
                                    "automated": False})
                    continue
                slot = {"label": f"{new_slot_prefix}{next_number}", "target": new_slot_target,
                        "host_ip": pool.pop(0), "add": [], "load": Fraction()}
                next_number += 1
                new_slots.append(slot)
            slot["add"].append(mac)
            slot["load"] += weight(mac)
            continue
        result[target]["add"].append(mac)
        result[target]["load"] += weight(mac)
    for label, slot in sorted(result.items()):
        for mac in slot["add"]:
            actions.append({"kind": "add_to_allowlist", "slot": label, "camera": name(mac),
                            "_mac": mac, "automated": True})
            actions.append({"kind": "pair_in_protect", "slot": label, "camera": name(mac),
                            "automated": False})
    for slot in new_slots:
        actions.append({"kind": "create_slot", "slot": slot["label"],
                        "target": slot["target"], "automated": True})
        for mac in slot["add"]:
            actions.append({"kind": "add_to_allowlist", "slot": slot["label"],
                            "camera": name(mac), "_mac": mac, "automated": True})
        actions.append({"kind": ("deploy_nas_service" if slot["target"] == "nas"
                                 else "start_mac_container"),
                        "slot": slot["label"], "automated": False})
        actions.append({"kind": "adopt_in_protect", "slot": slot["label"], "automated": False})
        for mac in slot["add"]:
            actions.append({"kind": "pair_in_protect", "slot": slot["label"],
                            "camera": name(mac), "automated": False})
    for label, slot in result.items():
        if slot["observed"] and not (slot["keep"] or slot["awaiting_pairing"] or slot["add"]):
            actions.append({"kind": "idle_slot", "slot": label, "automated": False})
    known = {mac for slot in slots.values() for mac in slot["allowlist"]} | set(eligible)
    plan = {"schema": SCHEMA, "slots": result, "new_slots": new_slots, "actions": actions,
            "_names": {mac: name(mac) for mac in known}}
    plan["revision"] = hashlib.sha256(json.dumps(
        public(plan), sort_keys=True).encode()).hexdigest()
    return plan


def public(plan: dict) -> dict:
    """Plan text for pages and logs: camera names only, no MAC or IP."""
    def slot_view(slot):
        return {"target": slot["target"], "load": str(slot["load"]),
                **{key: list(slot[key]) for key in ("keep", "add", "remove", "awaiting_pairing")
                   if key in slot},
                **({"observed": slot["observed"]} if "observed" in slot else {})}
    names = plan.get("_names", {})

    def rename(slot: dict) -> dict:
        return {key: (sorted(names.get(mac, "unknown camera") for mac in value)
                      if isinstance(value, list) else value)
                for key, value in slot_view(slot).items()}
    return {"schema": plan["schema"],
            "slots": {label: rename(slot) for label, slot in plan["slots"].items()},
            "new_slots": [{"label": slot["label"], **rename(slot)} for slot in plan["new_slots"]],
            "actions": [{k: v for k, v in action.items() if not k.startswith("_")}
                        for action in plan["actions"]],
            **({"revision": plan["revision"]} if "revision" in plan else {})}


def local_changes(plan: dict) -> bool:
    return any(action["automated"] for action in plan["actions"])


def apply_rollout(plan: dict, slot_dirs: dict[str, Path], *, new_slot_parent: Path,
                  template_label: str) -> list[str]:
    """Apply only the automated, local actions of a freshly computed plan."""
    changed = []
    template = json.loads((slot_dirs[template_label] / "config.json").read_text())
    for label, slot in sorted(plan["slots"].items()):
        removes = {a["_mac"] for a in plan["actions"]
                   if a["kind"] == "remove_from_allowlist" and a["slot"] == label}
        if not (removes or slot["add"]):
            continue
        path = slot_dirs[label] / "config.json"
        config = json.loads(path.read_text())
        streams = [s for s in config["paired_streams"] if _mac(s["camera_mac"]) not in removes]
        present = {_mac(s["camera_mac"]) for s in streams}
        for mac in slot["add"]:
            if mac not in present:
                streams.append({"camera_mac": mac, "source_ip": config["controller_ip"],
                                "ffmpeg_path": "/usr/bin/ffmpeg"})
        if not 1 <= len(streams) <= _MAX_CAMERAS_PER_SLOT:
            raise RolloutError("A slot must keep one to five allowlisted cameras")
        backup = path.with_name("config.json.before-rollout")
        if not backup.exists():
            atomic_private(backup, path.read_bytes())
        config["paired_streams"] = streams
        atomic_private(path, json.dumps(config, separators=(",", ":")).encode())
        changed.append(label)
    for slot in plan["new_slots"]:
        state_dir = Path(new_slot_parent) / slot["label"]
        address = str(ipaddress.IPv4Address(slot["host_ip"]))
        one_slot_plan = {"schema": "aikey-aiport-deployment-plan/2", "ai_key": {},
                         "instances": [{"slot": 1, "source_kind": "protect",
                                        "camera_ids": ["0" * 24], "host_ip": address}]}
        try:
            provision_slot(one_slot_plan, 1, state_dir,
                           controller_ip=template["controller_ip"],
                           controller_cert_file=slot_dirs[template_label] / "controller-ca.pem",
                           controller_pin=template["controller_pin"],
                           firmware_version=template["firmware_version"])
        except (InstanceStateError, AiPortPlanError) as exc:
            raise RolloutError("New slot identity could not be provisioned") from exc
        path = state_dir / "config.json"
        config = json.loads(path.read_text())
        if "paired_streams" not in config:
            config["paired_streams"] = [
                {"camera_mac": mac, "source_ip": config["controller_ip"],
                 "ffmpeg_path": "/usr/bin/ffmpeg"} for mac in slot["add"]]
            config["live_pool_detector"] = deepcopy(template["live_pool_detector"])
            atomic_private(path, json.dumps(config, separators=(",", ":")).encode())
            changed.append(slot["label"])
    return changed


def apply_and_register(rollout_path: Path, rollout: dict, plan: dict) -> list[str]:
    """Apply a plan's local actions and record new slots for the next run."""
    new = rollout["new_slots"]
    dirs = {slot["label"]: Path(slot["state_dir"]) for slot in rollout["slots"]}
    changed = apply_rollout(plan, dirs, new_slot_parent=Path(new["state_parent"]),
                            template_label=new.get("template", rollout["slots"][0]["label"]))
    if plan["new_slots"]:
        updated = deepcopy(rollout)
        for slot in plan["new_slots"]:
            updated["slots"].append({
                "label": slot["label"], "target": slot["target"],
                "state_dir": str(Path(new["state_parent"]) / slot["label"]),
                "health_port": int(new.get("health_port", 443))})
        atomic_private(Path(rollout_path), (json.dumps(updated, indent=2) + "\n").encode())
        rollout.clear()
        rollout.update(updated)
    return changed


def load_rollout_config(path: Path) -> dict:
    value = json.loads(Path(path).read_text())
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or not isinstance(value.get("slots"), list) or not value["slots"]
            or not isinstance(value.get("new_slots"), dict)):
        raise RolloutError("Invalid rollout configuration")
    for slot in value["slots"]:
        if (not isinstance(slot, dict) or not _LABEL.fullmatch(str(slot.get("label")))
                or slot.get("target") not in _TARGETS
                or type(slot.get("health_port")) is not int
                or not isinstance(slot.get("state_dir"), str)):
            raise RolloutError("Invalid rollout slot")
    return value


def compute(rollout: dict, report: dict) -> tuple[dict, dict[str, Path]]:
    """Observe every configured slot and plan against the inventory."""
    slots, dirs = {}, {}
    for slot in rollout["slots"]:
        state_dir = Path(slot["state_dir"])
        config = json.loads((state_dir / "config.json").read_text())
        observed = observe_slot(config, read_slot_health(state_dir, slot["health_port"]))
        slots[slot["label"]] = {"target": slot["target"], "host_ip": config.get("device_ip"),
                                **observed}
        dirs[slot["label"]] = state_dir
    new = rollout["new_slots"]
    plan = plan_rollout(report, slots, new_slot_addresses=list(new.get("addresses", [])),
                        new_slot_target=new.get("target", "nas"),
                        new_slot_prefix=new.get("label_prefix", "nas-slot-"))
    return plan, dirs


def main(argv: list[str] | None = None) -> int:
    from .camera_inventory import InventoryError, fetch_inventory
    parser = argparse.ArgumentParser(description=(
        "Plan AI Port slots from Protect's eligible cameras. Dry run by default; "
        "--apply changes only local slot files. Never pairs, adopts or restarts."))
    parser.add_argument("--rollout", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--inventory", type=Path)
    source.add_argument("--controller")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--web-trust-file", type=Path)
    parser.add_argument("--web-cert-file", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--revision", help="Apply only if the plan still has this revision")
    args = parser.parse_args(argv)
    try:
        rollout = load_rollout_config(args.rollout)
        report = (asyncio.run(fetch_inventory(args.controller, api_key_file=args.api_key_file,
                                              trust_file=args.web_trust_file,
                                              cert_file=args.web_cert_file))
                  if args.controller else json.loads(args.inventory.read_text()))
        plan, dirs = compute(rollout, report)
        if args.apply:
            if args.revision is None or args.revision != plan["revision"]:
                raise RolloutError("The plan changed; review the dry run and pass its revision")
            apply_and_register(args.rollout, rollout, plan)
            plan, dirs = compute(rollout, report)
    except (OSError, json.JSONDecodeError, RolloutError, InventoryError) as exc:
        parser.error(str(exc))
    print(json.dumps(public(plan), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
