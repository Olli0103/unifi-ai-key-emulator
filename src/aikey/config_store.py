"""Transactional configuration updates for the future administration service."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterator

from .config import ConfigError, atomic_private, validate_config


_MAX_CONFIG_BYTES = 1024 * 1024
_MAX_PATCH_BYTES = 256 * 1024
_REVISION = re.compile(r"[0-9a-f]{64}")
_WRITE_ONLY_PATHS = {
    ("device", "management_password"),
    ("device", "management_password_file"),
    ("inference", "api_key"),
    ("inference", "api_key_file"),
    ("embeddings", "bearer_token"),
    ("embeddings", "bearer_token_file"),
    ("database", "password"),
    ("database", "password_file"),
}
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "api_key", "authorization")
_INFERENCE_FIELDS = frozenset({"provider", "model", "base_url", "allow_remote",
                               "allow_insecure_http", "max_output_tokens",
                               "api_key_file"})
_IMMUTABLE_PATHS = {
    ("runtime", "mode"),
    ("runtime", "state_dir"),
    ("device", "mac"),
    ("device", "model"),
    ("device", "sysid"),
    ("device", "management_username"),
    ("device", "management_password_file"),
    ("controller", "ca_file"),
    ("database", "password_file"),
}


class ConfigurationStoreError(RuntimeError):
    """Configuration state cannot be read or changed safely."""


class RevisionConflict(ConfigurationStoreError):
    """The caller did not edit the current configuration revision."""


class UnsafeConfigurationChange(ConfigurationStoreError):
    """The requested change crosses a separate migration or secret boundary."""


@dataclass(frozen=True)
class ConfigurationSnapshot:
    revision: str
    configuration: dict[str, Any]


@dataclass(frozen=True)
class ConfigurationPreview:
    current_revision: str
    resulting_revision: str
    configuration: dict[str, Any]
    changed_fields: tuple[str, ...]
    restart_required: bool


def _revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _at(value: dict[str, Any], path: tuple[str, str]) -> Any:
    section = value.get(path[0])
    return section.get(path[1]) if isinstance(section, dict) else None


def _write_only(path: tuple[str, ...]) -> bool:
    return path in _WRITE_ONLY_PATHS or bool(
        path and any(part in path[-1].lower() for part in _SENSITIVE_KEY_PARTS)
    )


def _public(value: Any, path: tuple[str, ...] = ()) -> Any:
    if _write_only(path):
        return {"configured": bool(value), "write_only": True}
    if isinstance(value, dict):
        return {key: _public(item, path + (key,)) for key, item in value.items()}
    if isinstance(value, list):
        return [_public(item, path) for item in value]
    return deepcopy(value)


def _changed(before: Any, after: Any, path: tuple[str, ...] = ()) -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[str] = []
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                changes.append(".".join(path + (key,)))
            else:
                changes.extend(_changed(before[key], after[key], path + (key,)))
        return changes
    if before != after:
        return [".".join(path)]
    return []


def _merge_patch(target: Any, patch: Any, path: tuple[str, ...] = ()) -> Any:
    if len(path) > 8:
        raise UnsafeConfigurationChange("Configuration patch nesting exceeds the supported limit")
    if not isinstance(patch, dict):
        return deepcopy(patch)
    result = deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if not isinstance(key, str) or not key or len(key) > 128:
            raise UnsafeConfigurationChange("Configuration patch keys must be short nonempty strings")
        field = path + (key,)
        if _write_only(field):
            raise UnsafeConfigurationChange(
                "Secret replacement uses a separate write-only operation"
            )
        if value is None:
            result.pop(key, None)
        else:
            result[key] = _merge_patch(result.get(key), value, field)
    return result


class ConfigurationStore:
    """Validate, version and atomically replace one configuration file.

    The administration HTTP adapter can expose this small interface without
    returning secret file locations or allowing identity and index migrations.
    """

    def __init__(self, config_path: Path):
        self.path = Path(config_path).expanduser().absolute()
        self.lock_path = self.path.with_name("." + self.path.name + ".lock")
        self.history_dir = self.path.parent / ".aikey-config-history"

    def snapshot(self) -> ConfigurationSnapshot:
        content, checked = self._read(self.path)
        return ConfigurationSnapshot(_revision(content), _public(checked))

    def preview(self, expected_revision: str, patch: dict[str, Any]) -> ConfigurationPreview:
        content, current = self._read(self.path)
        return self._prepare(content, current, expected_revision, patch)

    def apply(self, expected_revision: str, patch: dict[str, Any]) -> ConfigurationSnapshot:
        with self._locked():
            content, current = self._read(self.path)
            preview = self._prepare(content, current, expected_revision, patch)
            if not preview.changed_fields:
                return ConfigurationSnapshot(preview.current_revision, _public(current))
            candidate = _merge_patch(current, patch)
            checked = self._validate(candidate)
            encoded = self._encode(checked)
            self._archive(content)
            self._replace(encoded)
            persisted, persisted_checked = self._read(self.path)
            if _revision(persisted) != _revision(encoded):
                raise ConfigurationStoreError("Configuration replacement could not be verified")
            return ConfigurationSnapshot(_revision(persisted), _public(persisted_checked))

    def rollback(self, expected_revision: str, target_revision: str) -> ConfigurationSnapshot:
        if not isinstance(target_revision, str) or not _REVISION.fullmatch(target_revision):
            raise ConfigurationStoreError("Rollback revision must be a SHA-256 identifier")
        with self._locked():
            current_content, current = self._read(self.path)
            current_revision = _revision(current_content)
            if current_revision != expected_revision:
                raise RevisionConflict("Configuration changed since it was read")
            target_path = self.history_dir / f"{target_revision}.json"
            target_content, target = self._read(target_path)
            if _revision(target_content) != target_revision:
                raise ConfigurationStoreError("Stored configuration revision does not match its identifier")
            self._assert_immutable(current, target)
            self._assert_search_profile(target)
            self._archive(current_content)
            self._replace(target_content)
            persisted, persisted_checked = self._read(self.path)
            return ConfigurationSnapshot(_revision(persisted), _public(persisted_checked))

    def preview_inference(self, expected_revision: str,
                          inference: dict[str, Any]) -> ConfigurationPreview:
        content, current = self._read(self.path)
        candidate = self._inference_candidate(current, inference)
        return self._prepare_inference(content, current, expected_revision, candidate)

    def apply_inference(self, expected_revision: str,
                        inference: dict[str, Any]) -> ConfigurationSnapshot:
        """Change only the vision provider, including its write-only key reference."""
        with self._locked():
            content, current = self._read(self.path)
            candidate = self._inference_candidate(current, inference)
            preview = self._prepare_inference(content, current, expected_revision, candidate)
            if not preview.changed_fields:
                return ConfigurationSnapshot(preview.current_revision, _public(current))
            encoded = self._encode(self._validate(candidate))
            self._archive(content)
            self._replace(encoded)
            persisted, checked = self._read(self.path)
            if _revision(persisted) != _revision(encoded):
                raise ConfigurationStoreError("Configuration replacement could not be verified")
            return ConfigurationSnapshot(_revision(persisted), _public(checked))

    def _inference_candidate(self, current: dict[str, Any],
                             inference: dict[str, Any]) -> dict[str, Any]:
        if (not isinstance(inference, dict) or not {"provider", "model", "base_url"} <=
                set(inference) or not set(inference) <= _INFERENCE_FIELDS):
            raise UnsafeConfigurationChange("Vision provider settings are incomplete")
        selected = deepcopy(inference)
        if ("api_key_file" not in selected
                and current["inference"].get("provider") == selected["provider"]
                and current["inference"].get("base_url") == selected["base_url"]
                and current["inference"].get("api_key_file")):
            selected["api_key_file"] = current["inference"]["api_key_file"]
        if "api_key_file" in inference:
            key_path = selected["api_key_file"]
            state = Path(current["runtime"]["state_dir"])
            if (not isinstance(key_path, str) or not Path(key_path).is_absolute()
                    or Path(key_path).parent != state):
                raise UnsafeConfigurationChange(
                    "Vision key file must be inside the processor state directory")
        candidate = deepcopy(current)
        candidate["inference"] = selected
        return candidate

    def _prepare_inference(self, content: bytes, current: dict[str, Any],
                           expected_revision: str,
                           candidate: dict[str, Any]) -> ConfigurationPreview:
        revision = _revision(content)
        if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                or not hmac.compare_digest(revision, expected_revision)):
            raise RevisionConflict("Configuration changed since it was read")
        checked = self._validate(candidate)
        self._assert_immutable(current, checked)
        self._assert_search_profile(checked)
        changes = tuple(_changed(current, checked))
        result_revision = revision if not changes else _revision(self._encode(checked))
        return ConfigurationPreview(revision, result_revision, _public(checked),
                                    changes, bool(changes))

    def _prepare(self, content: bytes, current: dict[str, Any], expected_revision: str,
                 patch: dict[str, Any]) -> ConfigurationPreview:
        current_revision = _revision(content)
        if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                or not hmac.compare_digest(current_revision, expected_revision)):
            raise RevisionConflict("Configuration changed since it was read")
        try:
            encoded_patch = json.dumps(patch, allow_nan=False, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc:
            raise UnsafeConfigurationChange("Configuration patch must be valid JSON") from exc
        if len(encoded_patch) > _MAX_PATCH_BYTES:
            raise UnsafeConfigurationChange("Configuration patch exceeds the size limit")
        candidate = _merge_patch(current, patch)
        checked = self._validate(candidate)
        self._assert_immutable(current, checked)
        self._assert_search_profile(checked)
        changes = tuple(_changed(current, checked))
        resulting = current_revision if not changes else _revision(self._encode(checked))
        return ConfigurationPreview(
            current_revision=current_revision,
            resulting_revision=resulting,
            configuration=_public(checked),
            changed_fields=changes,
            restart_required=bool(changes),
        )

    def _validate(self, value: dict[str, Any]) -> dict[str, Any]:
        try:
            return validate_config(value, base=self.path.parent)
        except ConfigError as exc:
            raise UnsafeConfigurationChange(str(exc)) from exc

    def _assert_immutable(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        changed = [".".join(path) for path in sorted(_IMMUTABLE_PATHS)
                   if _at(before, path) != _at(after, path)]
        if changed:
            raise UnsafeConfigurationChange(
                "Identity and credential locations require a separate migration: " + ", ".join(changed)
            )

    def _assert_search_profile(self, candidate: dict[str, Any]) -> None:
        profile = Path(candidate["runtime"]["state_dir"]) / "search-profile.json"
        if not profile.exists():
            return
        try:
            content = self._read_regular_bytes(profile, 16384)
            stored = json.loads(content)
            if not isinstance(stored, dict):
                raise ValueError
            fingerprint = stored.pop("fingerprint", None)
            encoded = json.dumps(stored, allow_nan=False, sort_keys=True, separators=(",", ":"))
            if not isinstance(fingerprint, str) or not hmac.compare_digest(
                    fingerprint, hashlib.sha256(encoded.encode()).hexdigest()):
                raise ValueError
            from .search import EmbeddingService
            expected = EmbeddingService(candidate["embeddings"]).identity
        except (OSError, ValueError, TypeError) as exc:
            raise UnsafeConfigurationChange(
                "Existing search profile is invalid; inspect it before changing configuration"
            ) from exc
        if stored != expected:
            raise UnsafeConfigurationChange(
                "Embedding profile changes require an explicit search-index migration"
            )

    def _read(self, path: Path) -> tuple[bytes, dict[str, Any]]:
        try:
            content = self._read_regular_bytes(path, _MAX_CONFIG_BYTES)
            raw = json.loads(content)
            if not isinstance(raw, dict):
                raise ValueError
            checked = validate_config(raw, base=path.parent)
            return content, checked
        except (ConfigError, OSError, UnicodeError, ValueError, TypeError) as exc:
            raise ConfigurationStoreError("Cannot read a valid regular configuration file") from exc

    @staticmethod
    def _encode(value: dict[str, Any]) -> bytes:
        try:
            return (json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()
        except (TypeError, ValueError) as exc:
            raise UnsafeConfigurationChange("Configuration cannot be serialized safely") from exc

    def _archive(self, content: bytes) -> None:
        revision = _revision(content)
        self.history_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = self.history_dir.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ConfigurationStoreError("Configuration history directory is unsafe")
        path = self.history_dir / f"{revision}.json"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                if self._read_regular_bytes(path, _MAX_CONFIG_BYTES) != content:
                    raise ConfigurationStoreError("Configuration history is inconsistent")
                return
            except OSError as exc:
                raise ConfigurationStoreError("Configuration history is unreadable") from exc
        except OSError as exc:
            raise ConfigurationStoreError("Cannot create configuration history") from exc
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            self._fsync_directory(self.history_dir)
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise ConfigurationStoreError("Cannot persist configuration history") from exc

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError
            os.fchmod(descriptor, 0o600)
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise ConfigurationStoreError("Cannot open the configuration update lock") from exc
        with os.fdopen(descriptor, "rb") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise ConfigurationStoreError("Cannot acquire the configuration update lock") from exc
            yield

    def _replace(self, content: bytes) -> None:
        try:
            atomic_private(self.path, content)
        except OSError as exc:
            raise ConfigurationStoreError(
                "Configuration replacement outcome is uncertain; read the active revision before retrying"
            ) from exc

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_regular_bytes(path: Path, limit: int) -> bytes:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
                raise OSError
            content = source.read(limit + 1)
            if len(content) > limit:
                raise OSError
            return content
