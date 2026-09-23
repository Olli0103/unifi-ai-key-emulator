"""Conservative Docker Compose reconciliation for independently planned AI Ports.

The default is a read-only dry run. Apply only starts selected, absent or
stopped services. It never recreates an existing container or touches volumes.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import ssl
import subprocess
import time
from typing import Callable

from .aiport_candidate import CandidateError, _private_file
from .aiport_deployment import AiPortPlanError, plan_ai_ports
from .aiport_nas_compose import NasComposeError, build_nas_compose
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


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(_private_file(path, 2 * 1024 * 1024))
    except (CandidateError, OSError, ValueError) as exc:
        raise ReconcileError("Plan and manifest must be existing private JSON files") from exc
    if not isinstance(value, dict):
        raise ReconcileError("Plan and manifest must be JSON objects")
    return value


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
        addresses = [item["host_ip"] for item in plan["instances"]]
        if any(not isinstance(address, str) for address in addresses):
            raise ReconcileError("Every planned AI Port slot needs a reserved address")
        rebuilt = plan_ai_ports(report, device_ips=addresses,
                                ai_key_ip=plan["ai_key"]["host_ip"],
                                camera_scope=plan["camera_scope"], previous_plan=plan)
        if rebuilt != plan:
            raise ReconcileError("Protect camera inventory or capacity changed; review a new plan")
        expected = build_nas_compose(plan, states, **options)
    except (AiPortPlanError, NasComposeError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ReconcileError):
            raise
        raise ReconcileError("Plan, camera inventory or slot identity no longer matches") from exc
    if manifest != expected:
        raise ReconcileError("Compose manifest differs from verified plan and slot identities")
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


def _inspect(run: Callable[[list[str], int], str], row: dict, service: dict) -> None:
    container_id = row.get("ID")
    if not isinstance(container_id, str) or not _CONTAINER_ID.fullmatch(container_id):
        raise ReconcileError("Docker Compose did not identify its container")
    try:
        image = json.loads(run(["docker", "inspect", "--format",
                                "{{json .Config.Image}}", container_id], 15))
        networks = json.loads(run(["docker", "inspect", "--format",
                                   "{{json .NetworkSettings.Networks}}", container_id], 15))
        selected = service["networks"]["aiport_lan"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ReconcileError("Docker container identity or network cannot be verified") from exc
    if image != service["image"] or not isinstance(networks, dict) or len(networks) != 1:
        raise ReconcileError("Docker container image or network differs from the manifest")
    actual = next(iter(networks.values()))
    if (not isinstance(actual, dict)
            or actual.get("IPAddress") != selected["ipv4_address"]
            or str(actual.get("MacAddress", "")).lower() != selected["mac_address"].lower()):
        raise ReconcileError("Docker container IP or MAC differs from the planned identity")


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


def reconcile(manifest_path: Path, manifest: dict, states: dict[int, Path],
              *, apply: bool = False, run: Callable[[list[str], int], str] = _run,
              pause: Callable[[float], None] = time.sleep) -> dict:
    """Validate Compose, inspect state, and optionally start only missing slots."""
    services = sorted(manifest["services"])
    run(_compose(manifest_path, "config", "--quiet"), 15)
    found = _rows(run(_compose(manifest_path, "ps", "--all", "--format", "json"), 15),
                  services)
    running = [name for name in services if name in found and found[name]["State"] == "running"]
    to_start = [name for name in services if name not in running]
    health = {}
    for name, row in found.items():
        _inspect(run, row, manifest["services"][name])
    for name in running:
        slot = int(name.removeprefix("aiport_slot_"))
        health[name] = _health(run, manifest_path, name, states[slot])
    if not apply:
        return {"mode": "dry_run", "running": running, "would_start": to_start,
                "health": health}
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
            _inspect(run, current[name], manifest["services"][name])
            slot = int(name.removeprefix("aiport_slot_"))
            for attempt in range(6):
                try:
                    health[name] = _health(run, manifest_path, name, states[slot])
                    break
                except ReconcileError:
                    if attempt == 5:
                        raise
                    pause(2)
        return {"mode": "applied", "preserved": running, "started": to_start,
                "health": health}
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
                   "parent": args.parent, "image": args.image, "uid": args.uid, "gid": args.gid}
        verify_inputs(plan, manifest, report, states, options)
        outcome = reconcile(args.compose, manifest, states, apply=args.apply)
    except (InventoryError, ReconcileError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(outcome, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
