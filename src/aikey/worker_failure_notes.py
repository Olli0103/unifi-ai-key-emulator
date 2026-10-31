"""Bounded historical exception categories, independent of replay tombstones.

No exception text, media, caption, URL or raw camera/event identifier is stored.
These notes neither authorize replay nor assert the current job/native outcome.
Old archives without a note remain unknown. Nothing is automatically deleted.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import secrets
import time

from .protocol import _json_object

SCHEMA = "aikey-worker-failure-note/1"
PHASE_SCHEMA = "aikey-worker-failure-note/2"
PHASES = frozenset({"queue_expired", "model_work_fence", "execution", "media_fetch",
    "media_decode", "inference_wait", "inference_request", "inference_response",
    "speech_request", "callback"})
SCOPE = "first_observed_worker_exception_per_job_and_input_not_current_outcome"
CAPACITY = 1024
CATEGORIES = frozenset({"timeout", "controller_media_http", "controller_callback_http",
    "vision_provider_error", "speech_provider_error", "local_clip_error", "face_store_error",
    "http_transport_error", "os_error", "worker_error", "unclassified_exception"})
STATES = frozenset({"failed", "completed", "callback_sending", "callback_uncertain"})
OUTCOMES = frozenset({"written", "already_recorded", "busy", "capacity", "unavailable"})
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = {"schema", "scope", "job_id", "input_fingerprint", "observed_at", "journal_state",
           "category", "http_status"}


def _identifier(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError("invalid_failure_note_identifier")
    return value


def _name(job_id, fingerprint):
    return hashlib.sha256((SCHEMA + "\0" + _identifier(job_id) + "\0"
                           + _identifier(fingerprint)).encode("ascii")).hexdigest() + ".json"


def _validate(value):
    if not isinstance(value, dict):
        raise ValueError("invalid_failure_note")
    schema = value.get("schema")
    expected = _FIELDS | {"phase"} if schema == PHASE_SCHEMA else _FIELDS
    if (set(value) != expected
            or schema not in {SCHEMA, PHASE_SCHEMA} or value["scope"] != SCOPE
            or not isinstance(value["category"], str) or value["category"] not in CATEGORIES
            or not isinstance(value["journal_state"], str) or value["journal_state"] not in STATES
            or type(value["observed_at"]) not in (int, float)
            or not 0 < value["observed_at"] <= time.time() + 300
            or not math.isfinite(value["observed_at"])):
        raise ValueError("invalid_failure_note")
    if schema == PHASE_SCHEMA and (not isinstance(value["phase"], str) or value["phase"] not in PHASES):
        raise ValueError("invalid_failure_note_phase")
    _name(value["job_id"], value["input_fingerprint"])
    status = value["http_status"]
    if value["category"] in {"controller_media_http", "controller_callback_http"}:
        if type(status) is not int or not 100 <= status <= 599:
            raise ValueError("invalid_failure_note_http_status")
    elif status is not None:
        raise ValueError("invalid_failure_note_http_status")
    return value


def _read(root_fd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=root_fd)
    with os.fdopen(fd, "rb") as source:
        meta = os.fstat(source.fileno())
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                or meta.st_mode & 0o077 or not 0 < meta.st_size <= 2048):
            raise ValueError("failure_note_unavailable")
        raw = source.read(2049)
    if len(raw) > 2048:
        raise ValueError("failure_note_unavailable")
    return _validate(_json_object(raw))


class FailureNotes:
    def __init__(self, state: Path):
        # Merely constructing a worker or reading its health creates no note.
        self.state = Path(state)
        self.root = self.state / "worker-failure-notes"

    def _directory(self, *, create):
        # Pin the directories once. Later path replacement cannot retarget any
        # read, lock, publication or cleanup to a different directory.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        parent_fd = os.open(self.state, flags)
        root_fd = -1
        try:
            parent = os.fstat(parent_fd)
            if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
                raise ValueError("failure_note_directory_unavailable")
            created = False
            if create:
                try:
                    os.mkdir("worker-failure-notes", mode=0o700, dir_fd=parent_fd)
                    created = True
                except FileExistsError:
                    pass
            root_fd = os.open("worker-failure-notes", flags, dir_fd=parent_fd)
            meta = os.fstat(root_fd)
            if meta.st_uid != os.geteuid():
                raise ValueError("failure_note_directory_unavailable")
            if created:
                os.fchmod(root_fd, 0o700)  # Before data; never repair an existing unsafe root.
                os.fsync(parent_fd)
                meta = os.fstat(root_fd)
            if meta.st_mode & 0o077:
                raise ValueError("failure_note_directory_unavailable")
            result, root_fd = root_fd, -1
            return result
        finally:
            os.close(parent_fd)
            if root_fd >= 0:
                os.close(root_fd)

    def record(self, job_id, fingerprint, *, observed_at, journal_state, category, http_status=None,
               phase=None):
        """First-note-wins publication with a non-waiting lock, not an I/O deadline."""
        fd = root_fd = -1
        pending = None
        try:
            name = _name(job_id, fingerprint)
            # Keep the v1 filename namespace: a v2 writer must not create a
            # second "first" note beside existing immutable v1 evidence.
            value = _validate({"schema": PHASE_SCHEMA if phase is not None else SCHEMA,
                **({"phase": phase} if phase is not None else {}),
                "scope": SCOPE, "job_id": job_id,
                "input_fingerprint": fingerprint, "observed_at": observed_at,
                "journal_state": journal_state, "category": category, "http_status": http_status})
            root_fd = self._directory(create=True)
            fd = os.open("writer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=root_fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                return "unavailable"
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "busy"
            try:
                previous = _read(root_fd, name)
            except FileNotFoundError:
                previous = None
            if previous is not None:
                if previous["job_id"] != job_id or previous["input_fingerprint"] != fingerprint:
                    return "unavailable"
                return "already_recorded"  # Keep the first failure, not a later retry's cause.
            count = 0
            with os.scandir(root_fd) as entries:
                for index, entry in enumerate(entries):
                    if index > CAPACITY or not entry.is_file(follow_symlinks=False):
                        return "unavailable"
                    if entry.name == "writer.lock":
                        continue
                    if not re.fullmatch(r"[0-9a-f]{64}\.json", entry.name):
                        return "unavailable"  # Retain stray/interrupted evidence; never delete it.
                    count += 1
                    if count >= CAPACITY:
                        return "capacity"
            raw = (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode()
            temporary = ".pending-" + secrets.token_hex(16)
            pending_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=root_fd)
            pending = temporary
            with os.fdopen(pending_fd, "wb") as source:
                os.fchmod(source.fileno(), 0o600)
                source.write(raw)
                source.flush()
                os.fsync(source.fileno())
                os.link(pending, name, src_dir_fd=root_fd, dst_dir_fd=root_fd,
                        follow_symlinks=False)  # No overwrite, even for a noncooperating writer.
                os.unlink(pending, dir_fd=root_fd)
                pending = None
                os.fsync(root_fd)
            return "written"
        except (OSError, ValueError, TypeError):
            return "unavailable"
        finally:
            if pending is not None and root_fd >= 0:
                try:
                    os.unlink(pending, dir_fd=root_fd)
                except OSError:
                    pass
            if fd >= 0:
                os.close(fd)
            if root_fd >= 0:
                os.close(root_fd)

    def lookup(self, job_id, fingerprint):
        """Read one private note; absence is unknown, not reconstructed history."""
        root_fd = -1
        try:
            name = _name(job_id, fingerprint)
            root_fd = self._directory(create=False)
            value = _read(root_fd, name)
            if value["job_id"] != job_id or value["input_fingerprint"] != fingerprint:
                raise ValueError("failure_note_binding_changed")
            return {"status": "recorded", "record": value}
        except FileNotFoundError:
            return {"status": "not_recorded", "record": None}
        except (OSError, ValueError, TypeError):
            return {"status": "unavailable", "record": None}
        finally:
            if root_fd >= 0:
                os.close(root_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--job", required=True, help="Opaque journal job hash, not a native event ID")
    parser.add_argument("--fingerprint", required=True, help="The matching journal input fingerprint")
    args = parser.parse_args(argv)
    result = FailureNotes(args.state).lookup(args.job, args.fingerprint)
    print(json.dumps({**result, "scope": SCOPE, "missing_legacy_cause": "unknown_not_reconstructed"}))
    return 0 if result["status"] == "recorded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
