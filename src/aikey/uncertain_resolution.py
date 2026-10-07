"""Reviewed resolution of callback-uncertain local index jobs (#12, #40).

The worker never archives a ``callback_uncertain`` record: the controller may
or may not have acted on the callback, so an automatic resend or a silent
archive could both be wrong. For *local index* jobs (``indexImages``,
``indexKeyFrames``: CLIP embeddings, no provider call, no cost) the outcome
can be checked afterwards. If the search index holds embedded rows for the
job's camera and event, Protect accepted the result, and the honest terminal
state is ``completed``.

This module turns that review into an explicit, auditable operation:

* ``inventory`` and ``plan`` only read. The plan lists each record's job ID,
  the SHA-256 of its journal file, and the action (``archive_as_completed``
  or ``keep`` with a fixed reason). Its digest is what an operator approves.
* ``apply`` needs that digest, a stopped Key (the worker holds the journal in
  memory), and a new private backup directory. Before any change it writes a
  byte copy of every source record and a checksummed manifest. Then, per
  record, it links the same tombstone the worker writes, fsyncs, and removes
  the active record. A rerun with the same backup resumes from the manifest.
* ``rollback`` restores every backed-up record byte for byte and removes only
  tombstones equal to the ones it wrote. It is idempotent.

Caption (paid) jobs are never eligible. Nothing here contacts Protect, the
provider or the database; the storage evidence is passed in.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

from .worker_archive import valid_tombstone

LOCAL_INDEX_OPERATIONS = frozenset({"indexImages", "indexKeyFrames"})
_RECORD_KEYS = {"fingerprint", "jobId", "operation", "state", "updatedAt"}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_BYTES = 65536
MANIFEST = "manifest.json"
# The job ID of an indexImages task is sha256("multipleImages:<camera>:<event>").
STORED_SQL = ("SELECT DISTINCT encode(sha256(convert_to('multipleImages:' || \"cameraId\" || ':' || "
              "\"eventId\", 'UTF8')), 'hex') FROM \"ramDetections\" WHERE embedding IS NOT NULL")


class ResolutionError(ValueError):
    """A fixed, non-secret reason the operation did not run or stopped."""


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        meta = os.fstat(handle.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > _MAX_BYTES:
            raise ResolutionError("unsafe_file")
        return handle.read(_MAX_BYTES + 1)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tombstone(record: dict) -> dict:
    return {"schema": 1, "jobId": record["jobId"], "fingerprint": record["fingerprint"],
            "state": "completed", "updatedAt": record["updatedAt"]}


def _encode(value: dict) -> bytes:
    # Same serialization as the worker's journal writer.
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _journal(state: Path) -> Path:
    directory = Path(state) / "worker-jobs"
    if directory.is_symlink() or not directory.is_dir():
        raise ResolutionError("journal_missing")
    return directory


def _tombstone_path(state: Path, job_id: str) -> Path:
    return Path(state) / "worker-archive" / job_id[:2] / f"{job_id}.json"


def _existing_tombstone(state: Path, job_id: str):
    path = _tombstone_path(state, job_id)
    if not path.exists() and not path.is_symlink():
        return None
    try:
        record = json.loads(_read(path))
    except (OSError, ValueError, ResolutionError):
        return "invalid"
    return record if valid_tombstone(record, job_id) else "invalid"


def plan(state: Path, stored: frozenset[str], *, now: float | None = None) -> dict:
    """Read-only: every callback-uncertain record and what would happen to it."""
    now = time.time() if now is None else now
    entries = []
    for path in sorted(_journal(state).glob("*.json")):
        try:
            raw = _read(path)
            record = json.loads(raw)
        except (OSError, ValueError, ResolutionError):
            continue                      # not ours to judge; the worker refuses a bad journal
        if not isinstance(record, dict) or record.get("state") != "callback_uncertain":
            continue
        job_id = path.stem
        reason = None
        if (set(record) != _RECORD_KEYS or record.get("jobId") != job_id or not _HEX64.fullmatch(job_id)
                or not isinstance(record.get("fingerprint"), str)
                or not _HEX64.fullmatch(record["fingerprint"])
                or type(record.get("updatedAt")) not in {int, float} or not 0 < record["updatedAt"] <= now):
            reason = "unexpected_record_shape"
        elif record["operation"] not in LOCAL_INDEX_OPERATIONS:
            reason = "not_local_index"               # captions and other paid work are never resolved here
        elif job_id not in stored:
            reason = "no_storage_evidence"
        else:
            existing = _existing_tombstone(state, job_id)
            if existing is not None and existing != _tombstone(record):
                reason = "tombstone_conflict"
        entries.append({"jobId": job_id, "sha256": _sha(raw),
                        "operation": record.get("operation") if isinstance(record.get("operation"), str) else None,
                        "action": "keep" if reason else "archive_as_completed", "reason": reason})
    digest = _sha(_encode({"entries": entries}))
    summary: dict[str, int] = {}
    for entry in entries:
        key = entry["action"] if entry["action"] != "keep" else f"keep:{entry['reason']}"
        summary[key] = summary.get(key, 0) + 1
    return {"schema": "aikey-uncertain-resolution/1", "digest": digest, "records": len(entries),
            "summary": dict(sorted(summary.items())), "entries": entries}


def public(report: dict) -> dict:
    """The plan without job IDs: counts, action codes and the approval digest."""
    return {key: report[key] for key in ("schema", "digest", "records", "summary")}


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    meta = path.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_mode & 0o077 or meta.st_uid != os.geteuid():
        raise ResolutionError("unsafe_directory")


def _link_tombstone(state: Path, record: dict) -> None:
    target = _tombstone_path(state, record["jobId"])
    _private_dir(target.parent.parent)
    _private_dir(target.parent)
    fd, temporary = tempfile.mkstemp(prefix=".archive-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_encode(_tombstone(record)))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if _existing_tombstone(state, record["jobId"]) != _tombstone(record):
                raise ResolutionError("tombstone_conflict") from None
        _fsync_dir(target.parent)
    finally:
        os.unlink(temporary)


def _manifest(backup: Path) -> dict | None:
    path = backup / MANIFEST
    if not path.exists():
        return None
    value = json.loads(_read(path))
    if not isinstance(value, dict) or value.get("schema") != "aikey-uncertain-resolution-backup/1":
        raise ResolutionError("manifest_invalid")
    return value


def apply(state: Path, backup: Path, *, approved_digest: str, stored: frozenset[str],
          key_stopped: bool, now: float | None = None) -> dict:
    """Archive the approved records as completed; resumable with the same backup."""
    if key_stopped is not True:
        raise ResolutionError("key_running")      # the worker holds the journal in memory
    state, backup = Path(state), Path(backup)
    journal = _journal(state)
    manifest = _manifest(backup) if backup.exists() else None
    if manifest is None:
        report = plan(state, stored, now=now)
        if report["digest"] != approved_digest:
            raise ResolutionError("plan_changed")
        chosen = [e for e in report["entries"] if e["action"] == "archive_as_completed"]
        if backup.exists() and any(backup.iterdir()):
            raise ResolutionError("backup_not_empty")
        _private_dir(backup)
        for entry in chosen:                          # every byte copy before any change
            raw = _read(journal / f"{entry['jobId']}.json")
            if _sha(raw) != entry["sha256"]:
                raise ResolutionError("plan_changed")
            _write_private(backup / f"{entry['jobId']}.json", raw)
        manifest = {"schema": "aikey-uncertain-resolution-backup/1", "digest": approved_digest,
                    "entries": [{"jobId": e["jobId"], "sha256": e["sha256"]} for e in chosen]}
        _write_private(backup / MANIFEST, _encode(manifest))
        _fsync_dir(backup)
    elif manifest["digest"] != approved_digest:
        raise ResolutionError("backup_belongs_to_another_plan")
    archived = already = 0
    for entry in manifest["entries"]:
        job_id = entry["jobId"]
        saved = _read(backup / f"{job_id}.json")
        if _sha(saved) != entry["sha256"]:
            raise ResolutionError("backup_corrupt")
        record = json.loads(saved)
        source = journal / f"{job_id}.json"
        if source.exists() or source.is_symlink():
            if _sha(_read(source)) != entry["sha256"]:
                raise ResolutionError("record_changed")
            _link_tombstone(state, record)
            source.unlink()
            _fsync_dir(journal)
            archived += 1
        elif _existing_tombstone(state, job_id) == _tombstone(record):
            already += 1                              # finished by an earlier, interrupted run
        else:
            raise ResolutionError("record_missing_without_tombstone")
    return {"archived": archived, "already_archived": already, "records": len(manifest["entries"])}


def rollback(state: Path, backup: Path) -> dict:
    """Restore every backed-up record and remove only the tombstones this wrote."""
    state, backup = Path(state), Path(backup)
    manifest = _manifest(backup)
    if manifest is None:
        raise ResolutionError("manifest_missing")
    journal = _journal(state)
    restored = removed = 0
    for entry in manifest["entries"]:
        job_id = entry["jobId"]
        saved = _read(backup / f"{job_id}.json")
        if _sha(saved) != entry["sha256"]:
            raise ResolutionError("backup_corrupt")
        source = journal / f"{job_id}.json"
        if source.exists() or source.is_symlink():
            if _sha(_read(source)) != entry["sha256"]:
                raise ResolutionError("record_changed")
        else:
            fd, temporary = tempfile.mkstemp(prefix=".journal-", dir=journal)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(saved)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, source)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            _fsync_dir(journal)
            restored += 1
        tombstone = _existing_tombstone(state, job_id)
        if tombstone == _tombstone(json.loads(saved)):
            _tombstone_path(state, job_id).unlink()
            _fsync_dir(_tombstone_path(state, job_id).parent)
            removed += 1
        elif tombstone is not None:
            raise ResolutionError("tombstone_conflict")
    return {"restored": restored, "tombstones_removed": removed, "records": len(manifest["entries"])}


def stored_from_container(container: str) -> frozenset[str]:
    """Job hashes with embedded rows, read with one fixed read-only query."""
    done = subprocess.run(["container", "exec", container, "psql", "-X", "-U", "unifi-protect",
                           "-d", "unifi-protect", "-Atq", "-c", STORED_SQL],
                          capture_output=True, text=True, timeout=60, check=False)
    if done.returncode != 0:
        raise ResolutionError("evidence_query_failed")
    return frozenset(line for line in done.stdout.split() if _HEX64.fullmatch(line))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aikey-uncertain-resolution")
    parser.add_argument("command", choices=("plan", "apply", "rollback"))
    parser.add_argument("--state", type=Path, required=True, help="AI Key state directory")
    parser.add_argument("--evidence-container", help="search database container (plan, apply)")
    parser.add_argument("--backup", type=Path, help="new private backup directory (apply, rollback)")
    parser.add_argument("--approve", help="plan digest being approved (apply)")
    parser.add_argument("--key-stopped", action="store_true", help="the AI Key container is stopped")
    args = parser.parse_args(argv)
    try:
        if args.command == "rollback":
            if args.backup is None or not args.key_stopped:
                raise ResolutionError("rollback needs --backup and --key-stopped")
            print(json.dumps(rollback(args.state, args.backup)))
            return 0
        if not args.evidence_container:
            raise ResolutionError("plan and apply need --evidence-container")
        stored = stored_from_container(args.evidence_container)
        if args.command == "plan":
            print(json.dumps(public(plan(args.state, stored)), indent=2))
            return 0
        if args.backup is None or not args.approve:
            raise ResolutionError("apply needs --backup and --approve")
        print(json.dumps(apply(args.state, args.backup, approved_digest=args.approve, stored=stored,
                               key_stopped=args.key_stopped)))
        return 0
    except (ResolutionError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, ResolutionError) else type(exc).__name__}),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
