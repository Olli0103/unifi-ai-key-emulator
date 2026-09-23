"""Read-only capacity and listener plan for an independent AI Port profile.

This module does not claim an adopted device, pair cameras, or open sockets.
Its conservative capacity limits follow the published AI Port FAQ. Unknown
camera resolution reserves the maximum supported resolution's capacity.
"""

from __future__ import annotations

import argparse
import asyncio
from fractions import Fraction
import ipaddress
import json
from pathlib import Path
import re

from .camera_inventory import InventoryError, fetch_inventory


AI_KEY_MANAGEMENT_PORT = 8080
AI_PORT_MANAGEMENT_PORT = 443
AI_PORT_CONTAINER_PORT = 8443
AI_PORT_DISCOVERY_PORT = 10001
AI_PORT_CONTROLLER_WS_PORT = 7442
AI_PORT_PROTECT_RTSP_PORT = 7447
_CAMERA_ID = re.compile(r"[0-9a-fA-F]{24}\Z")
_G3_G5_MODEL = re.compile(r"UVC G[345](?:\s|\Z)")
_SCOPES = frozenset({"legacy-only", "legacy-and-g3-g5"})
_CAPACITY = {
    "protect": {"HD": Fraction(1, 5), "2K": Fraction(1, 3),
                "4K": Fraction(1, 2), None: Fraction(1, 2)},
    "onvif": {"HD": Fraction(1, 3), "2K": Fraction(1, 2),
              "4K": Fraction(1), None: Fraction(1)},
}


class AiPortPlanError(ValueError):
    """The supplied inventory or address pool cannot form a safe plan."""


