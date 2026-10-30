"""One-use host-approved maintenance runbooks, outside the admin container.

The web process can submit only a published plan digest and request ID. It
cannot install runbooks, choose paths/arguments, or obtain host execution
authority. Host plans and the execution ledger must NEVER be mounted there.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import selectors
import stat
import subprocess
import sys
import tempfile
import time

from .config import atomic_private
from .config_store import ConfigurationStore
from .protocol import _json_object
from .process_exit import ExitWatcher, supported as exit_watcher_supported

_ID = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST = re.compile(r"[0-9a-f]{32}\Z")
_KINDS = {"backup", "restore", "update"}
_STATES = {"started", "succeeded", "rejected", "rolled_back", "uncertain"}
_SCHEMA = "aikey-maintenance/1"


class MaintenanceError(ValueError):
    """Fixed errors only; runbook output and private host paths are not echoed."""


def _encode(value):
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path, limit=1024 * 1024):
    try:
        return ConfigurationStore._read_regular_bytes(Path(path), limit)
    except (OSError, ValueError, RuntimeError):
        raise MaintenanceError("maintenance_file_unavailable") from None


def _directory(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise MaintenanceError("maintenance_directory_not_private")
    return path


def initialize(host: Path, bridge: Path):
    """Host-only setup: create separate authority and constrained IPC roots."""
    host, bridge = Path(host).absolute(), Path(bridge).absolute()
    if host == bridge or host in bridge.parents or bridge in host.parents:
        raise MaintenanceError("maintenance_roots_must_be_separate")
    for root in (host, bridge):
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _directory(root)
    for path in (host / "plans", host / "ledger", host / "staging", bridge / "catalog",
                 bridge / "requests", bridge / "receipts"):
        path.mkdir(mode=0o700, exist_ok=True)
        _directory(path)


def _identifier(value, pattern=_ID):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise MaintenanceError("invalid_maintenance_identifier")
    return value


def prepare(host: Path, bridge: Path, *, script: Path, inputs: dict,
            kind: str, profile: str, revision: str, timeout: int,
            expected_effect: str, guard_files: list[str], resources: dict[str, str] | None = None) -> str:
    """Publish one frozen operator-qualified Python runbook; never a web action.

    The runbook must perform its own locked preflight, current-state checks,
    backup/rollback and downstream read-back. It receives only these frozen
    host inputs on stdin and emits the fixed result protocol documented below.
    """
    initialize(host, bridge)
    if (not isinstance(kind, str) or kind not in _KINDS or not isinstance(profile, str)
            or not re.fullmatch(r"aikey|aiport(?::[a-z0-9-]{1,32})?", profile)
            or type(timeout) is not int or not 1 <= timeout <= 1800
            or not isinstance(inputs, dict) or len(_encode(inputs)) > 256 * 1024
            or not isinstance(expected_effect, str)
            or expected_effect not in {"read_only", "private_backup_export", "isolated_restore", "offline_replace"}
            or not isinstance(guard_files, list) or not 1 <= len(guard_files) <= 64):
        raise MaintenanceError("invalid_maintenance_plan")
    _identifier(revision)
    _json_object(_encode(inputs))
    source = _read(script)
    resources = {} if resources is None else resources
    if (not isinstance(resources, dict) or len(resources) > 256
            or any(not isinstance(name, str) or not re.fullmatch(r"(?:[a-z_]+/)*[a-z_]+\.py", name)
                   or name == "runbook.py" or not isinstance(value, str)
                   for name, value in resources.items())
            or sum(len(value.encode()) for value in resources.values()) > 8 * 1024 * 1024):
        raise MaintenanceError("invalid_maintenance_resources")
    guards = []
    for name in guard_files:
        if not isinstance(name, str) or not Path(name).is_absolute():
            raise MaintenanceError("invalid_maintenance_guard")
        guards.append({"path": name, "sha256": _digest(_read(name))})
    if revision not in {guard["sha256"] for guard in guards}:
        raise MaintenanceError("maintenance_revision_not_guarded")
    # The exact source/inputs/interpreter/version are bound into the one-use ID.
    interpreter = Path(sys.executable).resolve()
    plan = {"schema": _SCHEMA, "kind": kind, "profile": profile,
            "revision": revision, "timeout": timeout, "expected_effect": expected_effect,
            "script": source.decode("utf-8"), "inputs": inputs, "guards": guards, "resources": resources,
            "interpreter": str(interpreter), "interpreter_sha256": _digest(_read(interpreter, 64 * 1024 * 1024))}
    raw = _encode(plan)
    identifier = _digest(raw)
    if len(list((Path(host) / "plans").iterdir())) >= 128 and not (Path(host) / "plans" / (identifier + ".json")).exists():
        raise MaintenanceError("maintenance_plan_capacity")
    atomic_private(Path(host) / "plans" / (identifier + ".json"), raw)
    public = {key: plan[key] for key in ("kind", "profile", "revision", "timeout", "expected_effect")}
    public.update(schema=_SCHEMA, operation=identifier, one_use=True,
                  scope="host_approved_frozen_runbook", worker_liveness="not_asserted")
    atomic_private(Path(bridge) / "catalog" / (identifier + ".json"), _encode(public))
    return identifier


class Bridge:
    def __init__(self, root: Path):
        self.root = Path(root)

    def catalog(self):
        _directory(self.root / "catalog")
        files = sorted((self.root / "catalog").iterdir())
        if len(files) > 128:
            raise MaintenanceError("maintenance_catalog_over_capacity")
        result = []
        for path in files:
            _identifier(path.stem)
            if path.suffix != ".json":
                raise MaintenanceError("invalid_maintenance_catalog")
            row = _json_object(_read(path, 4096))
            if (set(row) != {"schema", "kind", "profile", "revision", "timeout", "expected_effect",
                             "operation", "one_use", "scope", "worker_liveness"}
                    or row["schema"] != _SCHEMA or row["operation"] != path.stem
                    or not isinstance(row["kind"], str) or row["kind"] not in _KINDS or row["one_use"] is not True
                    or not isinstance(row["profile"], str)
                    or not re.fullmatch(r"aikey|aiport(?::[a-z0-9-]{1,32})?", row["profile"])
                    or type(row["timeout"]) is not int or not 1 <= row["timeout"] <= 1800
                    or not isinstance(row["expected_effect"], str)
                    or row["expected_effect"] not in {"read_only", "private_backup_export", "isolated_restore", "offline_replace"}
                    or row["scope"] != "host_approved_frozen_runbook"
                    or row["worker_liveness"] != "not_asserted"):
                raise MaintenanceError("invalid_maintenance_catalog")
            _identifier(row["revision"])
            result.append(row)
        return result

    def submit(self, operation, revision, request_id):
        _identifier(operation)
        _identifier(revision)
        _identifier(request_id, _REQUEST)
        rows = {row["operation"]: row for row in self.catalog()}
        if operation not in rows or rows[operation]["revision"] != revision:
            raise MaintenanceError("maintenance_plan_conflict")
        directory = _directory(self.root / "requests")
        if len(list(directory.iterdir())) >= 128 and not (directory / (request_id + ".json")).exists():
            raise MaintenanceError("maintenance_queue_full")
        raw = _encode({"schema": _SCHEMA, "operation": operation,
                       "revision": revision, "request": request_id})
        path = directory / (request_id + ".json")
        with tempfile.NamedTemporaryFile(prefix=".pending-", dir=directory) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
            try:
                os.link(output.name, path)  # Atomic publication, never replace a prior ID.
            except FileExistsError:
                if _read(path, 4096) != raw:
                    raise MaintenanceError("maintenance_request_conflict") from None
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return {"request": request_id, "operation": operation, "state": "queued",
                "executed": False, "automatic_retry": False}

    def receipt(self, request_id):
        _identifier(request_id, _REQUEST)
        path = self.root / "receipts" / (request_id + ".json")
        if not path.exists():
            return {"request": request_id, "state": "not_reported", "executed": None}
        row = _json_object(_read(path, 4096))
        if (set(row) != {"schema", "request", "operation", "state", "automatic_retry"}
                or row["schema"] != _SCHEMA or row["request"] != request_id
                or not isinstance(row["state"], str) or row["state"] not in _STATES
                or row["automatic_retry"] is not False):
            raise MaintenanceError("maintenance_receipt_invalid")
        _identifier(row["operation"])
        return row


@contextmanager
def _owner(host):
    fd = os.open(Path(host) / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise MaintenanceError("maintenance_lock_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MaintenanceError("maintenance_worker_busy") from None
        yield fd
    finally:
        os.close(fd)


def _execute(plan, lease, host):
    """Own the entire process group; never stream raw runbook output to HTTP."""
    interpreter = Path(plan["interpreter"])
    if not exit_watcher_supported():
        return "rejected"  # No child effect without a non-reaping exit watcher.
    if any(_digest(_read(row["path"])) != row["sha256"] for row in plan["guards"]):
        return "rejected"
    if _digest(_read(interpreter, 64 * 1024 * 1024)) != plan["interpreter_sha256"]:
        return "rejected"
    with tempfile.TemporaryDirectory(prefix="run-", dir=_directory(Path(host) / "staging")) as directory:
        root = Path(directory)
        script = root / "runbook.py"
        atomic_private(script, plan["script"].encode())
        for name, content in plan["resources"].items():
            resource = root / name
            resource.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_private(resource, content.encode())
        with tempfile.TemporaryFile() as inputs:
            inputs.write(_encode(plan["inputs"]))
            inputs.seek(0)
            process = subprocess.Popen([str(interpreter), "-I", str(script)], stdin=inputs,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=root, start_new_session=True,
                pass_fds=(lease,), env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                      "AIKEY_MAINTENANCE_LEASE_FD": str(lease)})
            deadline = time.monotonic() + plan["timeout"]
            exceeded, output, errors, eofs = False, bytearray(), bytearray(), set()
            watcher = None
            with selectors.DefaultSelector() as selector:
                def consume(pipe, target):
                    nonlocal exceeded
                    try:
                        raw = os.read(pipe.fileno(), min(4096, 65537 - len(target)))
                    except BlockingIOError:
                        return None
                    if not raw:
                        eofs.add(pipe.fileno())
                        return False
                    target.extend(raw)
                    if len(target) > 65536:
                        exceeded = True
                    return True
                try:
                    # Linux waitid(WNOWAIT), or native macOS kqueue NOTE_EXIT.
                    # The leader stays unreaped through the last group signal.
                    watcher = ExitWatcher(process.pid)
                    for pipe, target in ((process.stdout, output), (process.stderr, errors)):
                        os.set_blocking(pipe.fileno(), False)
                        selector.register(pipe, selectors.EVENT_READ, target)
                    while selector.get_map() or not watcher.exited():
                        if time.monotonic() >= deadline:
                            exceeded = True
                            break
                        for key, _ in selector.select(.025):
                            if consume(key.fileobj, key.data) is False:
                                selector.unregister(key.fileobj)
                        if exceeded or watcher.exited():
                            break
                finally:
                    # Drain already-available bytes before deciding teardown is
                    # needed. macOS killpg on an empty zombie-only group gives
                    # EPERM, not ESRCH; completed + both EOF needs no signal.
                    for pipe, target in ((process.stdout, output), (process.stderr, errors)):
                        while not exceeded and consume(pipe, target):
                            pass
                    try:
                        if exceeded or watcher is None or not watcher.exited() or len(eofs) != 2:
                            for sig in (signal.SIGTERM, signal.SIGKILL):
                                try:
                                    os.killpg(process.pid, sig)
                                except ProcessLookupError:
                                    break
                                except PermissionError:
                                    exceeded = True  # No claim of successful containment.
                                    break
                                time.sleep(.05)
                        process.wait(timeout=2)
                    except BaseException:
                        process.stdout.close()
                        process.stderr.close()
                        raise
                    finally:
                        if watcher is not None:
                            watcher.close()
                    eof_deadline = time.monotonic() + .25
                    for pipe, target in ((process.stdout, output), (process.stderr, errors)):
                        try:
                            while not exceeded:
                                consumed = consume(pipe, target)
                                if consumed is False:
                                    break
                                if consumed is None:
                                    if time.monotonic() >= eof_deadline:
                                        exceeded = True
                                        break
                                    time.sleep(.01)
                        finally:
                            pipe.close()
            if exceeded or process.returncode != 0:
                return "uncertain"
            try:
                result = _json_object(bytes(output))
                if (set(result) != {"schema", "outcome"}
                        or result["schema"] != "aikey-maintenance-result/1"
                        or not isinstance(result["outcome"], str)
                        or result["outcome"] not in _STATES - {"started"}):
                    return "uncertain"
                return result["outcome"]
            except (TypeError, ValueError):
                return "uncertain"


def work_once(host: Path, bridge: Path):
    """Host-only serialized worker; a crashed started plan is never replayed."""
    initialize(host, bridge)
    handled = 0
    with _owner(host) as lease:
        published = {row["operation"] for row in Bridge(bridge).catalog()}
        unresolved = set()
        ledgers = list((Path(host) / "ledger").iterdir())
        if len(ledgers) > 128:
            raise MaintenanceError("maintenance_ledger_over_capacity")
        for ledger in ledgers:
            _identifier(ledger.stem)
            row = _json_object(_read(ledger, 4096))
            if (ledger.suffix != ".json" or set(row) != {"state"}
                    or not isinstance(row["state"], str) or row["state"] not in _STATES):
                raise MaintenanceError("maintenance_ledger_invalid")
            if row["state"] in {"started", "uncertain"}:
                unresolved.add(ledger.stem)
        paths = sorted(p for p in (Path(bridge) / "requests").iterdir()
                       if not p.name.startswith(".pending-"))
        if len(paths) > 128:
            raise MaintenanceError("maintenance_queue_full")
        for path in paths:
            _identifier(path.stem, _REQUEST)
            if (Path(bridge) / "receipts" / path.name).exists():
                Bridge(bridge).receipt(path.stem)
                continue
            request = _json_object(_read(path, 4096))
            if (path.suffix != ".json" or set(request) != {"schema", "request", "operation", "revision"}
                    or request["schema"] != _SCHEMA or request["request"] != path.stem):
                raise MaintenanceError("maintenance_request_invalid")
            operation = _identifier(request["operation"])
            if unresolved and operation not in unresolved:
                continue  # An unknown earlier host effect blocks ALL new plans.
            _identifier(request["revision"])
            raw = _read(Path(host) / "plans" / (operation + ".json"), 16 * 1024 * 1024)
            if _digest(raw) != operation:
                raise MaintenanceError("maintenance_plan_conflict")
            plan = _json_object(raw)
            if plan["revision"] != request["revision"]:
                raise MaintenanceError("maintenance_plan_conflict")
            ledger = Path(host) / "ledger" / (operation + ".json")
            if ledger.exists():
                prior = _json_object(_read(ledger, 4096))
                state = prior["state"] if prior["state"] != "started" else "uncertain"
                if state not in _STATES:
                    raise MaintenanceError("maintenance_ledger_invalid")
            elif operation not in published:
                state = "rejected"  # Withdrawn before admission: no runbook effect.
            else:
                atomic_private(ledger, _encode({"state": "started"}))
                state = "uncertain"
                try:
                    state = _execute(plan, lease, host)
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
            atomic_private(ledger, _encode({"state": state}))
            receipt = {"schema": _SCHEMA, "request": request["request"], "operation": operation,
                       "state": state, "automatic_retry": False}
            atomic_private(Path(bridge) / "receipts" / path.name, _encode(receipt))
            handled += 1
            break  # At most one bounded runbook per host invocation.
    return ({"handled": handled, "blocked": "prior_outcome_uncertain"}
            if unresolved and not handled else {"handled": handled})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "prepare-search", "prepare-state", "prepare-apple-update", "work-once"])
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--plan", type=Path, help="Private host prepare definition JSON; never supplied through HTTP")
    args = parser.parse_args(argv)
    if args.command == "work-once":
        def interrupted(signum, _):
            raise SystemExit(128 + signum)
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, interrupted)
    try:
        if args.command in {"prepare", "prepare-search", "prepare-state", "prepare-apple-update"}:
            spec = _json_object(_read(args.plan))
            if args.command == "prepare-search":
                from .maintenance_runbooks import prepare_search
                for key in ("config", "engine", "output", "manifest", "profile"):
                    if spec.get(key) is not None:
                        spec[key] = Path(spec[key])
                result = {"operation": prepare_search(args.host, args.bridge, **spec)}
            elif args.command == "prepare-state":
                from .maintenance_runbooks import prepare_state
                for key in ("directory", "archive", "engine", "destination"):
                    if spec.get(key) is not None:
                        spec[key] = Path(spec[key])
                result = {"operation": prepare_state(args.host, args.bridge, **spec)}
            elif args.command == "prepare-apple-update":
                from .maintenance_runbooks import prepare_apple_update
                for key in ("config", "state_dir", "certificate", "recovery_archive", "engine"):
                    if spec.get(key) is not None:
                        spec[key] = Path(spec[key])
                result = {"operation": prepare_apple_update(args.host, args.bridge, **spec)}
            else:
                spec["script"] = Path(spec["script"])
                result = {"operation": prepare(args.host, args.bridge, **spec)}
        else:
            result = work_once(args.host, args.bridge)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        print('{"error":"maintenance_unavailable"}')
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
