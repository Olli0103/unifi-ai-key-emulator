"""Read-only worker archive inventory and dry-run expiry plan (#5). Synthetic state only."""

import hashlib
import json
import os
import time

import pytest

from aikey.worker_archive import (CONTROLLER_BLOCKER, ArchiveError, inventory, main, plan, public,
                                  valid_tombstone)

NOW = float(int(time.time()))
DAY = 86400


def jid(name):
    return hashlib.sha256(name.encode()).hexdigest()


def tombstone(root, name, *, age_days, state="completed"):
    job_id = jid(name)
    bucket = root / "worker-archive" / job_id[:2]
    bucket.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / "worker-archive").chmod(0o700)
    record = {"schema": 1, "jobId": job_id, "fingerprint": jid(name + "-input"),
              "state": state, "updatedAt": NOW - age_days * DAY}
    (bucket / f"{job_id}.json").write_text(json.dumps(record))
    return job_id


def digest(root):
    value = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        value.update(str(path.relative_to(root)).encode())
        if path.is_symlink():
            value.update(os.readlink(path).encode())
        elif path.is_file():
            value.update(path.read_bytes())
    return value.hexdigest()


@pytest.fixture
def state(tmp_path):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    ids = {"old": tombstone(root, "old", age_days=120),
           "old_failed": tombstone(root, "old-failed", age_days=40, state="failed"),
           "recent": tombstone(root, "recent", age_days=2),
           "permit": tombstone(root, "permit", age_days=200),
           "reserved": tombstone(root, "reserved", age_days=200)}
    (root / "worker-test-scopes").mkdir(mode=0o700)
    (root / "worker-test-scopes" / f"{jid('p')}.json").write_text(
        json.dumps({"schema": 1, "job_id": ids["permit"], "camera_id": "fixture"}))
    (root / "caption-budget.json").write_text(json.dumps(
        {"schema": 1, "high_water_ns": 1, "reservations": [{"job_id": ids["reserved"]}]}))
    (root / "worker-jobs").mkdir(mode=0o700)
    (root / "worker-jobs" / f"{jid('live')}.json").write_text("{}")
    return root, ids


def test_inventory_reports_counts_only_and_changes_nothing(state):
    root, ids = state
    before = digest(root)
    report = inventory(root, now=NOW)
    shown = public(report)
    assert shown["tombstones"] == 5 and shown["states"] == {"completed": 4, "failed": 1}
    assert shown["ages"] == {"under_1d": 0, "1d_to_7d": 1, "7d_to_30d": 0, "30d_to_90d": 1, "over_90d": 3}
    assert shown["problems"] == dict.fromkeys(shown["problems"], 0)
    assert shown["references"] == {"active_journal": 1, "test_permit": 1, "caption_reservation": 1,
                                   "archived_and_referenced": 2, "archived_and_still_active": 0}
    assert not any(job_id in json.dumps(shown) for job_id in ids.values())
    assert digest(root) == before


def test_without_controller_evidence_nothing_is_expirable(state):
    root, _ = state
    result = plan(inventory(root, now=NOW), min_age_days=30, now=NOW)
    assert result == {"dry_run": True, "expirable": [], "blocked_by": [CONTROLLER_BLOCKER], "kept": 5}


def test_only_old_unreferenced_controller_absent_tombstones_are_expirable(state):
    root, ids = state
    before = digest(root)
    absent = frozenset(ids.values())                      # controller says none can come back
    result = plan(inventory(root, now=NOW), min_age_days=30, controller_absent=absent, now=NOW)
    assert result["blocked_by"] == [] and result["kept"] == 3
    assert result["expirable"] == sorted([ids["old"], ids["old_failed"]])
    # Referenced by a permit or a caption reservation: kept, however old.
    assert ids["permit"] not in result["expirable"] and ids["reserved"] not in result["expirable"]
    # Not confirmed absent: kept.
    partial = plan(inventory(root, now=NOW), min_age_days=30,
                   controller_absent=frozenset({ids["old"]}), now=NOW)
    assert partial["expirable"] == [ids["old"]]
    # Younger than the operator's age: kept.
    assert plan(inventory(root, now=NOW), min_age_days=150, controller_absent=absent,
                now=NOW)["expirable"] == []
    assert digest(root) == before


@pytest.mark.parametrize("age", [None, 0, 0.5, float("nan"), "30", True])
def test_the_age_must_be_chosen_explicitly(state, age):
    root, _ = state
    report = inventory(root, now=NOW)
    with pytest.raises((ArchiveError, TypeError)):
        plan(report, min_age_days=age) if age is not None else plan(report)


