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
_OPTIONAL_FIELDS = frozenset({"api_key_file", "max_requests_per_hour"})
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


_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}\Z")


class AiPortConfigurationStore:
    """Change AI Port inference without touching its identity or paired streams."""

    def __init__(self, config_path: Path, *, runtime_state_dir: Path | None = None):
        self.path = Path(config_path).expanduser().absolute()
        self.runtime_state_dir = (Path(runtime_state_dir).expanduser().absolute()
                                  if runtime_state_dir is not None else self.path.parent)
        if not self.runtime_state_dir.is_absolute():
            raise AiPortConfigurationError("aiport_runtime_state_directory_invalid")
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
            return content, load_config(
                self.path, check_decoder_executable=(self.runtime_state_dir == self.path.parent))
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

    def configured_camera_macs(self) -> frozenset[str]:
        """Return the validated local allowlist, not Protect pairing state."""
        _, value = self._read()
        return frozenset(stream["camera_mac"] for stream in value.get("paired_streams", ()))

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
            # After the new bytes are verified, so a crash never offers an undo
            # for the wrong revision (#17).
            self._write_provider_journal(before, self._revision(persisted))
            return self._public(persisted, checked)

    # --- detection role model (#17) ------------------------------------------

    def role_models(self) -> dict[str, dict[str, Any]]:
        _, current = self._read()
        detector = current.get("live_pool_detector", {})
        if detector.get("inference_backend") != "vision_api":
            return {"detection": {"model": None, "editable": False,
                                  "reason": "vision_provider_not_configured"}}
        return {"detection": {"model": detector.get("provider_config", {}).get("model"),
                              "editable": True, "reason": None}}

    def apply_role_model(self, expected_revision: str, role: str, model: str) -> AiPortSettings:
        """Change only the detection model; provider, endpoint, key and streams stay."""
        if role != "detection":
            raise AiPortConfigurationError("unknown_role")
        if (not isinstance(model, str) or not _MODEL_ID.fullmatch(model) or ".." in model):
            raise AiPortConfigurationError("invalid_model_id")
        with self._locked():
            content, current = self._read()
            before = self._revision(content)
            if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                    or not hmac.compare_digest(before, expected_revision)):
                raise AiPortRevisionConflict("aiport_config_changed")
            detector = current.get("live_pool_detector", {})
            if detector.get("inference_backend") != "vision_api":
                raise AiPortConfigurationError("vision_provider_not_configured")
            if detector.get("provider_config", {}).get("model") == model:
                return self._public(content, current)
            candidate = deepcopy(current)
            candidate["live_pool_detector"]["provider_config"]["model"] = model
            self._validate(candidate)
            encoded = self._encode(candidate)
            self._archive(content)
            atomic_private(self.path, encoded)
            persisted, checked = self._read()
            if persisted != encoded:
                raise AiPortConfigurationError("aiport_config_write_uncertain")
            self._write_provider_journal(before, self._revision(persisted))
            return self._public(persisted, checked)

    # --- one-step provider rollback of this local config (#17) --------------

    @property
    def _provider_journal(self) -> Path:
        # Named per config file: two configs may share a directory.
        return self.history_dir / f"provider-change-{self.path.name}.json"

    def _write_provider_journal(self, previous: str, current: str) -> None:
        record = json.dumps({"schema": 1, "from": previous, "to": current},
                            separators=(",", ":")).encode() + b"\n"
        try:
            atomic_private(self._provider_journal, record)
        except OSError:
            pass            # the save stands; without a fresh record no undo is offered

    def _read_provider_journal(self) -> dict | None:
        path = self._provider_journal
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        try:
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                raise ValueError
            value = json.loads(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise AiPortConfigurationError("aiport_undo_record_invalid") from exc
        if (not isinstance(value, dict) or set(value) != {"schema", "from", "to"}
                or value["schema"] != 1
                or not all(isinstance(value[k], str) and _REVISION.fullmatch(value[k])
                           for k in ("from", "to"))):
            raise AiPortConfigurationError("aiport_undo_record_invalid")
        return value

    def _archived(self, revision: str) -> bytes:
        path = self.history_dir / f"{revision}.json"
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > 4096):
            raise AiPortConfigurationError("aiport_history_unsafe")
        content = path.read_bytes()
        if self._revision(content) != revision:
            raise AiPortConfigurationError("aiport_history_inconsistent")
        return content

    def _prior_key_present(self, target: dict) -> bool:
        reference = (target.get("live_pool_detector", {}).get("provider_config", {})
                     .get("api_key_file"))
        if reference is None:
            return True
        if not isinstance(reference, str) or not reference:
            return False
        for candidate in (Path(reference), self.path.parent / Path(reference).name):
            try:
                info = candidate.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_size > 0:
                return True
        return False

    @staticmethod
    def _outside_provider(value: dict) -> dict:
        return {key: item for key, item in value.items() if key != "live_pool_detector"}

    def provider_rollback_status(self) -> dict[str, Any]:
        """Whether this config's last provider save can be undone; no paths or values."""
        try:
            journal = self._read_provider_journal()
            if journal is None:
                return {"available": False, "reason": "no_recorded_provider_change"}
            content, current = self._read()
            if self._revision(content) != journal["to"]:
                return {"available": False, "reason": "configuration_changed_since"}
            target = json.loads(self._archived(journal["from"]))
            if self._outside_provider(target) != self._outside_provider(json.loads(content)):
                return {"available": False, "reason": "identity_or_streams_differ"}
            if not self._prior_key_present(target):
                return {"available": False, "reason": "prior_key_missing"}
        except (AiPortConfigurationError, OSError, ValueError):
            return {"available": False, "reason": "undo_record_unreadable"}
        return {"available": True, "reason": None, "revision": journal["to"]}

    def rollback_provider(self, expected_revision: str) -> AiPortSettings:
        """Restore the exact local config from before its last provider save."""
        with self._locked():
            journal = self._read_provider_journal()
            if journal is None:
                raise AiPortConfigurationError("aiport_no_provider_change")
            content, _ = self._read()
            before = self._revision(content)
            if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                    or not hmac.compare_digest(before, expected_revision)
                    or before != journal["to"]):
                raise AiPortRevisionConflict("aiport_config_changed")
            try:
                target_content = self._archived(journal["from"])
                target = json.loads(target_content)
            except (OSError, ValueError) as exc:
                raise AiPortConfigurationError("aiport_history_unreadable") from exc
            if self._outside_provider(target) != self._outside_provider(json.loads(content)):
                raise AiPortConfigurationError("aiport_rollback_would_change_identity_or_streams")
            if not self._prior_key_present(target):
                raise AiPortConfigurationError("aiport_prior_key_missing")
            self._validate(target)
            self._archive(content)
            try:
                atomic_private(self.path, target_content)
            except OSError as exc:
                raise AiPortConfigurationError("aiport_config_write_uncertain") from exc
            persisted, checked = self._read()
            if persisted != target_content:
                raise AiPortConfigurationError("aiport_config_write_uncertain")
            self._provider_journal.unlink(missing_ok=True)
            return self._public(persisted, checked)

    def _candidate(self, current: dict, settings: dict[str, Any]) -> dict:
        # The API request cap is an optional cost control, off when absent or null.
        if ("paired_streams" not in current or not isinstance(settings, dict)
                or not _FIELDS - _OPTIONAL_FIELDS <= set(settings) <= _FIELDS):
            raise AiPortConfigurationError("aiport_provider_settings_incomplete")
        if "api_key_file" in settings:
            path = settings["api_key_file"]
            if (not isinstance(path, str) or not Path(path).is_absolute()
                    or Path(path).parent != self.runtime_state_dir):
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
        }
        if settings.get("max_requests_per_hour") is not None:
            candidate["live_pool_detector"]["max_requests_per_hour"] = (
                settings["max_requests_per_hour"])
        return candidate

    def _validate(self, candidate: dict) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.check")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(self._encode(candidate))
            load_config(temporary,
                        check_decoder_executable=(self.runtime_state_dir == self.path.parent))
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