def _ip(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise AiPortPlanError("A concrete IPv4 address is required") from exc
    if (address.is_unspecified or address.is_multicast or address.is_loopback
            or int(address) == 0xFFFFFFFF):
        raise AiPortPlanError("A reachable unicast LAN address is required")
    return str(address)


def _eligible(report: dict, camera_scope: str) -> tuple[
        dict[str, list[tuple[str, str | None, Fraction]]], int, int]:
    if (not isinstance(report, dict) or report.get("schema") != "aikey-camera-preflight/1"
            or not isinstance(report.get("cameras"), list)
            or len(report["cameras"]) > 256):
        raise AiPortPlanError("A complete camera preflight v1 report is required")
    groups: dict[str, list[tuple[str, str | None, Fraction]]] = {"protect": [], "onvif": []}
    seen = set()
    legacy_count = 0
    enhancement_count = 0
    for row in report["cameras"]:
        if not isinstance(row, dict):
            raise AiPortPlanError("Invalid camera row")
        camera_id = row.get("id")
        if not isinstance(camera_id, str) or not _CAMERA_ID.fullmatch(camera_id) or camera_id in seen:
            raise AiPortPlanError("Invalid or duplicate camera ID")
        seen.add(camera_id)
        processing_class = row.get("processing_class")
        if processing_class not in {"legacy_ingress_needed", "smart_event_candidate", "offline"}:
            raise AiPortPlanError("Unknown camera processing class")
        is_legacy = processing_class == "legacy_ingress_needed"
        is_enhancement = (camera_scope == "legacy-and-g3-g5"
                          and processing_class == "smart_event_candidate"
                          and row.get("source_kind") in (None, "protect")
                          and isinstance(row.get("model"), str)
                          and _G3_G5_MODEL.match(row["model"]) is not None)
        if not (is_legacy or is_enhancement):
            continue
        if row.get("state") != "CONNECTED":
            raise AiPortPlanError("Selected camera is not connected")
        source = row.get("source_kind")
        if source is None and isinstance(row.get("model"), str) and row["model"].startswith("UVC "):
            source = "protect"
        if source not in _CAPACITY:
            raise AiPortPlanError("Legacy camera source is unknown; Protect and ONVIF cannot share an AI Port")
        resolution = row.get("recording_resolution")
        if resolution not in _CAPACITY[source]:
            raise AiPortPlanError("Camera resolution must be HD, 2K, 4K or unknown")
        groups[source].append((camera_id, resolution, _CAPACITY[source][resolution]))
        legacy_count += int(is_legacy)
        enhancement_count += int(is_enhancement)
    return groups, legacy_count, enhancement_count


def plan_ai_ports(report: dict, *, device_ips: list[str] | None = None,
                  ai_key_ip: str | None = None,
                  camera_scope: str = "legacy-only") -> dict:
    """Size independent AI Port instances without pairing or opening ports.

    Each instance gets a different host IP because the management listener is
    fixed to 443. A single host UDP 10001 responder may cover all identities.
    """
    if camera_scope not in _SCOPES:
        raise AiPortPlanError("Unknown camera scope")
    groups, legacy_count, enhancement_count = _eligible(report, camera_scope)
    addresses = [_ip(value) for value in (device_ips or [])]
    if len(set(addresses)) != len(addresses):
        raise AiPortPlanError("AI Port addresses must be distinct")
    key_address = _ip(ai_key_ip) if ai_key_ip is not None else None
    if key_address in addresses:
        raise AiPortPlanError("AI Port and AI Key require separate host addresses")
    allocations = []
    for source in ("protect", "onvif"):
        bins: list[dict] = []
        for camera_id, resolution, weight in sorted(groups[source], key=lambda item: (-item[2], item[0])):
            destination = next((item for item in bins if item["load"] + weight <= 1), None)
            if destination is None:
                destination = {"source_kind": source, "load": Fraction(), "cameras": []}
                bins.append(destination)
            destination["load"] += weight
            destination["cameras"].append({"id": camera_id, "recording_resolution": resolution})
        allocations.extend(bins)
    instances = []
    for index, allocation in enumerate(allocations):
        address = addresses[index] if index < len(addresses) else None
        instances.append({
            "slot": index + 1,
            "source_kind": allocation["source_kind"],
            "camera_ids": [camera["id"] for camera in allocation["cameras"]],
            "resolution_unverified": any(camera["recording_resolution"] is None
                                         for camera in allocation["cameras"]),
            "reserved_capacity": str(allocation["load"]),
            "host_ip": address,
            "management_tcp": AI_PORT_MANAGEMENT_PORT,
            "apple_publish": (f"{address}:443:{AI_PORT_CONTAINER_PORT}/tcp" if address else None),
            "state": "planned_only",
        })
    return {
        "schema": "aikey-aiport-deployment-plan/2",
        "camera_scope": camera_scope,
        "legacy_camera_count": legacy_count,
        "enhancement_camera_count": enhancement_count,
        "selected_camera_count": legacy_count + enhancement_count,
        "ai_port_instances_required": len(instances),
        "ai_port_instances_without_address": sum(item["host_ip"] is None for item in instances),
        "ai_key": {"host_ip": key_address, "management_tcp": AI_KEY_MANAGEMENT_PORT},
        "host_discovery_udp": AI_PORT_DISCOVERY_PORT if instances else None,
        "controller_websocket_tcp": AI_PORT_CONTROLLER_WS_PORT if instances else None,
        "instances": instances,
        "adoption": "needs_evidence",
        "camera_pairing": "disabled",
        "camera_stream_ports": {
            "protect_outbound_tcp": ([AI_PORT_PROTECT_RTSP_PORT]
                                     if groups["protect"] else []),
            "onvif_outbound_tcp": "needs_evidence" if groups["onvif"] else [],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan AI Port capacity and fixed listeners from a private camera preflight")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--inventory", type=Path, help="Existing private camera preflight JSON")
    source.add_argument("--controller", help="Fetch the current Protect inventory from this private IPv4 host")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--web-trust-file", type=Path)
    parser.add_argument("--web-cert-file", type=Path)
    parser.add_argument("--ai-port-ip", action="append", default=[])
    parser.add_argument("--ai-key-ip")
    parser.add_argument("--camera-scope", choices=sorted(_SCOPES),
                        default="legacy-only")
    args = parser.parse_args(argv)
    if args.controller:
        if not all((args.api_key_file, args.web_trust_file, args.web_cert_file)):
            parser.error("Live inventory requires private API-key, web-trust and web-certificate files")
    elif any((args.api_key_file, args.web_trust_file, args.web_cert_file)):
        parser.error("Protect credential files are only used with --controller")
    try:
        if args.controller:
            report = asyncio.run(fetch_inventory(args.controller, api_key_file=args.api_key_file,
                                                  trust_file=args.web_trust_file,
                                                  cert_file=args.web_cert_file))
        else:
            report = json.loads(args.inventory.read_text())
        plan = plan_ai_ports(report, device_ips=args.ai_port_ip,
                             ai_key_ip=args.ai_key_ip,
                             camera_scope=args.camera_scope)
    except (OSError, json.JSONDecodeError, AiPortPlanError, InventoryError) as exc:
        parser.error(str(exc))
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
