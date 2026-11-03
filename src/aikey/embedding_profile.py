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


def ensure_session_embedding_profile(state_dir: Path, identity: dict[str, Any], *, legacy_root=False) -> None:
    """Keep E5 session documents/queries separate from the paired visual space.

    The old root profile is checked when it is an E5 profile, or when an E5-only
    operator explicitly uses it. Hybrid CLIP+E5 installs additionally pin their
    session space in its own directory. First creation records the configured
    identity; it does not retroactively attest old native rows/checkpoint bytes.
    """
    from .encoding_identity import UPGRADABLE_ENCODING
    from .startup_configuration import read_snapshot
    root = Path(state_dir)
    path = root / 'search-profile.json'
    previous = None
    try:
        raw, _ = read_snapshot(path, limit=16384)
        previous = json.loads(raw)
        if not isinstance(previous, dict):
            raise ValueError
    except FileNotFoundError:
        pass
    except (OSError, ValueError, UnicodeError):
        raise EmbeddingProfileError('Cannot validate the existing session/search profile space') from None
    if legacy_root or (previous is not None and previous.get('profile') == 'e5-session-v1'):
        ensure_embedding_profile(root, identity, upgradable=UPGRADABLE_ENCODING)
    ensure_embedding_profile(root / 'session-search', identity, upgradable=UPGRADABLE_ENCODING)
