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


def ensure_embedding_profile(state_dir: Path, identity: dict[str, Any]) -> None:
    """Create or verify search-profile.json without replacing an existing profile.

    This records configuration, not a checksum of remote model weights. Operators
    must keep the configured endpoint/model revision stable while an index exists.
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
        if previous != expected:
            raise EmbeddingProfileError(
                "Embedding profile changed; reconcile the search index before replacing search-profile.json"
            )
    except EmbeddingProfileError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise EmbeddingProfileError("Cannot read or persist embedding profile state; inspect it before continuing") from exc
