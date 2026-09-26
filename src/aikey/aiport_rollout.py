"""Idempotent dry-run/apply of AI Port slots from Protect's camera inventory.

Inputs are live and need no Protect administrator session:

* the camera preflight report (integration API): which cameras are eligible
  (connected legacy or G3-G5 Protect cameras) and their capacity weight;
* each deployed slot's pinned ``/healthz``: Protect only sends a camera's
  smart policy and stream to the AI Port it paired the camera with, so a
  camera with a policy or stream on a slot is paired to that slot.

The rollout never moves a paired camera, never renames or re-addresses a slot
and never pairs, unpairs, adopts or restarts anything. It only changes local
files: allowlist entries (``paired_streams``) of existing slots, the
identity of a new slot through :func:`provision_slot`, and, when a private
copy of the NAS Compose project is configured, an append-only service for a
new NAS slot (:mod:`aiport_compose_slots`). The exact Compose block is part of
the reviewed plan and its revision. A new slot stays *pending* until its
pinned health answers; if it does not by the deadline, :func:`verify_new_slots`
removes exactly that service again and unregisters the slot. Uploading state,
redeploying the NAS project, adopting and pairing stay user actions. A second
run after applying reports no local change.
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
import time

from .aiport_deployment import (
    _CAPACITY, _PROTECT_MODEL_MAX_PIXELS, AiPortPlanError, stream_capacity_points,
)
from .aiport_compose_slots import (
    ComposeSlotError, add_slot_service, colon_mac, remove_slot_service, slot_service,
)
from .aiport_ingest import IngressError, normalize_mac
from .aiport_instance_state import InstanceStateError, provision_slot
from .config import atomic_private


SCHEMA = "aikey-aiport-rollout/1"
_G3_G5_MODEL = re.compile(r"UVC G[345](?:\s|\Z)")
_LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
_MAX_CAMERAS_PER_SLOT = 5   # AiPortIngressPool accepts at most five streams
_TARGETS = frozenset({"mac", "nas"})
_MIN_WEIGHT = Fraction(1, 5)   # the smallest ingress reservation (<=1080p)


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
            "online": row.get("state") == "CONNECTED", "weight": _weight(row),
            # "model": the model's evidenced main-lens stream; "fallback": a guess.
            "basis": "model" if _PROTECT_MODEL_MAX_PIXELS.get(model) else "fallback"}
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
    paired, points = None, {}
    if isinstance(health, dict) and isinstance(health.get("pool_cameras"), list):
        rows = health["pool_cameras"]
        if len(rows) == len(allowlist):
            paired = {mac for mac, row in zip(allowlist, rows)
                      if isinstance(row, dict)
                      and (row.get("policy_enabled") is True or row.get("stream_active") is True)}
            # The stream Protect actually requested beats any model estimate.
            points = {mac: row["stream_points"] for mac, row in zip(allowlist, rows)
                      if isinstance(row, dict) and row.get("stream_points") in (2, 3, 5)}
    return {"allowlist": allowlist, "paired": paired, "points": points}


def _slot_mac(label: str, host_ip: str, used: set[str]) -> str:
    """A reviewable, stable, locally administered MAC for a planned slot."""
    for attempt in range(64):
        digest = hashlib.sha256(f"aikey-aiport-slot:{label}:{host_ip}:{attempt}".encode())
        mac = ("02" + digest.hexdigest()[:10]).upper()
        if mac not in used:
            return mac
    raise RolloutError("No free AI Port MAC")


def plan_rollout(report: dict, slots: dict[str, dict], *,
                 new_slot_addresses: list[str] = (), new_slot_target: str = "nas",
                 new_slot_prefix: str = "nas-slot-", compose: dict | None = None,
                 allow_estimated_capacity: bool = False) -> dict:
    """Diff slots against eligible cameras; ``slots[label]`` holds
    ``target``, ``host_ip``, optionally ``mac`` and the :func:`observe_slot`
    result. ``compose`` (text, template, state_parent, service_prefix) enables
    an append-only NAS service for each new NAS slot."""
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

    observed_points = {mac: value for slot in slots.values()
                       for mac, value in slot.get("points", {}).items()}

    def basis(mac: str) -> str:
        if mac in observed_points:
            return "observed"
        return eligible[mac]["basis"] if mac in eligible else "fallback"

    def weight(mac: str) -> Fraction:
        # The ingress enforces ten points per AI Port on the stream Protect
        # sends: observed points first, then the model's main-lens estimate.
        if mac in observed_points:
            return Fraction(observed_points[mac], 10)
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
                label = f"{new_slot_prefix}{next_number}"
                address = pool.pop(0)
                used_macs = {str(item.get("mac", "")).upper() for item in slots.values()}
                used_macs |= {item["mac"] for item in new_slots}
                slot = {"label": label, "number": next_number, "target": new_slot_target,
                        "host_ip": address, "mac": _slot_mac(label, address, used_macs),
                        "add": [], "load": Fraction()}
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
        # A new slot is justified only if its cameras do not fit an existing
        # slot at the smallest reservation, or their weight is evidenced.
        unverified = [mac for mac in slot["add"] if basis(mac) == "fallback" and any(
            result[label]["observed"] and result[label]["load"] + _MIN_WEIGHT <= 1
            and len(result[label]["keep"]) + len(result[label]["awaiting_pairing"])
            + len(result[label]["add"]) < _MAX_CAMERAS_PER_SLOT for label in result)]
        slot["capacity_basis"] = "unverified" if unverified else "evidenced"
        automated = not unverified or allow_estimated_capacity
        for mac in unverified:
            actions.append({"kind": "capacity_unverified", "slot": slot["label"],
                            "camera": name(mac), "automated": False})
        actions.append({"kind": "create_slot", "slot": slot["label"],
                        "target": slot["target"], "automated": automated})
        for mac in slot["add"]:
            actions.append({"kind": "add_to_allowlist", "slot": slot["label"],
                            "camera": name(mac), "_mac": mac, "automated": automated})
        if slot["target"] == "nas" and compose is not None:
            service = f"{compose['service_prefix']}{slot['number']}"
            source = f"{compose['state_parent']}/slot-{slot['number']}"
            try:
                block = slot_service(compose["text"], template=compose["template"],
                                     service=service, source=source,
                                     ipv4=slot["host_ip"], mac=slot["mac"])
            except (ComposeSlotError, ValueError) as exc:
                raise RolloutError("The NAS Compose project cannot take a new slot") from exc
            slot["compose"] = {"service": service, "source": source, "block": block,
                               "template": compose["template"]}
            actions.append({"kind": "compose_add_service", "slot": slot["label"],
                            "service": service, "ipv4_address": slot["host_ip"],
                            "mac_address": colon_mac(slot["mac"]), "bind_source": source,
                            "block": block, "automated": automated})
            actions.append({"kind": "upload_slot_state", "slot": slot["label"],
                            "target": source, "automated": False})
            actions.append({"kind": "redeploy_nas_project", "slot": slot["label"],
                            "automated": False})
            actions.append({"kind": "verify_slot_health", "slot": slot["label"],
                            "automated": False})
        else:
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
            "_names": {mac: name(mac) for mac in known},
            "compose_sha256": (hashlib.sha256(compose["text"].encode()).hexdigest()
                               if compose is not None else None)}
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
            "new_slots": [{"label": slot["label"], **rename(slot),
                           **{k: slot[k] for k in ("capacity_basis",) if k in slot}}
                          for slot in plan["new_slots"]],
            **({"compose_sha256": plan["compose_sha256"]} if plan.get("compose_sha256") else {}),
            "actions": [{k: v for k, v in action.items() if not k.startswith("_")}
                        for action in plan["actions"]],
            **({"revision": plan["revision"]} if "revision" in plan else {})}


def local_changes(plan: dict) -> bool:
    return any(action["automated"] for action in plan["actions"])


def _automated(plan: dict, kind: str, label: str) -> bool:
    return any(a["kind"] == kind and a["slot"] == label and a["automated"]
               for a in plan["actions"])


def apply_rollout(plan: dict, slot_dirs: dict[str, Path], *, new_slot_parent: Path,
                  template_label: str, compose_path: Path | None = None) -> list[str]:
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
        if not _automated(plan, "create_slot", slot["label"]):
            continue                      # capacity unverified: review first
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
                           firmware_version=template["firmware_version"],
                           mac=slot.get("mac"))
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
        if slot.get("compose") and _automated(plan, "compose_add_service", slot["label"]):
            if compose_path is None:
                raise RolloutError("The plan adds a Compose service but no Compose file is set")
            text = Path(compose_path).read_text()
            if hashlib.sha256(text.encode()).hexdigest() != plan["compose_sha256"]:
                raise RolloutError("The Compose file changed since the plan was made")
            try:
                updated = add_slot_service(
                    text, template=slot["compose"]["template"],
                    service=slot["compose"]["service"], source=slot["compose"]["source"],
                    ipv4=slot["host_ip"], mac=slot["mac"])
            except ComposeSlotError as exc:
                raise RolloutError("The NAS Compose project cannot take a new slot") from exc
            if slot["compose"]["block"] not in updated:
                raise RolloutError("The Compose change differs from the reviewed plan")
            if updated != text:
                backup = Path(compose_path).with_name(
                    Path(compose_path).name + f".before-{slot['label']}")
                if not backup.exists():
                    atomic_private(backup, text.encode())
                atomic_private(Path(compose_path), updated.encode())
                plan["compose_sha256"] = hashlib.sha256(updated.encode()).hexdigest()
                changed.append(slot["compose"]["service"])
    return changed


def apply_and_register(rollout_path: Path, rollout: dict, plan: dict) -> list[str]:
    """Apply a plan's local actions and record new slots for the next run."""
    new = rollout["new_slots"]
    dirs = {slot["label"]: Path(slot["state_dir"]) for slot in rollout["slots"]}
    compose = rollout.get("compose")
    changed = apply_rollout(plan, dirs, new_slot_parent=Path(new["state_parent"]),
                            template_label=new.get("template", rollout["slots"][0]["label"]),
                            compose_path=Path(compose["path"]) if compose else None)
    applied = [slot for slot in plan["new_slots"]
               if _automated(plan, "create_slot", slot["label"])]
    if applied:
        updated = deepcopy(rollout)
        now = time.time()
        for slot in applied:
            entry = {"label": slot["label"], "target": slot["target"],
                     "state_dir": str(Path(new["state_parent"]) / slot["label"]),
                     "health_port": int(new.get("health_port", 443))}
            if slot.get("compose"):
                entry["pending"] = {
                    "since": now,
                    "deadline": now + int(compose.get("health_timeout_seconds", 1800)),
                    "service": slot["compose"]["service"],
                    "block": slot["compose"]["block"]}
            updated["slots"].append(entry)
        atomic_private(Path(rollout_path), (json.dumps(updated, indent=2) + "\n").encode())
        rollout.clear()
        rollout.update(updated)
    return changed


