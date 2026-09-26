"""Staged, resumable Find Anything index rebuild for a new encoder revision (#18).

Protect ranks ``ramDetections.embedding`` against query vectors from the AI
Key's pinned encoder, so a new encoder revision needs every stored vector
re-embedded before queries switch. This module does that in stages:

1. ``stage`` embeds each live object's source image with the *target*
   encoder into an isolated table, ``aikey_rebuild."stage_<rev12>"``, which
   Protect never reads. Each batch selects live rows that have no staged
   vector yet, so an interrupted run resumes where it stopped, and a repeated
   batch is an idempotent upsert. Objects whose source image is gone are
   recorded as ``no_source``.
2. ``verify`` checks that every staged row carries the target revision and
   768 values and that every live row is covered.
3. ``cutover`` requires the AI Key to be quiesced (so no query or index write
   runs meanwhile) and a verified backup whose counts match the live index.
   In one transaction it locks the table, re-checks coverage, snapshots the
   previous vectors into ``aikey_rebuild."prev_<rev12>"`` and swaps in the
   staged ones. Then it replaces ``search-profile.json`` with the target
   profile. If that fails, the previous vectors are restored.
4. ``rollback`` restores the snapshot and the previous profile. It refuses
   when rows were added after cutover, unless an embedder for the previous
   revision re-embeds them, so a rollback never leaves mixed vectors.

``ramDetections.embedding`` is NOT NULL, so rows cannot be cleared: a
cutover needs full coverage. The journal and the staged profile live in
``<state>/index-rebuild/<rev12>/``, apart from the live profile.

The image source is injected. The AI Key receives object crops only inside
Protect tasks, so no source for a live rebuild exists yet; see #18.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Awaitable, Callable, Protocol

from .embedding_profile import _with_fingerprint

DIMENSIONS = 768
_REVISION = re.compile(r"[0-9a-f]{64}\Z")
STATES = ("staging", "staged", "verified", "cut_over", "rolled_back")


class RebuildError(RuntimeError):
    """The rebuild cannot safely proceed; the live index is unchanged unless stated."""


class MixedRevisionError(RebuildError):
    """Vectors from another encoder revision would enter the index."""


class Embedder(Protocol):
    async def revision(self) -> str | None: ...
    async def embed(self, image: bytes) -> list[float]: ...


Source = Callable[[str], Awaitable[bytes | None]]


class IndexStore(Protocol):
    """Storage operations; ``PgStore`` implements them with SQL, tests in memory."""

    def prepare(self, tag: str, revision: str) -> None: ...
    def uncovered(self, tag: str, limit: int) -> list[str]: ...
    def upsert(self, tag: str, revision: str, rows: list[tuple[str, str, list[float] | None]]) -> None: ...
    def staged_summary(self, tag: str) -> dict: ...
    def live_counts(self) -> dict: ...
    def cutover(self, tag: str, revision: str) -> int: ...
    def rows_without_snapshot(self, tag: str) -> list[str]: ...
    def restore(self, tag: str, replacements: dict[str, list[float]]) -> int: ...
    def has_snapshot(self, tag: str) -> bool: ...
    def swapped(self, tag: str) -> bool: ...


def _check_vector(values: Any) -> list[float]:
    if (not isinstance(values, list) or len(values) != DIMENSIONS
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
        raise RebuildError("Embeddings must have 768 finite values")
    return [float(v) for v in values]


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".rebuild-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


class Rebuild:
    """One rebuild generation from the live profile's revision to ``target_revision``."""

    def __init__(self, store: IndexStore, state_dir: Path, *, target_revision: str, target_source: str,
                 now: Callable[[], float] = time.time):
        if not isinstance(target_revision, str) or not _REVISION.fullmatch(target_revision):
            raise RebuildError("The target revision must be a 64-digit hex digest")
        self.store, self.now = store, now
        self.state_dir = Path(state_dir)
        self.live_profile = self.state_dir / "search-profile.json"
        current = self._read(self.live_profile)
        if current is None:
            raise RebuildError("No live search profile; nothing to rebuild")
        if not _REVISION.fullmatch(str(current.get("revision", ""))):
            raise RebuildError("The live profile has no pinned revision; pin it before a rebuild")
        self.tag = target_revision[:12]
        self.directory = self.state_dir / "index-rebuild" / self.tag
        self.journal_path = self.directory / "journal.json"
        existing = self._read(self.journal_path)
        if current["revision"] == target_revision and not (existing and existing.get("state") == "cut_over"):
            raise RebuildError("The target revision is already the live revision")
        base = self._read(self.directory / "previous-profile.json") if current["revision"] == target_revision else current
        identity = {k: v for k, v in (base or current).items() if k != "fingerprint"}
        identity.update(revision=target_revision, source=target_source)
        self.target_profile = _with_fingerprint(identity)
        self.journal = existing or {
            "schema": 1, "revision": target_revision, "from_revision": current["revision"],
            "state": "staging", "counts": {"embedded": 0, "no_source": 0}, "last_error": None}
        if self.journal.get("revision") != target_revision or self.journal.get("state") not in STATES:
            raise RebuildError("The rebuild journal belongs to another generation; inspect it")
        # A journal for a finished generation keeps its original "from" revision.
        if self.journal["state"] not in ("cut_over",) and self.journal["from_revision"] != current["revision"]:
            raise RebuildError("The live revision changed since this generation started; start a new one")

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            if path.stat().st_size > 65536:
                raise RebuildError(f"{path.name} exceeds its size limit")
            value = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        if not isinstance(value, dict):
            raise RebuildError(f"{path.name} is not a JSON object")
        return value

    def _save(self, **changes) -> None:
        self.journal.update(changes, updated_at=self.now())
        _atomic_json(self.journal_path, self.journal)

    @property
    def state(self) -> str:
        return self.journal["state"]

    async def _check_embedder(self, embedder: Embedder, revision: str) -> None:
        served = await embedder.revision()
        if served != revision:
            raise MixedRevisionError("The staging encoder does not serve the expected revision")

    async def stage(self, embedder: Embedder, source: Source, *, batch: int = 32,
                    max_batches: int | None = None) -> dict:
        """Embed uncovered live objects; resumable and idempotent. Returns counts."""
        if self.state != "staging":
            if self.state in ("staged", "verified", "cut_over"):
                return dict(self.journal["counts"], state=self.state)      # idempotent retry
            raise RebuildError(f"Cannot stage a generation in state {self.state}")
        if not 1 <= batch <= 256:
            raise RebuildError("batch must be 1..256")
        revision = self.journal["revision"]
        _atomic_json(self.directory / "search-profile.json", self.target_profile)
        self.store.prepare(self.tag, revision)
        done = 0
        while max_batches is None or done < max_batches:
            await self._check_embedder(embedder, revision)
            objects = self.store.uncovered(self.tag, batch)
            if not objects:
                self._save(state="staged", last_error=None)
                break
            rows = []
            for object_id in objects:
                image = await source(object_id)
                rows.append((object_id, "no_source", None) if image is None
                            else (object_id, "embedded", _check_vector(await embedder.embed(image))))
            # Write the whole batch first; the journal only summarizes what the
            # store holds, so a crash here is repaired by the next batch.
            self.store.upsert(self.tag, revision, rows)
            summary = self.store.staged_summary(self.tag)
            self._save(counts={"embedded": summary["embedded"], "no_source": summary["no_source"]},
                       last_error=None)
            done += 1
        return dict(self.journal["counts"], state=self.state)

    def verify(self) -> dict:
        """Refuse mixed revisions, bad vectors and incomplete coverage."""
        if self.state not in ("staged", "verified"):
            raise RebuildError(f"Cannot verify a generation in state {self.state}")
        summary = self.store.staged_summary(self.tag)
        live = self.store.live_counts()
        if summary["revisions"] != [self.journal["revision"]]:
            raise MixedRevisionError("The staging table holds vectors of another revision")
        if summary["bad_vectors"]:
            raise RebuildError("The staging table holds vectors without 768 values")
        report = {"live_rows": live["rows"], "embedded": summary["embedded"],
                  "no_source": summary["no_source"], "uncovered": summary["uncovered"]}
        if summary["uncovered"] or summary["no_source"]:
            # embedding is NOT NULL: a row without a staged vector would keep an
            # old-revision vector, which is exactly the mixing this prevents.
            raise RebuildError("Not every live row has a staged vector; stage again or resolve sources")
        self._save(state="verified", verified=report)
        return report

    def cutover(self, *, backup: dict, key_quiesced: Callable[[], bool],
                write_profile: Callable[[Path, dict], None] = _atomic_json) -> dict:
        """Swap vectors and profile atomically enough to roll back; idempotent."""
        if self.state == "cut_over":
            return {"state": "cut_over", "already": True}
        if self.state != "verified":
            raise RebuildError(f"Cannot cut over a generation in state {self.state}")
        if not key_quiesced():
            raise RebuildError("Stop the AI Key's search and indexing before a cutover")
        live = self.store.live_counts()
        counts = (backup.get("counts") or {}) if isinstance(backup, dict) else {}
        if (backup.get("verified") is not True or counts.get("embeddings") != live["rows"]
                or (counts.get("tables") or {}).get("ramDetections") != live["rows"]):
            raise RebuildError("A verified backup matching the live index is required")
        previous = self._read(self.live_profile)
        if previous is None or previous.get("revision") != self.journal["from_revision"]:
            raise RebuildError("The live profile no longer matches this generation")
        _atomic_json(self.directory / "previous-profile.json", previous)
        if self.store.has_snapshot(self.tag) and self.store.swapped(self.tag):
            # A crash after the vector swap committed but before the journal
            # recorded it: finish the profile step, keep the original snapshot.
            swapped = self.store.live_counts()["rows"]
        else:
            swapped = self.store.cutover(self.tag, self.journal["revision"])
        try:
            write_profile(self.live_profile, self.target_profile)
        except Exception as exc:
            self.store.restore(self.tag, {})
            _atomic_json(self.live_profile, previous)
            self._save(state="verified", last_error="profile write failed; vectors restored")
            raise RebuildError("Profile swap failed; the previous vectors and profile were restored") from exc
        self._save(state="cut_over", cut_over={"rows": swapped, "at": self.now()})
        return {"state": "cut_over", "rows": swapped}

    async def rollback(self, *, key_quiesced: Callable[[], bool],
                       previous_embedder: Embedder | None = None, source: Source | None = None) -> dict:
        """Restore the snapshot and the previous profile; idempotent."""
        if self.state == "rolled_back":
            return {"state": "rolled_back", "already": True}
        if self.state != "cut_over":
            raise RebuildError(f"Cannot roll back a generation in state {self.state}")
        if not key_quiesced():
            raise RebuildError("Stop the AI Key's search and indexing before a rollback")
        previous = self._read(self.directory / "previous-profile.json")
        if previous is None or not self.store.has_snapshot(self.tag):
            raise RebuildError("The rollback snapshot or previous profile is missing")
        added = self.store.rows_without_snapshot(self.tag)
        replacements = {}
        if added:
            if previous_embedder is None or source is None:
                raise RebuildError(f"{len(added)} rows were indexed after cutover; supply an embedder "
                                   "for the previous revision to re-embed them")
            await self._check_embedder(previous_embedder, previous["revision"])
            for object_id in added:
                image = await source(object_id)
                if image is None:
                    raise RebuildError("A row added after cutover has no source; rollback would mix vectors")
                replacements[object_id] = _check_vector(await previous_embedder.embed(image))
        restored = self.store.restore(self.tag, replacements)
        _atomic_json(self.live_profile, previous)
        self._save(state="rolled_back", rolled_back={"rows": restored, "reembedded": len(replacements),
                                                     "at": self.now()})
        return {"state": "rolled_back", "rows": restored, "reembedded": len(replacements)}


