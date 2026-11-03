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

A reviewed ``ObjectCropArchive`` source now binds exported authorized original
native crops to exact index object/event/camera associations and input hashes.
Target quality and a frozen positive/negative native readback plan are required
before cutover; the generation remains ``awaiting_native_readback`` until a
pinned running query encoder, native search/event reloads and session logout
are verified. Legacy arbitrary callback sources stay gated. Operator commands
do not manage services or edit controller/device configuration. The participating
runtime's stable authority lock supplies actual exclusion for command-line swaps.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Awaitable, Callable, Protocol

from .embedding_profile import _with_fingerprint
from .encoding_identity import CLIP_ENCODING

DIMENSIONS = 768
_REVISION = re.compile(r"[0-9a-f]{64}\Z")
STATES = ("staging", "staged", "verified", "awaiting_native_readback",
          "cut_over", "rolled_back", "cancelled")


class RebuildError(RuntimeError):
    """The rebuild cannot safely proceed; the live index is unchanged unless stated."""


class MixedRevisionError(RebuildError):
    """Vectors from another encoder revision would enter the index."""


class RebuildOutcomeUncertain(RebuildError):
    """A journal write may have committed; read it back before another request."""


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
    def cutover(self, tag: str, revision: str, **checks) -> int: ...
    def rows_without_snapshot(self, tag: str) -> list[str]: ...
    def restore(self, tag: str, replacements: dict[str, list[float]]) -> int: ...
    def has_snapshot(self, tag: str) -> bool: ...
    def swapped(self, tag: str) -> bool: ...


