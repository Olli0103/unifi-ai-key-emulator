"""Back up the Find Anything search index and prove the backup restores (#18).

``backup`` writes a ``pg_dump -Fc`` of the search database plus a manifest of
table row counts, embedding counts, vector dimensions and the pinned encoder
profile. ``verify`` restores a dump into a scratch database in the same
PostgreSQL container, compares it with the manifest and drops the scratch
database. Neither step writes to the live database. Only counts, dimensions
and digests are printed; no row content leaves the database.

``schema_check`` reads, and only reads, the storage Protect uses for basic
search: the pgvector version, the declared type of ``ramDetections.embedding``,
the widths actually stored, and any other vector columns. It fails when they
disagree with the pinned encoder profile, so vectors of two widths are never
mixed (#10).

``prune`` expires old backups (#5). A dump holds captions, embeddings and
search rows, so it must not be kept forever. It is a dry run unless
``apply`` is set, and it always keeps the newest verified backup.

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

_SCHEMA_SQL = r"""
select json_build_object(
  'extension', (select extversion from pg_extension where extname = 'vector'),
  'embedding_type', (select format_type(a.atttypid, a.atttypmod)
      from pg_attribute a join pg_class c on c.oid = a.attrelid
      join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public' and c.relname = 'ramDetections'
        and a.attname = 'embedding' and not a.attisdropped),
  'vector_columns', (select coalesce(json_agg(json_build_object(
          'table', c.relname, 'column', a.attname, 'type', format_type(a.atttypid, a.atttypmod),
          'kind', case c.relkind when 'i' then 'index' else 'table' end)
          order by c.relname, a.attname), '[]'::json)
      from pg_attribute a join pg_class c on c.oid = a.attrelid
      join pg_namespace n on n.oid = c.relnamespace join pg_type t on t.oid = a.atttypid
      where n.nspname = 'public' and t.typname = 'vector' and a.attnum > 0 and not a.attisdropped))
"""
_STORED_SQL = r"""
select json_build_object(
  'rows_with_embedding', (select count(*) from "ramDetections" where embedding is not null),
  'stored_dimensions', (select coalesce(json_agg(distinct vector_dims(embedding)), '[]'::json)
                        from "ramDetections" where embedding is not null))
"""
_VECTOR_TYPE = re.compile(r"vector(?:\((\d{1,5})\))?\Z")

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
    # Record the outcome so readiness checks can tell a verified backup from
    # one that merely exists (#18).
    manifest["verified"] = {"at": time.strftime("%Y%m%dT%H%M%S"), "restore_counts_match": True}
    temporary = Path(manifest_path).with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, manifest_path)
    return {"verified": True, "counts": restored, "sha256": manifest["sha256"]}


def schema_check(run: Runner, container: str, profile: dict) -> dict:
    """Compare Protect's search storage with the pinned encoder; read-only."""
    expected = profile.get("dimensions") if isinstance(profile, dict) else None
    if type(expected) is not int or not 1 <= expected <= 16000:
        raise BackupError("The encoder profile has no valid dimensions")
    query = ["psql", "-X", "-U", USER, "-d", DATABASE, "-Atq", "-c"]
    schema = json.loads(_exec(run, container, [*query, _SCHEMA_SQL]).stdout)
    if not isinstance(schema, dict) or not isinstance(schema.get("vector_columns"), list):
        raise BackupError("Unexpected schema reply")
    problems, stored = [], {"rows_with_embedding": None, "stored_dimensions": None}
    if not schema.get("extension"):
        problems.append("pgvector extension is not installed")
    declared = schema.get("embedding_type")
    match = _VECTOR_TYPE.fullmatch(declared) if isinstance(declared, str) else None
    if declared is None:
        problems.append("ramDetections.embedding does not exist")
    elif match is None:
        problems.append("ramDetections.embedding is not a vector column")
    elif match.group(1) is not None and int(match.group(1)) != expected:
        problems.append(f"ramDetections.embedding is declared {declared}, the profile needs {expected}")
    if declared is not None:
        stored = json.loads(_exec(run, container, [*query, _STORED_SQL]).stdout)
        widths = stored.get("stored_dimensions") if isinstance(stored, dict) else None
        if not isinstance(widths, list) or any(type(w) is not int for w in widths):
            raise BackupError("Unexpected stored-dimension reply")
        if any(width != expected for width in widths):
            problems.append(f"stored vectors have widths {sorted(widths)}, the profile needs {expected}")
    others = [column for column in schema["vector_columns"]
              if column.get("kind", "table") == "table"
              and (column.get("table"), column.get("column")) != ("ramDetections", "embedding")]
    return {"compatible": not problems, "problems": problems, "profile_dimensions": expected,
            "profile_model": profile.get("model"), "extension": schema.get("extension"),
            "embedding_type": declared, **stored, "other_vector_columns": others}