class MemoryStore:
    """In-memory store for tests and dry runs; mirrors PgStore's semantics."""

    def __init__(self, live: dict[str, list[float]]):
        self.live = {k: list(v) for k, v in live.items()}
        self.stage: dict[str, dict[str, tuple[str, str, list[float] | None]]] = {}
        self.snapshot: dict[str, dict[str, list[float]]] = {}
        self.fail_cutover = False

    def prepare(self, tag, revision):
        self.stage.setdefault(tag, {})

    def uncovered(self, tag, limit):
        staged = self.stage[tag]
        return sorted(k for k in self.live if k not in staged)[:limit]

    def upsert(self, tag, revision, rows):
        for object_id, status, vector in rows:
            self.stage[tag][object_id] = (revision, status, vector)

    def staged_summary(self, tag):
        staged = self.stage.get(tag, {})
        return {"embedded": sum(1 for _, s, _ in staged.values() if s == "embedded"),
                "no_source": sum(1 for _, s, _ in staged.values() if s == "no_source"),
                "revisions": sorted({r for r, _, _ in staged.values()}),
                "bad_vectors": sum(1 for _, s, v in staged.values()
                                   if s == "embedded" and (v is None or len(v) != DIMENSIONS)),
                "uncovered": sum(1 for k in self.live if k not in staged)}

    def live_counts(self):
        return {"rows": len(self.live)}

    def cutover(self, tag, revision):
        staged = self.stage[tag]
        if any(k not in staged or staged[k][1] != "embedded" or staged[k][0] != revision for k in self.live):
            raise RebuildError("Coverage changed before cutover; nothing was swapped")
        if self.fail_cutover:
            raise RebuildError("Simulated cutover transaction failure; nothing was swapped")
        # An earlier snapshot whose swap was undone holds the same vectors; replace it.
        self.snapshot[tag] = {k: list(v) for k, v in self.live.items()}
        for k in self.live:
            self.live[k] = list(staged[k][2])
        return len(self.live)

    def rows_without_snapshot(self, tag):
        return sorted(k for k in self.live if k not in self.snapshot[tag])

    def restore(self, tag, replacements):
        snapshot = self.snapshot[tag]
        for k in self.live:
            self.live[k] = list(replacements[k]) if k in replacements else list(snapshot[k])
        return len(self.live)

    def has_snapshot(self, tag):
        return tag in self.snapshot

    def swapped(self, tag):
        staged = self.stage.get(tag, {})
        return bool(self.live) and all(k in staged and staged[k][2] == v for k, v in self.live.items())


