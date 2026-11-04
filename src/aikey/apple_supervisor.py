"""Bring pinned Apple ``container`` services back after a Mac reboot or exit.

Apple ``container`` has no restart policy, so after a reboot, a crash or a
stopped ``container`` system service the Mac AI Port and AI Key stay down
until someone starts them by hand. This supervisor runs from launchd once a
minute and only ever *starts* an exact pinned container. It never stops,
recreates or restarts a running one, so it cannot interrupt a request in
flight or create a second device identity.

A pinned container is started only when all of these hold:

* its configuration still matches the pin (image, published address and
  port, bind-mounted state directory), so a stale container with the same
  name is not revived;
* no other running container publishes the same address/port or mounts the
  same state directory (the Mac keeps many stopped earlier containers of the
  same identity);
* the published host address is present on an interface;
* the service is not on hold (set for a manual redeploy) and has not already
  been started ``max_starts_per_hour`` times in the last hour.

Only container names, states and fixed reason codes are printed or stored.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .host_control_store import canonical_path, private_directory, writer, container_listing


CONTAINER = "/usr/local/bin/container"
SPEC_FILE = "services.json"
RUNTIME_FILE = "runtime.json"
_NAME = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_CONTAINER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_MAX_HOLD_MINUTES = 120
_HOUR = 3600.0

Runner = Callable[[list[str]], tuple[int, str]]


class SupervisorError(ValueError):
    """The spec or a command is not usable."""


@dataclass(frozen=True)
class Service:
    name: str
    container: str
    image: str
    host_address: str | None      # None: publishes no port (e.g. a sibling-only backend)
    host_port: int | None
    state_source: str             # the container's single bind mount


def _service(raw: object) -> Service:
    if not isinstance(raw, dict) or set(raw) != {
            "name", "container", "image", "host_address", "host_port", "state_source"}:
        raise SupervisorError("Each service needs exactly the pinned fields")
    name, container = raw["name"], raw["container"]
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise SupervisorError("Invalid service name")
    if not isinstance(container, str) or not _CONTAINER_ID.fullmatch(container):
        raise SupervisorError("Invalid container name")
    if not isinstance(raw["image"], str) or not raw["image"] or len(raw["image"]) > 200:
        raise SupervisorError("Invalid image reference")
    address, port = raw["host_address"], raw["host_port"]
    if (address is None) != (port is None):
        raise SupervisorError("Host address and port are set together or not at all")
    if address is not None:
        address = str(ipaddress.IPv4Address(address))
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise SupervisorError("Invalid host port")
    source = raw["state_source"]
    if not isinstance(source, str) or not source.startswith("/") or ".." in source.split("/"):
        raise SupervisorError("The state directory must be an absolute path")
    if str(canonical_path(source, SupervisorError)) != source:
        raise SupervisorError('supervisor_state_path_not_canonical')
    return Service(name, container, raw["image"], address, port, source)


def load_spec(path: Path) -> tuple[list[Service], int]:
    return _decode_spec(_read_object(path))


def _decode_spec(data: dict) -> tuple[list[Service], int]:
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise SupervisorError("Unsupported supervisor spec")
    services = [_service(item) for item in data.get("services", [])]
    names = [s.name for s in services]
    if len(set(names)) != len(names) or len({s.container for s in services}) != len(services):
        raise SupervisorError("Service and container names must be unique")
    if (len({s.state_source for s in services}) != len(services)
            or len({(s.host_address, s.host_port) for s in services if s.host_address is not None})
            != sum(s.host_address is not None for s in services)):
        raise SupervisorError('Pinned services must have distinct identities and published ports')
    roots = [Path(service.state_source) for service in services]
    if any(first in second.parents or second in first.parents
           for index, first in enumerate(roots) for second in roots[index + 1:]):
        raise SupervisorError('Pinned state roots must not overlap')
    limit = data.get("max_starts_per_hour", 3)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
        raise SupervisorError("max_starts_per_hour must be 1..20")
    return services, limit


def _write_private(path: Path, value: dict) -> None:
    from .config import atomic_private
    atomic_private(path, json.dumps(value, indent=1, sort_keys=True, allow_nan=False) + '\n')


def _read_object(path: Path) -> dict:
    from .private_files import read_private as _private
    from .protocol import _json_object
    try:
        return _json_object(_private(path, 65536))
    except FileNotFoundError:
        raise
    except (OSError, ValueError, TypeError, RuntimeError):
        raise SupervisorError('supervisor_state_unavailable') from None


def _outside_guests(directory, roots):
    directory = canonical_path(directory, SupervisorError)
    for value in roots:
        source = canonical_path(value, SupervisorError)
        if directory == source or directory in source.parents or source in directory.parents:
            raise SupervisorError('supervisor_authority_must_be_outside_guest_mounts')
    return directory


@contextmanager
def authority(state_dir, *, roots=(), create=False):
    """Same writer as the host reconciler, never stored in guest-writable state."""
    directory = canonical_path(Path(state_dir).absolute(), SupervisorError)

    def validate():
        found = list(roots)
        if (directory / SPEC_FILE).exists() or (directory / SPEC_FILE).is_symlink():
            found.extend(service.state_source for service in load_spec(directory / SPEC_FILE)[0])
        return _outside_guests(directory, found)
    validate()  # Before even creating a lock/journal, including first pin.
    if create:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_directory(directory, SupervisorError)
    with writer(directory, error=SupervisorError,
                validate=lambda path: private_directory(path, SupervisorError)) as (_, bound):
        validate()
        bound()
        yield directory, bound


def _check_upgrade_fence(directory):
    from .apple_upgrade_state import pending
    if pending(directory) is not None:
        raise SupervisorError('upgrade_needs_original_recovery')


def _observed(entry: dict) -> dict:
    """The pinned fields of one ``container list --format json`` entry."""
    config = entry.get("configuration") or {}
    ports = [(p.get("hostAddress"), p.get("hostPort"))
             for p in config.get("publishedPorts") or [] if isinstance(p, dict)]
    mounts = [m.get("source") for m in config.get("mounts") or []
              if isinstance(m, dict) and isinstance(m.get("type"), dict)
              and "virtiofs" in m["type"]]
    return {"id": entry.get("id"),
            "state": (entry.get("status") or {}).get("state"),
            "image": (config.get("image") or {}).get("reference"),
            "ports": ports, "state_sources": mounts}


def decide(services: list[Service], listing: list[dict], addresses: set[str],
           runtime: dict, limit: int, now: float) -> dict[str, str]:
    """Per service: running, start, or held/blocked with a fixed reason."""
    observed = [_observed(item) for item in listing if isinstance(item, dict)]
    by_id = {item["id"]: item for item in observed}
    result: dict[str, str] = {}
    for service in services:
        mine = by_id.get(service.container)
        if mine is None:
            result[service.name] = "blocked:missing"
            continue
        published = ([(service.host_address, service.host_port)]
                     if service.host_address is not None else [])
        if (mine["image"] != service.image or mine["ports"] != published
                or mine["state_sources"] != [service.state_source]):
            result[service.name] = "blocked:drift"
            continue
        if mine["state"] == "running":
            result[service.name] = "running"
            continue
        hold = runtime.get("holds", {}).get(service.name)
        if isinstance(hold, (int, float)) and hold > now:
            result[service.name] = "held"
            continue
        if any(other["id"] != service.container and other["state"] == "running"
               and (any(port in other["ports"] for port in published)
                    or service.state_source in other["state_sources"])
               for other in observed):
            result[service.name] = "blocked:conflict"
            continue
        if service.host_address is not None and service.host_address not in addresses:
            result[service.name] = "blocked:address_absent"
            continue
        recent = [t for t in runtime.get("starts", {}).get(service.name, [])
                  if isinstance(t, (int, float)) and now - t < _HOUR]
        if len(recent) >= limit:
            result[service.name] = "blocked:crash_loop"
            continue
        result[service.name] = "start"
    return result


def _run(argv: list[str], *, lease_fd=None, root_fd=None, effect=None, timeout=90, engine=None) -> tuple[int, str]:
    """Use the owned guardian, never orphan a native mutating CLI after a crash."""
    helper = Path(__file__).with_name('host_runtime_command.py')
    command = [sys.executable, '-I', str(helper), '--parent-pid', str(os.getpid()),
               '--timeout', str(timeout)]
    if lease_fd is not None:
        command += ['--lease-fd', str(lease_fd)]
    if engine is not None:
        command += ['--engine', engine[0], '--engine-sha256', engine[1]]
    if effect is not None:
        from .host_runtime_command import checked_effect
        checked_effect(effect)
        if root_fd is None or lease_fd is None:
            raise SupervisorError('host_command_effect_context_refused')
        command += ['--root-fd', str(root_fd), '--effect', json.dumps(effect, separators=(',', ':'))]
    command += ['--', *argv]
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR') if key in os.environ}
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,
                                   pass_fds=tuple(fd for fd in (lease_fd, root_fd) if fd is not None),
                                   start_new_session=True, env=env)
        raw, _ = process.communicate(timeout=timeout + 5)
    except BaseException as error:
        if process is not None:
            try:
                process.terminate()  # Guardian collects its own CLI before releasing the lease.
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass  # Still fenced by the guardian's inherited lease, not a cleanup success.
        if isinstance(error, subprocess.TimeoutExpired):
            raise SupervisorError('host_command_guardian_deadline') from None
        if isinstance(error, OSError):
            raise SupervisorError('host_command_exec_failed') from None
        raise
    from .protocol import _json_object
    try:
        if process.returncode != 0 or len(raw) > 8 * 1024 * 1024:
            raise ValueError
        result = _json_object(raw)
        if (set(result) != {'schema', 'spawned', 'command_terminal', 'exit_code', 'error', 'output', 'effect_saved'}
                or result['schema'] != 'aikey-host-command/1' or type(result['spawned']) is not bool
                or result['command_terminal'] is not True or not isinstance(result['output'], str)
                or len(result['output']) > 1024 * 1024):
            raise ValueError
        if result['error'] is not None:
            if result['error'] in {'owner_lost', 'cancelled', 'timeout', 'output_limit', 'exec_failed', 'output_refused', 'observation_failed'}:
                raise SupervisorError('host_command_' + result['error'])
            raise ValueError
        if effect is not None and result['effect_saved'] is not True:
            raise SupervisorError('host_command_effect_receipt_unavailable')
        if effect is None and result['effect_saved'] is not None:
            raise ValueError
        if type(result['exit_code']) is not int or result['spawned'] is not True:
            raise ValueError
        return result['exit_code'], result['output']
    except SupervisorError:
        raise
    except (ValueError, TypeError, KeyError):
        raise SupervisorError('host_command_protocol_refused') from None


def leased_runner(run, bound):
    if run is _run:
        def runner(argv):
            return _run(argv, lease_fd=bound.lease_fd)
        runner.with_effect = lambda argv, effect: _run(argv, lease_fd=bound.lease_fd,
            root_fd=bound.root_fd, effect=effect)
        return runner
    if hasattr(run, 'with_host_lease'):
        return run.with_host_lease(bound)
    return run  # Explicit test/application adapter; no implicit background executor.


def host_addresses(run: Runner) -> set[str]:
    code, output = run(["/sbin/ifconfig"])
    if code != 0:
        return set()
    return set(re.findall(r"\binet (\d+\.\d+\.\d+\.\d+)\b", output))


def _system_running(run: Runner) -> bool:
    code, output = run([CONTAINER, "system", "status"])
    return code == 0 and re.search(r"(?m)^status\s+running\s*$", output) is not None


def _tick_locked(state_dir: Path, run: Runner = _run, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    services, limit = load_spec(state_dir / SPEC_FILE)
    runtime_path = state_dir / RUNTIME_FILE
    runtime = _read_object(runtime_path) if runtime_path.exists() or runtime_path.is_symlink() else {}
    report: dict = {"system": "running", "services": {}}
    from .apple_upgrade_state import pending
    if pending(state_dir) is not None:
        report = {'system': 'upgrade_pending', 'services': {service.name: 'blocked:upgrade_pending' for service in services}}
        runtime['last'] = report
        _write_private(runtime_path, runtime)
        return report
    if not _system_running(run):
        run([CONTAINER, "system", "start", "--disable-kernel-install", "--timeout", "60"])
        if not _system_running(run):
            report["system"] = "unavailable"
            runtime["last"] = report
            _write_private(runtime_path, runtime)
            return report
        report["system"] = "started"
    code, output = run([CONTAINER, "list", "--all", "--format", "json"])
    try:
        listing = json.loads(output) if code == 0 else None
    except json.JSONDecodeError:
        listing = None
    if not isinstance(listing, list):
        report["system"] = "list_failed"
        runtime["last"] = report
        _write_private(runtime_path, runtime)
        return report
    decisions = decide(services, listing, host_addresses(run), runtime, limit, now)
    starts = runtime.setdefault("starts", {})
    for service in services:
        decision = decisions[service.name]
        if decision == "start":
            starts[service.name] = [t for t in starts.get(service.name, [])
                                    if now - t < _HOUR] + [now]
            started, _ = run([CONTAINER, "start", service.container])
            decision = "started" if started == 0 else "blocked:start_failed"
        report["services"][service.name] = decision
    runtime["last"] = report
    _write_private(runtime_path, runtime)
    return report


def _pin_locked(state_dir: Path, name: str, container: str, run: Runner = _run) -> Service:
    """Pin ``name`` to an existing container, read from its live configuration."""
    entries = container_listing(run, CONTAINER)
    entry = entries.get(container) if entries is not None else None
    if entry is None:
        raise SupervisorError("That container does not exist")
    seen = _observed(entry)
    _outside_guests(state_dir, [source for row in entries.values() for source in _observed(row)['state_sources']])
    if len(seen["ports"]) > 1 or len(seen["state_sources"]) != 1:
        raise SupervisorError("The container needs at most one published port and one bind mount")
    address, port = seen["ports"][0] if seen["ports"] else (None, None)
    service = _service({"name": name, "container": container, "image": seen["image"],
                        "host_address": address, "host_port": port,
                        "state_source": seen["state_sources"][0]})
    spec_path = state_dir / SPEC_FILE
    runtime_path = state_dir / RUNTIME_FILE
    if runtime_path.exists() or runtime_path.is_symlink():
        _read_object(runtime_path)  # Refuse unsafe auxiliary state before changing the pin.
    spec = _read_object(spec_path) if spec_path.exists() or spec_path.is_symlink() else {
        "schema": 1, "max_starts_per_hour": 3, "services": []}
    spec["services"] = [s for s in spec["services"] if s.get("name") != name] + [
        service.__dict__.copy()]
    _decode_spec(spec)  # Reject invalid replacements before changing durable state.
    _write_private(spec_path, spec)
    load_spec(spec_path)
    _release_locked(state_dir, name)
    return service


def _hold_locked(state_dir: Path, name: str, minutes: int, now: float | None = None) -> None:
    if not 1 <= minutes <= _MAX_HOLD_MINUTES:
        raise SupervisorError(f"Hold for 1..{_MAX_HOLD_MINUTES} minutes")
    services, _ = load_spec(state_dir / SPEC_FILE)
    if name not in {s.name for s in services}:
        raise SupervisorError("Unknown service")
    path = state_dir / RUNTIME_FILE
    runtime = _read_object(path) if path.exists() or path.is_symlink() else {}
    runtime.setdefault("holds", {})[name] = (time.time() if now is None else now) + minutes * 60
    _write_private(path, runtime)


def _release_locked(state_dir: Path, name: str) -> None:
    path = state_dir / RUNTIME_FILE
    if not path.exists() and not path.is_symlink():
        return
    runtime = _read_object(path)
    runtime.get("holds", {}).pop(name, None)
    runtime.get("starts", {}).pop(name, None)
    _write_private(path, runtime)


def tick(state_dir: Path, run: Runner = _run, now: float | None = None) -> dict:
    with authority(state_dir) as (directory, bound):
        bound()
        return _tick_locked(directory, leased_runner(run, bound), now)


def pin(state_dir: Path, name: str, container: str, run: Runner = _run) -> Service:
    rows = container_listing(run, CONTAINER)
    if rows is None or container not in rows:
        raise SupervisorError('supervisor_pin_observation_unavailable')
    try:
        roots = [source for entry in rows.values() for source in _observed(entry)['state_sources']]
    except (TypeError, AttributeError):
        raise SupervisorError('supervisor_pin_observation_unavailable') from None
    with authority(state_dir, roots=roots, create=True) as (directory, bound):
        _check_upgrade_fence(directory)
        bound()
        return _pin_locked(directory, name, container, run)


def hold(state_dir: Path, name: str, minutes: int, now: float | None = None) -> None:
    with authority(state_dir) as (directory, bound):
        _check_upgrade_fence(directory)
        bound()
        _hold_locked(directory, name, minutes, now)


def release(state_dir: Path, name: str) -> None:
    with authority(state_dir) as (directory, bound):
        _check_upgrade_fence(directory)
        bound()
        _release_locked(directory, name)


def launch_agent(state_dir: Path, python: str, label: str = "com.olli.local-apple-supervise") -> str:
    """launchd agent XML running one ``tick`` a minute and at login."""
    from xml.sax.saxutils import escape
    args = [python, "-m", "aikey.apple_supervisor", "tick", "--state-dir", str(state_dir)]
    items = "".join(f"<string>{escape(a)}</string>" for a in args)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>'
            f"<key>Label</key><string>{escape(label)}</string>"
            f"<key>ProgramArguments</key><array>{items}</array>"
            "<key>RunAtLoad</key><true/><key>StartInterval</key><integer>60</integer>"
            "<key>ProcessType</key><string>Background</string>"
            "</dict></plist>\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-apple-supervise")
    parser.add_argument("command", choices=("tick", "status", "pin", "hold", "release", "plist"))
    parser.add_argument("service", nargs="?")
    parser.add_argument("container", nargs="?")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--minutes", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.command == "tick":
            report = tick(args.state_dir)
            if report["system"] != "running" or any(
                    v != "running" for v in report["services"].values()):
                print(json.dumps(report, sort_keys=True))
        elif args.command == "status":
            path = args.state_dir / RUNTIME_FILE
            print(json.dumps(_read_object(path).get("last") if path.exists() or path.is_symlink() else None))
        elif args.command == "pin":
            if not args.service or not args.container:
                raise SupervisorError("pin needs SERVICE CONTAINER")
            print(json.dumps({"pinned": pin(args.state_dir, args.service, args.container).name}))
        elif args.command == "hold":
            hold(args.state_dir, args.service or "", args.minutes)
        elif args.command == "release":
            release(args.state_dir, args.service or "")
        else:
            print(launch_agent(args.state_dir.resolve(), sys.executable), end="")
    except (SupervisorError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"local-apple-supervise: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
