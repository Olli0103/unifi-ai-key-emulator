"""Persist one document/query encoder identity before either can serve work."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


class EmbeddingProfileError(RuntimeError):
    """The stored encoder identity cannot safely be used or changed."""


def pinned_value(state_dir: Path, key: str) -> Any:
    """One field of the stored profile, or None when there is no profile or field."""
    path = Path(state_dir) / "search-profile.json"
    try:
        if path.stat().st_size > 16384:
            return None
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value.get(key) if isinstance(value, dict) else None


def _with_fingerprint(identity: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(identity, allow_nan=False, sort_keys=True, separators=(",", ":"))
    value = json.loads(encoded)
    value["fingerprint"] = hashlib.sha256(encoded.encode()).hexdigest()
    return value


def ensure_embedding_profile(state_dir: Path, identity: dict[str, Any], *,
                             upgradable: tuple[str, ...] = ()) -> None:
    """Create or verify search-profile.json without replacing an existing profile.

    Keys in ``upgradable`` may be added once to a profile that is otherwise
    identical (a profile written before the key existed); the old file is kept
    as ``search-profile.json.before-upgrade``. Any other difference is refused.
    """
    try:
        encoded = json.dumps(identity, allow_nan=False, sort_keys=True, separators=(",", ":"))
        expected = json.loads(encoded)
        expected["fingerprint"] = hashlib.sha256(encoded.encode()).hexdigest()
        directory = Path(state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "search-profile.json"
        if not path.exists():
            descriptor, temporary = tempfile.mkstemp(prefix=".search-profile-", dir=directory)
            try:
                with os.fdopen(descriptor, "w") as output:
                    json.dump(expected, output, allow_nan=False, indent=2)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    # A second process may have created a profile while we wrote.
                    # Linking publishes a complete file and never replaces it.
                    os.link(temporary, path)
                except FileExistsError:
                    pass
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                os.unlink(temporary)
        if path.stat().st_size > 16384:
            raise EmbeddingProfileError("Embedding profile state exceeds its size limit; inspect it before continuing")
        previous = json.loads(path.read_text())
        added = [key for key in upgradable if key in identity and key not in previous]
        if added and previous == _with_fingerprint({k: v for k, v in identity.items() if k not in added}):
            # A profile written before these identity fields existed, otherwise
            # identical: record the new fields once, keeping the old file.
            backup = directory / "search-profile.json.before-upgrade"
            if not backup.exists():
                os.link(path, backup)
            descriptor, temporary = tempfile.mkstemp(prefix=".search-profile-", dir=directory)
            with os.fdopen(descriptor, "w") as output:
                json.dump(expected, output, allow_nan=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            previous = expected
        if previous != expected:
            raise EmbeddingProfileError(
                "Embedding profile changed; reconcile the search index before replacing search-profile.json"
            )
    except EmbeddingProfileError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise EmbeddingProfileError("Cannot read or persist embedding profile state; inspect it before continuing") from exc
