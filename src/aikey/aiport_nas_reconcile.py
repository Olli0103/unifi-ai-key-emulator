"""Conservative Docker Compose reconciliation for independently planned AI Ports.

The default is a read-only dry run. Apply only starts selected, absent or
stopped services. It never recreates an existing container or touches volumes.
"""

from __future__ import annotations

import argparse
import asyncio
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import ssl
import subprocess
import time
from typing import Callable

from .aiport_candidate import CandidateError, _private_file, load_config
from .aiport_deployment import (
    AI_PORT_CONTAINER_PORT, AiPortPlanError, _eligible, _ip, plan_ai_ports,
)
from .aiport_nas_compose import NasComposeError, build_nas_compose
from .aiport_ingest import IngressError, normalize_mac
from .camera_inventory import InventoryError, fetch_inventory


class ReconcileError(ValueError):
    """A precondition or bounded Docker operation failed."""


_HEALTH_SCRIPT = """import hashlib, http.client, json, pathlib, ssl, sys
cert = pathlib.Path('/state/device.crt')
raw = ssl.PEM_cert_to_DER_cert(cert.read_text())
digest = hashlib.sha256(raw).hexdigest()
if digest != sys.argv[1]:
    raise SystemExit(3)
context = ssl.create_default_context(cafile=str(cert))
context.check_hostname = False
connection = http.client.HTTPSConnection('127.0.0.1', 443, timeout=5, context=context)
connection.request('GET', '/healthz')
response = connection.getresponse()
if response.status != 200:
    raise SystemExit(4)
body = json.loads(response.read(4097))
if body.get('service') != 'aiport-candidate' or type(body.get('adopted')) is not bool or type(body.get('control_connected')) is not bool:
    raise SystemExit(5)
print(json.dumps({'service': body['service'], 'adopted': body['adopted'], 'control_connected': body['control_connected']}))
"""
_CONTAINER_ID = re.compile(r"[0-9a-fA-F]{12,64}\Z")
_HEALTH_ATTEMPTS = 16
_HEALTH_PAUSE_SECONDS = 2


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(_private_file(path, 2 * 1024 * 1024))
    except (CandidateError, OSError, ValueError) as exc:
        raise ReconcileError("Plan and manifest must be existing private JSON files") from exc
    if not isinstance(value, dict):
        raise ReconcileError("Plan and manifest must be JSON objects")
    return value