class PgStore:
    """SQL implementation on the search database (psycopg 3, optional dependency).

    Staging and snapshot tables live in schema ``aikey_rebuild``; the live
    table is only read, except inside the cutover and restore transactions.
    """

    SCHEMA = "aikey_rebuild"

    def __init__(self, connect: Callable[[], Any]):
        self.connect = connect

    def _names(self, tag):
        if not re.fullmatch(r"[0-9a-f]{12}", tag):
            raise RebuildError("Invalid generation tag")
        return f'{self.SCHEMA}."stage_{tag}"', f'{self.SCHEMA}."prev_{tag}"'

    def prepare(self, tag, revision):
        stage, _ = self._names(tag)
        with self.connect() as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.SCHEMA}")
            conn.execute(f"CREATE TABLE IF NOT EXISTS {stage} (object_id varchar PRIMARY KEY, "
                         "revision char(64) NOT NULL, status varchar NOT NULL "
                         "CHECK (status IN ('embedded','no_source')), embedding vector(768), "
                         "updated_at timestamptz NOT NULL DEFAULT now(), "
                         "CHECK ((status = 'embedded') = (embedding IS NOT NULL)))")

    def uncovered(self, tag, limit):
        stage, _ = self._names(tag)
        with self.connect() as conn:
            rows = conn.execute(
                f'SELECT d."smartDetectObjectId" FROM public."ramDetections" d LEFT JOIN {stage} s '
                f'ON s.object_id = d."smartDetectObjectId" WHERE s.object_id IS NULL '
                f'ORDER BY d."smartDetectObjectId" LIMIT %s', (limit,)).fetchall()
        return [r[0] for r in rows]

    def upsert(self, tag, revision, rows):
        stage, _ = self._names(tag)
        with self.connect() as conn, conn.transaction():
            for object_id, status, vector in rows:
                conn.execute(
                    f"INSERT INTO {stage} (object_id, revision, status, embedding) "
                    f"VALUES (%s, %s, %s, %s::vector) ON CONFLICT (object_id) DO UPDATE SET "
                    f"revision = EXCLUDED.revision, status = EXCLUDED.status, "
                    f"embedding = EXCLUDED.embedding, updated_at = now()",
                    (object_id, revision, status, None if vector is None else json.dumps(vector)))

    def staged_summary(self, tag):
        stage, _ = self._names(tag)
        with self.connect() as conn:
            embedded, no_source, revisions, bad = conn.execute(
                f"SELECT count(*) FILTER (WHERE status='embedded'), count(*) FILTER (WHERE status='no_source'), "
                f"coalesce(array_agg(DISTINCT revision), '{{}}'), "
                f"count(*) FILTER (WHERE status='embedded' AND vector_dims(embedding) <> 768) FROM {stage}").fetchone()
            (uncovered,) = conn.execute(
                f'SELECT count(*) FROM public."ramDetections" d LEFT JOIN {stage} s '
                f'ON s.object_id = d."smartDetectObjectId" WHERE s.object_id IS NULL').fetchone()
        return {"embedded": embedded, "no_source": no_source,
                "revisions": sorted(r.strip() for r in revisions), "bad_vectors": bad, "uncovered": uncovered}

    def live_counts(self):
        with self.connect() as conn:
            (rows,) = conn.execute('SELECT count(*) FROM public."ramDetections"').fetchone()
        return {"rows": rows}

    def cutover(self, tag, revision):
        stage, prev = self._names(tag)
        with self.connect() as conn, conn.transaction():
            conn.execute('LOCK TABLE public."ramDetections" IN SHARE ROW EXCLUSIVE MODE')
            (missing,) = conn.execute(
                f'SELECT count(*) FROM public."ramDetections" d LEFT JOIN {stage} s '
                f"ON s.object_id = d.\"smartDetectObjectId\" AND s.status = 'embedded' AND s.revision = %s "
                f"WHERE s.object_id IS NULL", (revision,)).fetchone()
            if missing:
                raise RebuildError("Coverage changed before cutover; nothing was swapped")
            # Only reached when the live vectors are not the staged ones (the
            # engine checks swapped() first), so an old snapshot is stale.
            conn.execute(f"DROP TABLE IF EXISTS {prev}")
            conn.execute(f'CREATE TABLE {prev} AS SELECT "smartDetectObjectId" AS object_id, embedding '
                         f'FROM public."ramDetections"')
            conn.execute(f"ALTER TABLE {prev} ADD PRIMARY KEY (object_id)")
            swapped = conn.execute(
                f'UPDATE public."ramDetections" d SET embedding = s.embedding FROM {stage} s '
                f'WHERE s.object_id = d."smartDetectObjectId"').rowcount
            (total,) = conn.execute('SELECT count(*) FROM public."ramDetections"').fetchone()
            if swapped != total:
                raise RebuildError("Swapped row count differs from the live rows; transaction rolled back")
        return swapped

    def rows_without_snapshot(self, tag):
        _, prev = self._names(tag)
        with self.connect() as conn:
            rows = conn.execute(f'SELECT d."smartDetectObjectId" FROM public."ramDetections" d LEFT JOIN {prev} p '
                                f'ON p.object_id = d."smartDetectObjectId" WHERE p.object_id IS NULL '
                                f'ORDER BY 1').fetchall()
        return [r[0] for r in rows]

    def restore(self, tag, replacements):
        _, prev = self._names(tag)
        with self.connect() as conn, conn.transaction():
            conn.execute('LOCK TABLE public."ramDetections" IN SHARE ROW EXCLUSIVE MODE')
            restored = conn.execute(f'UPDATE public."ramDetections" d SET embedding = p.embedding FROM {prev} p '
                                    f'WHERE p.object_id = d."smartDetectObjectId"').rowcount
            for object_id, vector in replacements.items():
                restored += conn.execute('UPDATE public."ramDetections" SET embedding = %s::vector '
                                         'WHERE "smartDetectObjectId" = %s',
                                         (json.dumps(vector), object_id)).rowcount
            (total,) = conn.execute('SELECT count(*) FROM public."ramDetections"').fetchone()
            if restored != total:
                raise RebuildError("Restored row count differs from the live rows; transaction rolled back")
        return restored

    def has_snapshot(self, tag):
        _, prev = self._names(tag)
        with self.connect() as conn:
            (exists,) = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (prev,)).fetchone()
        return bool(exists)

    def swapped(self, tag):
        stage, _ = self._names(tag)
        with self.connect() as conn:
            same, total = conn.execute(
                f'SELECT count(s.object_id) FILTER (WHERE d.embedding = s.embedding), count(*) '
                f'FROM public."ramDetections" d LEFT JOIN {stage} s ON s.object_id = d."smartDetectObjectId"'
            ).fetchone()
        return total > 0 and same == total


