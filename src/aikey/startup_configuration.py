"""Startup file provenance, not an attestation of referenced files or live state."""

import hashlib
import os
from pathlib import Path
import re
import stat


SCHEMA = "aikey-startup-configuration/1"
SCOPE = "file_bytes_at_process_start"


def read_snapshot(path: Path, *, limit: int) -> tuple[bytes, str]:
    """Acquire bounded regular-leaf bytes once; validation must use these bytes."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError("startup_configuration_unavailable")
        raw = source.read(limit + 1)
    if not raw or len(raw) > limit:
        raise ValueError("startup_configuration_unavailable")
    return raw, hashlib.sha256(raw).hexdigest()


def observation(revision: str | None) -> dict | None:
    if revision is None:
        return None
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise ValueError("invalid_startup_configuration_revision")
    return {"schema": SCHEMA, "scope": SCOPE, "revision": revision}


def revision(value: dict | None) -> str | None:
    """Legacy/programmatic peers report unknown; malformed attestations fail."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"schema", "scope", "revision"}
            or value.get("schema") != SCHEMA or value.get("scope") != SCOPE):
        raise ValueError("invalid_startup_configuration_observation")
    observation(value["revision"])
    if value["revision"] is None:
        raise ValueError("invalid_startup_configuration_observation")
    return value["revision"]
