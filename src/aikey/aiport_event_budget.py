"""Durable per-camera admission for live AI Port smart events.

An enter is charged before publication. A failed or uncertain send is not
refunded, so a restart cannot turn repeated attempts into unlimited alerts.
Only camera identifiers and event times are stored; no media or detections.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import time
from typing import Callable, Iterator

from .aiport_ingest import IngressError, normalize_mac
from .config import atomic_private


_HOUR_NS = 3_600_000_000_000
_MAX_BYTES = 1024 * 1024
_MAX_CAMERAS = 32
_MAX_EVENTS_PER_CAMERA = 3600


class EventBudgetError(RuntimeError):
    """A safe live-event admission decision could not be made."""


class EventBudget:
    """Enforce a rolling limit across restarts sharing one private state dir."""

    def __init__(self, state_dir: Path, *, limit: int,
                 clock_ns: Callable[[], int] = time.time_ns,
                 namespace: str = "event"):
        if type(limit) is not int or not 1 <= limit <= _MAX_EVENTS_PER_CAMERA:
            raise EventBudgetError("invalid_event_limit")
        if type(namespace) is not str or namespace not in {"event", "vision-request"}:
            raise EventBudgetError("invalid_budget_namespace")
        self.directory = Path(state_dir)
        self.path = self.directory / f"aiport-{namespace}-budget.json"
        self.lock_path = self.directory / f".aiport-{namespace}-budget.lock"
        self.limit = limit
        self.clock_ns = clock_ns
        self._check_directory()

    def claim(self, camera_mac: str) -> bool:
        camera = self._camera(camera_mac)
        with self._locked():
            now = self._now()
            state = self._read()
            if now < state["high_water_ns"]:
                raise EventBudgetError("event_clock_rollback")
            events = self._recent(state["events"], now)
            if camera not in events and len(events) >= _MAX_CAMERAS:
                raise EventBudgetError("event_state_full")
            camera_events = events.setdefault(camera, [])
            allowed = len(camera_events) < self.limit
            if allowed:
                camera_events.append(now)
            state.update(high_water_ns=now, events=events)
            self._write(state)
            return allowed

    def remaining(self, camera_mac: str) -> int:
        camera = self._camera(camera_mac)
        with self._locked():
            now = self._now()
            state = self._read()
            if now < state["high_water_ns"]:
                raise EventBudgetError("event_clock_rollback")
            recent = self._recent(state["events"], now)
            return max(0, self.limit - len(recent.get(camera, ())))

    @staticmethod
    def _camera(camera_mac: str) -> str:
        try:
            return normalize_mac(camera_mac)
        except IngressError as exc:
            raise EventBudgetError("invalid_event_camera") from exc

    def _now(self) -> int:
        now = self.clock_ns()
        if type(now) is not int or now <= 0:
            raise EventBudgetError("invalid_event_clock")
        return now

    @staticmethod
    def _recent(events: dict[str, list[int]], now: int) -> dict[str, list[int]]:
        cutoff = now - _HOUR_NS
        return {camera: kept for camera, values in events.items()
                if (kept := [at for at in values if cutoff < at <= now])}

    def _check_directory(self) -> None:
        try:
            meta = self.directory.lstat()
        except FileNotFoundError:
            self.directory.mkdir(parents=True, mode=0o700)
            meta = self.directory.lstat()
        if (not stat.S_ISDIR(meta.st_mode) or stat.S_ISLNK(meta.st_mode)
                or meta.st_uid != os.geteuid() or meta.st_mode & 0o077):
            raise EventBudgetError("unsafe_event_state")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.lock_path, flags, 0o600)
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid():
                os.close(fd)
                raise EventBudgetError("unsafe_event_lock")
            os.fchmod(fd, 0o600)
        except OSError as exc:
            raise EventBudgetError("event_lock_unavailable") from exc
        with os.fdopen(fd, "rb") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise EventBudgetError("event_lock_unavailable") from exc
            yield

    def _read(self) -> dict:
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return {"schema": 1, "high_water_ns": 0, "events": {}}
        except OSError as exc:
            raise EventBudgetError("event_state_unavailable") from exc
        try:
            with os.fdopen(fd, "rb") as source:
                meta = os.fstat(source.fileno())
                if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                        or meta.st_mode & 0o077 or meta.st_size > _MAX_BYTES):
                    raise ValueError
                data = source.read(_MAX_BYTES + 1)
            state = json.loads(data)
            if (not isinstance(state, dict)
                    or set(state) != {"schema", "high_water_ns", "events"}
                    or state["schema"] != 1
                    or type(state["high_water_ns"]) is not int
                    or state["high_water_ns"] < 0
                    or not isinstance(state["events"], dict)
                    or len(state["events"]) > _MAX_CAMERAS):
                raise ValueError
            for camera, values in state["events"].items():
                if (self._camera(camera) != camera or not isinstance(values, list)
                        or len(values) > _MAX_EVENTS_PER_CAMERA
                        or any(type(at) is not int or not 0 < at <= state["high_water_ns"]
                               for at in values)
                        or values != sorted(values)):
                    raise ValueError
            return state
        except (OSError, ValueError, TypeError, UnicodeError, EventBudgetError) as exc:
            raise EventBudgetError("event_state_corrupt") from exc

    def _write(self, state: dict) -> None:
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > _MAX_BYTES:
            raise EventBudgetError("event_state_full")
        try:
            atomic_private(self.path, encoded)
        except OSError as exc:
            raise EventBudgetError("event_claim_uncertain") from exc