class ClipEmbedder:
    """The staging encoder: a local CLIP server pinned to the target revision."""

    def __init__(self, clip_server: str, revision: str):
        from .clip import ClipClient
        if not _REVISION.fullmatch(revision or ""):
            raise RebuildError("The staging encoder needs a 64-digit revision")
        self.client = ClipClient({"clip_server": clip_server}, timeout_s=60, expected_revision=revision)

    async def revision(self):
        return await self.client.revision()

    async def embed(self, image):
        (vector,) = await self.client.embed_regions(image, [[0.0, 0.0, 1.0, 1.0]])
        return vector

    async def close(self):
        await self.client.close()


def status(state_dir: Path) -> list[dict]:
    """Every generation's journal state and counts; reads files only."""
    root = Path(state_dir) / "index-rebuild"
    out = []
    for journal in sorted(root.glob("*/journal.json")) if root.exists() else []:
        value = json.loads(journal.read_text())
        out.append({"generation": journal.parent.name, "state": value.get("state"),
                    "from": str(value.get("from_revision", ""))[:12], "to": str(value.get("revision", ""))[:12],
                    "counts": value.get("counts"), "last_error": value.get("last_error")})
    return out


# Cutover stays unavailable until both exist; flipping these is a reviewed code
# change, not a setting (#18).
APPROVED_IMAGE_SOURCE = None
APPROVED_NATIVE_READBACK = None
_FIXED_BLOCKERS = (
    "No approved stored-object image source: the AI Key receives crops only inside Protect tasks",
    "No approved native Protect post-cutover readback: Protect searches only the live table, "
    "so a staged index can be read back natively only after cutover")


