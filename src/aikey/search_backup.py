"""Back up the Find Anything search index and prove the backup restores (#18).

``backup`` writes a ``pg_dump -Fc`` of the search database plus a manifest of
table row counts, embedding counts, vector dimensions and the pinned encoder
profile. ``verify`` restores a dump into a scratch database in the same
PostgreSQL container, compares it with the manifest and drops the scratch
database. Neither step writes to the live database. Only counts, dimensions
and digests are printed; no row content leaves the database.

Restoring over the live index is a manual rollback step (see
docs/evidence/find-anything-contract.md), taken only with Protect's search
host disconnected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Callable, Sequence

USER = DATABASE = "unifi-protect"
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_COUNTS_SQL = r"""
select json_build_object(
  'tables', (select coalesce(json_object_agg(t.relname, t.n), '{}'::json) from (
      select c.relname, (xpath('/row/n/text()', query_to_xml(
          format('select count(*) as n from %I.%I', n.nspname, c.relname), false, true, '')))[1]::text::bigint as n
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where c.relkind = 'r' and n.nspname = 'public' order by c.relname) t),
  'embeddings', (select count(*) from "ramDetections" where embedding is not null),
  'dimensions', (select coalesce(json_agg(distinct vector_dims(embedding)), '[]'::json)
                 from "ramDetections" where embedding is not null))
"""

Runner = Callable[..., subprocess.CompletedProcess]


class BackupError(RuntimeError):
    """The search index could not be backed up or its backup did not verify."""


def _exec(run: Runner, container: str, args: Sequence[str], *, stdin=None, stdout=None,
          timeout: float = 600) -> subprocess.CompletedProcess:
    command = ["container", "exec", *(["-i"] if stdin is not None else []), container, *args]
    result = run(command, stdin=stdin, stdout=stdout if stdout is not None else subprocess.PIPE,
                 stderr=subprocess.PIPE, timeout=timeout, check=False)
    if result.returncode != 0:
        # PostgreSQL errors name objects, never row content; keep only the first line.
        detail = (result.stderr or b"").decode(errors="replace").strip().splitlines()[:1]
        raise BackupError(f"{args[0]} failed: {detail[0] if detail else result.returncode}")
    return result


def counts(run: Runner, container: str, database: str = DATABASE) -> dict:
    result = _exec(run, container, ["psql", "-X", "-U", USER, "-d", database, "-Atq", "-c", _COUNTS_SQL])
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or not isinstance(value.get("tables"), dict):
        raise BackupError("Unexpected count reply")
    return value


def backup(run: Runner, container: str, out_dir: Path, profile_path: Path | None = None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    dump = out_dir / f"search-{stamp}.dump"
    before = counts(run, container)
    descriptor = os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        _exec(run, container, ["pg_dump", "-U", USER, "-d", DATABASE, "-Fc", "--no-owner"], stdout=output)
        output.flush()
        os.fsync(output.fileno())
    after = counts(run, container)
    if before != after:
        # Protect wrote while dumping; the dump is consistent but the manifest
        # would not describe it. Retry when the index is quiet.
        dump.unlink()
        raise BackupError("The index changed during the backup; retry")
    manifest = {"dump": dump.name, "sha256": _sha256(dump), "bytes": dump.stat().st_size,
                "created": stamp, "counts": before}
    if profile_path is not None and Path(profile_path).is_file():
        manifest["profile"] = json.loads(Path(profile_path).read_text())
    path = out_dir / f"search-{stamp}.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(path, 0o600)
    return manifest


def verify(run: Runner, container: str, manifest_path: Path) -> dict:
    manifest = json.loads(Path(manifest_path).read_text())
    dump = Path(manifest_path).parent / manifest["dump"]
    if _sha256(dump) != manifest["sha256"]:
        raise BackupError("Dump digest does not match its manifest")
    scratch = f"aikey_restore_check_{os.getpid()}"
    if not _NAME.match(scratch) or scratch == DATABASE:
        raise BackupError("Invalid scratch database name")
    _exec(run, container, ["createdb", "-U", USER, scratch])
    try:
        with open(dump, "rb") as source:
            _exec(run, container, ["pg_restore", "-U", USER, "-d", scratch, "--no-owner", "--exit-on-error"],
                  stdin=source)
        restored = counts(run, container, scratch)
    finally:
        _exec(run, container, ["dropdb", "-U", USER, "--if-exists", scratch])
    if restored != manifest["counts"]:
        raise BackupError("Restored counts differ from the manifest")
    return {"verified": True, "counts": restored, "sha256": manifest["sha256"]}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None, run: Runner = subprocess.run) -> int:
    parser = argparse.ArgumentParser(prog="aikey-search-backup")
    parser.add_argument("--container", default="local-postgres-search")
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("backup")
    make.add_argument("--out", required=True, type=Path)
    make.add_argument("--profile", type=Path, help="search-profile.json to record with the dump")
    check = sub.add_parser("verify")
    check.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    if not _NAME.match(args.container):
        parser.error("invalid container name")
    try:
        if args.command == "backup":
            manifest = backup(run, args.container, args.out, args.profile)
            report = {"manifest": manifest["dump"].replace(".dump", ".json"), "bytes": manifest["bytes"],
                      "sha256": manifest["sha256"][:12], "counts": manifest["counts"]}
        else:
            result = verify(run, args.container, args.manifest)
            report = {"verified": True, "sha256": result["sha256"][:12], "counts": result["counts"]}
    except (BackupError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
