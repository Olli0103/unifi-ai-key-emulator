"""Read-only inventory and dry-run expiry plan for the worker archive (#5).

The archive holds one tombstone per terminal job: a hashed job ID, a hashed
input fingerprint, the terminal state and a timestamp. No media, captions or
camera IDs. The worker reads it at admission, so a task Protect sends again
returns ``already_completed`` (or is refused after a failure) instead of
being run and charged a second time. The caption budget dedupes only for
24 hours; after that the archive is the only replay guard.

Protect decides whether a task comes back: a retroactive run can re-dispatch
events of any retained age, and ``retryFailTasks`` retries failed ones. Age
alone is therefore never a safe expiry boundary. A tombstone is expirable
only when all of these hold:

* the archive is well formed (no symlink, stray, malformed or interrupted
  entry anywhere; otherwise the whole plan is blocked);
* no local reference: active journal, one-use permit or caption reservation;
* older than an operator-chosen ``min_age_days`` (no default here);
* the controller has confirmed its event can no longer be dispatched
  (``controller_absent``). Without that evidence nothing is expirable.

This module never deletes anything.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import time

_HEX64 = re.compile(r"[0-9a-f]{64}")
_BUCKET = re.compile(r"[0-9a-f]{2}")
_MAX_BYTES = 65536
_TOMBSTONE_KEYS = {"schema", "jobId", "fingerprint", "state", "updatedAt"}
_AGE_BUCKETS = ((1, "under_1d"), (7, "1d_to_7d"), (30, "7d_to_30d"), (90, "30d_to_90d"))
CONTROLLER_BLOCKER = ("Protect can re-dispatch retained events (retroactive runs, retryFailTasks); "
                      "expiry needs controller-confirmed absence of each event")


class ArchiveError(ValueError):
    """The archive or the plan request cannot be evaluated safely."""


def valid_tombstone(record: object, job_id: str, *, now: float | None = None) -> bool:
    """The one tombstone shape the worker accepts at admission."""
    now = time.time() if now is None else now
    return (isinstance(record, dict) and set(record) == _TOMBSTONE_KEYS
            and record["schema"] == 1 and record["jobId"] == job_id
            and record["state"] in {"completed", "failed"}
            and isinstance(record["fingerprint"], str) and _HEX64.fullmatch(record["fingerprint"]) is not None
            and type(record["updatedAt"]) in {int, float} and math.isfinite(record["updatedAt"])
            and 0 < record["updatedAt"] < now + 300)


def _private_dir(path: Path) -> bool:
    meta = path.lstat()
    return (stat.S_ISDIR(meta.st_mode) and not stat.S_ISLNK(meta.st_mode)
            and meta.st_uid == os.geteuid() and not meta.st_mode & 0o077)


def _read_json(path: Path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        meta = os.fstat(handle.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > _MAX_BYTES:
            raise ValueError
        return json.loads(handle.read(_MAX_BYTES + 1))


def _references(state_root: Path, problems: dict) -> tuple[set[str], dict[str, int]]:
    """Job IDs something local still points at, and how many per source."""
    refs: dict[str, set[str]] = {"active_journal": set(), "test_permit": set(), "caption_reservation": set()}
    jobs = state_root / "worker-jobs"
    if jobs.is_symlink():
        problems["unsafe"] += 1
    elif jobs.is_dir():
        refs["active_journal"] = {p.stem for p in jobs.glob("*.json") if _HEX64.fullmatch(p.stem)}
    scopes = state_root / "worker-test-scopes"
    if scopes.is_symlink():
        problems["unsafe"] += 1
    elif scopes.is_dir():
        for path in scopes.glob("*.json"):
            try:
                value = _read_json(path)
                if isinstance(value, dict) and isinstance(value.get("job_id"), str):
                    refs["test_permit"].add(value["job_id"])
            except (OSError, ValueError):
                problems["unreadable_reference"] += 1
    budget = state_root / "caption-budget.json"
    if budget.exists() or budget.is_symlink():
        try:
            value = _read_json(budget)
            refs["caption_reservation"] = {item["job_id"] for item in value["reservations"]}
        except (OSError, ValueError, KeyError, TypeError):
            problems["unreadable_reference"] += 1
    return set().union(*refs.values()), {key: len(value) for key, value in refs.items()}


def inventory(state_root: Path, *, now: float | None = None) -> dict:
    """Counts only; never modifies the archive. ``_tombstones`` is for plan()."""
    now = time.time() if now is None else now
    state_root = Path(state_root)
    archive = state_root / "worker-archive"
    problems = {"unsafe": 0, "malformed": 0, "stray": 0, "interrupted_temp": 0,
                "unreadable_reference": 0}
    tombstones: dict[str, dict] = {}
    if archive.is_symlink() or (archive.exists() and not _private_dir(archive)):
        problems["unsafe"] += 1
    elif archive.is_dir():
        for bucket in sorted(archive.iterdir()):
            if bucket.is_symlink() or not bucket.is_dir():
                problems["unsafe" if bucket.is_symlink() else "stray"] += 1
                continue
            if not _BUCKET.fullmatch(bucket.name) or not _private_dir(bucket):
                problems["unsafe" if _BUCKET.fullmatch(bucket.name) else "stray"] += 1
                continue
            for path in sorted(bucket.iterdir()):
                if path.name.startswith(".archive-"):
                    # The worker died between writing a tombstone and linking it.
                    problems["interrupted_temp"] += 1
                    continue
                job_id = path.stem
                if path.suffix != ".json" or not _HEX64.fullmatch(job_id) or job_id[:2] != bucket.name:
                    problems["stray"] += 1
                    continue
                if path.is_symlink():
                    problems["unsafe"] += 1
                    continue
                try:
                    record = _read_json(path)
                except (OSError, ValueError):
                    record = None
                if not valid_tombstone(record, job_id, now=now):
                    problems["malformed"] += 1
                    continue
                tombstones[job_id] = record
    referenced, by_source = _references(state_root, problems)
    interrupted = len(referenced & tombstones.keys() & _active(state_root))
    states = {"completed": 0, "failed": 0}
    ages = {label: 0 for _, label in _AGE_BUCKETS} | {"over_90d": 0}
    for record in tombstones.values():
        states[record["state"]] += 1
        age = (now - record["updatedAt"]) / 86400
        ages[next((label for limit, label in _AGE_BUCKETS if age < limit), "over_90d")] += 1
    return {"schema": "aikey-worker-archive-inventory/1", "tombstones": len(tombstones),
            "states": states, "ages": ages, "problems": problems,
            "references": by_source | {"archived_and_referenced": len(referenced & tombstones.keys()),
                                       "archived_and_still_active": interrupted},
            "_tombstones": tombstones, "_referenced": referenced}


def _active(state_root: Path) -> set[str]:
    jobs = state_root / "worker-jobs"
    if jobs.is_symlink() or not jobs.is_dir():
        return set()
    return {p.stem for p in jobs.glob("*.json") if _HEX64.fullmatch(p.stem)}


def plan(report: dict, *, min_age_days: float, controller_absent: frozenset[str] | None = None,
         now: float | None = None) -> dict:
    """Dry run: which tombstones could expire, and why the rest cannot."""
    if type(min_age_days) not in {int, float} or not math.isfinite(min_age_days) or min_age_days < 1:
        raise ArchiveError("min_age_days must be chosen explicitly and be at least 1")
    now = time.time() if now is None else now
    blockers = [f"{name}: {count}" for name, count in report["problems"].items() if count]
    if report["references"]["archived_and_still_active"]:
        blockers.append("An archive run was interrupted; the worker must finish it first")
    if controller_absent is None:
        blockers.append(CONTROLLER_BLOCKER)
    candidates = []
    if not blockers:
        absent = frozenset(controller_absent)
        for job_id, record in report["_tombstones"].items():
            if (job_id in absent and job_id not in report["_referenced"]
                    and (now - record["updatedAt"]) / 86400 > min_age_days):
                candidates.append(job_id)
    return {"dry_run": True, "expirable": sorted(candidates), "blocked_by": blockers,
            "kept": report["tombstones"] - len(candidates)}


def public(report: dict) -> dict:
    return {key: value for key, value in report.items() if not key.startswith("_")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aikey-worker-archive",
                                     description="Read-only worker archive inventory (never deletes).")
    parser.add_argument("--state", required=True, type=Path, help="AI Key state directory")
    parser.add_argument("--min-age-days", type=float, help="show the dry-run plan for this age")
    args = parser.parse_args(argv)
    try:
        report = inventory(args.state)
        output = public(report)
        if args.min_age_days is not None:
            result = plan(report, min_age_days=args.min_age_days)
            output["plan"] = {"expirable": len(result["expirable"]), "blocked_by": result["blocked_by"],
                              "kept": result["kept"]}
    except (ArchiveError, OSError) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}))
        return 1
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
