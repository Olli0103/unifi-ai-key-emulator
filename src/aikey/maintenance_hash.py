"""Descriptor-bound, bounded hashing of host-approved maintenance artifacts."""

import hashlib
import os
from pathlib import Path
import stat


def artifact_digest(path: Path, limit: int = 64 * 1024 ** 3) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= limit:
            raise ValueError("maintenance_artifact_unavailable")
        digest, total = hashlib.sha256(), 0
        while raw := source.read(min(1024 * 1024, limit - total + 1)):
            total += len(raw)
            if total > limit:
                raise ValueError("maintenance_artifact_too_large")
            digest.update(raw)
        return digest.hexdigest()
