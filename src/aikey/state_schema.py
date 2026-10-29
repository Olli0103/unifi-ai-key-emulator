"""Versioned AI Key device-state migrations with backup and rollback (#24).

``device-state.json`` carries the adopted identity and the management
credential Protect set. A release that changes its layout must migrate it
without losing either, and a newer layout must never be read by an older
release. This module keeps that bounded:

* ``check_schema`` refuses a missing, invalid, too old or newer ``schema``
  with a distinct message; a newer one names the pre-upgrade backup.
* ``migrate`` applies pure, ordered steps (``MIGRATIONS[n]`` turns schema
  ``n`` into ``n + 1``) and refuses any step that alters an identity field.
* ``upgrade_state_file`` writes a migrated state only after keeping the
  original once as ``device-state.json.schema-<n>.bak``, and writes
  atomically (file and directory fsync), so an interrupted write leaves the
  original in place and a retry converges.
* ``rollback_state_file`` restores that backup for a downgrade.

The current release is schema 1 and has no migrations, so loading today's
state changes nothing.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable

CURRENT_SCHEMA = 1
OLDEST_SCHEMA = 1
IDENTITY_FIELDS = ("mac", "adopted", "credential", "management", "factory_enrollment_used")
Migration = Callable[[dict], dict]
MIGRATIONS: dict[int, Migration] = {}
_MAX_BYTES = 65536


class StateSchemaError(ValueError):
    """The device state cannot be used by this release; nothing was written."""


def check_schema(state: object, *, current: int = CURRENT_SCHEMA, oldest: int = OLDEST_SCHEMA) -> int:
    if not isinstance(state, dict):
        raise StateSchemaError("Device state is not a JSON object")
    version = state.get("schema")
    if type(version) is not int:
        raise StateSchemaError("Device state has no integer schema")
    if version > current:
        raise StateSchemaError(
            f"Device state schema {version} is newer than this release supports ({current}); "
            "run the newer release, or restore device-state.json.schema-<n>.bak to downgrade")
    if version < oldest:
        raise StateSchemaError(f"Device state schema {version} predates the supported range")
    return version


def migrate(state: dict, *, migrations: dict[int, Migration] | None = None,
            current: int = CURRENT_SCHEMA, oldest: int = OLDEST_SCHEMA) -> tuple[dict, list[int]]:
    """Pure: return the state at ``current`` and the schemas it passed through."""
    migrations = MIGRATIONS if migrations is None else migrations
    version = check_schema(state, current=current, oldest=oldest)
    identity = {key: state.get(key) for key in IDENTITY_FIELDS}
    steps, value = [], json.loads(json.dumps(state))
    while version < current:
        step = migrations.get(version)
        if step is None:
            raise StateSchemaError(f"No migration from device state schema {version}")
        value = step(json.loads(json.dumps(value)))
        if not isinstance(value, dict) or value.get("schema") != version + 1:
            raise StateSchemaError(f"Migration from schema {version} produced an invalid state")
        if any(value.get(key) != identity[key] for key in IDENTITY_FIELDS):
            # The adopted identity and credential are Protect's; a migration
            # that changes them would orphan the adoption.
            raise StateSchemaError(f"Migration from schema {version} altered the device identity")
        steps.append(version)
        version += 1
    return value, steps


def backup_path(path: Path, version: int) -> Path:
    return Path(path).with_name(f"{Path(path).name}.schema-{version}.bak")


def _write_atomic(path: Path, raw: bytes) -> None:
    directory = Path(path).parent
    fd, name = tempfile.mkstemp(prefix=f".{Path(path).name}-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read(path: Path) -> tuple[bytes, dict]:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
        raise StateSchemaError("Device state must be a small regular file")
    raw = path.read_bytes()
    try:
        return raw, json.loads(raw)
    except ValueError as exc:
        raise StateSchemaError("Device state is not valid JSON") from exc


def upgrade_state_file(path: Path, *, migrations: dict[int, Migration] | None = None,
                       current: int = CURRENT_SCHEMA, oldest: int = OLDEST_SCHEMA) -> dict:
    """Migrate the state file in place if needed; returns what happened."""
    raw, state = _read(path)
    version = check_schema(state, current=current, oldest=oldest)
    if version == current:
        return {"schema": version, "migrated": [], "backup": None, "state": state}
    migrated, steps = migrate(state, migrations=migrations, current=current, oldest=oldest)
    backup = backup_path(path, version)
    if backup.exists():
        existing, _ = _read(backup)
        if existing != raw:
            raise StateSchemaError("A different pre-upgrade backup already exists; inspect it")
    else:
        _write_atomic(backup, raw)
    _write_atomic(path, json.dumps(migrated, allow_nan=False, separators=(",", ":")).encode())
    return {"schema": current, "migrated": steps, "backup": backup.name, "state": migrated}


def rollback_state_file(path: Path, version: int, *, current: int = CURRENT_SCHEMA,
                        oldest: int = OLDEST_SCHEMA) -> dict:
    """Restore the schema-``version`` backup (for running an older release)."""
    backup = backup_path(path, version)
    if not backup.exists():
        raise StateSchemaError(f"No schema {version} backup to restore")
    raw, state = _read(backup)
    if state.get("schema") != version:
        raise StateSchemaError("The backup does not hold the schema it is named for")
    _, live = _read(path)
    if any(live.get(key) != state.get(key) for key in ("mac",)):
        raise StateSchemaError("The backup belongs to another device identity")
    _write_atomic(path, raw)
    return {"restored_schema": version, "from": backup.name}
