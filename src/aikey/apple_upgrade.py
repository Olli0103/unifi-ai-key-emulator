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
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import time
import re
from typing import Callable

from . import apple_supervisor as supervisor
from .apple_supervisor import CONTAINER, Runner, SupervisorError, _CONTAINER_ID, _observed, _run

Probe = Callable[[], bool]
_DEFAULT_MTU = 1280
_ALLOWED_INIT = {"arguments", "environment", "executable", "rlimits", "supplementalGroups",
                 "terminal", "user", "workingDirectory"}
OWNER_LABEL = 'org.local-aikey.upgrade'
_OPERATION = re.compile('[0-9a-f]{32}\\Z')


class UpgradeError(ValueError):
    """A fixed, non-secret reason why an upgrade is not planned or not safe."""


@dataclass(frozen=True)
class Plan:
    service: str
    old: str
    new: str
    image: str
    argv: tuple[str, ...]
    configuration_sha256: str = ''
    image_environment: tuple[str, ...] = ()
    image_workdir: str = '/'


def _configuration_hash(entry: dict) -> str:
    config = entry.get('configuration')
    if not isinstance(config, dict):
        raise UpgradeError('configuration_unavailable')
    raw = json.dumps(config, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > 128 * 1024:
        raise UpgradeError('configuration_unavailable')
    return hashlib.sha256(raw).hexdigest()


def _listing(run: Runner) -> dict[str, dict] | None:
    """A complete successful observation; failed/ambiguous output is not absence."""
    from .host_control_store import container_listing
    return container_listing(run, CONTAINER)


def _old_state(rows, planned: Plan, state: str) -> bool:
    try:
        return (rows is not None and planned.old in rows
                and _observed(rows[planned.old])['state'] == state
                and _configuration_hash(rows[planned.old]) == planned.configuration_sha256)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _new_state(rows, planned: Plan, state: str, configuration: str | None = None,
               *, operation: str | None = None) -> bool:
    try:
        if rows is None or planned.new not in rows or _observed(rows[planned.new])['state'] != state:
            return False
        if _observed(rows[planned.new])['image'] != planned.image:
            return False
        if operation is not None and (rows[planned.new].get('configuration') or {}).get('labels', {}).get(OWNER_LABEL) != operation:
            return False
        if configuration is not None and _configuration_hash(rows[planned.new]) != configuration:
            return False
        actual = run_argv(rows[planned.new], {'env': planned.image_environment,
                          'workdir': planned.image_workdir}, new_name=planned.new, new_image=planned.image)
        return tuple(actual) == planned.argv
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _image_config(run: Runner, reference: str) -> dict:
    code, output = run([CONTAINER, "image", "inspect", reference])
    from .protocol import _unique_object, _reject_constant, _finite_float
    try:
        data = (json.loads(output, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
                           parse_float=_finite_float)
                if type(code) is int and code == 0 and isinstance(output, str) and len(output) <= 1024 * 1024 else None)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    variants = data.get("variants") if isinstance(data, dict) else None
    if not isinstance(variants, list) or len(variants) != 1:
        raise UpgradeError("image_unavailable")
    variant = variants[0]
    if not isinstance(variant, dict) or not isinstance(variant.get('config'), dict):
        raise UpgradeError('image_unavailable')
    config = variant['config']
    inner = config.get("config")
    if (not isinstance(inner, dict) or not isinstance(inner.get('Env', []), list)
            or len(inner.get('Env', [])) > 128
            or any(not isinstance(item, str) or len(item) > 4096 or '\x00' in item for item in inner.get('Env', []))
            or not isinstance(inner.get('WorkingDir', '/'), str)
            or config.get('os') != 'linux' or config.get('architecture') not in {'arm64', 'amd64'}):
        raise UpgradeError('image_unavailable')
    return {"platform": (config.get("os"), config.get("architecture")),
            "env": list(inner.get("Env") or []), "workdir": inner.get("WorkingDir") or "/"}


def run_argv(entry: dict, old_image: dict, *, new_name: str, new_image: str) -> list[str]:
    """``container run`` arguments that recreate ``entry`` from ``new_image``."""
    config = entry.get("configuration") or {}
    init = config.get("initProcess") or {}
    problems = []
    for field, reason in (("capAdd", "capabilities"), ("capDrop", "capabilities"),
                          ("sysctls", "sysctls"),
                          ("publishedSockets", "published_sockets")):
        if config.get(field):
            problems.append(reason)
    labels = config.get('labels') or {}
    if (not isinstance(labels, dict) or set(labels) - {OWNER_LABEL}
            or OWNER_LABEL in labels and (not isinstance(labels[OWNER_LABEL], str)
                                         or not _OPERATION.fullmatch(labels[OWNER_LABEL]))):
        problems.append('labels')
    for field in ("rosetta", "ssh", "useInit", "virtualization"):
        if config.get(field):
            problems.append(field)
    if set(init) - _ALLOWED_INIT or init.get("rlimits") or init.get("supplementalGroups") \
            or init.get("terminal"):
        problems.append("process_options")
    if sorted(init.get("environment") or []) != sorted(old_image["env"]):
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
    entries = _listing(run)
    if entries is None:
        raise UpgradeError("list_failed")
    supervisor._outside_guests(Path(state_dir).absolute(),
        [source for row in entries.values() for source in _observed(row)['state_sources']])
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
    if new['env'] != old_image['env'] or new['workdir'] != old_image['workdir']:
        raise UpgradeError('image_defaults_migration_not_supported')
    argv = run_argv(entry, old_image, new_name=new_name, new_image=new_image)
    return Plan(service.name, service.container, new_name, new_image, tuple(argv),
                _configuration_hash(entry), tuple(old_image['env']), old_image['workdir'])


def _wait(probe: Probe, timeout: float, sleep: Callable[[float], None],
          clock: Callable[[], float], interval: float) -> bool:
    import math
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
            or type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0):
        raise UpgradeError('invalid_wait_budget')
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            if probe() is True and clock() < deadline:
                return True
        except Exception:
            pass
        if clock() >= deadline:
            return False
        sleep(min(interval, max(0, deadline - clock())))
    return False