def test_an_interrupted_archive_run_blocks_the_plan(state):
    root, ids = state
    # Linked into the archive but not yet removed from the active journal.
    (root / "worker-jobs" / f"{ids['old']}.json").write_text("{}")
    report = inventory(root, now=NOW)
    assert report["references"]["archived_and_still_active"] == 1
    result = plan(report, min_age_days=30, controller_absent=frozenset(ids.values()), now=NOW)
    assert result["expirable"] == [] and any("interrupted" in b for b in result["blocked_by"])


@pytest.mark.parametrize("damage,problem", [
    ("temp", "interrupted_temp"), ("malformed", "malformed"), ("extra_key", "malformed"),
    ("wrong_bucket", "stray"), ("stray_file", "stray"), ("symlink_file", "unsafe"),
    ("symlink_bucket", "unsafe"), ("open_bucket", "unsafe"), ("future", "malformed"),
    ("oversized", "malformed"), ("bad_budget", "unreadable_reference")])
def test_damaged_entries_are_counted_and_block_the_plan_untouched(state, tmp_path, damage, problem):
    root, ids = state
    archive = root / "worker-archive"
    bucket = archive / ids["old"][:2]
    target = bucket / f"{ids['old']}.json"
    if damage == "temp":
        (bucket / ".archive-abc123").write_text("{}")
    elif damage == "malformed":
        target.write_text("{not json")
    elif damage == "extra_key":
        target.write_text(json.dumps(dict(json.loads(target.read_text()), camera="x")))
    elif damage == "wrong_bucket":
        other = archive / ("00" if ids["old"][:2] != "00" else "01")
        other.mkdir(mode=0o700, exist_ok=True)
        (other / f"{ids['old']}.json").write_text(target.read_text())
    elif damage == "stray_file":
        (bucket / "notes.txt").write_text("x")
    elif damage == "symlink_file":
        outside = tmp_path / "outside.json"
        outside.write_text(target.read_text())
        name = jid("linked")
        link_bucket = archive / name[:2]
        link_bucket.mkdir(mode=0o700, exist_ok=True)
        (link_bucket / f"{name}.json").symlink_to(outside)
    elif damage == "symlink_bucket":
        real = tmp_path / "elsewhere"
        real.mkdir(mode=0o700)
        name = next(f"{a}{b}" for a in "0123456789abcdef" for b in "0123456789abcdef"
                    if not (archive / f"{a}{b}").exists())
        (archive / name).symlink_to(real)
    elif damage == "open_bucket":
        bucket.chmod(0o755)
    elif damage == "future":
        target.write_text(json.dumps(dict(json.loads(target.read_text()), updatedAt=NOW + 3600)))
    elif damage == "oversized":
        target.write_text(" " * 70000)
    else:
        (root / "caption-budget.json").write_text("{broken")
    before = digest(root)
    report = inventory(root, now=NOW)
    assert report["problems"][problem] >= 1
    result = plan(report, min_age_days=30, controller_absent=frozenset(ids.values()), now=NOW)
    assert result["expirable"] == [] and any(b.startswith(problem) for b in result["blocked_by"])
    assert digest(root) == before


def test_a_symlinked_archive_directory_is_never_followed(tmp_path):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    real = tmp_path / "real-archive"
    real.mkdir(mode=0o700)
    (root / "worker-archive").symlink_to(real)
    report = inventory(root, now=NOW)
    assert report["problems"]["unsafe"] == 1 and report["tombstones"] == 0


def test_an_absent_archive_is_an_empty_inventory(tmp_path):
    report = inventory(tmp_path, now=NOW)
    assert report["tombstones"] == 0 and not any(report["problems"].values())


def test_the_worker_and_the_inventory_share_one_tombstone_shape():
    job_id = jid("x")
    good = {"schema": 1, "jobId": job_id, "fingerprint": jid("y"), "state": "failed", "updatedAt": NOW}
    assert valid_tombstone(good, job_id, now=NOW)
    for change in ({"state": "callback_uncertain"}, {"jobId": jid("z")}, {"schema": 2},
                   {"fingerprint": "short"}, {"updatedAt": float("inf")}, {"updatedAt": True}):
        assert not valid_tombstone(dict(good, **change), job_id, now=NOW)


def test_cli_prints_counts_and_a_blocked_plan(state, capsys):
    root, ids = state
    before = digest(root)
    assert main(["--state", str(root), "--min-age-days", "30"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["tombstones"] == 5 and output["plan"]["expirable"] == 0
    assert output["plan"]["blocked_by"] == [CONTROLLER_BLOCKER]
    assert not any(job_id in json.dumps(output) for job_id in ids.values())
    assert digest(root) == before