def verify_new_slots(rollout_path: Path, rollout: dict, *,
                     health=read_slot_health, now: float | None = None) -> dict[str, str]:
    """Settle pending slots: healthy, still waiting, or rolled back.

    A pending slot whose pinned health answers is kept. One still silent
    after its deadline has exactly its Compose service removed again and is
    unregistered; its identity directory stays for a later, reviewed retry.
    """
    now = time.time() if now is None else now
    compose = rollout.get("compose")
    result, keep, changed = {}, [], False
    for slot in rollout["slots"]:
        pending = slot.get("pending")
        if not pending:
            keep.append(slot)
            continue
        if isinstance(health(Path(slot["state_dir"]), slot["health_port"]), dict):
            slot = {k: v for k, v in slot.items() if k != "pending"}
            result[slot["label"]], changed = "healthy", True
            keep.append(slot)
        elif now > pending["deadline"]:
            if compose is not None:
                path = Path(compose["path"])
                try:
                    text = remove_slot_service(path.read_text(), block=pending["block"])
                except ComposeSlotError as exc:
                    raise RolloutError("Rollback needs a manual Compose edit") from exc
                atomic_private(path, text.encode())
            result[slot["label"]], changed = "rolled_back", True
        else:
            result[slot["label"]] = "waiting"
            keep.append(slot)
    if changed:
        updated = {**rollout, "slots": keep}
        atomic_private(Path(rollout_path), (json.dumps(updated, indent=2) + "\n").encode())
        rollout.clear()
        rollout.update(updated)
    return result


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
                or not isinstance(slot.get("state_dir"), str)
                or "pending" in slot and not (
                    isinstance(slot["pending"], dict)
                    and set(slot["pending"]) == {"since", "deadline", "service", "block"})):
            raise RolloutError("Invalid rollout slot")
    compose = value.get("compose")
    if compose is not None and (
            not isinstance(compose, dict)
            or not {"path", "template", "state_parent"} <= set(compose)
            <= {"path", "template", "state_parent", "service_prefix", "health_timeout_seconds"}
            or not all(isinstance(compose[k], str) for k in ("path", "template", "state_parent"))
            or type(compose.get("health_timeout_seconds", 1800)) is not int
            or not 60 <= compose.get("health_timeout_seconds", 1800) <= 86400):
        raise RolloutError("Invalid rollout Compose settings")
    return value