class _Transaction:
    """One host writer and durable intent; no saved argument replay authority."""
    def __init__(self, directory, planned, original_run, bound, configuration=None):
        self.directory, self.planned, self.original_run, self.bound = directory, planned, original_run, bound
        self.value = None
        self.registry = None
        self.failure_code = None
        self.failure_exit_code = None
        self.configuration = configuration

    def begin(self):
        from .private_files import read_private as _private
        from . import apple_upgrade_state as journal
        raw = _private(self.directory / supervisor.SPEC_FILE, 65536)
        revision = hashlib.sha256(_private(self.configuration, 1024 * 1024)).hexdigest() if self.configuration else None
        self.value = journal.prepare(self.directory, self.planned, raw, self.bound, configuration_revision=revision)
        self.registry = hashlib.sha256(raw).hexdigest()
        journal.reserve_configuration(self.value)

    def guard(self):
        from .private_files import read_private as _private
        self.bound()
        if self.value is not None and self.configuration is not None and hashlib.sha256(
                _private(self.configuration, 1024 * 1024)).hexdigest() != self.value['configuration_revision']:
            raise UpgradeError('upgrade_configuration_changed')
        if self.value is not None and hashlib.sha256(
                _private(self.directory / supervisor.SPEC_FILE, 65536)).hexdigest() != self.registry:
            raise UpgradeError('upgrade_registry_changed')

    def step(self, action, state):
        from . import apple_upgrade_state as journal
        previous = next((row for row in self.value['actions'] if row['action'] == action), None)
        if previous is not None and previous['state'] == state == 'observed':
            return
        journal.transition(self.directory, self.value, action, state, self.bound)

    def request(self, action):
        self.guard()
        self.step(action, 'requested')

    def run(self, argv):
        self.guard()
        action = None
        if argv == [CONTAINER, 'stop', self.planned.old]:
            action = 'stop_old'
        elif argv == list(self.planned.argv):
            action = 'run_new'
        elif argv == [CONTAINER, 'stop', self.planned.new]:
            action = 'stop_new'
        elif argv == [CONTAINER, 'delete', self.planned.new]:
            action = 'delete_new'
        elif argv == [CONTAINER, 'start', self.planned.old]:
            action = 'restart_old'
        elif argv[1:] != ['list', '--all', '--format', 'json'] and argv[1:3] != ['image', 'inspect']:
            raise UpgradeError('upgrade_unreviewed_command')
        if self.value is not None and action is not None:
            self.request(action)
        actual = list(argv)
        if self.value is not None and action == 'run_new':
            # A lost client acknowledgement must not turn a foreign same-name
            # container into recovery authority. This own metadata does not
            # change guest arguments, mounts, credentials or permissions.
            actual[3:3] = ['--label', OWNER_LABEL + '=' + self.value['operation']]
        if self.value is not None and action is not None and hasattr(self.original_run, 'with_effect'):
            from .host_runtime_command import argv_digest
            context = {key: self.value[key] for key in ('operation', 'authority', 'authority_device', 'authority_inode')}
            context.update(action=action, argv_sha256=argv_digest(actual))
            result = self.original_run.with_effect(actual, context)
        else:
            result = self.original_run(actual)
        if (not isinstance(result, tuple) or len(result) != 2
                or type(result[0]) is not int or not isinstance(result[1], str)):
            raise UpgradeError('upgrade_command_outcome_unavailable')
        if self.value is not None and action is not None and result[0] == 0:
            self.step(action, 'acknowledged')
        elif self.value is not None and action is not None and self.failure_code is None:
            self.failure_code, self.failure_exit_code = 'host_command_nonzero_exit', result[0]
        return result

    def hold(self, minutes):
        self.request('hold')
        supervisor._hold_locked(self.directory, self.planned.service, minutes)
        self.step('hold', 'acknowledged')
        runtime = supervisor._read_object(self.directory / supervisor.RUNTIME_FILE)
        expires = runtime.get('holds', {}).get(self.planned.service)
        if type(expires) not in (int, float) or expires <= 0:
            raise UpgradeError('upgrade_hold_not_observed')
        self.step('hold', 'observed')

    def release(self):
        self.guard()
        supervisor._release_locked(self.directory, self.planned.service)

    def old_state(self, rows, planned, state):
        result = _old_state(rows, planned, state)
        if result and self.value is not None:
            action = 'stop_old' if state == 'stopped' else 'restart_old'
            previous = next((row for row in self.value['actions'] if row['action'] == action), None)
            if previous is not None and previous['state'] == 'acknowledged':
                self.step(action, 'observed')
        return result

    def new_state(self, rows, planned, state, configuration=None):
        result = _new_state(rows, planned, state, configuration,
                            operation=self.value['operation'] if self.value is not None else None)
        if result and self.value is not None:
            action = 'stop_new' if state == 'stopped' else 'run_new'
            previous = next((row for row in self.value['actions'] if row['action'] == action), None)
            if previous is not None and previous['state'] == 'acknowledged':
                self.step(action, 'observed')
        return result

    def new_absent(self, rows):
        if rows is None or self.planned.new in rows:
            return False
        previous = next((row for row in self.value['actions'] if row['action'] == 'delete_new'), None)
        if previous is not None and previous['state'] == 'acknowledged':
            self.step('delete_new', 'observed')
        if not any(row['action'] == 'new_absent' for row in self.value['actions']):
            self.request('new_absent')
            self.step('new_absent', 'acknowledged')
            self.step('new_absent', 'observed')
        return True

    def pin(self, name):
        from copy import deepcopy
        from .private_files import read_private as _private
        action = 'pin_new' if name == self.planned.new else 'pin_old'
        self.request(action)
        service = supervisor._pin_locked(self.directory, self.planned.service, name, run=self.run)
        expected = deepcopy(self.value['registry_before'])
        expected['services'] = [row for row in expected['services'] if row['name'] != service.name] + [service.__dict__.copy()]
        raw = _private(self.directory / supervisor.SPEC_FILE, 65536)
        if supervisor._read_object(self.directory / supervisor.SPEC_FILE) != expected:
            raise UpgradeError('upgrade_pin_not_observed')
        runtime = supervisor._read_object(self.directory / supervisor.RUNTIME_FILE)
        if self.planned.service in runtime.get('holds', {}):
            raise UpgradeError('upgrade_pin_not_observed')
        self.registry = hashlib.sha256(raw).hexdigest()
        self.step(action, 'acknowledged')
        self.step(action, 'observed')

    def finish(self, result, error=None):
        from . import apple_upgrade_state as journal
        if self.value is None:
            return
        phase = 'committed' if result == 'swapped' else 'rolled_back' if result.startswith('rolled_back:') else 'uncertain'
        failure = None if phase == 'committed' else 'operation_failed' if phase == 'rolled_back' else 'rollback_incomplete'
        if self.failure_code is None and error is not None and isinstance(error, (SupervisorError, UpgradeError)):
            code = error.args[0] if error.args else None
            if isinstance(code, str) and code in journal.FAILURE_CODES:
                self.failure_code = code
        if self.failure_code is None and result in journal.FAILURE_CODES:
            self.failure_code = result
        if phase in journal.NORMAL_TERMINAL:
            journal.reserve_configuration(self.value, settle=True)
        journal.finish(self.directory, self.value, phase, failure, self.bound,
            failure_code=self.failure_code, failure_exit_code=self.failure_exit_code)


