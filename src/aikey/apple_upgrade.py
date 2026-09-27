"""Same-host, idle-gated upgrade of a pinned Apple ``container`` service (#24).

An upgrade replaces the pinned container of one supervised service (the Mac
AI Port or AI Key) with a new container from a new image. Everything else must
stay as it was: the same user, read-only root, tmpfs, resources, DNS,
published address and port, bind-mounted state, entrypoint and arguments. The
run arguments are therefore derived from the live container's own
configuration, never retyped by hand. A setting this tool cannot reproduce
exactly (custom environment, capabilities, labels, sockets, extra networks
and similar) blocks the plan with a fixed reason instead of being dropped.

``swap`` waits until the service is idle, holds the supervisor, stops the old
container, starts the new one and waits for readiness. On success it pins the
new container; otherwise it deletes the new container, restarts the old one
and pins it again. The old container is kept (stopped) for a later rollback.
Only container names, image references and fixed codes are printed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Callable

from . import apple_supervisor as supervisor
from .apple_supervisor import CONTAINER, Runner, SupervisorError, _CONTAINER_ID, _observed, _run

Probe = Callable[[], bool]
_DEFAULT_MTU = 1280
_ALLOWED_INIT = {"arguments", "environment", "executable", "rlimits", "supplementalGroups",
                 "terminal", "user", "workingDirectory"}


class UpgradeError(ValueError):
    """A fixed, non-secret reason why an upgrade is not planned or not safe."""


@dataclass(frozen=True)
class Plan:
    service: str
    old: str
    new: str
    image: str
    argv: tuple[str, ...]


def _image_config(run: Runner, reference: str) -> dict:
    code, output = run([CONTAINER, "image", "inspect", reference])
    try:
        data = json.loads(output) if code == 0 else None
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list) and data:
        data = data[0]
    variants = data.get("variants") if isinstance(data, dict) else None
    if not isinstance(variants, list) or len(variants) != 1:
        raise UpgradeError("image_unavailable")
    config = variants[0].get("config") or {}
    inner = config.get("config") or {}
    return {"platform": (config.get("os"), config.get("architecture")),
            "env": list(inner.get("Env") or []), "workdir": inner.get("WorkingDir") or "/"}


def run_argv(entry: dict, old_image: dict, *, new_name: str, new_image: str) -> list[str]:
    """``container run`` arguments that recreate ``entry`` from ``new_image``."""
    config = entry.get("configuration") or {}
    init = config.get("initProcess") or {}
    problems = []
    for field, reason in (("capAdd", "capabilities"), ("capDrop", "capabilities"),
                          ("labels", "labels"), ("sysctls", "sysctls"),
                          ("publishedSockets", "published_sockets")):
        if config.get(field):
            problems.append(reason)
    for field in ("rosetta", "ssh", "useInit", "virtualization"):
        if config.get(field):
            problems.append(field)
    if set(init) - _ALLOWED_INIT or init.get("rlimits") or init.get("supplementalGroups") \
            or init.get("terminal"):
        problems.append("process_options")
    extra_env = sorted(set(init.get("environment") or []) - set(old_image["env"]))
    if extra_env:
        # Passing them on the command line would expose values (possibly secrets).
        problems.append("custom_environment")
    networks = config.get("networks") or []
    if (len(networks) != 1 or networks[0].get("network") != "default"
            or set((networks[0].get("options") or {})) - {"hostname", "mtu"}
            or (networks[0].get("options") or {}).get("mtu", _DEFAULT_MTU) != _DEFAULT_MTU):
        problems.append("network")
    dns = config.get("dns") or {}
    if dns.get("options") or dns.get("searchDomains") or set(dns) - {
            "nameservers", "options", "searchDomains", "domain"} or dns.get("domain"):
        problems.append("dns_options")
    user = ((init.get("user") or {}).get("raw") or {}).get("userString")
    if not isinstance(user, str) or not user:
        problems.append("user")
    executable = init.get("executable")
    if not isinstance(executable, str) or not executable:
        problems.append("entrypoint")
    resources = config.get("resources") or {}
    cpus, memory = resources.get("cpus"), resources.get("memoryInBytes")
    if (type(cpus) is not int or cpus < 1 or type(memory) is not int
            or memory < 1 << 20 or memory % (1 << 20)):
        problems.append("resources")
    mounts: list[str] = []
    for mount in config.get("mounts") or []:
        kind = mount.get("type") or {}
        options = [o for o in mount.get("options") or [] if isinstance(o, str)]
        if "tmpfs" in kind:
            mounts += ["--tmpfs", mount["destination"] + (":" + ",".join(options) if options else "")]
        elif "virtiofs" in kind and set(options) <= {"ro"}:
            mounts += ["-v", f"{mount['source']}:{mount['destination']}" + (":ro" if options else "")]
        else:
            problems.append("mount")
    ports: list[str] = []
    for port in config.get("publishedPorts") or []:
        if port.get("count", 1) != 1 or port.get("proto", "tcp") not in {"tcp", "udp"}:
            problems.append("published_port")
            continue
        suffix = "" if port.get("proto", "tcp") == "tcp" else "/udp"
        ports += ["-p", f"{port['hostAddress']}:{port['hostPort']}:{port['containerPort']}{suffix}"]
    if problems:
        raise UpgradeError("unsupported:" + ",".join(sorted(set(problems))))
    argv = [CONTAINER, "run", "-d", "--name", new_name, "--user", user]
    if config.get("readOnly"):
        argv.append("--read-only")
    argv += mounts
    argv += ["--cpus", str(cpus), "--memory", f"{memory >> 20}M"]
    for server in dns.get("nameservers") or []:
        argv += ["--dns", server]
    argv += ports
    workdir = init.get("workingDirectory")
    if workdir and workdir != old_image["workdir"]:
        argv += ["--workdir", workdir]
    argv += ["--entrypoint", executable, new_image, *[str(a) for a in init.get("arguments") or []]]
    return argv


def plan(state_dir: Path, service_name: str, new_name: str, new_image: str,
         run: Runner = _run) -> Plan:
    """Read-only: check the pin and derive the replacement's run arguments."""
    if not isinstance(new_name, str) or not _CONTAINER_ID.fullmatch(new_name):
        raise UpgradeError("invalid_new_name")
    services, _ = supervisor.load_spec(state_dir / supervisor.SPEC_FILE)
    service = next((s for s in services if s.name == service_name), None)
    if service is None:
        raise UpgradeError("unknown_service")
    code, output = run([CONTAINER, "list", "--all", "--format", "json"])
    try:
        listing = json.loads(output) if code == 0 else None
    except json.JSONDecodeError:
        listing = None
    if not isinstance(listing, list):
        raise UpgradeError("list_failed")
    entries = {item.get("id"): item for item in listing if isinstance(item, dict)}
    if new_name in entries:
        raise UpgradeError("new_name_in_use")
    entry = entries.get(service.container)
    if entry is None:
        raise UpgradeError("pinned_container_missing")
    seen = _observed(entry)
    published = ([(service.host_address, service.host_port)]
                 if service.host_address is not None else [])
    if (seen["image"] != service.image or seen["ports"] != published
            or seen["state_sources"] != [service.state_source]):
        raise UpgradeError("pin_drift")
    if seen["state"] != "running":
        raise UpgradeError("pinned_container_not_running")
    if new_image == service.image:
        raise UpgradeError("same_image")
    old_image = _image_config(run, service.image)
    new = _image_config(run, new_image)
    if new["platform"] != old_image["platform"]:
        raise UpgradeError("platform_mismatch")
    argv = run_argv(entry, old_image, new_name=new_name, new_image=new_image)
    return Plan(service.name, service.container, new_name, new_image, tuple(argv))


