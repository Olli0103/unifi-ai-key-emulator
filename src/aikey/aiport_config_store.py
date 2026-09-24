"""Transactional provider settings for an already provisioned AI Port pool."""

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
import secrets
import stat
from typing import Any, Iterator

from .aiport_candidate import CandidateError, load_config
from .config import atomic_private


_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = frozenset({"provider", "model", "base_url", "allow_remote",
                     "allow_insecure_http", "max_output_tokens", "api_key_file",
                     "threshold", "smart_types", "max_events_per_hour",
                     "max_requests_per_hour"})
_PROVIDER_FIELDS = frozenset({"provider", "model", "base_url", "allow_remote",
                              "allow_insecure_http", "max_output_tokens", "api_key_file"})


class AiPortConfigurationError(ValueError):
    """A fixed safe error without private camera or provider details."""


class AiPortRevisionConflict(AiPortConfigurationError):
    pass


@dataclass(frozen=True)
class AiPortSettings:
    revision: str
    camera_count: int
    backend: str
    provider: str | None
    model: str | None
    base_url: str | None
    key_configured: bool
    allow_remote: bool
    allow_insecure_http: bool
    max_output_tokens: int | None
    threshold: float | None
    smart_types: tuple[str, ...]
    max_events_per_hour: int | None
    max_requests_per_hour: int | None


@dataclass(frozen=True)
class AiPortPreview:
    current_revision: str
    resulting_revision: str
    settings: AiPortSettings
    restart_required: bool


class AiPortConfigurationStore:
    """Change AI Port inference without touching its identity or paired streams."""

    def __init__(self, config_path: Path):
        self.path = Path(config_path).expanduser().absolute()
        self.lock_path = self.path.with_name("." + self.path.name + ".admin.lock")
        self.history_dir = self.path.parent / ".aiport-config-history"

    @staticmethod
    def _revision(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _read(self) -> tuple[bytes, dict]:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_mode & 0o077 or info.st_size > 4096):
                    raise ValueError
                content = source.read(4097)
                if len(content) > 4096:
                    raise ValueError
            return content, load_config(self.path)
        except (OSError, ValueError, CandidateError) as exc:
            raise AiPortConfigurationError("aiport_config_unavailable") from exc

    def _public(self, content: bytes, value: dict) -> AiPortSettings:
        detector = value.get("live_pool_detector", {})
        provider = detector.get("provider_config", {})
        backend = detector.get("inference_backend", "local" if detector else "off")
        return AiPortSettings(
            self._revision(content), len(value.get("paired_streams", ())), backend,
            provider.get("provider"), provider.get("model"), provider.get("base_url"),
            bool(provider.get("api_key_file")),
            provider.get("allow_remote") is True,
            provider.get("allow_insecure_http") is True,
            provider.get("max_output_tokens"), detector.get("threshold"),
            tuple(detector.get("smart_types", ())), detector.get("max_events_per_hour"),
            detector.get("max_requests_per_hour"),
        )

    def snapshot(self) -> AiPortSettings:
        content, value = self._read()
        return self._public(content, value)

    def preview(self, expected_revision: str, settings: dict[str, Any]) -> AiPortPreview:
        content, current = self._read()
        before = self._revision(content)
        if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                or not hmac.compare_digest(before, expected_revision)):
            raise AiPortRevisionConflict("aiport_config_changed")
        candidate = self._candidate(current, settings)
        self._validate(candidate)
        encoded = self._encode(candidate)
        after = self._revision(encoded) if encoded != content else before
        return AiPortPreview(before, after, self._public(encoded, candidate), after != before)

    def apply(self, expected_revision: str, settings: dict[str, Any]) -> AiPortSettings:
        with self._locked():
            content, current = self._read()
            before = self._revision(content)
            if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                    or not hmac.compare_digest(before, expected_revision)):
                raise AiPortRevisionConflict("aiport_config_changed")
            candidate = self._candidate(current, settings)
            self._validate(candidate)
            encoded = self._encode(candidate)
            if encoded == content:
                return self._public(content, current)
            self._archive(content)
            atomic_private(self.path, encoded)
            persisted, checked = self._read()
            if persisted != encoded:
                raise AiPortConfigurationError("aiport_config_write_uncertain")
            return self._public(persisted, checked)

    def _candidate(self, current: dict, settings: dict[str, Any]) -> dict:
        if ("paired_streams" not in current or not isinstance(settings, dict)
                or set(settings) not in (_FIELDS - {"api_key_file"}, _FIELDS)):
            raise AiPortConfigurationError("aiport_provider_settings_incomplete")
        if "api_key_file" in settings:
            path = settings["api_key_file"]
            if (not isinstance(path, str) or not Path(path).is_absolute()
                    or Path(path).parent != self.path.parent):
                raise AiPortConfigurationError("aiport_key_must_be_in_state_directory")
        provider = {key: settings[key] for key in _PROVIDER_FIELDS if key in settings}
        previous = current.get("live_pool_detector", {}).get("provider_config", {})
        if ("api_key_file" not in provider
                and previous.get("provider") == provider.get("provider")
                and previous.get("base_url") == provider.get("base_url")
                and previous.get("api_key_file")):
            provider["api_key_file"] = previous["api_key_file"]
        candidate = deepcopy(current)
        candidate["live_pool_detector"] = {
            "inference_backend": "vision_api", "provider_config": provider,
            "threshold": settings["threshold"],
            "smart_types": settings["smart_types"],
            "max_events_per_hour": settings["max_events_per_hour"],
            "max_requests_per_hour": settings["max_requests_per_hour"],
        }
        return candidate

    def _validate(self, candidate: dict) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.check")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(self._encode(candidate))
            load_config(temporary)
        except (OSError, CandidateError, TypeError, ValueError) as exc:
            raise AiPortConfigurationError("aiport_provider_settings_invalid") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _encode(value: dict) -> bytes:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                           allow_nan=False) + "\n").encode()

    def _archive(self, content: bytes) -> None:
        self.history_dir.mkdir(mode=0o700, exist_ok=True)
        info = self.history_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise AiPortConfigurationError("aiport_history_unsafe")
        archived = self.history_dir / f"{self._revision(content)}.json"
        try:
            archived_info = archived.lstat()
        except FileNotFoundError:
            archived_info = None
        if archived_info is not None:
            if (not stat.S_ISREG(archived_info.st_mode)
                    or archived_info.st_uid != os.geteuid()
                    or archived_info.st_mode & 0o077
                    or archived_info.st_size != len(content)
                    or archived.read_bytes() != content):
                raise AiPortConfigurationError("aiport_history_inconsistent")
            return
        atomic_private(archived, content)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = -1
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                                 0o600)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise OSError
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "rb") as lock:
                descriptor = -1
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                yield
        except OSError as exc:
            raise AiPortConfigurationError("aiport_config_lock_unavailable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
