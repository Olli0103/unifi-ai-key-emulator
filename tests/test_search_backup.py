"""Search-index backup and scratch-restore verification (#18)."""

import json
import subprocess
import time as _time

import pytest

from aikey import search_backup
from aikey.search_backup import BackupError, backup, main, prune, verify

COUNTS = {"tables": {"ramDetections": 2, "migrations": 8}, "embeddings": 2, "dimensions": [768]}


class FakeContainer:
    """Plays `container exec` for psql, pg_dump, createdb, pg_restore and dropdb."""

    def __init__(self):
        self.databases = {"unifi-protect": dict(COUNTS)}
        self.calls = []
        self.restore_counts = None             # what a restore yields, when overridden
        self.change_during_dump = False

    def __call__(self, command, *, stdin=None, stdout=None, stderr=None, timeout=None, check=False):
        assert command[:2] == ["container", "exec"]
        args = [a for a in command[2:] if a != "-i"][1:]
        self.calls.append(args[0] if args[0] != "psql" else "psql:" + args[args.index("-d") + 1])

        def ok(out=b""):
            return subprocess.CompletedProcess(command, 0, out, b"")
        database = args[args.index("-d") + 1] if "-d" in args else None
        if args[0] == "psql":
            return ok(json.dumps(self.databases[database]).encode())
        if args[0] == "pg_dump":
            assert database == "unifi-protect"
            stdout.write(b"PGDMP-fixture")
            if self.change_during_dump:
                self.databases["unifi-protect"] = {**COUNTS, "embeddings": 3}
            return ok()
        if args[0] == "createdb":
            assert args[-1] not in self.databases
            self.databases[args[-1]] = None
            return ok()
        if args[0] == "pg_restore":
            assert database != "unifi-protect" and stdin.read() == b"PGDMP-fixture"
            self.databases[database] = self.restore_counts or dict(COUNTS)
            return ok()
        if args[0] == "dropdb":
            assert args[-1] != "unifi-protect"
            self.databases.pop(args[-1], None)
            return ok()
        raise AssertionError(args)


def test_backup_then_verify_round_trips_through_a_scratch_database(tmp_path):
    fake = FakeContainer()
    profile = tmp_path / "search-profile.json"
    profile.write_text(json.dumps({"revision": "a" * 64}))
    manifest = backup(fake, "pg", tmp_path / "out", profile)
    assert manifest["counts"] == COUNTS and manifest["profile"]["revision"] == "a" * 64
    dump = tmp_path / "out" / manifest["dump"]
    assert dump.read_bytes() == b"PGDMP-fixture" and oct(dump.stat().st_mode)[-3:] == "600"
    result = verify(fake, "pg", tmp_path / "out" / manifest["dump"].replace(".dump", ".json"))
    assert result["verified"] and result["counts"] == COUNTS
    saved = json.loads((tmp_path / "out" / manifest["dump"].replace(".dump", ".json")).read_text())
    assert saved["verified"]["restore_counts_match"] is True
    assert set(fake.databases) == {"unifi-protect"}          # scratch database dropped
    assert fake.databases["unifi-protect"] == COUNTS          # live database untouched
    assert "pg_restore" in fake.calls and fake.calls[-1] == "dropdb"


def test_a_restore_with_different_counts_fails_and_still_drops_the_scratch_database(tmp_path):
    fake = FakeContainer()
    manifest = backup(fake, "pg", tmp_path)
    fake.restore_counts = {**COUNTS, "embeddings": 1}
    with pytest.raises(BackupError, match="differ"):
        verify(fake, "pg", tmp_path / manifest["dump"].replace(".dump", ".json"))
    assert set(fake.databases) == {"unifi-protect"}


def test_a_tampered_dump_is_refused_before_any_restore(tmp_path):
    fake = FakeContainer()
    manifest = backup(fake, "pg", tmp_path)
    (tmp_path / manifest["dump"]).write_bytes(b"other")
    with pytest.raises(BackupError, match="digest"):
        verify(fake, "pg", tmp_path / manifest["dump"].replace(".dump", ".json"))
    assert "createdb" not in fake.calls


def test_a_backup_taken_while_the_index_changes_is_discarded(tmp_path):
    fake = FakeContainer()
    fake.change_during_dump = True
    with pytest.raises(BackupError, match="changed"):
        backup(fake, "pg", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_the_cli_prints_counts_and_digests_only(tmp_path, capsys):
    fake = FakeContainer()
    assert main(["--container", "pg", "backup", "--out", str(tmp_path)], run=fake) == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"manifest", "bytes", "sha256", "counts"} and len(report["sha256"]) == 12
    assert main(["--container", "pg", "verify", str(tmp_path / report["manifest"])], run=fake) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True