def _swap_locked(state_dir: Path, planned: Plan, *, transaction, idle: Probe, ready: Probe, run: Runner,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
         idle_timeout: float = 600, ready_timeout: float = 180, hold_minutes: int = 15) -> str:
    """Replace the pinned container; returns a fixed result code."""
    if not _wait(idle, idle_timeout, sleep, clock, 2):
        return "not_idle"                                   # nothing changed
    # Waiting for idle is not a lease on the earlier plan. Every reviewed
    # setting must still match before the first mutation.
    try:
        if plan(state_dir, planned.service, planned.new, planned.image, run=run) != planned:
            return 'plan_changed'
    except (UpgradeError, SupervisorError, OSError, ValueError):
        return 'plan_changed'
    transaction.begin()
    try:
        transaction.hold(hold_minutes)
    except (SupervisorError, OSError, ValueError):
        return "hold_failed"  # A possibly partial hold stays fenced by the intent.
    if run([CONTAINER, "stop", planned.old])[0] != 0:
        transaction.release()
        return "stop_failed"
    if not transaction.old_state(_listing(run), planned, 'stopped'):
        return 'stop_outcome_uncertain'
    started = run(list(planned.argv))[0] == 0
    rows = _listing(run)
    if started and (not transaction.old_state(rows, planned, 'stopped') or not transaction.new_state(rows, planned, 'running')):
        return 'run_outcome_uncertain'
    new_configuration = _configuration_hash(rows[planned.new]) if started else None
    if started:
        transaction.request('ready_new')
    if started and _wait(ready, ready_timeout, sleep, clock, 2):
        transaction.step('ready_new', 'acknowledged')
        rows = _listing(run)
        if (not transaction.old_state(rows, planned, 'stopped')
                or not transaction.new_state(rows, planned, 'running', new_configuration)):
            return 'run_outcome_uncertain'
        transaction.step('ready_new', 'observed')
        transaction.pin(planned.new)
        return "swapped"
    if not started:
        # A failed/lost run acknowledgement is not ownership of this name.
        # Never stop/delete a target that may have appeared independently,
        # and never restart the old identity beside an uncertain new owner.
        return "run_outcome_uncertain"  # Even current absence is not proof a late run cannot arrive.
    transaction.step('ready_new', 'failed')
    transaction.failure_code = transaction.failure_code or 'candidate_readiness_unverified'
    # Retain the hold and both recovery objects unless compensation is
    # positively observed. Never start the old identity beside an uncertain
    # new owner merely because a CLI acknowledged a request.
    rows = _listing(run)
    if not transaction.old_state(rows, planned, 'stopped') or not transaction.new_state(rows, planned, 'running', new_configuration):
        return 'rollback_outcome_uncertain'
    stopped = run([CONTAINER, "stop", planned.new])[0]
    if stopped != 0 or not transaction.new_state(_listing(run), planned, 'stopped', new_configuration):
        return 'rollback_outcome_uncertain'
    removed = run([CONTAINER, "delete", planned.new])[0]
    rows = _listing(run)
    if removed != 0 or not transaction.old_state(rows, planned, 'stopped') or not transaction.new_absent(rows):
        return 'rollback_outcome_uncertain'
    restarted = run([CONTAINER, "start", planned.old])[0]
    rows = _listing(run)
    if restarted != 0 or not transaction.old_state(rows, planned, 'running') or not transaction.new_absent(rows):
        return 'rollback_outcome_uncertain'
    transaction.request('ready_old')
    back = _wait(ready, ready_timeout, sleep, clock, 2)
    if not back:
        return "rollback_not_ready"
    transaction.step('ready_old', 'acknowledged')
    rows = _listing(run)
    if not transaction.old_state(rows, planned, 'running') or not transaction.new_absent(rows):
        return 'rollback_outcome_uncertain'
    transaction.step('ready_old', 'observed')
    transaction.pin(planned.old)
    return "rolled_back:" + ("run_failed" if not started else "not_ready")


