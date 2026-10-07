"""Idempotent diff between deployed AI Port slots and Protect's pairings.

Protect is the source of truth for which camera is paired to which AI Port
(``aiports[].pairedCameras``). Each deployed slot's ``paired_streams`` is only
a local allowlist of streams that instance will accept. This module compares
the two and classifies every difference:

* ``remove_from_allowlist``: a stream Protect will never start on this slot
  (stale entry). Safe to apply to the local config file.
* ``add_to_allowlist``: Protect paired the camera, but the slot would reject
  its stream. The camera's Protect host becomes the source IP; review first.
* ``manual_pairing``: an eligible camera that no AI Port has paired. Pairing
  is a Protect action and is never automated here.
* ``unmanaged_ai_port`` / ``missing_ai_port`` / ``over_capacity``: reported
  only.

Applying the safe actions changes ``paired_streams`` of the local files only;
running the diff again afterwards reports no action. Output carries camera
names and slot labels, never MAC or IP addresses.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import re

from .aiport_ingest import IngressError, normalize_mac
from .config import atomic_private


_CAMERA_ID = re.compile(r"[0-9a-fA-F]{24}\Z")
_G3_G5_MODEL = re.compile(r"UVC G[345](?:\s|\Z)")
SAFE_ACTIONS = frozenset({"remove_from_allowlist"})
REVIEW_ACTIONS = frozenset({"add_to_allowlist"})


class SlotDiffError(ValueError):
    """Fixed error text without camera or network identities."""


def _mac(value: object) -> str:
    try:
        return normalize_mac(value)
    except (IngressError, TypeError) as exc:
        raise SlotDiffError("invalid device identity") from exc


def _rows(value: object, kind: str) -> list[dict]:
    rows = value if isinstance(value, list) else None
    if rows is None or len(rows) > 256 or not all(isinstance(row, dict) for row in rows):
        raise SlotDiffError(f"Protect {kind} export must be a list of objects")
    return rows


def _name(row: dict) -> str:
    return row.get("name") if isinstance(row.get("name"), str) else "unnamed"


def diff_slots(slots: dict[str, dict], protect_aiports: object,
               protect_cameras: object) -> dict:
    """Compare deployed slot configs (label -> config) with Protect state."""
    cameras = {}
    for row in _rows(protect_cameras, "cameras"):
        if not isinstance(row.get("id"), str) or not _CAMERA_ID.fullmatch(row["id"]):
            raise SlotDiffError("Invalid Protect camera ID")
        cameras[row["id"]] = row
    by_mac = {_mac(row.get("mac")): row for row in cameras.values()}
    ports = {_mac(row.get("mac")): row for row in _rows(protect_aiports, "aiports")}
    paired_anywhere: set[str] = set()
    for port in ports.values():
        ids = port.get("pairedCameras")
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            raise SlotDiffError("Invalid AI Port pairing list")
        paired_anywhere.update(ids)

    actions: list[dict] = []
    report: dict[str, dict] = {}
    seen_ports: set[str] = set()
    for label in sorted(slots):
        config = slots[label]
        if not isinstance(config, dict) or not isinstance(config.get("paired_streams"), list):
            raise SlotDiffError("Slot config needs paired_streams")
        port_mac = _mac(config.get("mac"))
        allow = {_mac(stream.get("camera_mac")): stream
                 for stream in config["paired_streams"]}
        port = ports.get(port_mac)
        if port is None:
            report[label] = {"state": "missing_ai_port"}
            actions.append({"action": "missing_ai_port", "slot": label})
            continue
        seen_ports.add(port_mac)
        paired = {}
        for camera_id in port["pairedCameras"]:
            camera = cameras.get(camera_id)
            if camera is None:
                raise SlotDiffError("AI Port pairs a camera missing from the export")
            paired[_mac(camera.get("mac"))] = camera
        points = sum((Fraction(str(camera.get("aiPortCapacityPoints", 0)))
                      for camera in paired.values()), Fraction())
        report[label] = {
            "paired": sorted(_name(camera) for camera in paired.values()),
            "allowlisted": sorted(_name(by_mac[mac]) if mac in by_mac else "unknown"
                                  for mac in allow),
            "capacity_points": float(points),
        }
        if points > 1:
            actions.append({"action": "over_capacity", "slot": label})
        for mac in sorted(set(allow) - set(paired)):
            actions.append({"action": "remove_from_allowlist", "slot": label,
                            "camera": _name(by_mac[mac]) if mac in by_mac else "unknown",
                            "_mac": mac})
        for mac in sorted(set(paired) - set(allow)):
            actions.append({"action": "add_to_allowlist", "slot": label,
                            "camera": _name(paired[mac]), "_mac": mac,
                            "_source_ip": paired[mac].get("host")})
    for port_mac in sorted(set(ports) - seen_ports):
        actions.append({"action": "unmanaged_ai_port",
                        "cameras": sorted(_name(cameras[i]) for i in ports[port_mac]["pairedCameras"]
                                          if i in cameras)})
    for camera in sorted(cameras.values(), key=_name):
        eligible = (isinstance(camera.get("type"), str)
                    and _G3_G5_MODEL.match(camera["type"]) is not None
                    and camera.get("state") == "CONNECTED")
        if eligible and camera["id"] not in paired_anywhere:
            actions.append({"action": "manual_pairing", "camera": _name(camera)})
    return {"schema": "aikey-aiport-slot-diff/1", "slots": report, "actions": actions}


def public(diff: dict) -> dict:
    """The diff without private identities (MAC and IP fields)."""
    return {**diff, "actions": [{k: v for k, v in action.items() if not k.startswith("_")}
                                for action in diff["actions"]]}


def apply_allowlist(slots: dict[str, dict], diff: dict, *,
                    include_review: bool = False) -> dict[str, dict]:
    """Return slot configs with only safe allowlist actions applied."""
    allowed = SAFE_ACTIONS | (REVIEW_ACTIONS if include_review else frozenset())
    result = deepcopy(slots)
    for action in diff["actions"]:
        if action["action"] not in allowed:
            continue
        streams = result[action["slot"]]["paired_streams"]
        if action["action"] == "remove_from_allowlist":
            streams[:] = [stream for stream in streams
                          if _mac(stream.get("camera_mac")) != action["_mac"]]
        elif not isinstance(action.get("_source_ip"), str):
            raise SlotDiffError("A paired camera has no Protect host to allowlist")
        else:
            template = streams[0] if streams else {}
            streams.append({"camera_mac": action["_mac"],
                            "source_ip": action["_source_ip"],
                            "ffmpeg_path": template.get("ffmpeg_path", "/usr/bin/ffmpeg")})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(
        "Diff deployed AI Port slot allowlists against Protect's pairings. "
        "Never pairs, unpairs or restarts anything."))
    parser.add_argument("--slot", action="append", required=True, metavar="LABEL=CONFIG",
                        help="Local slot config.json, e.g. mac=state/aiport-mac/config.json")
    parser.add_argument("--protect-aiports", type=Path, required=True,
                        help="Private export of Protect's aiports list")
    parser.add_argument("--protect-cameras", type=Path, required=True,
                        help="Private export of Protect's cameras list")
    parser.add_argument("--apply-local", action="store_true",
                        help="Write safe allowlist removals to the local config files")
    args = parser.parse_args(argv)
    try:
        paths = {}
        for item in args.slot:
            label, sep, path = item.partition("=")
            if not sep or not label or label in paths:
                raise SlotDiffError("Each --slot needs a unique LABEL=CONFIG")
            paths[label] = Path(path)
        texts = {label: path.read_text() for label, path in paths.items()}
        slots = {label: json.loads(text) for label, text in texts.items()}
        diff = diff_slots(slots, json.loads(args.protect_aiports.read_text()),
                          json.loads(args.protect_cameras.read_text()))
        if args.apply_local:
            updated = apply_allowlist(slots, diff)
            for label, config in updated.items():
                if config != slots[label]:
                    path = paths[label]
                    backup = path.with_name(path.name + ".before-slot-diff")
                    if not backup.exists():
                        atomic_private(backup, path.read_bytes())
                    indented = texts[label].startswith("{\n")
                    encoded = (json.dumps(config, indent=2, ensure_ascii=False) + "\n"
                               if indented else json.dumps(config, separators=(",", ":")))
                    atomic_private(path, encoded.encode())
    except (OSError, json.JSONDecodeError, SlotDiffError) as exc:
        parser.error(str(exc))
    print(json.dumps(public(diff), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