def test_failures_report_only_the_first_error_line(tmp_path, capsys):
    def failing(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, b"", b"psql: error: connection refused\nsecond line")
    assert main(["backup", "--out", str(tmp_path)], run=failing) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["detail"] == "psql failed: psql: error: connection refused"


def test_invalid_container_names_are_refused():
    with pytest.raises(SystemExit):
        main(["--container", "pg; rm -rf", "verify", "x"], run=None)
    assert search_backup._NAME.match("local-postgres-search")


# --- #5 backup expiry ------------------------------------------------------


def _stamp_time(stamp):
    return _time.mktime(_time.strptime(stamp, "%Y%m%dT%H%M%S"))


def _backup(directory, stamp, *, verified=False):
    (directory / f"search-{stamp}.dump").write_bytes(b"PGDMP-fixture")
    value = {"dump": f"search-{stamp}.dump", "sha256": "0" * 64, "counts": COUNTS}
    if verified:
        value["verified"] = {"at": stamp, "restore_counts_match": True}
    (directory / f"search-{stamp}.json").write_text(json.dumps(value))


STAMPS = ["20260801T010000", "20260805T010000", "20260810T010000",
          "20260901T010000", "20260920T010000", "20260926T010000"]
NOW = _stamp_time("20260927T010000")


def test_prune_is_a_dry_run_and_keeps_recent_and_newest_verified(tmp_path):
    for stamp in STAMPS:
        _backup(tmp_path, stamp, verified=stamp == "20260801T010000")
    before = sorted(p.name for p in tmp_path.iterdir())
    plan = prune(tmp_path, keep=2, max_age_days=30, now=NOW)
    # Older than 30 days and beyond the newest two: 05 Aug and 10 Aug. 01 Aug is
    # the only verified backup, and 01 Sep is under 30 days old, so both stay.
    assert plan == {"applied": False, "kept": 4, "newest_verified": "search-20260801T010000",
                    "expired": ["search-20260810T010000", "search-20260805T010000"]}
    assert sorted(p.name for p in tmp_path.iterdir()) == before       # nothing deleted
    applied = prune(tmp_path, keep=2, max_age_days=30, now=NOW, apply=True)
    assert applied["expired"] == plan["expired"]
    left = sorted(p.name for p in tmp_path.iterdir())
    assert "search-20260805T010000.dump" not in left and "search-20260810T010000.json" not in left
    assert "search-20260801T010000.dump" in left and len(left) == 8     # four pairs remain
    assert prune(tmp_path, keep=2, max_age_days=30, now=NOW)["expired"] == []   # converged


def test_prune_never_goes_below_keep_even_when_everything_is_old(tmp_path):
    for stamp in STAMPS[:3]:
        _backup(tmp_path, stamp)
    plan = prune(tmp_path, keep=3, max_age_days=1, now=NOW, apply=True)
    assert plan["expired"] == [] and len(list(tmp_path.iterdir())) == 6


@pytest.mark.parametrize("damage", ["orphan_dump", "symlink", "stray", "bad_manifest", "wrong_dump"])
def test_prune_refuses_an_ambiguous_directory_untouched(tmp_path, damage):
    for stamp in STAMPS:
        _backup(tmp_path, stamp)
    if damage == "orphan_dump":
        (tmp_path / "search-20260701T010000.dump").write_bytes(b"PGDMP")
    elif damage == "symlink":
        (tmp_path / "search-20260702T010000.json").symlink_to(tmp_path / "search-20260801T010000.json")
    elif damage == "stray":
        (tmp_path / "search-notes.txt").write_text("x")
    elif damage == "bad_manifest":
        (tmp_path / "search-20260801T010000.json").write_text("{not json")
    else:
        (tmp_path / "search-20260801T010000.json").write_text(json.dumps({"dump": "search-other.dump"}))
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(BackupError, match="review it first|does not name"):
        prune(tmp_path, keep=1, max_age_days=1, now=NOW, apply=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


@pytest.mark.parametrize("keep,age", [(0, 30), (2, 0), (True, 30)])
def test_prune_rejects_unsafe_bounds(tmp_path, keep, age):
    with pytest.raises(BackupError):
        prune(tmp_path, keep=keep, max_age_days=age)


def test_prune_cli_is_dry_by_default(tmp_path, capsys):
    for stamp in STAMPS:
        _backup(tmp_path, stamp)
    assert main(["prune", "--out", str(tmp_path), "--keep", "1", "--max-age-days", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] is False and report["expired"] and len(list(tmp_path.iterdir())) == 12