_STAMP = re.compile(r"search-(\d{8}T\d{6})\Z")


def prune(out_dir: Path, *, keep: int = 3, max_age_days: float = 30, apply: bool = False,
          now: float | None = None) -> dict:
    """Expire backups beyond ``keep`` newest that are older than ``max_age_days``.

    Only matched ``search-<stamp>.dump`` / ``.json`` pairs are considered; the
    newest verified backup is always kept, so a rollback path survives. Any
    symlink, unmatched file or unreadable manifest stops the run untouched.
    """
    if type(keep) is not int or keep < 1:
        raise BackupError("keep must be at least 1")
    if not isinstance(max_age_days, (int, float)) or max_age_days < 1:
        raise BackupError("max_age_days must be at least 1")
    out_dir = Path(out_dir)
    if out_dir.is_symlink() or not out_dir.is_dir():
        raise BackupError("Backup directory must be a real directory")
    now = time.time() if now is None else now
    stems: dict[str, set[str]] = {}
    for path in out_dir.iterdir():
        if not path.name.startswith("search-"):
            continue
        if path.is_symlink() or not path.is_file():
            raise BackupError("Backup directory holds a symlink or non-file; review it first")
        if path.suffix not in {".dump", ".json"} or not _STAMP.fullmatch(path.stem):
            raise BackupError("Backup directory holds an unexpected search-* file; review it first")
        stems.setdefault(path.stem, set()).add(path.suffix)
    if any(suffixes != {".dump", ".json"} for suffixes in stems.values()):
        raise BackupError("A backup is missing its dump or manifest; review it first")
    backups = []
    for stem in sorted(stems, reverse=True):
        try:
            manifest = json.loads((out_dir / f"{stem}.json").read_text())
        except ValueError as exc:
            raise BackupError("A backup manifest is unreadable; review it first") from exc
        if not isinstance(manifest, dict) or manifest.get("dump") != f"{stem}.dump":
            raise BackupError("A backup manifest does not name its own dump")
        created = time.mktime(time.strptime(_STAMP.fullmatch(stem).group(1), "%Y%m%dT%H%M%S"))
        verified = (isinstance(manifest.get("verified"), dict)
                    and manifest["verified"].get("restore_counts_match") is True)
        backups.append({"stem": stem, "age_days": (now - created) / 86400, "verified": verified})
    newest_verified = next((b["stem"] for b in backups if b["verified"]), None)
    expire = [b["stem"] for index, b in enumerate(backups)
              if index >= keep and b["age_days"] > max_age_days and b["stem"] != newest_verified]
    if apply:
        for stem in expire:
            for suffix in (".dump", ".json"):
                (out_dir / f"{stem}{suffix}").unlink()
    return {"applied": apply, "kept": len(backups) - len(expire), "expired": expire,
            "newest_verified": newest_verified}


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
    shape = sub.add_parser("schema-check", help="compare search storage with the pinned profile")
    shape.add_argument("--profile", required=True, type=Path)
    expire = sub.add_parser("prune", help="expire old backups (dry run unless --apply)")
    expire.add_argument("--out", required=True, type=Path)
    expire.add_argument("--keep", type=int, default=3)
    expire.add_argument("--max-age-days", type=float, default=30)
    expire.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if not _NAME.match(args.container):
        parser.error("invalid container name")
    try:
        if args.command == "backup":
            manifest = backup(run, args.container, args.out, args.profile)
            report = {"manifest": manifest["dump"].replace(".dump", ".json"), "bytes": manifest["bytes"],
                      "sha256": manifest["sha256"][:12], "counts": manifest["counts"]}
        elif args.command == "schema-check":
            report = schema_check(run, args.container, json.loads(args.profile.read_text()))
            print(json.dumps(report, sort_keys=True))
            return 0 if report["compatible"] else 2
        elif args.command == "prune":
            report = prune(args.out, keep=args.keep, max_age_days=args.max_age_days, apply=args.apply)
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