def _latest_backup(backups_dir: Path | None, live_rows: int | None, now: float) -> dict:
    if backups_dir is None or not Path(backups_dir).is_dir():
        return {"latest": None, "verified": False, "matches_live": None,
                "reason": "No backup directory is configured"}
    manifests = sorted(Path(backups_dir).glob("search-*.json"), key=lambda p: p.name, reverse=True)
    for path in manifests:
        try:
            value = json.loads(path.read_text())
            counts = value["counts"]
            dump = Path(backups_dir) / value["dump"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        rows = (counts.get("tables") or {}).get("ramDetections")
        verified = isinstance(value.get("verified"), dict) and value["verified"].get("restore_counts_match") is True
        matches = None if live_rows is None else rows == live_rows == counts.get("embeddings")
        age = max(0, int(now - path.stat().st_mtime))
        reason = None
        if not dump.is_file():
            reason = "The latest backup's dump file is missing"
        elif not verified:
            reason = "The latest backup has no recorded scratch-restore verification"
        elif matches is False:
            reason = f"The latest backup holds {rows} rows; the live index holds {live_rows}"
        elif matches is None:
            reason = "Live row count unavailable; cannot compare with the backup"
        return {"latest": path.name, "rows": rows, "age_s": age, "verified": verified and dump.is_file(),
                "matches_live": matches, "reason": reason}
    return {"latest": None, "verified": False, "matches_live": None, "reason": "No backup manifest found"}


def migration_plan(state_dir: Path, *, backups_dir: Path | None = None, live_rows: int | None = None,
                   now: Callable[[], float] = time.time) -> dict:
    """Read-only index-migration status and dry-run plan; apply is never available here.

    Reads the live profile, every generation's journal and the newest backup
    manifest. ``live_rows`` is the live ``ramDetections`` count when the caller
    can read it. Nothing is written.
    """
    state_dir = Path(state_dir)
    try:
        live = json.loads((state_dir / "search-profile.json").read_text())
    except (OSError, ValueError):
        live = None
    live_revision = live.get("revision") if isinstance(live, dict) else None
    pinned = isinstance(live_revision, str) and bool(_REVISION.fullmatch(live_revision))
    generations = []
    root = state_dir / "index-rebuild"
    for journal_path in sorted(root.glob("*/journal.json")) if root.is_dir() else []:
        entry = {"generation": journal_path.parent.name, "problems": []}
        try:
            journal = json.loads(journal_path.read_text())
            if not isinstance(journal, dict) or journal.get("state") not in STATES:
                raise ValueError
        except (OSError, ValueError):
            entry.update(state="unreadable", problems=["The journal is unreadable; inspect it"])
            generations.append(entry)
            continue
        target, source_rev = str(journal.get("revision", "")), str(journal.get("from_revision", ""))
        counts = journal.get("counts") or {}
        entry.update(state=journal["state"], to=target[:12], **{"from": source_rev[:12]})
        if not _REVISION.fullmatch(target) or target[:12] != entry["generation"]:
            entry["problems"].append("The journal's target revision does not match its generation")
        if journal["state"] == "cut_over":
            if target != live_revision:
                entry["problems"].append("Recorded as cut over, but the live profile has another revision")
        elif journal["state"] != "rolled_back":
            if target == live_revision:
                entry["problems"].append("The target revision is already the live revision")
            elif source_rev != live_revision:
                entry["problems"].append(f"Started from {source_rev[:12]}, but the live revision is "
                                         f"{str(live_revision)[:12]}; this generation is stale")
        embedded, no_source = counts.get("embedded", 0), counts.get("no_source", 0)
        coverage = {"embedded": embedded, "no_source": no_source, "live_rows": live_rows}
        if live_rows:
            coverage["percent"] = round(100 * embedded / live_rows, 1)
            coverage["uncovered"] = max(0, live_rows - embedded - no_source)
        entry["coverage"] = coverage
        entry["previous_profile"] = (journal_path.parent / "previous-profile.json").is_file()
        entry["last_error"] = journal.get("last_error")
        generations.append(entry)
    backup = _latest_backup(backups_dir, live_rows, now())
    active = [g for g in generations if g.get("state") in ("staging", "staged", "verified") and not g["problems"]]
    cut = [g for g in generations if g.get("state") == "cut_over"]
    if cut:
        ready = all(g["previous_profile"] for g in cut)
        rollback = {"ready": ready, "reason": None if ready else "A cut-over generation lacks its previous profile",
                    "note": "The snapshot table is checked when the rollback runs"}
    else:
        rollback = {"ready": backup["verified"] and backup["matches_live"] is True,
                    "reason": backup["reason"] or None,
                    "note": "No cutover happened; the verified backup restores the live index"}
    reasons = list(_FIXED_BLOCKERS)
    if not pinned:
        reasons.append("The live profile has no pinned encoder revision")
    if not active:
        reasons.append("No staged generation for a new encoder revision")
    for g in active:
        if g["state"] != "verified":
            reasons.append(f"Generation {g['generation']} is {g['state']}, not verified")
        if g["coverage"].get("uncovered") or g["coverage"]["no_source"]:
            reasons.append(f"Generation {g['generation']} does not cover every live row")
    for g in generations:
        reasons.extend(f"Generation {g['generation']}: {p}" for p in g["problems"])
    if backup["reason"]:
        reasons.append(backup["reason"])
    reasons.append("The AI Key must be stopped for a cutover; this page never stops it")
    generation = active[0] if active else None

    def step(name, done, blocked=None):
        return {"step": name, "status": "done" if done else ("blocked" if blocked else "pending"),
                "detail": blocked}
    steps = [
        step("Pin the live encoder revision", pinned, None if pinned else "Pin it first"),
        step("Run a second CLIP server for the target revision", False),
        step("Stage every live object with the target encoder", bool(generation and generation["state"] in (
            "staged", "verified")), _FIXED_BLOCKERS[0]),
        step("Verify revision, dimensions and coverage", bool(generation and generation["state"] == "verified")),
        step("Take and verify a backup matching the live index",
             backup["verified"] and backup["matches_live"] is True, backup["reason"]),
        step("Stop the AI Key and cut over in one transaction", False, _FIXED_BLOCKERS[0]),
        step("Switch find_anything.clip_server, start the Key, and read back native search", False,
             _FIXED_BLOCKERS[1]),
    ]
    return {"live": {"pinned": pinned, "revision": str(live_revision)[:12] if pinned else None,
                     "source": live.get("source") if isinstance(live, dict) else None,
                     "rows": live_rows},
            "generations": generations, "backup": backup, "rollback": rollback,
            "apply": {"available": False, "reasons": reasons}, "steps": steps,
            "no_op": not generations}


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="aikey-index-rebuild")
    parser.add_argument("command", choices=["status", "plan"])
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--backups", type=Path)
    parser.add_argument("--live-rows", type=int)
    args = parser.parse_args(argv)
    if args.command == "plan":
        print(json.dumps(migration_plan(args.state_dir, backups_dir=args.backups, live_rows=args.live_rows),
                         indent=2))
        return 0
    # Staging, cutover and rollback need an image source; the AI Key only
    # receives object crops inside Protect tasks, so they stay library calls
    # until a source is approved (#18).
    print(json.dumps(status(args.state_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