def _wait(probe: Probe, timeout: float, sleep: Callable[[float], None],
          clock: Callable[[], float], interval: float) -> bool:
    deadline = clock() + timeout
    while True:
        try:
            if probe():
                return True
        except Exception:
            pass
        if clock() >= deadline:
            return False
        sleep(interval)


def swap(state_dir: Path, planned: Plan, *, idle: Probe, ready: Probe, run: Runner = _run,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
         idle_timeout: float = 600, ready_timeout: float = 180, hold_minutes: int = 15) -> str:
    """Replace the pinned container; returns a fixed result code."""
    if not _wait(idle, idle_timeout, sleep, clock, 2):
        return "not_idle"                                   # nothing changed
    try:
        supervisor.hold(state_dir, planned.service, hold_minutes)
    except (SupervisorError, OSError, ValueError):
        return "hold_failed"                                # nothing changed
    if run([CONTAINER, "stop", planned.old])[0] != 0:
        supervisor.release(state_dir, planned.service)
        return "stop_failed"
    started = run(list(planned.argv))[0] == 0
    if started and _wait(ready, ready_timeout, sleep, clock, 2):
        supervisor.pin(state_dir, planned.service, planned.new, run=run)   # also releases the hold
        return "swapped"
    run([CONTAINER, "stop", planned.new])
    run([CONTAINER, "delete", planned.new])
    run([CONTAINER, "start", planned.old])
    back = _wait(ready, ready_timeout, sleep, clock, 2)
    supervisor.pin(state_dir, planned.service, planned.old, run=run)
    if not back:
        return "rollback_not_ready"
    return "rolled_back:" + ("run_failed" if not started else "not_ready")