def swap(state_dir: Path, planned: Plan, *, idle: Probe, ready: Probe, run: Runner = _run,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
         idle_timeout: float = 600, ready_timeout: float = 180, hold_minutes: int = 15,
         configuration: Path | None = None) -> str:
    """Durable one-writer swap; an interrupted intent fences ordinary supervision."""
    from . import apple_upgrade_state as journal
    if run is _run and configuration is None:
        return 'configuration_reservation_required'
    with supervisor.authority(state_dir) as (directory, bound), ExitStack() as scope:
        if journal.pending(directory) is not None:
            return 'recovery_required'
        if configuration is not None:
            from .host_control_store import configuration_writer
            selected = next((row for row in supervisor.load_spec(directory / supervisor.SPEC_FILE)[0]
                             if row.name == planned.service), None)
            if selected is None or Path(configuration).absolute() != Path(selected.state_source) / 'config.json':
                return 'configuration_profile_mismatch'
            scope.enter_context(configuration_writer(Path(configuration).absolute()))
        transaction = _Transaction(directory, planned, supervisor.leased_runner(run, bound), bound, configuration)
        try:
            result = _swap_locked(directory, planned, transaction=transaction, idle=idle, ready=ready,
                                  run=transaction.run, sleep=sleep, clock=clock,
                                  idle_timeout=idle_timeout, ready_timeout=ready_timeout, hold_minutes=hold_minutes)
            transaction.finish(result)
            return result
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            try:
                transaction.finish('upgrade_outcome_uncertain', error)
            except (OSError, ValueError, TypeError, RuntimeError):
                pass  # Keep the last durable intent; never continue compensation blindly.
            return 'upgrade_outcome_uncertain'


