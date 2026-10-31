"""Opt-in cooperative model-work/maintenance fencing on one shared private inode.

Readers own independent open-file descriptions. A writer leaves a durable
maintenance intent before effects; losing its process/lock never clears that
intent. No expiry, automatic recovery, HTTP request or replay authority here.
The operator must provision the same inode for every participating consumer.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

SCHEMA = "local-aikey-model-work-fence/1"
FILENAME = "model-work.lock"
MAX_BYTES = 2048


class ModelWorkFenceError(RuntimeError):
    """Fixed local diagnostic; never include a path, model or request payload."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _validate(value):
    if (not isinstance(value, dict) or set(value) != {"schema", "phase", "transaction"}
            or value.get("schema") != SCHEMA or value.get("phase") not in {"idle", "maintenance"}
            or not isinstance(value.get("transaction"), str)
            or not re.fullmatch("[0-9a-f]{32}", value["transaction"])):
        raise ModelWorkFenceError("model_work_fence_unavailable")
    return value


def _read(fd):
    raw = os.pread(fd, MAX_BYTES + 1, 0)
    if not raw or len(raw) > MAX_BYTES:
        raise ModelWorkFenceError("model_work_fence_unavailable")
    try:
        return _validate(json.loads(raw, object_pairs_hook=_object))
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ModelWorkFenceError("model_work_fence_unavailable") from None


def _identity(meta):
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
            or meta.st_mode & 0o077):
        raise ModelWorkFenceError("model_work_fence_unavailable")
    return (meta.st_dev, meta.st_ino)


class ModelWorkLease:
    def __init__(self, fence, fd=-1, *, exclusive=False):
        self.fence, self.fd, self.exclusive = fence, fd, exclusive
        if fd >= 0:
            fence._leases += 1

    def close(self):
        if self.fd >= 0:
            fd, self.fd = self.fd, -1
            try:
                os.close(fd)  # Kernel releases this lease's independent lock.
            finally:
                self.fence._leases -= 1

    def __del__(self):
        self.close()

    def _write(self, value):
        if not self.exclusive or self.fd < 0:
            raise ModelWorkFenceError("model_work_fence_not_exclusive")
        raw = (json.dumps(_validate(value), sort_keys=True) + "\n").encode("ascii")
        # Preserve the inode/hard links. All cooperative readers are excluded;
        # an interrupted/truncated write is unavailable, never implicitly idle.
        os.ftruncate(self.fd, 0)
        offset = 0
        while offset < len(raw):
            written = os.pwrite(self.fd, raw[offset:], offset)
            if written <= 0:
                raise ModelWorkFenceError("model_work_fence_write_failed")
            offset += written
        os.fsync(self.fd)

    def begin(self, transaction):
        proposed = _validate({"schema": SCHEMA, "phase": "maintenance", "transaction": transaction})
        if not self.exclusive or self.fd < 0 or _read(self.fd)["phase"] != "idle":
            raise ModelWorkFenceError("model_work_fence_unresolved")
        self._write(proposed)

    def finish(self, transaction):
        if not self.exclusive or self.fd < 0:
            raise ModelWorkFenceError("model_work_fence_not_exclusive")
        value = _read(self.fd)
        if value != {"schema": SCHEMA, "phase": "maintenance", "transaction": transaction}:
            raise ModelWorkFenceError("model_work_fence_transaction_changed")
        self._write({**value, "phase": "idle"})


