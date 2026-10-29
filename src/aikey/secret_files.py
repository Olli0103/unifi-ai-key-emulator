"""Bounded credential-file reads; never opens a provider or changes a file."""

import os
from pathlib import Path
import stat


class SecretFileError(ValueError):
    """Fixed failure without credential bytes, file paths or OS details."""


def read_secret(path: str | Path) -> str:
    """Read one nonempty UTF-8 secret from a private regular leaf file."""
    limit = 4096
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                    or not 0 < info.st_size <= limit):
                raise ValueError
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError
        value = raw.decode("utf-8").strip()
        if not value:
            raise ValueError
        return value
    except (OSError, UnicodeError, TypeError, ValueError):
        raise SecretFileError("credential_file_unavailable") from None