def _verify_external_slot_capacity(plan: dict, report: dict,
                                   selected: dict[int, Path]) -> None:
    """Permit unknown live resolution only for slots this apply cannot touch."""
    fresh = plan_ai_ports(report, ai_key_ip=plan["ai_key"]["host_ip"],
                          camera_scope=plan["camera_scope"])
    for field in ("schema", "camera_scope", "selected_camera_count", "ai_key",
                  "host_discovery_udp", "controller_websocket_tcp", "camera_pairing",
                  "camera_stream_ports", "adoption"):
        if plan.get(field) != fresh[field]:
            raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")
    # An AI Port can make a previously legacy camera advertise smart types.
    # The initial legacy/enhancement split is descriptive; exact IDs, source
    # and selected-slot capacity below remain mandatory.
    if (plan.get("legacy_camera_count") + plan.get("enhancement_camera_count")
            != plan["selected_camera_count"]):
        raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")
    slots = plan["instances"]
    if (plan.get("ai_port_instances_required") != len(slots)
            or plan.get("ai_port_instances_without_address") != sum(
                slot.get("host_ip") is None for slot in slots)):
        raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")
    groups, _, _ = _eligible(report, plan["camera_scope"])
    current = {camera_id: (source, resolution, weight)
               for source, cameras in groups.items()
               for camera_id, resolution, weight in cameras}
    assigned: set[str] = set()
    addresses: set[str] = set()
    for index, slot in enumerate(slots, start=1):
        if (not isinstance(slot, dict) or slot.get("slot") != index
                or slot.get("source_kind") not in groups
                or not isinstance(slot.get("camera_ids"), list)
                or not slot["camera_ids"] or slot.get("management_tcp") != 443
                or slot.get("state") != "planned_only"):
            raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")
        address = slot.get("host_ip")
        if address is not None:
            address = _ip(address)
            if address in addresses or address == plan["ai_key"]["host_ip"]:
                raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")
            addresses.add(address)
        if slot.get("apple_publish") != (
                f"{address}:443:{AI_PORT_CONTAINER_PORT}/tcp" if address else None):
            raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")
        rows = []
        for camera_id in slot["camera_ids"]:
            if camera_id in assigned or camera_id not in current:
                raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")
            assigned.add(camera_id)
            row = current[camera_id]
            if row[0] != slot["source_kind"]:
                raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")
            rows.append(row)
        load = sum((row[2] for row in rows), Fraction())
        try:
            reserved = Fraction(slot["reserved_capacity"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise ReconcileError("Protect camera inventory or capacity changed; review a new plan") from exc
        if not 0 < reserved <= 1 or reserved > load:
            raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")
        if index in selected or all(row[1] is not None for row in rows):
            if (reserved != load or type(slot.get("resolution_unverified")) is not bool
                    or all(row[1] is not None for row in rows)
                    and slot["resolution_unverified"]):
                raise ReconcileError("Selected NAS slot lacks verified camera capacity")
    if assigned != set(current):
        raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")


def _verify_configured_cameras(plan: dict, report: dict, states: dict[int, Path],
                               controller_ip: str) -> None:
    """A selected slot cannot accept a camera outside its fresh plan assignment."""
    rows = {row.get("id"): row for row in report["cameras"] if isinstance(row, dict)}
    configured_across_slots: set[str] = set()
    for slot, state_dir in states.items():
        try:
            config = load_config(Path(state_dir) / "config.json",
                                 check_decoder_executable=False)
            streams = ([config["paired_stream"]] if "paired_stream" in config else
                       config.get("paired_streams", []))
            if not streams:
                continue
            planned = plan["instances"][slot - 1]
            allowed = {normalize_mac(rows[camera_id]["mac"])
                       for camera_id in planned["camera_ids"]}
            if len(allowed) != len(planned["camera_ids"]):
                raise ValueError
            for stream in streams:
                camera = stream["camera_mac"]
                if (camera not in allowed or camera in configured_across_slots
                        or planned["source_kind"] == "protect"
                        and stream["source_ip"] != controller_ip):
                    raise ValueError
                configured_across_slots.add(camera)
        except (CandidateError, IngressError, KeyError, IndexError, TypeError,
                ValueError, OSError) as exc:
            raise ReconcileError(
                "Configured AI Port camera is outside its verified slot") from exc


def verify_inputs(plan: dict, manifest: dict, report: dict,
                  states: dict[int, Path], options: dict,
                  *, now: int | None = None) -> list[str]:
    """Reject inventory drift, stale reports and edited deployment manifests."""
    current_time = int(time.time()) if now is None else now
    fetched = report.get("fetched_at") if isinstance(report, dict) else None
    if (not isinstance(report, dict) or report.get("schema") != "aikey-camera-preflight/1"
            or report.get("source") != "local_protect_integration_api"
            or report.get("protect_version") != "7.3.60"
            or report.get("processing_enabled") is not False
            or type(fetched) is not int or not 0 <= current_time - fetched <= 300):
        raise ReconcileError("A fresh, supported pinned Protect inventory is required")
    if not isinstance(plan, dict) or not isinstance(plan.get("instances"), list):
        raise ReconcileError("A complete AI Port plan is required")
    try:
        try:
            rebuilt = plan_ai_ports(report,
                                    ai_key_ip=plan["ai_key"]["host_ip"],
                                    camera_scope=plan["camera_scope"], previous_plan=plan)
        except AiPortPlanError as exc:
            if str(exc) != "An existing AI Port slot exceeds current camera capacity":
                raise
            _verify_external_slot_capacity(plan, report, states)
        else:
            # Pairing can make a formerly legacy G3-G5 camera advertise smart
            # types. That only changes these descriptive counts; the exact
            # camera IDs, slots, addresses and capacity must still match.
            descriptive = {"legacy_camera_count", "enhancement_camera_count"}
            counts = tuple(plan.get(key) for key in sorted(descriptive))
            if (any(type(value) is not int or value < 0 for value in counts)
                    or sum(counts) != plan.get("selected_camera_count")
                    or {key: value for key, value in rebuilt.items()
                        if key not in descriptive} != {
                            key: value for key, value in plan.items()
                            if key not in descriptive}):
                raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")
        expected = build_nas_compose(plan, states, **options)
    except (AiPortPlanError, NasComposeError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ReconcileError):
            raise
        raise ReconcileError("Plan, camera inventory or slot identity no longer matches") from exc
    if manifest != expected:
        raise ReconcileError("Compose manifest differs from verified plan and slot identities")
    _verify_configured_cameras(plan, report, states, options["controller_ip"])
    if not expected["services"]:
        raise ReconcileError("No NAS services selected")
    return sorted(expected["services"])


def _run(argv: list[str], timeout: int = 15) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReconcileError("Docker command unavailable or timed out") from exc
    if result.returncode != 0 or len(result.stdout) > 65536 or len(result.stderr) > 65536:
        raise ReconcileError("Docker command failed; inspect its logs locally")
    return result.stdout


def _compose(manifest_path: Path, *args: str) -> list[str]:
    return ["docker", "compose", "-f", str(manifest_path), *args]


def _rows(output: str, services: list[str]) -> dict[str, dict]:
    try:
        stripped = output.strip()
        parsed = json.loads(stripped) if stripped.startswith("[") else [
            json.loads(line) for line in stripped.splitlines()]
    except (ValueError, TypeError) as exc:
        raise ReconcileError("Docker Compose returned invalid status JSON") from exc
    if not isinstance(parsed, list):
        raise ReconcileError("Docker Compose status must be JSON lines")
    found: dict[str, dict] = {}
    for row in parsed:
        if not isinstance(row, dict) or row.get("Service") not in services:
            raise ReconcileError("Unexpected service in the AI Port Compose project")
        name = row["Service"]
        if name in found or row.get("Publishers") not in (None, []):
            raise ReconcileError("Duplicate service or unexpected published host port")
        if row.get("State") not in ("running", "created", "exited"):
            raise ReconcileError("AI Port service has an unsafe or unknown Docker state")
        found[name] = row
    return found


def _network_name(manifest: dict) -> str:
    try:
        network = manifest["networks"]["aiport_lan"]
        if network.get("external") is True:
            name = network["name"]
        else:
            name = f"{manifest['name']}_aiport_lan"
    except (AttributeError, KeyError, TypeError) as exc:
        raise ReconcileError("AI Port network cannot be identified") from exc
    if not isinstance(name, str) or not name:
        raise ReconcileError("AI Port network cannot be identified")
    return name


def _verify_external_network(run: Callable[[list[str], int], str],
                             manifest: dict) -> None:
    try:
        network = manifest["networks"]["aiport_lan"]
    except (KeyError, TypeError) as exc:
        raise ReconcileError("AI Port network cannot be identified") from exc
    if not isinstance(network, dict):
        raise ReconcileError("AI Port network cannot be identified")
    if network.get("external") is not True:
        return
    expected = manifest.get("x-aikey-network-check")
    name = _network_name(manifest)
    if (not isinstance(expected, dict) or expected.get("name") != name
            or expected.get("driver") != "macvlan"):
        raise ReconcileError("External AI Port network lacks its verified contract")
    try:
        actual = json.loads(run(["docker", "network", "inspect", "--format",
                                 "{{json .}}", name], 15))
        ipam = actual["IPAM"]["Config"]
        parent = actual["Options"]["parent"]
    except (ReconcileError, ValueError, KeyError, TypeError) as exc:
        raise ReconcileError("Existing AI Port macvlan network cannot be inspected") from exc
    if (not isinstance(actual, dict) or actual.get("Name") != name
            or actual.get("Driver") != "macvlan" or parent != expected.get("parent")
            or not isinstance(ipam, list) or len(ipam) != 1
            or not isinstance(ipam[0], dict)
            or ipam[0].get("Subnet") != expected.get("subnet")
            or ipam[0].get("Gateway") != expected.get("gateway")):
        raise ReconcileError("Existing AI Port macvlan differs from the verified LAN")


def _inspect(run: Callable[[list[str], int], str], row: dict, service: dict,
             network_name: str) -> None:
    container_id = row.get("ID")
    if not isinstance(container_id, str) or not _CONTAINER_ID.fullmatch(container_id):
        raise ReconcileError("Docker Compose did not identify its container")
    try:
        image = json.loads(run(["docker", "inspect", "--format",
                                "{{json .Config.Image}}", container_id], 15))
        command = json.loads(run(["docker", "inspect", "--format",
                                  "{{json .Config.Cmd}}", container_id], 15))
        user = json.loads(run(["docker", "inspect", "--format",
                               "{{json .Config.User}}", container_id], 15))
        host = json.loads(run(["docker", "inspect", "--format",
                               "{{json .HostConfig}}", container_id], 15))
        mounts = json.loads(run(["docker", "inspect", "--format",
                                 "{{json .Mounts}}", container_id], 15))
        networks = json.loads(run(["docker", "inspect", "--format",
                                   "{{json .NetworkSettings.Networks}}", container_id], 15))
        selected = service["networks"]["aiport_lan"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ReconcileError("Docker container identity or network cannot be verified") from exc
    if (image != service["image"] or command != service["command"]
            or not isinstance(networks, dict) or len(networks) != 1):
        raise ReconcileError("Docker container image, command or network differs from the manifest")
    expected_volume = service["volumes"][0]
    if (user != service["user"] or not isinstance(host, dict)
            or host.get("ReadonlyRootfs") is not True
            or host.get("Privileged") is not False
            or host.get("CapAdd") not in (None, [])
            or host.get("CapDrop") != service["cap_drop"]
            or host.get("SecurityOpt") != service["security_opt"]
            or host.get("Sysctls") != {key: str(value) for key, value in service["sysctls"].items()}
            or host.get("PublishAllPorts") is not False
            or host.get("PortBindings") not in (None, {})
            or not isinstance(mounts, list) or len(mounts) != 1
            or not isinstance(mounts[0], dict)
            or mounts[0].get("Type") != "bind"
            or mounts[0].get("Source") != expected_volume["source"]
            or mounts[0].get("Destination") != expected_volume["target"]
            or mounts[0].get("RW") is not True):
        raise ReconcileError("Docker container user, isolation or state mount differs from the manifest")
    actual = networks.get(network_name)
    if (not isinstance(actual, dict)
            or network_name not in networks
            or actual.get("IPAddress") != selected["ipv4_address"]
            or str(actual.get("MacAddress", "")).lower() != selected["mac_address"].lower()):
        raise ReconcileError("Docker container network, IP or MAC differs from the planned identity")


def _health(run: Callable[[list[str], int], str], manifest_path: Path,
            service: str, state: Path) -> dict:
    try:
        raw = state.joinpath("device.crt").read_text()
        pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(raw)).hexdigest()
        output = run(_compose(manifest_path, "exec", "-T", service,
                              "python", "-c", _HEALTH_SCRIPT, pin), 12)
        health = json.loads(output)
    except (OSError, ValueError) as exc:
        raise ReconcileError(f"{service} failed its pinned local health check") from exc
    if (not isinstance(health, dict) or health.get("service") != "aiport-candidate"
            or type(health.get("adopted")) is not bool
            or type(health.get("control_connected")) is not bool):
        raise ReconcileError(f"{service} returned invalid health")
    return health


def _readiness(health: dict) -> str:
    if not health["adopted"]:
        return "awaiting_adoption"
    return "connected" if health["control_connected"] else "controller_disconnected"


def reconcile(manifest_path: Path, manifest: dict, states: dict[int, Path],
              *, apply: bool = False, run: Callable[[list[str], int], str] = _run,
              pause: Callable[[float], None] = time.sleep) -> dict:
    """Validate Compose, inspect state, and optionally start only missing slots."""
    services = sorted(manifest["services"])
    run(_compose(manifest_path, "config", "--quiet"), 15)
    network_name = _network_name(manifest)
    _verify_external_network(run, manifest)
    found = _rows(run(_compose(manifest_path, "ps", "--all", "--format", "json"), 15),
                  services)
    running = [name for name in services if name in found and found[name]["State"] == "running"]
    to_start = [name for name in services if name not in running]
    health = {}
    readiness = {}
    for name, row in found.items():
        _inspect(run, row, manifest["services"][name], network_name)
    for name in running:
        slot = int(name.removeprefix("aiport_slot_"))
        health[name] = _health(run, manifest_path, name, states[slot])
        readiness[name] = _readiness(health[name])
    if not apply:
        return {"mode": "dry_run", "running": running, "would_start": to_start,
                "health": health, "readiness": readiness}
    if any(status == "controller_disconnected" for status in readiness.values()):
        raise ReconcileError("An adopted AI Port is disconnected; no new slots were started")
    attempted = []
    try:
        for name in to_start:
            attempted.append(name)
            run(_compose(manifest_path, "up", "-d", "--no-deps", "--no-recreate",
                         "--no-build", "--pull", "never", name), 90)
            current = _rows(run(_compose(manifest_path, "ps", "--all", "--format", "json"),
                                15), services)
            if name not in current or current[name]["State"] != "running":
                raise ReconcileError(f"{name} did not enter running state")
            _inspect(run, current[name], manifest["services"][name], network_name)
            slot = int(name.removeprefix("aiport_slot_"))
            for attempt in range(_HEALTH_ATTEMPTS):
                try:
                    health[name] = _health(run, manifest_path, name, states[slot])
                    readiness[name] = _readiness(health[name])
                    if readiness[name] == "controller_disconnected":
                        raise ReconcileError(f"{name} is adopted but disconnected from Protect")
                    break
                except ReconcileError:
                    if attempt == _HEALTH_ATTEMPTS - 1:
                        raise
                    pause(_HEALTH_PAUSE_SECONDS)
        return {"mode": "applied", "preserved": running, "started": to_start,
                "health": health, "readiness": readiness}
    except ReconcileError:
        for name in reversed(attempted):
            try:
                run(_compose(manifest_path, "stop", "--timeout", "10", name), 30)
            except ReconcileError:
                pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run or safely start verified NAS AI Port slots")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--compose", required=True, type=Path)
    parser.add_argument("--slot-state", action="append", required=True)
    parser.add_argument("--controller-ip", required=True)
    parser.add_argument("--controller-pin", required=True)
    parser.add_argument("--nas-ip", required=True)
    parser.add_argument("--subnet", required=True)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--external-network")
    parser.add_argument("--image", required=True)
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--gid", required=True, type=int)
    parser.add_argument("--api-key-file", required=True, type=Path)
    parser.add_argument("--web-trust-file", required=True, type=Path)
    parser.add_argument("--web-cert-file", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        states = {}
        for assignment in args.slot_state:
            number, path = assignment.split("=", 1)
            slot = int(number)
            if slot in states or slot <= 0 or not path:
                raise ReconcileError("Duplicate or invalid slot state")
            states[slot] = Path(path)
        plan, manifest = _read_json(args.plan), _read_json(args.compose)
        report = asyncio.run(fetch_inventory(args.controller_ip,
                                             api_key_file=args.api_key_file,
                                             trust_file=args.web_trust_file,
                                             cert_file=args.web_cert_file))
        options = {"controller_ip": args.controller_ip, "controller_pin": args.controller_pin,
                   "nas_ip": args.nas_ip, "subnet": args.subnet, "gateway": args.gateway,
                   "parent": args.parent, "image": args.image, "uid": args.uid, "gid": args.gid,
                   "external_network": args.external_network}
        verify_inputs(plan, manifest, report, states, options)
        outcome = reconcile(args.compose, manifest, states, apply=args.apply)
    except (InventoryError, ReconcileError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(outcome, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
