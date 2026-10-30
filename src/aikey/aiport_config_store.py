"""Transactional provider settings for an already provisioned AI Port pool."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterator

from .aiport_candidate import CandidateError, parse_config
from .file_lock import acquire as acquire_file_lock
from .config import atomic_private
from .config_store import (ConfigurationPreview, ConfigurationSnapshot, ConfigurationStore,
                           ConfigurationStoreError, _changed, _merge_patch, _public)
from .role_changes import RoleModelPreview


_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = frozenset({"provider", "model", "base_url", "allow_remote",
                     "allow_insecure_http", "max_output_tokens", "api_key_file",
                     "threshold", "smart_types", "max_requests_per_hour"})
_OPTIONAL_FIELDS = frozenset({"api_key_file", "max_requests_per_hour"})
_PROVIDER_FIELDS = frozenset({"provider", "model", "base_url", "allow_remote",
                              "allow_insecure_http", "max_output_tokens", "api_key_file"})
_RECOGNITION_SCOPES = {"face": "live_face", "sound": "live_sound", "speech": "live_speech_cameras"}
_ROLE_PROVIDER_FIELDS = {"detection": "provider_config", "ocr": "ocr_provider_config"}
_POLICY_ROOTS = frozenset({"live_pool_detector", "live_face", "live_sound",
                          "live_speech_cameras", "live_speech_max_events_per_hour"})


class AiPortConfigurationError(ValueError):
    """A fixed safe error without private camera or provider details."""


class AiPortRevisionConflict(AiPortConfigurationError):
    pass


class AiPortUnsafePolicyChange(AiPortConfigurationError):
    """Pure policy admission refused before any configuration write."""


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
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_mode & 0o077 or info.st_size > 4096):
                    raise ValueError
                content = source.read(4097)
                if len(content) > 4096:
                    raise ValueError
            return content, parse_config(
                content, state_dir=self.path.parent,
                check_decoder_executable=(self.runtime_state_dir == self.path.parent))
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
            tuple(detector.get("smart_types", ())), detector.get("max_requests_per_hour"),
        )

    def snapshot(self) -> AiPortSettings:
        content, value = self._read()
        return self._public(content, value)

    @staticmethod
    def _assert_policy_only(before: dict, after: dict) -> None:
        if any(before.get(name) != after.get(name) for name in set(before) | set(after)
               if name not in _POLICY_ROOTS):
            raise AiPortUnsafePolicyChange("aiport_identity_pairing_or_runtime_change")

    @staticmethod
    def _assert_role_routes(before: dict, after: dict) -> None:
        old = before.get("live_pool_detector") or {}
        new = after.get("live_pool_detector") or {}
        for path in (("provider_config",), ("ocr_provider_config",), ("fallback", "provider_config")):
            a, b = old, new
            for name in path:
                a = a.get(name, {}) if isinstance(a, dict) else {}
                b = b.get(name, {}) if isinstance(b, dict) else {}
            key = a.get("api_key_file") if isinstance(a, dict) else None
            if (key and isinstance(b, dict) and key == b.get("api_key_file")
                    and any(a.get(n) != b.get(n) for n in ("provider", "base_url"))):
                raise AiPortUnsafePolicyChange("aiport_credential_route_requires_typed_operation")

    def _prepare_patch(self, content: bytes, expected_revision: str, patch: dict):
        revision = self._revision(content)
        if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                or not hmac.compare_digest(revision, expected_revision)):
            raise AiPortRevisionConflict("aiport_config_changed")
        try:
            if not isinstance(patch, dict) or len(json.dumps(patch, allow_nan=False).encode()) > 4096:
                raise ValueError
            current = json.loads(content)
            candidate = _merge_patch(current, patch)
            self._assert_policy_only(current, candidate)
            self._assert_role_routes(current, candidate)
            self._validate(candidate)
        except (ConfigurationStoreError, TypeError, ValueError) as exc:
            if isinstance(exc, AiPortUnsafePolicyChange):
                raise
            raise AiPortUnsafePolicyChange("aiport_policy_change_invalid") from exc
        changes = tuple(_changed(current, candidate))
        encoded = self._encode(candidate) if changes else content
        preview = ConfigurationPreview(revision, self._revision(encoded), _public(candidate),
                                       changes, bool(changes))
        return encoded, preview

    def preview_patch(self, expected_revision: str, patch: dict) -> ConfigurationPreview:
        content, _ = self._read()
        return self._prepare_patch(content, expected_revision, patch)[1]

    def apply_patch(self, expected_revision: str, patch: dict) -> ConfigurationSnapshot:
        with self._locked():
            content, _ = self._read()
            encoded, preview = self._prepare_patch(content, expected_revision, patch)
            if encoded != content:
                self._archive(content)
                atomic_private(self.path, encoded)
            persisted, checked = self._read()
            if persisted != encoded:
                raise AiPortConfigurationError("aiport_policy_write_uncertain")
            return ConfigurationSnapshot(preview.resulting_revision, _public(checked))

    def rollback(self, expected_revision: str, target_revision: str) -> ConfigurationSnapshot:
        if not isinstance(target_revision, str) or not _REVISION.fullmatch(target_revision):
            raise AiPortConfigurationError("aiport_history_revision_invalid")
        with self._locked():
            content, _ = self._read()
            if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                    or not hmac.compare_digest(self._revision(content), expected_revision)):
                raise AiPortRevisionConflict("aiport_config_changed")
            try:
                target = ConfigurationStore._read_regular_bytes(
                    self.history_dir / (target_revision + ".json"), 4096)
                if self._revision(target) != target_revision:
                    raise ValueError
                candidate = json.loads(target)
                self._assert_policy_only(json.loads(content), candidate)
                self._validate(candidate)
                def refs_present(value):
                    if isinstance(value, dict):
                        return all(self._key_reference_present(v) if k == "api_key_file"
                                   else refs_present(v) for k, v in value.items())
                    if isinstance(value, list):
                        return all(refs_present(v) for v in value)
                    return True
                if not refs_present(candidate):
                    raise AiPortConfigurationError("aiport_prior_key_missing")
            except (OSError, ConfigurationStoreError, ValueError, TypeError) as exc:
                if isinstance(exc, AiPortConfigurationError):
                    raise
                raise AiPortConfigurationError("aiport_history_unavailable") from exc
            if target != content:
                self._archive(content)
                atomic_private(self.path, target)
            persisted, checked = self._read()
            if persisted != target:
                raise AiPortConfigurationError("aiport_policy_write_uncertain")
            return ConfigurationSnapshot(target_revision, _public(checked))

    def plate_camera_status(self) -> dict:
        """Internal existing-camera scope and public OCR route; never a key reference."""
        content, current = self._read()
        detector = current.get("live_pool_detector", {})
        route = detector.get("ocr_provider_config") or detector.get("provider_config") or {}
        return {"revision": self._revision(content),
                "eligible": detector.get("inference_backend") == "vision_api",
                "cameras": tuple(s["camera_mac"] for s in current.get("paired_streams", ())),
                "plate_cameras": tuple(detector.get("plate_cameras", ())),
                "separate_ocr": bool(detector.get("ocr_provider_config")),
                "vehicle_detection_enabled": "vehicle" in detector.get("smart_types", ()),
                "route": {k: route.get(k) for k in ("provider", "model", "base_url")}}

    def _plate_camera_patch(self, expected_revision, selected):
        content, current = self._read()
        if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                or not hmac.compare_digest(self._revision(content), expected_revision)):
            raise AiPortRevisionConflict("aiport_config_changed")
        detector = current.get("live_pool_detector", {})
        cameras = [s["camera_mac"] for s in current.get("paired_streams", ())]
        if (detector.get("inference_backend") != "vision_api" or not isinstance(selected, list)
                or len(selected) > len(cameras) or any(not isinstance(c, str) for c in selected)
                or len(set(selected)) != len(selected) or not set(selected) <= set(cameras)):
            raise AiPortConfigurationError("aiport_plate_camera_selection_invalid")
        if set(selected) == set(detector.get("plate_cameras", ())):
            return {}
        if not selected:
            # An OCR route without plate permission is invalid in the runtime schema.
            # The explicit preview must disclose this removal; key files are retained.
            change = {"plate_cameras": None, "ocr_provider_config": None}
        else:
            original = json.loads(content)
            change = {"plate_cameras": [raw["camera_mac"] for raw, camera in
                       zip(original["paired_streams"], cameras, strict=True) if camera in selected]}
        return {"live_pool_detector": change}

    def preview_plate_cameras(self, expected_revision, selected) -> ConfigurationPreview:
        return self.preview_patch(expected_revision, self._plate_camera_patch(expected_revision, selected))

    def apply_plate_cameras(self, expected_revision, selected) -> ConfigurationSnapshot:
        return self.apply_patch(expected_revision, self._plate_camera_patch(expected_revision, selected))

    def camera_threshold_status(self) -> dict:
        """Internal camera IDs; the browser adapter substitutes opaque choices."""
        content, current = self._read()
        detector = current.get("live_pool_detector", {})
        return {"revision": self._revision(content),
                "eligible": detector.get("inference_backend") == "vision_api",
                "global_threshold": detector.get("threshold"),
                "cameras": tuple(s["camera_mac"] for s in current.get("paired_streams", ())),
                "thresholds": deepcopy(detector.get("camera_thresholds", {}))}

    def _camera_threshold_candidate(self, content, current, expected_revision, thresholds):
        if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                or not hmac.compare_digest(self._revision(content), expected_revision)):
            raise AiPortRevisionConflict("aiport_config_changed")
        cameras = {s["camera_mac"] for s in current.get("paired_streams", ())}
        detector = current.get("live_pool_detector", {})
        if (detector.get("inference_backend") != "vision_api" or not isinstance(thresholds, dict)
                or not set(thresholds) <= cameras
                or any(type(v) not in (int, float) or not 0 < v <= 1 for v in thresholds.values())):
            raise AiPortConfigurationError("aiport_camera_thresholds_invalid")
        candidate = json.loads(content)
        # A semantic no-op must retain omission, raw MAC formatting and exact bytes.
        if detector.get("camera_thresholds", {}) == thresholds:
            return content
        if thresholds:
            candidate["live_pool_detector"]["camera_thresholds"] = deepcopy(thresholds)
        else:
            candidate["live_pool_detector"].pop("camera_thresholds", None)
        self._validate(candidate)
        encoded = self._encode(candidate)
        if len(encoded) > 4096:
            raise AiPortConfigurationError("aiport_camera_thresholds_invalid")
        return encoded

    def preview_camera_thresholds(self, expected_revision, thresholds) -> AiPortPreview:
        content, current = self._read()
        encoded = self._camera_threshold_candidate(content, current, expected_revision, thresholds)
        return AiPortPreview(self._revision(content), self._revision(encoded),
                             self._public(content, current), encoded != content)

    @property
    def _threshold_journal(self):
        return self.history_dir / ("camera-threshold-change-" + self.path.name + ".json")

    def apply_camera_thresholds(self, expected_revision, thresholds) -> AiPortSettings:
        with self._locked():
            content, current = self._read()
            encoded = self._camera_threshold_candidate(content, current, expected_revision, thresholds)
            if encoded != content:
                self._archive(content)
                atomic_private(self.path, encoded)
            persisted, current = self._read()
            if persisted != encoded:
                raise AiPortConfigurationError("aiport_camera_threshold_write_uncertain")
            if encoded != content:
                atomic_private(self._threshold_journal, self._encode({"schema": 1,
                    "from": self._revision(content), "to": self._revision(encoded)}))
            return self._public(persisted, current)

    def _threshold_undo_target(self, content):
        info = self._threshold_journal.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            raise AiPortConfigurationError("aiport_camera_threshold_undo_invalid")
        journal = json.loads(ConfigurationStore._read_regular_bytes(self._threshold_journal, 4096))
        if (not isinstance(journal, dict) or set(journal) != {"schema", "from", "to"}
                or type(journal["schema"]) is not int or journal["schema"] != 1
                or not all(isinstance(journal[k], str) and _REVISION.fullmatch(journal[k])
                           for k in ("from", "to")) or journal["to"] != self._revision(content)):
            raise AiPortRevisionConflict("aiport_camera_threshold_undo_unavailable")
        target = self._archived(journal["from"])
        before, after = json.loads(content), json.loads(target)
        self._validate(after)
        for raw in (before, after):
            detector = raw.get("live_pool_detector")
            if not isinstance(detector, dict):
                raise AiPortConfigurationError("aiport_camera_threshold_undo_invalid")
            detector.pop("camera_thresholds", None)
        if before != after or target == content:
            raise AiPortConfigurationError("aiport_camera_threshold_undo_invalid")
        return target

    def camera_threshold_undo_status(self):
        try:
            content, _ = self._read()
            self._threshold_undo_target(content)
        except (OSError, ValueError, ConfigurationStoreError):
            return {"available": False}
        return {"available": True, "revision": self._revision(content)}

    def undo_camera_thresholds(self, expected_revision):
        with self._locked():
            content, _ = self._read()
            if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                    or not hmac.compare_digest(self._revision(content), expected_revision)):
                raise AiPortRevisionConflict("aiport_config_changed")
            target = self._threshold_undo_target(content)
            self._archive(content)
            atomic_private(self.path, target)
            persisted, checked = self._read()
            if persisted != target:
                raise AiPortConfigurationError("aiport_camera_threshold_write_uncertain")
            self._threshold_journal.unlink()
            return self._public(persisted, checked)

    def catalog_configuration(self, expected_revision: str, role: str) -> tuple[dict, Path, bool]:
        """Internal-only selected-role references, never an HTTP response."""
        content, current = self._read()
        if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                or not hmac.compare_digest(self._revision(content), expected_revision)):
            raise AiPortRevisionConflict("aiport_config_changed")
        detector = current.get("live_pool_detector", {})
        field = _ROLE_PROVIDER_FIELDS.get(role)
        if (field is None or detector.get("inference_backend") != "vision_api"
                or not isinstance(detector.get(field), dict)):
            raise AiPortConfigurationError("catalog_role_not_configured")
        return deepcopy(detector[field]), self.runtime_state_dir, False

    def configured_camera_macs(self) -> frozenset[str]:
        """Return the validated local allowlist, not Protect pairing state."""
        _, value = self._read()
        return frozenset(stream["camera_mac"] for stream in value.get("paired_streams", ()))

    def recognition_permissions(self, feature: str) -> tuple[str, tuple[str, ...] | None]:
        """Internal saved scope; the HTTP adapter must use opaque selection labels."""
        if not isinstance(feature, str) or feature not in _RECOGNITION_SCOPES:
            raise AiPortConfigurationError("aiport_unknown_recognition_feature")
        content, current = self._read()
        role = current.get(_RECOGNITION_SCOPES[feature])
        if role is None:
            return self._revision(content), None
        return self._revision(content), tuple(role if feature == "speech" else role["cameras"])

    def _recognition_restriction(self, expected_revision: str, feature: str,
                                retained: list[str]) -> tuple[bytes, dict, bytes]:
        if not isinstance(feature, str) or feature not in _RECOGNITION_SCOPES:
            raise AiPortConfigurationError("aiport_unknown_recognition_feature")
        content, current = self._read()
        if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                or not hmac.compare_digest(self._revision(content), expected_revision)):
            raise AiPortRevisionConflict("aiport_config_changed")
        field = _RECOGNITION_SCOPES[feature]
        role = current.get(field)
        if role is None:
            raise AiPortConfigurationError("aiport_recognition_not_configured")
        cameras = role if feature == "speech" else role["cameras"]
        if (not isinstance(retained, list) or len(retained) > len(cameras)
                or any(not isinstance(camera, str) for camera in retained)
                or len(set(retained)) != len(retained)
                or not set(retained) <= set(cameras)):
            raise AiPortConfigurationError("aiport_recognition_remove_only")
        if (feature == "speech" and not set(current.get("live_sound", {}).get("cameras", ()))
                <= set(retained)):
            raise AiPortConfigurationError("aiport_audio_required_by_sound")
        # Validate against canonical camera IDs, but preserve every unrelated
        # saved value/default omission instead of writing loader normalization.
        original = json.loads(content)
        candidate = deepcopy(original)
        if retained:
            raw_cameras = original[field] if feature == "speech" else original[field]["cameras"]
            remaining = [raw for raw, camera in zip(raw_cameras, cameras, strict=True)
                         if camera in retained]
            if feature == "speech":
                candidate[field] = remaining
            else:
                candidate[field]["cameras"] = remaining
        else:
            del candidate[field]
            if feature == "speech":
                candidate.pop("live_speech_max_events_per_hour", None)
        if candidate == original:
            return content, current, content
        self._validate(candidate)
        return content, candidate, self._encode(candidate)

    def preview_recognition_restriction(self, expected_revision: str, feature: str,
                                       retained: list[str]) -> AiPortPreview:
        content, candidate, encoded = self._recognition_restriction(expected_revision, feature, retained)
        return AiPortPreview(self._revision(content), self._revision(encoded),
                             self._public(encoded, candidate), encoded != content)

    def restrict_recognition(self, expected_revision: str, feature: str,
                             retained: list[str]) -> AiPortSettings:
        """Remove only saved scope. Runtime needs restart; history/models/keys stay.

        A previous provider Undo becomes stale and cannot restore removed scope.
        This operation never writes a new provider-undo record.
        """
        # Reject invalid requests before creating the shared apply lock/history.
        self._recognition_restriction(expected_revision, feature, retained)
        with self._locked():
            content, candidate, encoded = self._recognition_restriction(expected_revision, feature, retained)
            if encoded == content:
                return self._public(content, candidate)
            self._archive(content)
            try:
                atomic_private(self.path, encoded)
            except OSError as exc:
                raise AiPortConfigurationError("aiport_config_write_uncertain") from exc
            persisted, checked = self._read()
            if persisted != encoded:
                raise AiPortConfigurationError("aiport_config_write_uncertain")
            return self._public(persisted, checked)

    def preview(self, expected_revision: str, settings: dict[str, Any]) -> AiPortPreview:
        content, current = self._read()
        before = self._revision(content)
        if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                or not hmac.compare_digest(before, expected_revision)):
            raise AiPortRevisionConflict("aiport_config_changed")
        # Edit the same captured raw bytes, not parser-normalized camera names
        # or default values. Validation still uses the complete runtime parser.
        candidate = self._candidate(json.loads(content), settings)
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
            candidate = self._candidate(json.loads(content), settings)
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

    def ocr_provider_status(self) -> dict:
        """Private operator selection fields, never a credential or key path."""
        content, _ = self._read()
        raw = json.loads(content)
        detector = raw.get("live_pool_detector") or {}
        explicit = detector.get("ocr_provider_config")
        selected = explicit if explicit is not None else detector.get("provider_config", {})
        return {"revision": self._revision(content),
                "eligible": detector.get("inference_backend") == "vision_api"
                            and bool(detector.get("plate_cameras")),
                "plate_camera_count": len(detector.get("plate_cameras", [])),
                "assignment": "explicit" if explicit is not None else "legacy_shared_detection",
                "key_configured": bool(explicit and explicit.get("api_key_file")),
                "selection": {k: selected[k] for k in _PROVIDER_FIELDS - {"api_key_file"}
                              if k in selected}}

    def _ocr_candidate(self, expected_revision: str, settings: dict):
        content, _ = self._read()
        if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                or not hmac.compare_digest(self._revision(content), expected_revision)):
            raise AiPortRevisionConflict("aiport_config_changed")
        required = _PROVIDER_FIELDS - {"api_key_file"}
        if (not isinstance(settings, dict) or not required <= set(settings) <= _PROVIDER_FIELDS
                or any(type(settings[k]) is not bool for k in ("allow_remote", "allow_insecure_http"))
                or not isinstance(settings["model"], str) or not _MODEL_ID.fullmatch(settings["model"])
                or ".." in settings["model"]):
            raise AiPortConfigurationError("aiport_ocr_settings_invalid")
        candidate = json.loads(content)
        detector = candidate.get("live_pool_detector") or {}
        if detector.get("inference_backend") != "vision_api" or not detector.get("plate_cameras"):
            raise AiPortConfigurationError("aiport_ocr_existing_permission_required")
        selected = deepcopy(settings)
        if "api_key_file" in selected:
            path = selected["api_key_file"]
            if (not isinstance(path, str) or not Path(path).is_absolute()
                    or Path(path).parent != self.runtime_state_dir):
                raise AiPortConfigurationError("aiport_key_must_be_in_state_directory")
        previous = detector.get("ocr_provider_config") or {}
        if ("api_key_file" not in selected and previous.get("api_key_file")
                and all(previous.get(k) == selected.get(k) for k in ("provider", "base_url"))):
            selected["api_key_file"] = previous["api_key_file"]
        if selected["provider"] in {"openai", "anthropic"} and not selected.get("api_key_file"):
            raise AiPortConfigurationError("aiport_ocr_separate_key_required")
        detector["ocr_provider_config"] = selected
        self._validate(candidate)
        # A repeated semantic no-op must not re-encode old raw defaults/spellings.
        encoded = content if candidate == json.loads(content) else self._encode(candidate)
        if len(encoded) > 4096:
            raise AiPortConfigurationError("aiport_ocr_configuration_too_large")
        return content, candidate, encoded

    def preview_ocr_provider(self, expected_revision: str, settings: dict) -> ConfigurationPreview:
        content, candidate, encoded = self._ocr_candidate(expected_revision, settings)
        return ConfigurationPreview(self._revision(content), self._revision(encoded),
                                    {"ocr_provider": _public(candidate["live_pool_detector"]["ocr_provider_config"])},
                                    ("live_pool_detector.ocr_provider_config",) if encoded != content else (),
                                    encoded != content)

    def apply_ocr_provider(self, expected_revision: str, settings: dict) -> AiPortSettings:
        """Independent OCR only; retain identity, streams, plate scope and caps."""
        self._ocr_candidate(expected_revision, settings)  # refuse before lock creation
        with self._locked():
            content, candidate, encoded = self._ocr_candidate(expected_revision, settings)
            if encoded == content:
                return self._public(content, candidate)
            self._archive(content)
            try:
                atomic_private(self.path, encoded)
            except OSError as exc:
                raise AiPortConfigurationError("aiport_config_write_uncertain") from exc
            persisted, checked = self._read()
            if persisted != encoded:
                raise AiPortConfigurationError("aiport_config_write_uncertain")
            self._write_provider_journal(self._revision(content), self._revision(persisted))
            return self._public(persisted, checked)

    # --- detection role model (#17) ------------------------------------------

    def role_models(self) -> dict[str, dict[str, Any]]:
        _, current = self._read()
        detector = current.get("live_pool_detector", {})
        if detector.get("inference_backend") != "vision_api":
            return {"detection": {"model": None, "editable": False,
                                  "reason": "vision_provider_not_configured"}}
        return {role: {"model": detector[field].get("model"), "editable": True, "reason": None}
                for role, field in _ROLE_PROVIDER_FIELDS.items() if isinstance(detector.get(field), dict)}

    def model_registry(self) -> dict:
        from .model_roles import port_roles
        content, current = self._read()
        return port_roles(current, self._revision(content))

    def preview_role_model(self, expected_revision: str, role: str, model: str) -> RoleModelPreview:
        """Preview one local config, never instantiate a detector or write a key."""
        if role not in _ROLE_PROVIDER_FIELDS:
            raise AiPortConfigurationError("unknown_role")
        field = _ROLE_PROVIDER_FIELDS[role]
        if not isinstance(model, str) or not _MODEL_ID.fullmatch(model) or ".." in model:
            raise AiPortConfigurationError("invalid_model_id")
        content, current = self._read()
        revision = self._revision(content)
        if (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)
                or not hmac.compare_digest(revision, expected_revision)):
            raise AiPortRevisionConflict("aiport_config_changed")
        detector = current.get("live_pool_detector", {})
        if detector.get("inference_backend") != "vision_api":
            raise AiPortConfigurationError("vision_provider_not_configured")
        if not isinstance(detector.get(field), dict):
            raise AiPortConfigurationError("role_not_configured")
        previous = detector[field].get("model")
        changed = previous != model
        resulting = revision
        if changed:
            candidate = json.loads(content)
            candidate["live_pool_detector"][field]["model"] = model
            self._validate(candidate)
            resulting = self._revision(self._encode(candidate))
        return RoleModelPreview(revision, resulting, role, previous, model, changed)

    def apply_role_model(self, expected_revision: str, role: str, model: str) -> AiPortSettings:
        """Change only the selected model; other roles, keys and streams stay."""
        if role not in _ROLE_PROVIDER_FIELDS:
            raise AiPortConfigurationError("unknown_role")
        field = _ROLE_PROVIDER_FIELDS[role]
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
            if not isinstance(detector.get(field), dict):
                raise AiPortConfigurationError("role_not_configured")
            if detector[field].get("model") == model:
                return self._public(content, current)
            candidate = json.loads(content)
            candidate["live_pool_detector"][field]["model"] = model
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
        detector = target.get("live_pool_detector", {})
        if not isinstance(detector, dict) or any(
                field in detector and not isinstance(detector[field], dict)
                for field in _ROLE_PROVIDER_FIELDS.values()):
            return False
        return all(self._key_reference_present((detector.get(field) or {}).get("api_key_file"))
                   for field in _ROLE_PROVIDER_FIELDS.values())

    def _key_reference_present(self, reference) -> bool:
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
        # Settings this form does not manage (local fallback, plate cameras,
        # held-package mode, per-camera gates and score server) are kept.
        # A legacy object-event cap
        # is dropped: object events have no per-camera ceiling.
        kept = {key: value for key, value in current.get("live_pool_detector", {}).items()
                if key in {"fallback", "plate_cameras", "ocr_provider_config", "held_package_followup",
                           "camera_thresholds", "score_server"}}
        candidate["live_pool_detector"] = {
            "inference_backend": "vision_api", "provider_config": provider,
            "threshold": settings["threshold"],
            "smart_types": settings["smart_types"], **deepcopy(kept),
        }
        if settings.get("max_requests_per_hour") is not None:
            candidate["live_pool_detector"]["max_requests_per_hour"] = (
                settings["max_requests_per_hour"])
        return candidate

    def _validate(self, candidate: dict) -> None:
        try:
            parse_config(self._encode(candidate), state_dir=self.path.parent,
                         check_decoder_executable=(self.runtime_state_dir == self.path.parent))
        except (OSError, CandidateError, TypeError, ValueError) as exc:
            raise AiPortConfigurationError("aiport_provider_settings_invalid") from exc

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
                acquire_file_lock(lock.fileno())
                yield
        except OSError as exc:
            raise AiPortConfigurationError("aiport_config_lock_unavailable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
