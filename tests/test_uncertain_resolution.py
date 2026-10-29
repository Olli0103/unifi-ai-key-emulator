"""Reviewed resolution of callback-uncertain local index jobs. Synthetic journals only."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from aikey import uncertain_resolution as ur
from aikey.uncertain_resolution import ResolutionError
from aikey.worker import JobProcessor, _json
from aikey.worker_archive import valid_tombstone
from test_worker import configuration

pytest_plugins = ["test_worker"]
NOW = 1_790_600_000.0


def job(label):
    return hashlib.sha256(f"multipleImages:camera:{label}".encode()).hexdigest()


def write(state, job_id, *, operation="indexImages", state_name="callback_uncertain", **extra):
    directory = state / "worker-jobs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = {"jobId": job_id, "fingerprint": hashlib.sha256(job_id.encode()).hexdigest(),
              "state": state_name, "updatedAt": NOW - 80_000, "operation": operation, **extra}
    (directory / f"{job_id}.json").write_bytes(_json(record))
    return record


@pytest.fixture
def journal(tmp_path):
    state = tmp_path / "state"
    ids = {name: job(name) for name in ("stored-1", "stored-2", "unstored", "caption", "odd", "done")}
    write(state, ids["stored-1"])
    write(state, ids["stored-2"], operation="indexKeyFrames")
    write(state, ids["unstored"])
    write(state, ids["caption"], operation="recognizeKeyFrames")          # a paid caption
    write(state, ids["odd"], result={"unexpected": True})
    write(state, ids["done"], state_name="completed", result={"status": "processed"})
    stored = frozenset({ids["stored-1"], ids["stored-2"], ids["caption"], ids["odd"]})
    return state, ids, stored


def digest(root: Path) -> str:
    value = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        value.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return value.hexdigest()


def test_the_plan_reads_only_and_never_offers_paid_or_unproven_jobs(journal):
    state, ids, stored = journal
    before = digest(state)
    report = ur.plan(state, stored, now=NOW)
    assert digest(state) == before
    actions = {e["jobId"]: (e["action"], e["reason"]) for e in report["entries"]}
    assert actions == {
        ids["stored-1"]: ("archive_as_completed", None),
        ids["stored-2"]: ("archive_as_completed", None),
        ids["unstored"]: ("keep", "no_storage_evidence"),
        ids["caption"]: ("keep", "not_local_index"),
        ids["odd"]: ("keep", "unexpected_record_shape")}                  # completed record not listed
    assert report["summary"] == {"archive_as_completed": 2, "keep:no_storage_evidence": 1,
                                 "keep:not_local_index": 1, "keep:unexpected_record_shape": 1}
    assert ur.plan(state, stored, now=NOW)["digest"] == report["digest"]  # stable for approval
    assert ids["stored-1"] not in json.dumps(ur.public(report))


@pytest.mark.parametrize("change,error", [
    ({"key_stopped": False}, "key_running"),
    ({"approved_digest": "0" * 64}, "plan_changed"),
])
def test_apply_refuses_a_running_key_or_an_unapproved_plan(journal, tmp_path, change, error):
    state, _, stored = journal
    before = digest(state)
    arguments = dict(approved_digest=ur.plan(state, stored, now=NOW)["digest"], stored=stored,
                     key_stopped=True, now=NOW)
    arguments.update(change)
    with pytest.raises(ResolutionError, match=error):
        ur.apply(state, tmp_path / "backup", **arguments)
    assert digest(state) == before and not (tmp_path / "backup").exists()


def test_a_record_changed_after_approval_or_a_used_backup_is_refused(journal, tmp_path):
    state, ids, stored = journal
    approved = ur.plan(state, stored, now=NOW)["digest"]
    (tmp_path / "used").mkdir()
    (tmp_path / "used" / "x").write_text("x")
    with pytest.raises(ResolutionError, match="backup_not_empty"):
        ur.apply(state, tmp_path / "used", approved_digest=approved, stored=stored, key_stopped=True, now=NOW)
    write(state, ids["stored-1"], updatedAt=NOW - 1)                      # rewritten after review
    with pytest.raises(ResolutionError, match="plan_changed"):
        ur.apply(state, tmp_path / "backup", approved_digest=approved, stored=stored, key_stopped=True, now=NOW)


async def test_apply_backs_up_first_then_writes_worker_tombstones(journal, tmp_path, services):
    state, ids, stored = journal
    report = ur.plan(state, stored, now=NOW)
    originals = {i: (state / "worker-jobs" / f"{ids[i]}.json").read_bytes() for i in ids}
    result = ur.apply(state, tmp_path / "backup", approved_digest=report["digest"], stored=stored,
                      key_stopped=True, now=NOW)
    assert result == {"archived": 2, "already_archived": 0, "records": 2}
    backup = tmp_path / "backup"
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["digest"] == report["digest"] and len(manifest["entries"]) == 2
    for name in ("stored-1", "stored-2"):
        saved = backup / f"{ids[name]}.json"
        assert saved.read_bytes() == originals[name] and saved.stat().st_mode & 0o077 == 0
        assert not (state / "worker-jobs" / f"{ids[name]}.json").exists()
        tombstone = json.loads((state / "worker-archive" / ids[name][:2] / f"{ids[name]}.json").read_text())
        assert valid_tombstone(tombstone, ids[name]) and tombstone["state"] == "completed"
    for name in ("unstored", "caption", "odd", "done"):                   # untouched
        assert (state / "worker-jobs" / f"{ids[name]}.json").read_bytes() == originals[name]
    # The real worker loads the journal and reads the tombstone it would write itself.
    worker = JobProcessor(configuration(services), state)
    try:
        assert worker._archived_record(ids["stored-1"])["state"] == "completed"
        assert ids["stored-1"] not in worker._history and ids["caption"] in worker._history
    finally:
        await worker.stop()


def test_an_interrupted_apply_resumes_and_a_rerun_is_a_no_op(journal, tmp_path):
    state, ids, stored = journal
    approved = ur.plan(state, stored, now=NOW)["digest"]
    source = state / "worker-jobs" / f"{ids['stored-1']}.json"
    kept = source.read_bytes()
    ur.apply(state, tmp_path / "backup", approved_digest=approved, stored=stored, key_stopped=True, now=NOW)
    source.write_bytes(kept)                     # as if the run stopped after linking, before unlinking
    assert ur.apply(state, tmp_path / "backup", approved_digest=approved, stored=stored,
                    key_stopped=True, now=NOW) == {"archived": 1, "already_archived": 1, "records": 2}
    assert ur.apply(state, tmp_path / "backup", approved_digest=approved, stored=stored,
                    key_stopped=True, now=NOW) == {"archived": 0, "already_archived": 2, "records": 2}
    with pytest.raises(ResolutionError, match="another_plan"):
        ur.apply(state, tmp_path / "backup", approved_digest="1" * 64, stored=stored, key_stopped=True, now=NOW)


def test_rollback_restores_bytes_removes_only_its_tombstones_and_is_idempotent(journal, tmp_path):
    state, ids, stored = journal
    before = digest(state / "worker-jobs")
    approved = ur.plan(state, stored, now=NOW)["digest"]
    ur.apply(state, tmp_path / "backup", approved_digest=approved, stored=stored, key_stopped=True, now=NOW)
    assert ur.rollback(state, tmp_path / "backup") == {"restored": 2, "tombstones_removed": 2, "records": 2}
    assert digest(state / "worker-jobs") == before
    assert not list((state / "worker-archive").rglob("*.json"))
    assert ur.rollback(state, tmp_path / "backup") == {"restored": 0, "tombstones_removed": 0, "records": 2}


def test_a_corrupt_backup_or_a_foreign_tombstone_stops_rollback(journal, tmp_path):
    state, ids, stored = journal
    approved = ur.plan(state, stored, now=NOW)["digest"]
    ur.apply(state, tmp_path / "backup", approved_digest=approved, stored=stored, key_stopped=True, now=NOW)
    target = state / "worker-archive" / ids["stored-2"][:2] / f"{ids['stored-2']}.json"
    foreign = json.loads(target.read_text()) | {"state": "failed"}
    target.write_bytes(_json(foreign))
    with pytest.raises(ResolutionError, match="tombstone_conflict"):
        ur.rollback(state, tmp_path / "backup")
    saved = tmp_path / "backup" / f"{ids['stored-1']}.json"
    os.chmod(saved, 0o600)
    saved.write_bytes(saved.read_bytes() + b" ")
    with pytest.raises(ResolutionError, match="backup_corrupt"):
        ur.rollback(state, tmp_path / "backup")


def test_a_conflicting_existing_tombstone_keeps_the_record(journal):
    state, ids, stored = journal
    target = state / "worker-archive" / ids["stored-1"][:2]
    target.mkdir(parents=True, mode=0o700)
    (state / "worker-archive").chmod(0o700)
    record = json.loads((state / "worker-jobs" / f"{ids['stored-1']}.json").read_text())
    (target / f"{ids['stored-1']}.json").write_bytes(_json(
        {"schema": 1, "jobId": ids["stored-1"], "fingerprint": record["fingerprint"],
         "state": "failed", "updatedAt": record["updatedAt"]}))
    actions = {e["jobId"]: e["reason"] for e in ur.plan(state, stored, now=NOW)["entries"]}
    assert actions[ids["stored-1"]] == "tombstone_conflict"