def compute(rollout: dict, report: dict, *,
            allow_estimated_capacity: bool = False) -> tuple[dict, dict[str, Path]]:
    """Observe every configured slot and plan against the inventory."""
    slots, dirs = {}, {}
    for slot in rollout["slots"]:
        state_dir = Path(slot["state_dir"])
        config = json.loads((state_dir / "config.json").read_text())
        observed = observe_slot(config, read_slot_health(state_dir, slot["health_port"]))
        slots[slot["label"]] = {"target": slot["target"], "host_ip": config.get("device_ip"),
                                "mac": config.get("mac"), **observed}
        dirs[slot["label"]] = state_dir
    new = rollout["new_slots"]
    compose = rollout.get("compose")
    plan = plan_rollout(report, slots, new_slot_addresses=list(new.get("addresses", [])),
                        new_slot_target=new.get("target", "nas"),
                        new_slot_prefix=new.get("label_prefix", "nas-slot-"),
                        allow_estimated_capacity=allow_estimated_capacity,
                        compose=None if compose is None else {
                            "text": Path(compose["path"]).read_text(),
                            "template": compose["template"],
                            "state_parent": compose["state_parent"],
                            "service_prefix": compose.get("service_prefix", "aiport_slot_")})
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
    parser.add_argument("--allow-estimated-capacity", action="store_true",
                        help="Also create a new slot needed only by a fallback capacity guess")
    parser.add_argument("--verify", action="store_true",
                        help="Settle pending new slots by their pinned health (rolls back "
                             "a slot silent past its deadline)")
    args = parser.parse_args(argv)
    try:
        rollout = load_rollout_config(args.rollout)
        if args.verify:
            print(json.dumps(verify_new_slots(args.rollout, rollout), indent=2))
            return 0
        report = (asyncio.run(fetch_inventory(args.controller, api_key_file=args.api_key_file,
                                              trust_file=args.web_trust_file,
                                              cert_file=args.web_cert_file))
                  if args.controller else json.loads(args.inventory.read_text()))
        plan, dirs = compute(rollout, report,
                             allow_estimated_capacity=args.allow_estimated_capacity)
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
