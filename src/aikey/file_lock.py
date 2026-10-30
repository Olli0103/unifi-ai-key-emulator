"""Bounded advisory locking for local administration transactions."""

import errno
import fcntl
import time


def acquire(fd: int, timeout: float = 5.0) -> None:
    """Acquire an exclusive lock or fail without retaining an executor forever."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Configuration writer is busy") from None
            time.sleep(min(0.025, remaining))
