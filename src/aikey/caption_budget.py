"""Durable, installation-wide admission for paid caption attempts.

Reservations are charged before media or a provider is contacted. A failed or
uncertain attempt is never refunded. This module deliberately does not choose
which camera gets the next slot; scheduling belongs at the caller boundary.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Callable, Iterator

from .config import atomic_private


HOUR_NS = 3_600_000_000_000
RETENTION_NS = 24 * HOUR_NS
LIMIT = 12
_MAX_RECORDS = 300
_MAX_BYTES = 128 * 1024
_HASH = re.compile(r"[0-9a-f]{64}")
_CAMERA = re.compile(r"[A-Za-z0-9_-]{1,128}")


class CaptionBudgetError(RuntimeError):
    """The budget cannot make a safe admission decision."""


class CaptionBudgetExhausted(CaptionBudgetError):
    def __init__(self, next_at_ns: int):
        super().__init__("Global caption budget is exhausted")
        self.next_at_ns = next_at_ns


@dataclass(frozen=True)
class Reservation:
    new: bool
    remaining: int
    next_at_ns: int | None


class CaptionBudget:
    """Reserve at most twelve new captions in any rolling hour.

    A single lock file serializes processes sharing the same state directory.
    The 24-hour replay window is bounded; the worker's result journal remains
    responsible for returning completed results after that window.
    """

    def __init__(self, state_dir: Path, *, clock_ns: Callable[[], int] = time.time_ns):
        self.directory = Path(state_dir)
        self.path = self.directory / "caption-budget.json"
        self.lock_path = self.directory / ".caption-budget.lock"
        self.clock_ns = clock_ns
        self._check_directory()

    def reserve(self, job_id: str, fingerprint: str, camera_id: str) -> Reservation:
        if not isinstance(job_id, str) or not _HASH.fullmatch(job_id):
            raise CaptionBudgetError("Job ID must be a SHA-256 identifier")
        if not isinstance(fingerprint, str) or not _HASH.fullmatch(fingerprint):
            raise CaptionBudgetError("Job fingerprint must be a SHA-256 identifier")
        if not isinstance(camera_id, str) or not _CAMERA.fullmatch(camera_id):
            raise CaptionBudgetError("Camera ID is invalid")
        with self._locked():
            now = self.clock_ns()
            if type(now) is not int or now <= 0:
                raise CaptionBudgetError("Wall clock is invalid")
            state = self._read()
            for item in state["reservations"]:
                if item["job_id"] == job_id:
                    if item["fingerprint"] != fingerprint or item["camera_id"] != camera_id:
                        raise CaptionBudgetError("Job identity was reused with different input")
                    count = sum(
                        now - HOUR_NS < entry["at_ns"] <= now for entry in state["reservations"]
                    )
                    return Reservation(False, max(0, LIMIT - count), None)
            if now < state["high_water_ns"]:
                raise CaptionBudgetError("Wall clock moved backwards; review before admission")
            retained = [
                item for item in state["reservations"] if now - item["at_ns"] < RETENTION_NS
            ]
            recent = [item for item in retained if now - item["at_ns"] < HOUR_NS]
            if len(recent) >= LIMIT:
                state.update(high_water_ns=now, reservations=retained)
                self._write(state)
                raise CaptionBudgetExhausted(min(item["at_ns"] for item in recent) + HOUR_NS)
            if len(retained) >= _MAX_RECORDS:
                raise CaptionBudgetError("Caption reservation journal is full")
            retained.append(
                {"job_id": job_id, "fingerprint": fingerprint, "camera_id": camera_id, "at_ns": now}
            )
            state.update(high_water_ns=now, reservations=retained)
            self._write(state)
            next_at = min(item["at_ns"] for item in recent) + HOUR_NS if recent else None
            return Reservation(True, LIMIT - len(recent) - 1, next_at)

    def _check_directory(self) -> None:
        try:
            meta = self.directory.lstat()
        except FileNotFoundError:
            self.directory.mkdir(parents=True, mode=0o700)
            meta = self.directory.lstat()
        if (
            not stat.S_ISDIR(meta.st_mode)
            or stat.S_ISLNK(meta.st_mode)
            or meta.st_uid != os.geteuid()
            or meta.st_mode & 0o077
        ):
            raise CaptionBudgetError("Caption state directory is unsafe")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.lock_path, flags, 0o600)
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode):
                os.close(fd)
                raise CaptionBudgetError("Caption lock is not a regular file")
            os.fchmod(fd, 0o600)
        except OSError as exc:
            raise CaptionBudgetError("Cannot open caption budget lock") from exc
        with os.fdopen(fd, "rb") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise CaptionBudgetError("Cannot lock caption budget") from exc
            yield

    def _read(self) -> dict:
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags)
        except FileNotFoundError:
            return {"schema": 1, "high_water_ns": 0, "reservations": []}
        except OSError as exc:
            raise CaptionBudgetError("Cannot read caption budget") from exc
        try:
            with os.fdopen(fd, "rb") as source:
                meta = os.fstat(source.fileno())
                if not stat.S_ISREG(meta.st_mode) or meta.st_size > _MAX_BYTES:
                    raise ValueError
                data = source.read(_MAX_BYTES + 1)
            state = json.loads(data)
            if (
                not isinstance(state, dict)
                or set(state) != {"schema", "high_water_ns", "reservations"}
                or state["schema"] != 1
                or type(state["high_water_ns"]) is not int
                or state["high_water_ns"] < 0
                or not isinstance(state["reservations"], list)
                or len(state["reservations"]) > _MAX_RECORDS
            ):
                raise ValueError
            seen = set()
            for item in state["reservations"]:
                if (
                    not isinstance(item, dict)
                    or set(item) != {"job_id", "fingerprint", "camera_id", "at_ns"}
                    or not isinstance(item["job_id"], str)
                    or not _HASH.fullmatch(item["job_id"])
                    or not isinstance(item["fingerprint"], str)
                    or not _HASH.fullmatch(item["fingerprint"])
                    or not isinstance(item["camera_id"], str)
                    or not _CAMERA.fullmatch(item["camera_id"])
                    or type(item["at_ns"]) is not int
                    or not 0 < item["at_ns"] <= state["high_water_ns"]
                    or item["job_id"] in seen
                ):
                    raise ValueError
                seen.add(item["job_id"])
            return state
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            raise CaptionBudgetError("Caption budget is corrupt; review before admission") from exc

    def _write(self, state: dict) -> None:
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > _MAX_BYTES:
            raise CaptionBudgetError("Caption budget exceeds storage limit")
        try:
            atomic_private(self.path, encoded)
        except OSError as exc:
            raise CaptionBudgetError(
                "Caption reservation outcome is uncertain; review before retrying"
            ) from exc