class ModelWorkFence:
    @classmethod
    def from_state(cls, state: Path):
        """Explicit operator enrollment is the private marker, not new policy.

        Absence leaves legacy behavior unchanged. A broken/unsafe marker never
        silently disables an enrolled consumer; construction fails closed.
        """
        try:
            (Path(state) / FILENAME).lstat()
        except FileNotFoundError:
            return cls(state, enabled=False)
        except OSError:
            raise ModelWorkFenceError("model_work_fence_unavailable") from None
        return cls(state, enabled=True)

    def __init__(self, state: Path, *, enabled=False):
        self._parent_fd = self._anchor_fd = -1
        self._leases = self._waiters = 0
        self._closed = False
        self.enabled = enabled
        self._binding = None
        self._inode = None
        if type(enabled) is not bool:
            raise ModelWorkFenceError("model_work_fence_requires_boolean")
        if not enabled:
            return  # Default is unchanged; never invent a local guard inode.
        try:
            self._parent_fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            meta = os.fstat(self._parent_fd)
            if meta.st_uid != os.geteuid() or meta.st_mode & 0o022:
                raise ModelWorkFenceError("model_work_fence_unavailable")
            self._anchor_fd = os.open(FILENAME, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                                      dir_fd=self._parent_fd)
            self._inode = _identity(os.fstat(self._anchor_fd))
            self._binding = hashlib.sha256((SCHEMA + "\0" + ":".join(
                str(part) for part in self._inode)).encode("ascii")).hexdigest()
            try:
                fcntl.flock(self._anchor_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                pass  # A live writer may be changing bytes; work will wait.
            else:
                try:
                    _read(self._anchor_fd)
                finally:
                    fcntl.flock(self._anchor_fd, fcntl.LOCK_UN)
        except (OSError, ValueError, TypeError):
            self.close()
            raise ModelWorkFenceError("model_work_fence_unavailable") from None
        except BaseException:
            self.close()
            raise

    def close(self):
        self._closed = True
        for name in ("_anchor_fd", "_parent_fd"):
            fd = getattr(self, name, -1)
            if fd >= 0:
                setattr(self, name, -1)
                os.close(fd)
        # Active leases deliberately retain their own descriptors until the
        # actual work finishes, including shielded model threads.

    def __del__(self):
        self.close()

    def status(self):
        phase = "disabled" if not self.enabled else "closed" if self._closed else "unavailable"
        fd = -1
        if self.enabled and not self._closed:
            try:
                fd = self._open()
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    phase = "writer_active"
                else:
                    phase = "idle" if _read(fd)["phase"] == "idle" else "unresolved"
            except (OSError, ValueError, TypeError, ModelWorkFenceError):
                phase = "unavailable"
            finally:
                if fd >= 0:
                    os.close(fd)
        return {"schema": SCHEMA, "enabled": self.enabled, "binding": self._binding,
                "closed": self._closed, "leases": self._leases, "waiters": self._waiters,
                "maintenance": phase}

    def _open(self, *, writable=False):
        if self._closed or not self.enabled:
            raise ModelWorkFenceError("model_work_fence_unavailable")
        fd = -1
        try:
            fd = os.open(FILENAME, (os.O_RDWR if writable else os.O_RDONLY)
                         | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=self._parent_fd)
            if _identity(os.fstat(fd)) != self._inode:
                raise ModelWorkFenceError("model_work_fence_binding_changed")
            return fd
        except BaseException:
            if fd >= 0:
                os.close(fd)
            raise

    async def acquire(self, *, deadline=None):
        """Cancellable wait inside the caller's budget; no model request/retry."""
        if deadline is not None and (type(deadline) not in (float, int)
                                     or not 0 < deadline <= 10**15 or not math.isfinite(deadline)):
            raise ModelWorkFenceError("model_work_fence_invalid_deadline")
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("model_work_fence_wait_expired")
        if not self.enabled and not self._closed:
            return ModelWorkLease(self)
        self._waiters += 1
        try:
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("model_work_fence_wait_expired")
                fd = self._open()
                try:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass
                    else:
                        if _read(fd)["phase"] == "idle":
                            if deadline is not None and time.monotonic() >= deadline:
                                raise TimeoutError("model_work_fence_wait_expired")
                            lease, fd = ModelWorkLease(self, fd), -1
                            return lease
                finally:
                    if fd >= 0:
                        os.close(fd)
                delay = 0.05 if deadline is None else min(0.05, max(0, deadline-time.monotonic()))
                await asyncio.sleep(delay)
        finally:
            self._waiters -= 1

    def try_exclusive(self):
        """Non-waiting writer; unresolved intent requires separate reconciliation."""
        fd = self._open(writable=True)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return None
            if _read(fd)["phase"] != "idle":
                raise ModelWorkFenceError("model_work_fence_unresolved")
            lease, fd = ModelWorkLease(self, fd, exclusive=True), -1
            return lease
        finally:
            if fd >= 0:
                os.close(fd)


def initialize(state: Path, transaction: str):
    """Operator-only one-time creation; never overwrite, recover or unlink."""
    value = _validate({"schema": SCHEMA, "phase": "idle", "transaction": transaction})
    parent_fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        meta = os.fstat(parent_fd)
        if meta.st_uid != os.geteuid() or meta.st_mode & 0o022:
            raise ModelWorkFenceError("model_work_fence_unavailable")
        fd = os.open(FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent_fd)
        with os.fdopen(fd, "wb") as target:
            os.fchmod(target.fileno(), 0o600)
            target.write((json.dumps(value, sort_keys=True) + "\n").encode("ascii"))
            target.flush()
            os.fsync(target.fileno())
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