def _check_vector(values: Any, dimensions=DIMENSIONS) -> list[float]:
    if (not isinstance(values, list) or len(values) != dimensions
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
        raise RebuildError(f"Embeddings must have {dimensions} finite values")
    result = [float(v) for v in values]
    norm = math.hypot(*result)
    if not math.isfinite(norm) or norm <= 0:
        raise RebuildError("Embeddings must have nonzero finite norm")
    return result


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".rebuild-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _atomic_json(path: Path, value: dict) -> None:
    raw = (json.dumps(value, allow_nan=False, indent=2, sort_keys=True)+"\n").encode()
    _atomic_bytes(path, raw)


@contextmanager
def _generation_lock(state_dir: Path, generation: str):
    """One immediate-refusal writer authority shared by every generation."""
    from .file_lock import acquire

    directory = descriptor = None
    try:
        try:
            directory = os.open(state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            parent = os.fstat(directory)
            if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
                raise RebuildError("The index state directory is not owner-controlled")
            for path in (state_dir / "index-rebuild", state_dir / "index-rebuild" / generation):
                if path.exists() or path.is_symlink():
                    meta = path.lstat()
                    if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.geteuid()
                            or meta.st_mode & 0o022):
                        raise RebuildError("The index generation directory is not owner-controlled")
            descriptor = os.open(".index-rebuild.lock", os.O_RDWR | os.O_CREAT
                                 | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            meta = os.fstat(descriptor)
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                    or meta.st_mode & 0o077 or meta.st_nlink != 1):
                raise RebuildError("The index writer lock is not a private regular file")
            try:
                acquire(descriptor, timeout=0)
            except TimeoutError:
                raise RebuildError("The index generation writer is busy; no change was made") from None
        except OSError as exc:
            raise RebuildError("The index generation authority is unavailable") from exc
        yield directory
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


class Rebuild:
    """One rebuild generation from the live profile's revision to ``target_revision``."""

    profile_kind = 'clip-basic-v1'
    dimensions = DIMENSIONS
    model_names = ('clip-ViT-L-14',)
    backends = ('local-clip-onnx',)
    target_encoding = CLIP_ENCODING
    allow_unversioned_source = False

    def __init__(self, store: IndexStore, state_dir: Path, *, target_revision: str, target_source: str,
                 now: Callable[[], float] = time.time, generation: str | None = None):
        if not isinstance(target_revision, str) or not _REVISION.fullmatch(target_revision):
            raise RebuildError("The target revision must be a 64-digit hex digest")
        self.store, self.now = store, now
        self.target_revision, self.target_source = target_revision, target_source
        self.state_dir = Path(state_dir)
        self.live_profile = self.state_dir / "search-profile.json"
        current = self._read(self.live_profile)
        if current is None:
            raise RebuildError("No live search profile; nothing to rebuild")
        if (current.get("profile") != self.profile_kind or current.get("model") not in self.model_names
                or current.get("dimensions") != self.dimensions or current.get("backend") not in self.backends):
            raise RebuildError("The live profile does not match this index space and storage width")
        if current != _with_fingerprint({k: v for k, v in current.items() if k != "fingerprint"}):
            raise RebuildError("The live search-profile fingerprint is invalid")
        if not _REVISION.fullmatch(str(current.get("revision", ""))) and not (
                self.allow_unversioned_source and current.get("revision") is None):
            raise RebuildError("The live profile has no pinned revision; pin it before a rebuild")
        self.tag = generation or target_revision[:12]
        if not isinstance(self.tag, str) or not re.fullmatch(r"[0-9a-f]{12}", self.tag):
            raise RebuildError("The generation identifier must be 12 lowercase hex digits")
        self.directory = self.state_dir / "index-rebuild" / self.tag
        self.journal_path = self.directory / "journal.json"
        existing = self._read(self.journal_path)
        candidate = {k: v for k, v in current.items() if k != "fingerprint"}
        candidate.update(revision=target_revision, source=target_source, **self.target_encoding)
        candidate = self._target_identity(candidate)
        at_target = current == _with_fingerprint(candidate)
        if at_target and not (existing and existing.get("state") in ("verified", "awaiting_native_readback", "cut_over")):
            raise RebuildError("The target revision is already the live revision")
        base = self._read(self.directory / "previous-profile.json") if at_target else current
        if at_target and (base is None or base.get("revision") != existing.get("from_revision")
                          or base == _with_fingerprint(candidate)):
            raise RebuildError("The previous profile does not match this generation")
        identity = {k: v for k, v in base.items() if k != "fingerprint"}
        identity.update(revision=target_revision, source=target_source, **self.target_encoding)
        identity = self._target_identity(identity)
        self.target_profile = _with_fingerprint(identity)
        self.journal = existing or {
            "schema": 1, "revision": target_revision, "from_revision": current.get("revision"),
            "generation_id": self.tag, "space": self.profile_kind,
            "from_unversioned": current.get("revision") is None,
            "state": "staging", "counts": {"embedded": 0, "no_source": 0}, "last_error": None}
        if (self.journal.get("revision") != target_revision or self.journal.get("state") not in STATES
                or self.journal.get("generation_id", target_revision[:12]) != self.tag):
            raise RebuildError("The rebuild journal belongs to another generation; inspect it")
        if (existing and existing.get("target_fingerprint") is not None
                and (existing["target_fingerprint"] != self.target_profile["fingerprint"]
                     or existing.get("from_fingerprint") != base.get("fingerprint"))):
            raise RebuildError("The generation's full encoder identity changed; use a new generation")
        # A journal for a finished generation keeps its original "from" revision.
        recovering_profile = at_target and self.journal["state"] == "verified"
        if recovering_profile and (current != self.target_profile or not self.store.has_snapshot(self.tag)
                                   or not self.store.swapped(self.tag)):
            raise RebuildError("The interrupted profile swap cannot be verified; inspect it")
        if (self.journal["state"] not in ("cut_over", "awaiting_native_readback") and not recovering_profile
                and self.journal["from_revision"] != current.get("revision")):
            raise RebuildError("The live revision changed since this generation started; start a new one")

    def _target_identity(self, identity):
        return dict(identity, encoding_required=True)

    def _target_source_supported(self):
        from .clip import _local_server
        return _local_server(self.target_source)

    @staticmethod
    def _record(path: Path | str, *, dir_fd: int | None = None, include_bytes=False) -> tuple:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        except FileNotFoundError:
            return (None, None, None) if include_bytes else (None, None)
        except OSError as exc:
            raise RebuildError("The index record is unavailable") from exc
        try:
            meta = os.fstat(descriptor)
            if not stat.S_ISREG(meta.st_mode) or meta.st_size > 65536:
                raise RebuildError("The index record is not a bounded regular file")
            stream = os.fdopen(descriptor, "rb")
            descriptor = None
            with stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise RebuildError("The index record exceeds its size limit")
        except OSError as exc:
            raise RebuildError("The index record could not be read") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError
                value[key] = item
            return value

        def finite(text):
            value = float(text)
            if not math.isfinite(value):
                raise ValueError
            return value

        def invalid(_):
            raise ValueError

        try:
            value = json.loads(raw, object_pairs_hook=unique, parse_float=finite,
                               parse_constant=invalid)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise RebuildError("The index record is not valid JSON") from exc
        if not isinstance(value, dict):
            raise RebuildError("The index record is not a JSON object")
        result = value, hashlib.sha256(raw).hexdigest()
        return (*result, raw) if include_bytes else result

    @staticmethod
    def _read(path: Path) -> dict | None:
        return Rebuild._record(path)[0]

    @contextmanager
    def _mutation(self):
        with _generation_lock(self.state_dir, self.tag) as state_fd:
            fresh = type(self)(self.store, self.state_dir, target_revision=self.target_revision,
                            target_source=self.target_source, now=self.now, generation=self.tag)
            self.journal, self.target_profile = fresh.journal, fresh.target_profile
            self._state_fd = state_fd
            try:
                yield
            finally:
                del self._state_fd

    def _save(self, **changes) -> None:
        self.journal.update(changes, updated_at=self.now())
        _atomic_json(self.journal_path, self.journal)

    @property
    def state(self) -> str:
        return self.journal["state"]

    def _check_source_identity(self):
        expected = self.journal.get("source_identity")
        if expected is not None and expected != self.store.source_identity():
            raise RebuildError("The frozen index source changed; export a fresh source and use a new generation")

    async def _check_embedder(self, embedder: Embedder, revision: str) -> None:
        served = await embedder.revision()
        if served != revision:
            raise MixedRevisionError("The staging encoder does not serve the expected revision")

    async def stage(self, embedder: Embedder, source: Source, *, batch: int = 32,
                    max_batches: int | None = None) -> dict:
        _require_approval(readback=False, source=source)
        if type(batch) is not int or not 1 <= batch <= 256:
            raise RebuildError("batch must be 1..256")
        if max_batches is not None and (type(max_batches) is not int or max_batches < 1):
            raise RebuildError("max_batches must be a positive integer")
        with self._mutation():
            return await self._stage(embedder, source, batch=batch, max_batches=max_batches)

    async def _stage(self, embedder: Embedder, source: Source, *, batch: int,
                     max_batches: int | None) -> dict:
        """Embed uncovered live objects; resumable and idempotent. Returns counts."""
        _require_approval(readback=False, source=source)
        allowed = ("staging", "staged", "verified", "awaiting_native_readback", "cut_over")
        if self.state not in allowed:
            raise RebuildError(f"Cannot stage a generation in state {self.state}")
        if _reviewed_source(source):
            if source.space != self.profile_kind:
                raise RebuildError("The source archive belongs to another embedding space")
            source.validate_binding()
            binding = self.journal.get("source_archive_sha256")
            if binding is not None and binding != source.manifest_sha256:
                raise RebuildError("The source archive differs from this generation's frozen source")
            if binding is None:
                if self.state != "staging" or self.journal["counts"]["embedded"]:
                    raise RebuildError("The older unbound stage requires a fresh generation")
                self._save(source_archive_sha256=source.manifest_sha256,
                           source_identity=self.store.source_identity(),
                           from_fingerprint=self._read(self.live_profile)["fingerprint"],
                           target_fingerprint=self.target_profile["fingerprint"])
            if self.state in ("staging", "staged", "verified"):
                self._check_source_identity()
        elif self.journal.get("source_archive_sha256") is not None:
            raise RebuildError("This generation requires its frozen source archive")
        if self.state != "staging":
            return dict(self.journal["counts"], state=self.state)
        if not 1 <= batch <= 256:
            raise RebuildError("batch must be 1..256")
        revision = self.journal["revision"]
        _atomic_json(self.directory / "search-profile.json", self.target_profile)
        self.store.prepare(self.tag, revision)
        done = 0
        while max_batches is None or done < max_batches:
            self._check_source_identity()
            await self._check_embedder(embedder, revision)
            objects = self.store.uncovered(self.tag, batch)
            if not objects:
                self._save(state="staged", last_error=None)
                break
            rows = []
            for object_id in objects:
                image = await source(object_id)
                rows.append((object_id, "no_source", None) if image is None
                            else (object_id, "embedded", _check_vector(await embedder.embed(image), self.dimensions)))
            # Write the whole batch first; the journal only summarizes what the
            # store holds, so a crash here is repaired by the next batch.
            self._check_source_identity()
            self.store.upsert(self.tag, revision, rows)
            summary = self.store.staged_summary(self.tag)
            self._save(counts={"embedded": summary["embedded"], "no_source": summary["no_source"]},
                       last_error=None)
            done += 1
        return dict(self.journal["counts"], state=self.state)

    def verify(self) -> dict:
        with self._mutation():
            return self._verify()

    def _verify(self) -> dict:
        """Refuse mixed revisions, bad vectors and incomplete coverage."""
        if self.state not in ("staged", "verified"):
            raise RebuildError(f"Cannot verify a generation in state {self.state}")
        self._check_source_identity()
        summary = self.store.staged_summary(self.tag)
        live = self.store.live_counts()
        if summary["revisions"] != ([self.journal["revision"]] if live["rows"] else []):
            raise MixedRevisionError("The staging table holds vectors of another revision")
        if summary["bad_vectors"]:
            raise RebuildError(f"The staging table holds vectors without {self.dimensions} values")
        report = {"live_rows": live["rows"], "embedded": summary["embedded"],
                  "no_source": summary["no_source"], "uncovered": summary["uncovered"]}
        if summary["uncovered"] or summary["no_source"]:
            # embedding is NOT NULL: a row without a staged vector would keep an
            # old-revision vector, which is exactly the mixing this prevents.
            raise RebuildError("Not every live row has a staged vector; stage again or resolve sources")
        self._save(state="verified", verified=report)
        return report

    async def qualify(self, embedder, plan) -> dict:
        from .index_quality import qualify_staged, validate_plan
        from .index_readback import ReadbackError
        with self._mutation():
            validate_plan(self, plan)
            frozen = self._read(self.directory / "readback-plan.json")
            if frozen is not None and frozen != plan.value:
                raise ReadbackError("Quality targets are frozen for this generation; use a new generation")
            # Persist before the first target query, including failed attempts.
            _atomic_json(self.directory / "readback-plan.json", plan.value)
            self._save(quality={"plan_sha256": plan.sha256, "passed": False})
            receipt = await qualify_staged(self, embedder, plan)
            _atomic_json(self.directory / "staged-quality.json", receipt)
            self._save(quality={k: receipt[k] for k in (
                "plan_sha256", "source_archive_sha256", "target_revision", "staged_identity", "passed")})
            return receipt

    def _backup_matches(self, backup, rows):
        counts = (backup.get("counts") or {}) if isinstance(backup, dict) else {}
        return (isinstance(backup, dict) and backup.get("verified") is True
                and type(counts.get("embeddings")) is int and counts.get("embeddings") == rows
                and type((counts.get("tables") or {}).get("ramDetections")) is int
                and (counts.get("tables") or {}).get("ramDetections") == rows)

    def _native_cutover_admission(self, source, plan):
        if source is None and plan is None:
            return  # Legacy synthetic approval fixture; default gates remain closed.
        if self.state == "verified":
            self._check_source_identity()
            if getattr(plan, "value", {}).get("empty_index") is True and self.store.live_counts()["rows"] != 0:
                raise RebuildError("An empty-index plan cannot cut over existing history")
        if (not _reviewed_pair(source, plan) or source.space != self.profile_kind
                or plan.space != self.profile_kind or not self._target_source_supported()
                or source.validate_binding() != self.journal.get("source_archive_sha256")
                or not plan.matches(source_archive_sha256=source.manifest_sha256,
                    from_revision=self.journal["from_revision"], target_revision=self.target_revision)):
            raise RebuildError("The native cutover source or plan differs from this generation")
        from_profile = plan.value.get("from_profile_fingerprint")
        target_profile = plan.value.get("target_profile_fingerprint")
        if ((from_profile is not None and from_profile != self.journal.get("from_fingerprint"))
                or (target_profile is not None and target_profile != self.target_profile["fingerprint"])):
            raise RebuildError("The readback plan's full encoder identities do not match this generation")
        quality = self.journal.get("quality")
        if (not isinstance(quality, dict) or quality.get("passed") is not True
                or quality.get("plan_sha256") != plan.sha256
                or quality.get("target_revision") != self.target_revision
                or quality.get("source_archive_sha256") != source.manifest_sha256
                or (self.state == "verified"
                    and quality.get("staged_identity") != self.store.staged_identity(self.tag))):
            raise RebuildError("Fresh matching staged-index quality is required before native cutover")
        if self._read(self.directory / "readback-plan.json") != plan.value:
            raise RebuildError("The frozen native readback plan changed")
        if self.state in ("awaiting_native_readback", "cut_over") and (
                self._read(self.live_profile) != self.target_profile or not self.store.has_snapshot(self.tag)):
            raise RebuildError("The committed native generation no longer matches its profile or snapshot")

    def cutover(self, *, backup: dict, key_quiesced: Callable[[], bool],
                write_profile: Callable[[Path, dict], None] = _atomic_json,
                source=None, readback_plan=None) -> dict:
        _require_approval(readback=True, source=source, readback_plan=readback_plan)
        with self._mutation():
            self._native_cutover_admission(source, readback_plan)
            return self._cutover(backup=backup, key_quiesced=key_quiesced,
                                 write_profile=write_profile, source=source, readback_plan=readback_plan)

    def _cutover(self, *, backup: dict, key_quiesced: Callable[[], bool],
                 write_profile: Callable[[Path, dict], None], source=None, readback_plan=None) -> dict:
        """Swap vectors and profile; native use stays pending until separate readback."""
        _require_approval(readback=True, source=source, readback_plan=readback_plan)
        target_state = "awaiting_native_readback" if readback_plan is not None else "cut_over"
        if self.state in ("cut_over", "awaiting_native_readback"):
            return {"state": self.state, "already": True}
        if self.state != "verified":
            raise RebuildError(f"Cannot cut over a generation in state {self.state}")
        if not key_quiesced():
            raise RebuildError("Stop the AI Key's search and indexing before a cutover")
        live = self.store.live_counts()
        if not self._backup_matches(backup, live["rows"]):
            raise RebuildError("A verified backup matching the live index is required")
        previous, _, previous_raw = self._record(self.live_profile, include_bytes=True)
        if previous == self.target_profile:
            # Both swaps committed, but the journal write may have been
            # interrupted. Re-check durable evidence before finalizing; never
            # overwrite the original snapshot or previous profile on retry.
            saved_previous = self._read(self.directory / "previous-profile.json")
            if (saved_previous is None or saved_previous.get("revision") != self.journal["from_revision"]
                    or not self.store.has_snapshot(self.tag) or not self.store.swapped(self.tag)):
                raise RebuildError("The interrupted profile swap cannot be verified; inspect it")
            self._save(state=target_state, cut_over={"rows": live["rows"], "at": self.now()})
            return {"state": target_state, "rows": live["rows"]}
        if previous is None or previous.get("revision") != self.journal["from_revision"]:
            raise RebuildError("The live profile no longer matches this generation")
        _atomic_bytes(self.directory / "previous-profile.json", previous_raw)
        if self.store.has_snapshot(self.tag) and self.store.swapped(self.tag):
            # A crash after the vector swap committed but before the journal
            # recorded it: finish the profile step, keep the original snapshot.
            swapped = self.store.live_counts()["rows"]
        else:
            checks = {}
            if self.journal.get("source_identity") is not None:
                checks = dict(expected_source_identity=self.journal["source_identity"],
                              expected_staged_identity=self.journal["quality"]["staged_identity"])
            swapped = self.store.cutover(self.tag, self.journal["revision"], **checks)
        try:
            write_profile(self.live_profile, self.target_profile)
        except Exception as exc:
            self.store.restore(self.tag, {})
            _atomic_bytes(self.live_profile, previous_raw)
            self._save(state="verified", last_error="profile write failed; vectors restored")
            raise RebuildError("Profile swap failed; the previous vectors and profile were restored") from exc
        self._save(state=target_state, cut_over={"rows": swapped, "at": self.now()})
        return {"state": target_state, "rows": swapped}

    async def confirm_native(self, plan, native, *, runtime) -> dict:
        from .index_readback import readback_for_plan
        with self._mutation():
            if (self.state != "awaiting_native_readback"
                    or getattr(plan, "space", None) != self.profile_kind
                    or (self.journal.get("quality") or {}).get("plan_sha256") != plan.sha256
                    or self._read(self.directory / "readback-plan.json") != plan.value
                    or self._read(self.live_profile) != self.target_profile):
                raise RebuildError("Native readback does not match the pending migrated generation")
            attempt = secrets.token_hex(16)
            self._save(native_readback={"plan_sha256": plan.sha256, "passed": False,
                                       "attempt_id": attempt, "logout_acknowledged": False})
            receipt = dict(await readback_for_plan(plan, native, runtime=runtime), attempt_id=attempt)
            # The native session must be scoped outside this generation operation;
            # the caller then logs out. Until that acknowledgement, do not finalize.
            _atomic_json(self.directory / "native-readback-pending.json", receipt)
            self._save(native_readback={"plan_sha256": plan.sha256, "passed": True,
                                       "attempt_id": attempt, "logout_acknowledged": False})
            return receipt

    def finalize_native(self, *, logout_acknowledged: bool) -> dict:
        with self._mutation():
            receipt = self._read(self.directory / "native-readback-pending.json")
            quality = self.journal.get("quality") or {}
            if self.state == "cut_over":
                final = self._read(self.directory / "native-readback.json")
                if (isinstance(final, dict) and final.get("logout_acknowledged") is True
                        and final.get("plan_sha256") == quality.get("plan_sha256")
                        and final.get("target_revision") == self.target_revision
                        and self._read(self.live_profile) == self.target_profile):
                    return {"state": "cut_over", "native_verified": True, "already": True}
                raise RebuildError("The final native receipt no longer matches this generation")
            if (self.state != "awaiting_native_readback" or logout_acknowledged is not True
                    or not isinstance(receipt, dict) or receipt.get("passed") is not True
                    or (self.journal.get("native_readback") or {}).get("passed") is not True
                    or receipt.get("attempt_id") != (self.journal.get("native_readback") or {}).get("attempt_id")
                    or receipt.get("plan_sha256") != quality.get("plan_sha256")
                    or receipt.get("target_revision") != self.target_revision
                    or self._read(self.live_profile) != self.target_profile):
                raise RebuildError("Successful native readback and acknowledged logout are required")
            _atomic_json(self.directory / "native-readback.json", dict(receipt, logout_acknowledged=True))
            self._save(state="cut_over", native_readback={"plan_sha256": quality["plan_sha256"],
                       "passed": True, "attempt_id": receipt["attempt_id"], "logout_acknowledged": True})
            return {"state": "cut_over", "native_verified": True}

    async def rollback(self, *, key_quiesced: Callable[[], bool],
                       previous_embedder: Embedder | None = None, source: Source | None = None) -> dict:
        with self._mutation():
            return await self._rollback(key_quiesced=key_quiesced,
                                        previous_embedder=previous_embedder, source=source)

    async def _rollback(self, *, key_quiesced: Callable[[], bool],
                        previous_embedder: Embedder | None, source: Source | None) -> dict:
        """Restore the snapshot and the previous profile; idempotent."""
        if self.state == "rolled_back":
            return {"state": "rolled_back", "already": True}
        if self.state not in ("cut_over", "awaiting_native_readback"):
            raise RebuildError(f"Cannot roll back a generation in state {self.state}")
        if not key_quiesced():
            raise RebuildError("Stop the AI Key's search and indexing before a rollback")
        previous, _, previous_raw = self._record(self.directory / "previous-profile.json", include_bytes=True)
        if previous is None or not self.store.has_snapshot(self.tag):
            raise RebuildError("The rollback snapshot or previous profile is missing")
        added = self.store.rows_without_snapshot(self.tag)
        replacements = {}
        if added:
            if not isinstance(previous.get("revision"), str) or not _REVISION.fullmatch(previous["revision"]):
                raise RebuildError("The previous checkpoint is unversioned; newly indexed rows block automatic rollback")
            if previous_embedder is None or source is None:
                raise RebuildError(f"{len(added)} rows were indexed after cutover; supply an embedder "
                                   "for the previous revision to re-embed them")
            if self.journal.get("source_archive_sha256") is not None:
                from .encoding_identity import UPGRADABLE_ENCODING
                expected_encoding = {k: previous.get(k) for k in UPGRADABLE_ENCODING}
                if (any(v is None for v in expected_encoding.values())
                        or getattr(previous_embedder, "encoding_identity", None) != expected_encoding):
                    raise RebuildError("The retained previous encoder must match the full prior encoding contract")
            await self._check_embedder(previous_embedder, previous["revision"])
            for object_id in added:
                image = await source(object_id)
                if image is None:
                    raise RebuildError("A row added after cutover has no source; rollback would mix vectors")
                replacements[object_id] = _check_vector(await previous_embedder.embed(image), self.dimensions)
        restored = self.store.restore(self.tag, replacements)
        _atomic_bytes(self.live_profile, previous_raw)
        self._save(state="rolled_back", rolled_back={"rows": restored, "reembedded": len(replacements),
                                                     "at": self.now()})
        return {"state": "rolled_back", "rows": restored, "reembedded": len(replacements)}

    def cancel(self, *, expected_journal: str | None = None,
               expected_profile: str | None = None) -> dict:
        """Cancel only an idle, existing, not-yet-live generation; retain its data."""
        _check_cancel_revisions(expected_journal, expected_profile)
        with self._mutation():
            return self._cancel(expected_journal=expected_journal, expected_profile=expected_profile)

    def _cancel(self, *, expected_journal: str | None, expected_profile: str | None) -> dict:
        self.journal, result = _cancel_record(self.state_dir, self.tag,
            expected_journal=expected_journal, expected_profile=expected_profile, now=self.now,
            state_fd=self._state_fd)
        return result


def _check_cancel_revisions(journal, profile):
    if journal is None and profile is None:
        return
    if any(not isinstance(v, str) or not _REVISION.fullmatch(v) for v in (journal, profile)):
        raise RebuildError("Two valid index migration revisions are required")


def _atomic_cancel_json(directory_fd, value):
    temporary = ".cancel-" + secrets.token_hex(12)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, "journal.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


@contextmanager
def _cancel_directories(state_dir, generation, state_fd):
    root_fd = generation_fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open("index-rebuild", flags, dir_fd=state_fd)
        generation_fd = os.open(generation, flags, dir_fd=root_fd)
        for fd in (root_fd, generation_fd):
            meta = os.fstat(fd)
            if meta.st_uid != os.geteuid() or meta.st_mode & 0o022:
                raise RebuildError("The index generation directory is not owner-controlled")
        _check_cancel_aliases(state_dir, generation, state_fd, root_fd, generation_fd)
        yield root_fd, generation_fd
    finally:
        if generation_fd is not None:
            os.close(generation_fd)
        if root_fd is not None:
            os.close(root_fd)


def _check_cancel_aliases(state_dir, generation, state_fd, root_fd, generation_fd):
    for path, parent_fd, expected_fd in ((state_dir, None, state_fd),
            ("index-rebuild", state_fd, root_fd), (generation, root_fd, generation_fd)):
        try:
            actual = os.stat(path, dir_fd=parent_fd, follow_symlinks=False)
            expected = os.fstat(expected_fd)
        except OSError as exc:
            raise RebuildError("The index generation directory changed; refresh") from exc
        if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise RebuildError("The index generation directory changed; refresh")


def _cancel_record(state_dir, generation, *, expected_journal, expected_profile, state_fd,
                   now=time.time):
    """Called only under the shared writer lock; needs no database capability."""
    with _cancel_directories(state_dir, generation, state_fd) as (root_fd, generation_fd):
        return _cancel_open_record(state_dir, generation, state_fd, root_fd, generation_fd,
                                   expected_journal, expected_profile, now)


def _valid_from_checkpoint(journal):
    revision = journal.get("from_revision")
    known = isinstance(revision, str) and bool(_REVISION.fullmatch(revision))
    full = all(isinstance(journal.get(k), str) and _REVISION.fullmatch(journal[k])
               for k in ("from_fingerprint", "target_fingerprint"))
    unknown = (revision is None and journal.get("from_unversioned") is True
               and journal.get("space") == "e5-session-v1" and full
               and journal["from_fingerprint"] != journal["target_fingerprint"])
    return known or unknown


def _cancel_open_record(state_dir, generation, state_fd, root_fd, generation_fd,
                        expected_journal, expected_profile, now):
    journal, journal_revision = Rebuild._record("journal.json", dir_fd=generation_fd)
    profile, profile_revision = Rebuild._record("search-profile.json", dir_fd=state_fd)
    if journal is None:
        raise RebuildError("No staged generation exists to cancel")
    if expected_journal is not None and (journal_revision != expected_journal
                                         or profile_revision != expected_profile):
        raise RebuildError("The index migration changed; refresh before cancelling")
    counts = journal.get("counts")
    if (type(journal.get("schema")) is not int or journal["schema"] != 1
            or journal.get("state") not in STATES
            or not isinstance(journal.get("revision"), str) or not _REVISION.fullmatch(journal["revision"])
            or not _valid_from_checkpoint(journal)
            or journal.get("generation_id", journal["revision"][:12]) != generation
            or (journal["revision"] == journal["from_revision"] and (
                journal.get("from_fingerprint") is None
                or journal.get("from_fingerprint") == journal.get("target_fingerprint")))
            or not isinstance(counts, dict) or set(counts) != {"embedded", "no_source"}
            or any(type(v) is not int or v < 0 for v in counts.values())):
        raise RebuildError("The index generation identity is invalid")
    if journal["state"] not in ("staging", "staged", "verified", "cancelled"):
        raise RebuildError(f"Cannot cancel a generation in state {journal['state']}; use rollback")
    if (profile is None or profile.get("revision") != journal["from_revision"]
            or (journal.get("from_fingerprint") is not None
                and profile.get("fingerprint") != journal["from_fingerprint"])):
        raise RebuildError("The live index changed; cancellation cannot replace rollback")
    if journal["state"] == "cancelled":
        return journal, {"state": "cancelled", "already": True}
    updated = dict(journal, state="cancelled", updated_at=now(),
                   cancelled={"at": now(), "staged_data_retained": True})
    _check_cancel_aliases(state_dir, generation, state_fd, root_fd, generation_fd)
    try:
        _atomic_cancel_json(generation_fd, updated)
        _check_cancel_aliases(state_dir, generation, state_fd, root_fd, generation_fd)
    except (OSError, RebuildError) as exc:
        raise RebuildOutcomeUncertain("Cancellation outcome is uncertain; refresh before retrying") from exc
    return updated, {"state": "cancelled", "staged_data_retained": True}


def cancel_generation(state_dir: Path, generation: str, *, expected_journal: str,
                      expected_profile: str) -> dict:
    """Revision-bound administration; no database, image source or model authority."""
    if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{12}", generation):
        raise RebuildError("Invalid index generation")
    if expected_journal is None or expected_profile is None:
        raise RebuildError("Two valid index migration revisions are required")
    _check_cancel_revisions(expected_journal, expected_profile)
    state_dir = Path(state_dir)
    with _generation_lock(state_dir, generation) as state_fd:
        return _cancel_record(state_dir, generation, expected_journal=expected_journal,
                              expected_profile=expected_profile, state_fd=state_fd)[1]


class MemoryStore:
    """In-memory store for tests and dry runs; mirrors PgStore's semantics."""

    def __init__(self, live: dict[str, list[float]]):
        self.live = {k: list(v) for k, v in live.items()}
        self.stage: dict[str, dict[str, tuple[str, str, list[float] | None]]] = {}
        self.snapshot: dict[str, dict[str, list[float]]] = {}
        self.fail_cutover = False

    def source_identity(self):
        return hashlib.sha256(json.dumps(sorted(self.live)).encode()).hexdigest()

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

    def cutover(self, tag, revision, *, expected_source_identity=None, expected_staged_identity=None):
        staged = self.stage[tag]
        if expected_source_identity is not None and self.source_identity() != expected_source_identity:
            raise RebuildError("The frozen source changed before the atomic swap")
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
        return all(k in staged and staged[k][2] == v for k, v in self.live.items())


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

    def source_rows(self, *, limit=100_000):
        if type(limit) is not int or not 1 <= limit <= 100_000:
            raise RebuildError("The index source row budget is invalid")
        with self.connect() as conn:
            rows = conn.execute(
                'SELECT "smartDetectObjectId", "eventId", "cameraId" FROM public."ramDetections" '
                'ORDER BY "smartDetectObjectId" LIMIT %s', (limit+1,)).fetchall()
        if len(rows) > limit:
            raise RebuildError("The complete index projection exceeds the source row budget")
        return [dict(zip(("object_id", "event_id", "camera_id"), row)) for row in rows]

    def source_identity(self):
        with self.connect() as conn:
            (digest,) = conn.execute("""
                SELECT encode(sha256(convert_to(coalesce(string_agg(
                  encode(sha256(convert_to(concat_ws(chr(31),"smartDetectObjectId","eventId",
                    "cameraId",start::text),'UTF8')),'hex'),'' ORDER BY "smartDetectObjectId"),''),
                  'UTF8')),'hex') FROM public."ramDetections"
                """).fetchone()
        return digest

    def staged_identity(self, tag):
        stage, _ = self._names(tag)
        with self.connect() as conn:
            (digest,) = conn.execute(
                "SELECT encode(sha256(convert_to(coalesce(string_agg("
                "encode(sha256(convert_to(concat_ws(chr(31),s.object_id,s.revision,s.status,"
                's.embedding::text,d."smartDetectObjectId",d."eventId",d."cameraId",d.start::text),'
                "'UTF8')),'hex'),'' ORDER BY coalesce(s.object_id,d.\"smartDetectObjectId\")),''),"
                f"'UTF8')),'hex') FROM {stage} s FULL OUTER JOIN public.\"ramDetections\" d "
                'ON d."smartDetectObjectId"=s.object_id').fetchone()
        return digest

    def rank_staged(self, tag, vector, *, k, min_cosine, camera_id, start_ms, end_ms):
        stage, _ = self._names(tag)
        values = _check_vector(vector)
        if type(k) is not int or not 1 <= k <= 10:
            raise RebuildError("The staged query bound is invalid")
        with self.connect() as conn:
            rows = conn.execute(
                f'SELECT s.object_id,d."eventId",d."cameraId",d.start,'
                f'1-(s.embedding <=> %s::vector) AS similarity FROM {stage} s '
                'JOIN public."ramDetections" d ON d."smartDetectObjectId"=s.object_id '
                "WHERE s.status='embedded' AND (%s::varchar IS NULL OR d.\"cameraId\"=%s) "
                'AND (%s::bigint IS NULL OR (d.start >= %s AND d.start < %s)) '
                'AND 1-(s.embedding <=> %s::vector) >= %s '
                'ORDER BY similarity DESC,s.object_id LIMIT %s',
                (json.dumps(values), camera_id, camera_id, start_ms, start_ms, end_ms,
                 json.dumps(values), min_cosine, k)).fetchall()
        return [dict(zip(("object_id", "event_id", "camera_id", "start_ms", "similarity"), row))
                for row in rows]

    def source_association(self, object_id):
        """Exact current object/event/camera join, without returning embeddings."""
        with self.connect() as conn:
            rows = conn.execute(
                'SELECT "eventId", "cameraId" FROM public."ramDetections" '
                'WHERE "smartDetectObjectId" = %s', (object_id,)).fetchall()
        return tuple(rows[0]) if len(rows) == 1 else None

    def live_counts(self):
        with self.connect() as conn:
            (rows,) = conn.execute('SELECT count(*) FROM public."ramDetections"').fetchone()
        return {"rows": rows}

    def cutover(self, tag, revision, *, expected_source_identity=None, expected_staged_identity=None):
        stage, prev = self._names(tag)
        with self.connect() as conn, conn.transaction():
            conn.execute('LOCK TABLE public."ramDetections" IN SHARE ROW EXCLUSIVE MODE')
            conn.execute(f'LOCK TABLE {stage} IN SHARE ROW EXCLUSIVE MODE')
            if ((expected_source_identity is not None and self.source_identity() != expected_source_identity)
                    or (expected_staged_identity is not None
                        and self.staged_identity(tag) != expected_staged_identity)):
                raise RebuildError("The frozen source or qualified stage changed before the atomic swap")
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
        return same == total


class ClipEmbedder:
    """The staging encoder: a local CLIP server pinned to the target revision."""

    def __init__(self, clip_server: str, revision: str, *, encoding_identity=None):
        from .clip import ClipClient
        if not _REVISION.fullmatch(revision or ""):
            raise RebuildError("The staging encoder needs a 64-digit revision")
        self.client = ClipClient({"clip_server": clip_server}, timeout_s=60, expected_revision=revision,
                                 require_encoding=True, expected_encoding=encoding_identity)
        self.encoding_identity = self.client.expected_encoding

    async def revision(self):
        return await self.client.revision()

    async def embed(self, image):
        (vector,) = await self.client.embed_regions(image, [[0.0, 0.0, 1.0, 1.0]])
        return vector

    async def embed_text(self, text):
        return await self.client.embed_text(text)

    async def close(self):
        await self.client.close()


def status(state_dir: Path) -> list[dict]:
    """Every generation's journal state and counts; reads files only."""
    root = Path(state_dir) / "index-rebuild"
    out = []
    for journal in sorted(root.glob("*/journal.json")) if root.exists() else []:
        value = Rebuild._read(journal)
        if value is None:
            raise RebuildError("The index record disappeared")
        out.append({"generation": journal.parent.name, "state": value.get("state"),
                    "from": str(value.get("from_revision", ""))[:12], "to": str(value.get("revision", ""))[:12],
                    "counts": value.get("counts"), "last_error": value.get("last_error")})
    return out


# Arbitrary source/readback callbacks stay unavailable. These legacy fixture
# hooks are never operator settings; the reviewed archive/plan path is explicit.
APPROVED_IMAGE_SOURCE = None
APPROVED_NATIVE_READBACK = None
_FIXED_BLOCKERS = (
    "No approved stored-object image source selected: export and freeze authorized original object crops",
    "No approved native Protect post-cutover readback selected: freeze positive/negative queries "
    "and verify the pinned running encoder after restart")


def _reviewed_source(source):
    from .index_sources import ObjectCropArchive
    from .index_sessions import SessionTextArchive
    return type(source) in (ObjectCropArchive, SessionTextArchive)


def _reviewed_pair(source, plan):
    from .index_sources import ObjectCropArchive
    from .index_sessions import SessionTextArchive
    from .index_readback import NativeReadbackPlan
    from .index_session_readback import SessionReadbackPlan
    return ((type(source) is ObjectCropArchive and type(plan) is NativeReadbackPlan)
            or (type(source) is SessionTextArchive and type(plan) is SessionReadbackPlan))


def _require_approval(*, readback: bool, source: Source | None = None, readback_plan=None) -> None:
    """The library refuses what the plan refuses, before any write (#18)."""
    if not APPROVED_IMAGE_SOURCE and not _reviewed_source(source):
        raise RebuildError(_FIXED_BLOCKERS[0])
    if readback and not APPROVED_NATIVE_READBACK and not _reviewed_pair(source, readback_plan):
        raise RebuildError(_FIXED_BLOCKERS[1])


def _latest_backup(backups_dir: Path | None, live_rows: int | None, now: float, *, space='clip-basic-v1') -> dict:
    def unavailable(reason):
        return {"latest": None, "verified": False, "matches_live": None,
                "reason": reason}

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate backup field")
            result[key] = value
        return result

    if backups_dir is None:
        return unavailable("No backup directory is configured")
    try:
        directory = os.open(backups_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return unavailable("The backup directory is unavailable or unsafe")
    try:
        manifests = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.startswith("search-") and entry.name.endswith(".json"):
                    manifests.append(entry.name)
                    if len(manifests) > 512:
                        return unavailable("Too many backup manifests; review the backup directory")
        if not manifests:
            return unavailable("No backup manifest found")
        name = max(manifests)
        # Never hide a damaged newest record by silently selecting an older one.
        if not re.fullmatch(r"search-[0-9]{8}T[0-9]{6}\.json", name):
            return unavailable("The latest backup manifest is invalid or unsafe")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            descriptor = os.open(name, flags, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as manifest:
                meta = os.fstat(manifest.fileno())
                if not stat.S_ISREG(meta.st_mode) or meta.st_size > 64 * 1024:
                    raise ValueError("invalid backup manifest")
                raw = manifest.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise ValueError("oversized backup manifest")
            value = json.loads(raw, object_pairs_hook=unique)
            if not isinstance(value, dict) or value.get("dump") != name[:-5] + ".dump":
                raise ValueError("invalid dump reference")
            counts = value["counts"]
            if not isinstance(counts, dict) or not isinstance(counts.get("tables"), dict):
                raise ValueError("invalid backup counts")
            if space == 'e5-session-v1':
                rows, embeddings = counts["tables"]["smartDetectSessionsSearch"], counts["session_embeddings"]
            else:
                rows, embeddings = counts["tables"]["ramDetections"], counts["embeddings"]
            if any(type(count) is not int or count < 0 for count in (rows, embeddings)):
                raise ValueError("invalid backup counts")
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return unavailable("The latest backup manifest is invalid or unsafe")
        verified = isinstance(value.get("verified"), dict) and value["verified"].get("restore_counts_match") is True
        matches = ((embeddings == live_rows and rows >= embeddings) if space == 'e5-session-v1'
                   and type(live_rows) is int and live_rows >= 0 else
                   (rows == live_rows == embeddings if type(live_rows) is int and live_rows >= 0 else None))
        age = max(0, int(now - meta.st_mtime))
        try:
            descriptor = os.open(value["dump"], flags, dir_fd=directory)
            try:
                regular_dump = stat.S_ISREG(os.fstat(descriptor).st_mode)
            finally:
                os.close(descriptor)
        except OSError:
            regular_dump = False
        reason = None
        if not regular_dump:
            reason = "The latest backup's dump file is missing or unsafe"
        elif not verified:
            reason = "The latest backup has no recorded scratch-restore verification"
        elif matches is False:
            reason = f"The latest backup holds {rows} rows; the live index holds {live_rows}"
        elif matches is None:
            reason = "Live row count unavailable; cannot compare with the backup"
        return {"latest": name, "rows": rows, "age_s": age, "verified": verified and regular_dump,
                "matches_live": matches, "reason": reason}
    except OSError:
        return unavailable("The backup directory is unavailable or unsafe")
    finally:
        os.close(directory)


def _recorded_qualification(directory, journal):
    """Historical bounded receipts only; no source/model/native I/O on page GET."""
    flags = {"source_recorded": False, "quality_recorded": False, "native_verified_recorded": False}
    source = journal.get("source_archive_sha256")
    if not isinstance(source, str) or not _REVISION.fullmatch(source):
        return flags
    flags["source_recorded"] = True
    quality = journal.get("quality")
    if not isinstance(quality, dict) or quality.get("passed") is not True:
        return flags
    try:
        from .index_readback import load_readback_plan
        plan = load_readback_plan(Rebuild._read(directory / "readback-plan.json"))
        receipt = Rebuild._read(directory / "staged-quality.json")
        if (not plan.matches(source_archive_sha256=source, from_revision=journal.get("from_revision"),
                             target_revision=journal.get("revision"))
                or quality.get("plan_sha256") != plan.sha256 or not isinstance(receipt, dict)
                or receipt.get("passed") is not True or receipt.get("plan_sha256") != plan.sha256
                or receipt.get("staged_identity") != quality.get("staged_identity")
                or receipt.get("target_revision") != journal.get("revision")):
            return flags
        flags["quality_recorded"] = True
        native = Rebuild._read(directory / "native-readback.json")
        recorded = journal.get("native_readback")
        if (journal.get("state") == "cut_over" and isinstance(native, dict)
                and isinstance(recorded, dict) and recorded.get("passed") is True
                and recorded.get("logout_acknowledged") is True
                and native.get("passed") is True and native.get("logout_acknowledged") is True
                and native.get("plan_sha256") == plan.sha256
                and native.get("target_revision") == journal.get("revision")
                and native.get("attempt_id") == recorded.get("attempt_id")):
            flags["native_verified_recorded"] = True
    except (OSError, ValueError, RuntimeError):
        pass  # A damaged receipt never becomes historical success.
    return flags


def migration_plan(state_dir: Path, *, backups_dir: Path | None = None, live_rows: int | None = None,
                   now: Callable[[], float] = time.time) -> dict:
    """Read-only index-migration status and dry-run plan; apply is never available here.

    Reads the live profile, every generation's journal and the newest backup
    manifest. ``live_rows`` is the live ``ramDetections`` count when the caller
    can read it. Nothing is written.
    """
    state_dir = Path(state_dir)
    try:
        live, profile_revision = Rebuild._record(state_dir / "search-profile.json")
    except (OSError, ValueError, RebuildError):
        live = profile_revision = None
    live_revision = live.get("revision") if isinstance(live, dict) else None
    pinned = isinstance(live_revision, str) and bool(_REVISION.fullmatch(live_revision))
    generations = []
    root = state_dir / "index-rebuild"
    for journal_path in sorted(root.glob("*/journal.json")) if root.is_dir() else []:
        entry = {"generation": journal_path.parent.name, "problems": []}
        try:
            journal, journal_revision = Rebuild._record(journal_path)
            if not isinstance(journal, dict) or journal.get("state") not in STATES:
                raise ValueError
        except (OSError, ValueError, RebuildError):
            entry.update(state="unreadable", problems=["The journal is unreadable; inspect it"])
            generations.append(entry)
            continue
        target, source_rev = str(journal.get("revision", "")), str(journal.get("from_revision", ""))
        counts = journal.get("counts")
        if (not isinstance(counts, dict) or any(type(counts.get(k, 0)) is not int
                or counts.get(k, 0) < 0 for k in ("embedded", "no_source"))):
            entry.update(state="unreadable", problems=["The journal counts are invalid; inspect it"])
            generations.append(entry)
            continue
        entry.update(state=journal["state"], to=target[:12], **{"from": source_rev[:12]})
        if (not _REVISION.fullmatch(target)
                or journal.get("generation_id", target[:12]) != entry["generation"]):
            entry["problems"].append("The journal's target revision does not match its generation")
        at_target = (live.get("fingerprint") == journal["target_fingerprint"]
                     if isinstance(live, dict) and journal.get("target_fingerprint") is not None
                     else target == live_revision)
        from_matches = (live.get("fingerprint") == journal["from_fingerprint"]
                        if isinstance(live, dict) and journal.get("from_fingerprint") is not None
                        else source_rev == live_revision)
        if journal["state"] in ("cut_over", "awaiting_native_readback"):
            if not at_target:
                entry["problems"].append("Recorded as cut over, but the live profile has another revision")
        elif journal["state"] not in ("rolled_back", "cancelled"):
            if at_target:
                entry["problems"].append("The target revision is already the live revision")
            elif not from_matches:
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
        entry["qualification"] = _recorded_qualification(journal_path.parent, journal)
        entry["cancel"] = {"available": journal["state"] in ("staging", "staged", "verified")
                           and type(journal.get("schema")) is int and journal["schema"] == 1
                           and _valid_from_checkpoint(journal)
                           and set(counts) == {"embedded", "no_source"}
                           and not entry["problems"] and (pinned or journal.get("from_unversioned") is True),
                           "journal_revision": journal_revision, "profile_revision": profile_revision}
        generations.append(entry)
    backup = _latest_backup(backups_dir, live_rows, now(),
                            space=live.get('profile', 'clip-basic-v1') if isinstance(live, dict) else 'clip-basic-v1')
    active = [g for g in generations if g.get("state") in ("staging", "staged", "verified") and not g["problems"]]
    cut = [g for g in generations if g.get("state") in ("cut_over", "awaiting_native_readback")]
    if cut:
        ready = all(g["previous_profile"] for g in cut)
        rollback = {"ready": ready, "reason": None if ready else "A cut-over generation lacks its previous profile",
                    "note": "The snapshot table is checked when the rollback runs"}
    else:
        rollback = {"ready": backup["verified"] and backup["matches_live"] is True,
                    "reason": backup["reason"] or None,
                    "note": "No cutover happened; the verified backup restores the live index"}
    source_recorded = any(g.get("qualification", {}).get("source_recorded") for g in generations)
    quality_recorded = any(g.get("qualification", {}).get("quality_recorded") for g in generations)
    native_recorded = any(g.get("qualification", {}).get("native_verified_recorded") for g in generations)
    text_space = isinstance(live, dict) and live.get("profile") == "e5-session-v1"
    blockers = (("No approved session text source selected: export and freeze authorized descriptions",
                 _FIXED_BLOCKERS[1]) if text_space else _FIXED_BLOCKERS)
    reasons = []
    if not source_recorded:
        reasons.append(blockers[0])
    if not quality_recorded:
        reasons.append(blockers[1])
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
        step("Export and freeze authorized session descriptions" if text_space else
             "Export and freeze authorized original object crops", source_recorded,
             None if source_recorded else blockers[0]),
        step("Run a second versioned E5 server for the target revision" if text_space else
             "Run a second CLIP server for the target revision", quality_recorded),
        step("Stage every live object with the target encoder", bool(generation and generation["state"] in (
            "staged", "verified")), None if source_recorded else blockers[0]),
        step("Verify revision, dimensions and coverage", bool(generation and generation["state"] == "verified")),
        step("Meet frozen positive and negative staged-index quality targets", quality_recorded),
        step("Take and verify a backup matching the live index",
             backup["verified"] and backup["matches_live"] is True, backup["reason"]),
        step("Stop the AI Key and cut over in one transaction", bool(cut),
             None if cut else "Use the participating runtime's exclusive index lock and verified backup"),
        step("Switch embeddings.endpoint/revision, start the Key, and read back native session search" if text_space else
             "Switch find_anything.clip_server, start the Key, and read back native search", native_recorded,
             None if native_recorded else blockers[1]),
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
    parser.add_argument("command", choices=["status", "plan", "export", "stage", "verify",
                        "qualify", "cutover", "readback", "rollback"])
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--backups", type=Path)
    parser.add_argument("--live-rows", type=int)
    parser.add_argument("--space", choices=['clip-basic-v1', 'e5-session-v1'], default='clip-basic-v1')
    parser.add_argument("--generation")
    from .index_commands import add_arguments, execute
    add_arguments(parser)
    args = parser.parse_args(argv)
    if args.command not in {"status", "plan"}:
        return execute(args)
    display_state = args.state_dir/'session-search' if args.space == 'e5-session-v1' else args.state_dir
    if args.command == "plan":
        print(json.dumps(migration_plan(display_state, backups_dir=args.backups, live_rows=args.live_rows),
                         indent=2))
        return 0
    print(json.dumps(status(display_state), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