# --- readiness predicates on content-free health --------------------------

def aiport_idle(health: dict) -> bool:
    """Connected, and no smart event is open (every entered event has left)."""
    return (health.get("control_connected") is True
            and type(health.get("smart_events_entered")) is int
            and type(health.get("smart_events_left")) is int
            and health['smart_events_entered'] >= 0
            and health.get("smart_events_entered") == health.get("smart_events_left"))


def aiport_ready(health: dict, streams: int) -> bool:
    return (health.get("adopted") is True and health.get("control_connected") is True
            and health.get("active_streams") == streams
            and health.get("streams_with_decoded_frames") == streams)


def aikey_idle(health: dict) -> bool:
    worker = health.get("worker") or {}
    return all(type(worker.get(k)) is int and worker[k] == 0 for k in ("queued", "active", "pending"))


def aikey_ready(health: dict) -> bool:
    device, search = health.get("device") or {}, health.get("search") or {}
    return device.get("adopted") is True and device.get("connected") is True \
        and search.get("connected") is True


def _probes(args, service) -> tuple[Probe, Probe]:
    """Fresh own CA+exact-leaf HTTPS health and loaded configuration binding."""
    import asyncio
    from .admin_runtime import RuntimeObserver, _normalize
    from .startup_configuration import revision
    from .private_files import read_private as _private
    from .host_control_store import canonical_path
    directory = canonical_path(Path(service.state_source), UpgradeError)
    if args.slot_state is not None and canonical_path(args.slot_state.absolute(), UpgradeError) != directory:
        raise UpgradeError('health_profile_state_mismatch')
    if service.host_address is None:
        raise UpgradeError('health_profile_has_no_published_endpoint')
    target = f'https://{service.host_address}:{service.host_port}/healthz'
    if args.health_url is not None and args.health_url != target:
        raise UpgradeError('health_profile_endpoint_mismatch')
    if args.health_port is not None and args.health_port != service.host_port:
        raise UpgradeError('health_profile_endpoint_mismatch')
    expected = hashlib.sha256(_private(directory / 'config.json', 1 << 20)).hexdigest()

    def health() -> dict:
        async def read():
            observer = RuntimeObserver({'profile': (args.kind, directory)}, {'profile': target})
            async with asyncio.timeout(4):
                value = await observer._request('profile')
            _normalize(args.kind, value)
            from .host_activation import SCHEMA
            if value.get('host_activation_schema') != SCHEMA:
                raise UpgradeError('health_profile_activation_contract_unavailable')
            if (revision(value.get('startup_configuration')) != expected
                    or hashlib.sha256(_private(directory / 'config.json', 1 << 20)).hexdigest() != expected):
                raise UpgradeError('health_profile_configuration_mismatch')
            return value
        return asyncio.run(read())
    if args.kind == 'aiport':
        return lambda: aiport_idle(health()), lambda: aiport_ready(health(), args.streams)
    return lambda: aikey_idle(health()), lambda: aikey_ready(health())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-apple-upgrade")
    parser.add_argument("command", choices=("plan", "swap", "recover", "status"))
    parser.add_argument("--state-dir", type=Path, required=True, help="supervisor state")
    parser.add_argument("--service", required=True)
    parser.add_argument("--new-name")
    parser.add_argument("--image")
    parser.add_argument("--kind", choices=("aiport", "aikey"))
    parser.add_argument("--slot-state", type=Path, help="AI Port state directory (aiport)")
    parser.add_argument("--health-port", type=int)
    parser.add_argument("--streams", type=int, help="expected decoding streams (aiport)")
    parser.add_argument("--health-url", help="AI Key /healthz URL (aikey)")
    args = parser.parse_args(argv)
    try:
        from . import apple_upgrade_state as journal
        if args.command in {'status', 'recover'}:
            if args.new_name is not None or args.image is not None:
                raise UpgradeError('recovery_uses_original_intent')
            with supervisor.authority(args.state_dir) as (directory, _):
                value = journal.read(directory)
                if value is not None and value['plan']['service'] != args.service:
                    raise UpgradeError('recovery_service_mismatch')
                if args.command == 'status':
                    print(json.dumps({'service': args.service, 'phase': value['phase'] if value else 'none',
                                      'recovery_required': journal.pending(directory) is not None,
                                      'failure_code': value['failure_code'] if value else None,
                                      'failure_exit_code': value['failure_exit_code'] if value else None}))
                    return 0
                if value is None or value['phase'] in journal.TERMINAL:
                    print(json.dumps({'service': args.service, 'result': 'no_pending_upgrade'}))
                    return 0
                planned = Plan(**dict(value['plan'], argv=tuple(value['plan']['argv']),
                                       image_environment=tuple(value['plan']['image_environment'])))
        else:
            if args.new_name is None or args.image is None:
                raise UpgradeError('plan_requires_new_name_and_image')
            planned = plan(args.state_dir, args.service, args.new_name, args.image)
        if args.command == "plan":
            print(json.dumps({"service": planned.service, "old": planned.old,
                              "new": planned.new, "image": planned.image,
                              "run_flags": [a for a in planned.argv[:planned.argv.index(planned.image)]
                                            if a.startswith("-")]}))
            return 0
        if args.kind == "aiport" and (type(args.streams) is not int or not 1 <= args.streams <= 5):
            raise UpgradeError('aiport_requires_expected_streams')
        if args.kind is None:
            raise UpgradeError("swap needs --kind")
        services, _ = supervisor.load_spec(args.state_dir / supervisor.SPEC_FILE)
        service = next((row for row in services if row.name == args.service), None)
        if service is None:
            raise UpgradeError('unknown_service')
        expected_entrypoint = 'local-aiport-candidate' if args.kind == 'aiport' else 'local-aikey'
        index = planned.argv.index('--entrypoint')
        if (planned.argv[index + 1] != expected_entrypoint
                or '--config' not in planned.argv[index + 3:]
                or planned.argv[planned.argv.index('--config', index + 3) + 1] != '/state/config.json'):
            raise UpgradeError('health_profile_kind_or_config_mismatch')
        idle, ready = _probes(args, service)
        if args.command == 'recover':
            from .apple_upgrade_recovery import recover
            result = recover(args.state_dir, args.service, ready=ready, expected_operation=value['operation'])
        else:
            result = swap(args.state_dir, planned, idle=idle, ready=ready,
                          configuration=Path(service.state_source) / 'config.json')
        print(json.dumps({"service": planned.service, "result": result}))
        return 0 if result in {'swapped', 'recovered_old'} else 1
    except (UpgradeError, SupervisorError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, UpgradeError)
                          else exc.__class__.__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