# --- readiness predicates on content-free health --------------------------

def aiport_idle(health: dict) -> bool:
    """Connected, and no smart event is open (every entered event has left)."""
    return (health.get("control_connected") is True
            and type(health.get("smart_events_entered")) is int
            and health.get("smart_events_entered") == health.get("smart_events_left"))


def aiport_ready(health: dict, streams: int) -> bool:
    return (health.get("adopted") is True and health.get("control_connected") is True
            and health.get("active_streams") == streams
            and health.get("streams_with_decoded_frames") == streams)


def aikey_idle(health: dict) -> bool:
    worker = health.get("worker") or {}
    return all(worker.get(k) == 0 for k in ("queued", "active", "pending"))


def aikey_ready(health: dict) -> bool:
    device, search = health.get("device") or {}, health.get("search") or {}
    return device.get("adopted") is True and device.get("connected") is True \
        and search.get("connected") is True


def _probes(args) -> tuple[Probe, Probe]:
    if args.kind == "aiport":
        from .aiport_rollout import read_slot_health

        def health() -> dict:
            return read_slot_health(args.slot_state, args.health_port) or {}
        return (lambda: aiport_idle(health()), lambda: aiport_ready(health(), args.streams))
    import ssl
    from urllib.request import urlopen

    def key_health() -> dict:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE        # loopback-reachable self-signed device API
        with urlopen(args.health_url, timeout=3, context=context) as response:
            return json.loads(response.read(1 << 20))
    return (lambda: aikey_idle(key_health()), lambda: aikey_ready(key_health()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-apple-upgrade")
    parser.add_argument("command", choices=("plan", "swap"))
    parser.add_argument("--state-dir", type=Path, required=True, help="supervisor state")
    parser.add_argument("--service", required=True)
    parser.add_argument("--new-name", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--kind", choices=("aiport", "aikey"))
    parser.add_argument("--slot-state", type=Path, help="AI Port state directory (aiport)")
    parser.add_argument("--health-port", type=int, default=443)
    parser.add_argument("--streams", type=int, help="expected decoding streams (aiport)")
    parser.add_argument("--health-url", help="AI Key /healthz URL (aikey)")
    args = parser.parse_args(argv)
    try:
        planned = plan(args.state_dir, args.service, args.new_name, args.image)
        if args.command == "plan":
            print(json.dumps({"service": planned.service, "old": planned.old,
                              "new": planned.new, "image": planned.image,
                              "run_flags": [a for a in planned.argv[:planned.argv.index(planned.image)]
                                            if a.startswith("-")]}))
            return 0
        if args.kind == "aiport" and (args.slot_state is None or not args.streams):
            raise UpgradeError("aiport swap needs --slot-state and --streams")
        if args.kind == "aikey" and not args.health_url:
            raise UpgradeError("aikey swap needs --health-url")
        if args.kind is None:
            raise UpgradeError("swap needs --kind")
        idle, ready = _probes(args)
        result = swap(args.state_dir, planned, idle=idle, ready=ready)
        print(json.dumps({"service": planned.service, "result": result}))
        return 0 if result == "swapped" else 1
    except (UpgradeError, SupervisorError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, UpgradeError)
                          else exc.__class__.__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
