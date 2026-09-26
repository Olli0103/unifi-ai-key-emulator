"""Isolated AI Port candidate with explicit camera and detection policies."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import ssl
import stat
import time
from urllib.parse import quote

import aiohttp
from aiohttp import web

from .aiport_ingest import (
    AiPortIngress, AiPortIngressPool, IngressError, executable_path,
    normalize_mac, private_source_ip,
)
from .aiport_detection import DetectionError, ObjectObservation, RFDetrNanoDetector
from .aiport_api_detection import ApiObjectDetector, _frame_mode
from .aiport_motion import (MotionDetector, MotionSettingsError,
                            motion_event_payload, parse_motion_settings)
from .aiport_onnx_detection import OnnxRFDetrNanoDetector
from .aiport_camera_engine import (
    _PACKAGE_COOLDOWN_SECONDS, CameraEventCandidate, CameraPolicyEngine,
)
from .aiport_event_budget import EventBudget, EventBudgetError
from .aiport_inference import FairInference
from .aiport_tracking import TemporalTracker, TrackChange, TrackingError
from .aiport_smart_events import (SmartEventError, camera_event_payload,
                                  smart_event_payload)
from .aiport_snapshots import (
    SmartSnapshot, SnapshotError, make_smart_snapshot, validated_upload_url,
)
from .aiport_recorded_probe import (
    RecordedProbeError, infer_recorded_person, parse_recorded_probe,
    read_recorded_frame,
)
from .aiport_smart_settings import (
    SmartPolicy, SmartSettingsError, parse_motion_probe, parse_smart_settings,
    summarize_recognition_accuracy, summarize_secondary_lens_zones,
    summarize_smart_request,
)
from .aiport_credentials import CredentialError, CredentialStore
from .aiport_adoption import AdoptionError, AdoptionStore, validate_management
from .aiport_virtual_hardware import (
    VirtualHardwareError, VirtualSoundLedStore, VirtualTimezoneStore,
)
from .device import VerifiedConnector
from .providers import ProviderError, validate_inference_config


_MAC = re.compile(r"[0-9A-Fa-f]{12}\Z")
_PIN = re.compile(r"[0-9A-Fa-f]{64}\Z")
_VERSION = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\Z")
_PROBE_NONCE = re.compile(r"[0-9a-f]{32}\Z")
_MAX_MANAGE = 8192
_DISCONNECT_GRACE_SECONDS = 15
# Bound on closing open native events while stopping (e.g. a redeploy).
_STOP_CLOSE_SECONDS = 2.0
_ALLOWED_TOP_LEVEL = frozenset(("username", "password", "mgmt", "hosts", "protocol", "mode"))
_ALLOWED_MGMT = frozenset(("token", "hosts", "protocol", "mode", "nvr",
                           "username", "password", "controller", "consoleId",
                           "consoleName"))
_OBSERVABLE_FUNCTIONS = frozenset({
    "ubnt_avclient_hello", "ubnt_avclient_paramAgreement", "GetStreamList",
    "ubnt_avclient_timeSync", "ubnt_avclient_time", "ubnt_avclient_features",
    "GetSystemStats", "NetworkStatus", "GetFeatures", "GetVideoSettings",
    "GetIspSettings", "GetUiStreamPoints", "GetAvclientState",
    "ResetAIPortStreams", "EventSmartDetect",
    "ChangeVideoSettings", "ChangeIspSettings",
    "StartService", "StopService", "UpdateUsernamePassword",
    "ChangeSoundLedSettings", "ChangeNvrSettings",
    "UiStreamControl", "OnvifStreamControl", "ChangeDeviceSettings", "GetRequest",
    "ChangeSmartDetectSettings", "ChangeSmartMotionSettings",
    "ChangeAnalyticsSettings", "ChangeAudioEventsSettings",
    "ChangeEventSettings", "ChangeAvclientEventSettings",
    "UpdateFeatureFlags", "EventFeatureFlagsUpdated", "EventAIPortStatus",
    "UpdateFaceDBRequest",
})


class CandidateError(ValueError):
    """Unsafe or incomplete isolated AI Port candidate configuration."""


@dataclass
class _PendingPoolSnapshot:
    camera_mac: str
    snapshot: SmartSnapshot
    expires: float
    crop_available: bool = True
    full_available: bool = True


def _private_file(path: Path, max_size: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > max_size:
                raise CandidateError("Candidate state file must be private and size-bounded")
            return handle.read(max_size + 1)
    except OSError as exc:
        raise CandidateError("Candidate state file is unavailable") from exc


def _private_ipv4(value: object) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError) as exc:
        raise CandidateError("Candidate requires an explicit IPv4 address") from exc
    if not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified:
        raise CandidateError("Candidate requires a private LAN IPv4 address")
    return str(address)


def _offline_decoder_path(value: object) -> str:
    """Validate syntax when an admin runs outside the decoder's container."""
    if (type(value) is not str or not Path(value).is_absolute()
            or len(value) > 4096 or "\x00" in value):
        raise IngressError("invalid_decoder")
    return value


def load_config(path: Path, *, check_decoder_executable: bool = True) -> dict:
    raw = _private_file(path, 4096)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CandidateError("Invalid candidate configuration JSON") from exc
    required = {"controller_ip", "device_ip", "mac", "controller_pin", "firmware_version"}
    allowed = required | {"paired_stream", "paired_streams", "diagnostic_hello_until",
                          "diagnostic_stream", "live_detector", "live_pool_detector",
                          "diagnostic_streams",
                          "diagnostic_detector",
                          "diagnostic_pool_detector", "diagnostic_pool_event_until",
                          "diagnostic_smart_probe_until",
                          "diagnostic_event_until",
                          "diagnostic_native_event_probe",
                          "diagnostic_recorded_event_probe",
                          "diagnostic_smart_type", "diagnostic_pool_smart_types",
                          "diagnostic_adoption_until", "diagnostic_resume_until",
                          "diagnostic_function_fingerprints_until"}
    if not isinstance(value, dict) or not required <= set(value) or not set(value) <= allowed:
        raise CandidateError("Candidate configuration fields do not match the isolated profile")
    if "paired_stream" in value:
        stream = value["paired_stream"]
        if (not isinstance(stream, dict) or set(stream) != {
                "camera_mac", "source_ip", "ffmpeg_path"}
                or "diagnostic_hello_until" in value
                or "diagnostic_stream" in value
                or "diagnostic_streams" in value):
            raise CandidateError("Paired stream requires one isolated camera policy")
        try:
            stream["camera_mac"] = normalize_mac(stream["camera_mac"])
            stream["source_ip"] = private_source_ip(stream["source_ip"])
            stream["ffmpeg_path"] = (executable_path(stream["ffmpeg_path"])
                                      if check_decoder_executable else
                                      _offline_decoder_path(stream["ffmpeg_path"]))
        except IngressError as exc:
            raise CandidateError("Invalid paired stream policy") from exc
        live_probe_fields = {"diagnostic_detector", "diagnostic_smart_probe_until",
                             "diagnostic_event_until"}
        recorded_probe_fields = {"diagnostic_recorded_event_probe",
                                 "diagnostic_smart_probe_until",
                                 "diagnostic_event_until"}
        required_probe_fields = (recorded_probe_fields
                                 if "diagnostic_recorded_event_probe" in value
                                 else live_probe_fields)
        if (set(value) & required_probe_fields
                and not required_probe_fields <= set(value)):
            raise CandidateError("Paired camera AI probe requires a complete bounded policy")
    if "live_detector" in value:
        detector = value["live_detector"]
        if ("paired_stream" not in value
                or any(key.startswith("diagnostic_") for key in value)
                or not isinstance(detector, dict)
                or set(detector) != {"checkpoint_path", "checkpoint_sha256",
                                     "threshold", "smart_type", "max_events_per_hour"}
                or not isinstance(detector["checkpoint_path"], str)
                or not Path(detector["checkpoint_path"]).is_absolute()
                or not isinstance(detector["checkpoint_sha256"], str)
                or not _PIN.fullmatch(detector["checkpoint_sha256"])
                or type(detector["smart_type"]) is not str
                or detector["smart_type"] not in {"person", "vehicle", "animal"}
                or type(detector["max_events_per_hour"]) is not int
                or not 1 <= detector["max_events_per_hour"] <= 120):
            raise CandidateError("Invalid live detector policy")
        try:
            RFDetrNanoDetector(object(), threshold=detector["threshold"])
        except DetectionError as exc:
            raise CandidateError("Invalid live detector threshold") from exc
    if "paired_streams" in value:
        streams = value["paired_streams"]
        if ("paired_stream" in value or "live_pool_detector" not in value
                or any(key.startswith("diagnostic_") for key in value)
                or "live_detector" in value or not isinstance(streams, list)
                or not 2 <= len(streams) <= 5):
            raise CandidateError("Invalid live camera pool")
        seen = set()
        for stream in streams:
            if not isinstance(stream, dict) or set(stream) != {
                    "camera_mac", "source_ip", "ffmpeg_path"}:
                raise CandidateError("Invalid live camera pool")
            try:
                camera_mac = normalize_mac(stream["camera_mac"])
                stream["source_ip"] = private_source_ip(stream["source_ip"])
                stream["ffmpeg_path"] = (executable_path(stream["ffmpeg_path"])
                                          if check_decoder_executable else
                                          _offline_decoder_path(stream["ffmpeg_path"]))
            except IngressError as exc:
                raise CandidateError("Invalid live camera pool") from exc
            if camera_mac in seen:
                raise CandidateError("Duplicate live camera identity")
            seen.add(camera_mac)
            stream["camera_mac"] = camera_mac
    if "live_pool_detector" in value:
        detector = value["live_pool_detector"]
        common = {"threshold", "smart_types", "max_events_per_hour"}
        pytorch_fields = common | {"checkpoint_path", "checkpoint_sha256"}
        onnx_fields = common | {"inference_backend", "model_path", "model_sha256"}
        api_fields = common | {"inference_backend", "provider_config",
                               "max_requests_per_hour"}
        backend = detector.get("inference_backend") if isinstance(detector, dict) else None
        is_api = backend == "vision_api"
        is_onnx = isinstance(backend, str) and backend in {
            "onnx_cpu", "onnx_openvino_gpu"}
        supported_types = ({"person", "vehicle", "animal", "package"} if is_api
                           else {"person", "vehicle", "animal"})
        if ("paired_streams" not in value or not isinstance(detector, dict)
                or not (set(detector) == (api_fields if is_api else
                                          onnx_fields if is_onnx else pytorch_fields)
                        # The API request cap is an optional cost control.
                        or is_api and set(detector) == api_fields - {"max_requests_per_hour"})
                or backend is not None and not (is_api or is_onnx)
                or not is_api and (
                    not isinstance(detector["model_path" if is_onnx else
                                            "checkpoint_path"], str)
                    or not Path(detector["model_path" if is_onnx else
                                         "checkpoint_path"]).is_absolute()
                    or not isinstance(detector["model_sha256" if is_onnx else
                                               "checkpoint_sha256"], str)
                    or not _PIN.fullmatch(detector["model_sha256" if is_onnx else
                                                    "checkpoint_sha256"]))
                or not isinstance(detector["smart_types"], list)
                or not 1 <= len(detector["smart_types"]) <= len(supported_types)
                or any(type(kind) is not str or kind not in supported_types
                       for kind in detector["smart_types"])
                or len(set(detector["smart_types"])) != len(detector["smart_types"])
                or type(detector["max_events_per_hour"]) is not int
                or not 1 <= detector["max_events_per_hour"] <= 3600):
            raise CandidateError("Invalid live pool detector policy")
        try:
            RFDetrNanoDetector(object(), threshold=detector["threshold"])
        except DetectionError as exc:
            raise CandidateError("Invalid live pool detector threshold") from exc
        if is_api:
            provider = detector["provider_config"]
            allowed_provider_fields = {"provider", "model", "base_url", "api_key_file",
                                       "allow_remote", "allow_insecure_http",
                                       "max_output_tokens"}
            if (not isinstance(provider, dict) or not set(provider) <= allowed_provider_fields
                    or "api_key" in provider
                    or detector.get("max_requests_per_hour") is not None
                    and (type(detector["max_requests_per_hour"]) is not int
                         or not 2 <= detector["max_requests_per_hour"] <= 3600)
                    or type(provider.get("max_output_tokens", 256)) is not int
                    or not 1 <= provider.get("max_output_tokens", 256) <= 512):
                raise CandidateError("Invalid live API detector policy")
            try:
                validate_inference_config(provider, require_api_key=False)
            except ProviderError as exc:
                raise CandidateError("Invalid live API detector provider") from exc
    if "diagnostic_hello_until" in value:
        until = value["diagnostic_hello_until"]
        if type(until) is not int or until < 0 or until > time.time() + 600:
            raise CandidateError("Diagnostic hello must expire within ten minutes")
    if "diagnostic_adoption_until" in value:
        until = value["diagnostic_adoption_until"]
        if type(until) is not int or until < 0 or until > time.time() + 600:
            raise CandidateError("Diagnostic adoption must expire within ten minutes")
    if "diagnostic_resume_until" in value:
        until = value["diagnostic_resume_until"]
        if type(until) is not int or until < 0 or until > time.time() + 600:
            raise CandidateError("Diagnostic resume must expire within ten minutes")
        if "diagnostic_adoption_until" in value:
            raise CandidateError("Adoption and existing-device resume cannot be combined")
    if "diagnostic_function_fingerprints_until" in value:
        until = value["diagnostic_function_fingerprints_until"]
        if type(until) is not int or until < 0 or until > time.time() + 600:
            raise CandidateError("Function fingerprint diagnostic must expire within ten minutes")
    if "diagnostic_stream" in value:
        stream = value["diagnostic_stream"]
        if (not isinstance(stream, dict) or set(stream) != {
                "camera_mac", "source_ip", "ffmpeg_path"}
                or "diagnostic_hello_until" not in value):
            raise CandidateError("Stream diagnostic requires a bounded hello")
        try:
            stream["camera_mac"] = normalize_mac(stream["camera_mac"])
            stream["source_ip"] = private_source_ip(stream["source_ip"])
            stream["ffmpeg_path"] = (executable_path(stream["ffmpeg_path"])
                                      if check_decoder_executable else
                                      _offline_decoder_path(stream["ffmpeg_path"]))
        except IngressError as exc:
            raise CandidateError("Invalid stream diagnostic policy") from exc
    if "diagnostic_streams" in value:
        streams = value["diagnostic_streams"]
        if ("diagnostic_stream" in value or "diagnostic_hello_until" not in value
                or not isinstance(streams, list) or not 2 <= len(streams) <= 5
                or "diagnostic_detector" in value
                or ("diagnostic_pool_detector" in value
                    and "diagnostic_pool_event_until" not in value)
                or "diagnostic_smart_probe_until" in value
                or "diagnostic_event_until" in value):
            raise CandidateError("Multi-camera diagnostic accepts streams only")
        seen = set()
        for stream in streams:
            if not isinstance(stream, dict) or set(stream) != {
                    "camera_mac", "source_ip", "ffmpeg_path"}:
                raise CandidateError("Invalid multi-camera stream policy")
            try:
                camera_mac = normalize_mac(stream["camera_mac"])
                stream["source_ip"] = private_source_ip(stream["source_ip"])
                stream["ffmpeg_path"] = (executable_path(stream["ffmpeg_path"])
                                          if check_decoder_executable else
                                          _offline_decoder_path(stream["ffmpeg_path"]))
            except IngressError as exc:
                raise CandidateError("Invalid multi-camera stream policy") from exc
            if camera_mac in seen:
                raise CandidateError("Duplicate multi-camera identity")
            seen.add(camera_mac)
            stream["camera_mac"] = camera_mac
    if "diagnostic_pool_detector" in value:
        detector = value["diagnostic_pool_detector"]
        if ("diagnostic_streams" not in value or not isinstance(detector, dict)
                or set(detector) != {"checkpoint_path", "checkpoint_sha256",
                                     "threshold", "max_frames_per_camera"}
                or not isinstance(detector["checkpoint_path"], str)
                or not Path(detector["checkpoint_path"]).is_absolute()
                or not isinstance(detector["checkpoint_sha256"], str)
                or not _PIN.fullmatch(detector["checkpoint_sha256"])
                or type(detector["max_frames_per_camera"]) is not int
                or not 2 <= detector["max_frames_per_camera"] <= 120):
            raise CandidateError("Invalid bounded pool detector policy")
        try:
            RFDetrNanoDetector(object(), threshold=detector["threshold"])
        except DetectionError as exc:
            raise CandidateError("Invalid bounded pool detector threshold") from exc
    if "diagnostic_pool_event_until" in value:
        until = value["diagnostic_pool_event_until"]
        if ("diagnostic_pool_detector" not in value or type(until) is not int
                or until <= int(time.time())
                or until != value.get("diagnostic_hello_until")):
            raise CandidateError("Pool event diagnostic requires bounded streams and detector")
    if "diagnostic_detector" in value:
        detector = value["diagnostic_detector"]
        if (("diagnostic_stream" not in value and "paired_stream" not in value)
                or not isinstance(detector, dict)
                or set(detector) != {"checkpoint_path", "checkpoint_sha256",
                                     "threshold", "max_frames"}
                or not isinstance(detector["checkpoint_path"], str)
                or not Path(detector["checkpoint_path"]).is_absolute()
                or not isinstance(detector["checkpoint_sha256"], str)
                or not _PIN.fullmatch(detector["checkpoint_sha256"])
                or type(detector["max_frames"]) is not int
                or not 1 <= detector["max_frames"] <= (
                    600 if "paired_stream" in value
                    and "diagnostic_event_until" in value else
                    120 if "diagnostic_event_until" in value else 3)):
            raise CandidateError("Invalid bounded detector policy")
        try:
            RFDetrNanoDetector(object(), threshold=detector["threshold"])
        except DetectionError as exc:
            raise CandidateError("Invalid bounded detector threshold") from exc
    if "diagnostic_native_event_probe" in value:
        probe = value["diagnostic_native_event_probe"]
        if ("diagnostic_detector" in value or "diagnostic_stream" not in value
                or "diagnostic_event_until" not in value
                or value.get("diagnostic_smart_type", "person") != "person"
                or not isinstance(probe, dict)
                or set(probe) != {"camera_mac", "nonce", "box"}
                or not isinstance(probe["nonce"], str)
                or not _PROBE_NONCE.fullmatch(probe["nonce"])
                or not isinstance(probe["box"], list)
                or len(probe["box"]) != 4
                or any(type(number) not in (int, float) or not math.isfinite(number)
                       for number in probe["box"])):
            raise CandidateError("Invalid one-use native event probe")
        try:
            probe["camera_mac"] = normalize_mac(probe["camera_mac"])
        except IngressError as exc:
            raise CandidateError("Invalid one-use native event probe") from exc
        x1, y1, x2, y2 = probe["box"]
        if (probe["camera_mac"] != value["diagnostic_stream"]["camera_mac"]
                or not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1)):
            raise CandidateError("Native event probe must match the bounded camera")
    if "diagnostic_recorded_event_probe" in value:
        if ("diagnostic_detector" in value
                or "diagnostic_native_event_probe" in value
                or ("diagnostic_stream" not in value
                    and "paired_stream" not in value)
                or "diagnostic_event_until" not in value
                or value.get("diagnostic_smart_type", "person") != "person"):
            raise CandidateError("Recorded event probe requires one person stream")
        try:
            parse_recorded_probe(
                value["diagnostic_recorded_event_probe"],
                state_dir=path.parent,
                camera_mac=value.get("paired_stream", value.get("diagnostic_stream"))["camera_mac"])
        except RecordedProbeError as exc:
            raise CandidateError(str(exc)) from exc
    if "diagnostic_smart_probe_until" in value:
        until = value["diagnostic_smart_probe_until"]
        if (("diagnostic_stream" not in value and "paired_stream" not in value)
                or type(until) is not int
                or until <= int(time.time()) or until > time.time() + 600
                or ("paired_stream" not in value
                    and until != value.get("diagnostic_hello_until"))):
            raise CandidateError("Smart settings probe requires a bounded camera stream")
    if "diagnostic_event_until" in value:
        until = value["diagnostic_event_until"]
        if (type(until) is not int or until != value.get("diagnostic_smart_probe_until")
                or ("diagnostic_detector" not in value
                    and "diagnostic_native_event_probe" not in value
                    and "diagnostic_recorded_event_probe" not in value)
                or ("diagnostic_detector" in value
                    and value["diagnostic_detector"]["max_frames"] < 2)):
            raise CandidateError("Smart event probe requires a bounded source and policy")
        if ("paired_stream" in value and not ({"diagnostic_detector",
                "diagnostic_recorded_event_probe"} & set(value))):
            raise CandidateError("Paired camera event probe requires a local detector")
    if "diagnostic_smart_type" in value:
        if (not isinstance(value["diagnostic_smart_type"], str)
                or value["diagnostic_smart_type"] not in {"person", "vehicle", "animal"}
                or not ("diagnostic_event_until" in value
                        or "diagnostic_pool_event_until" in value)):
            raise CandidateError("Smart type requires a bounded event diagnostic")
    if "diagnostic_pool_smart_types" in value:
        kinds = value["diagnostic_pool_smart_types"]
        if ("diagnostic_pool_event_until" not in value
                or "diagnostic_smart_type" in value
                or not isinstance(kinds, list) or not 1 <= len(kinds) <= 3
                or any(type(kind) is not str or kind not in {
                    "person", "vehicle", "animal"} for kind in kinds)
                or len(set(kinds)) != len(kinds)):
            raise CandidateError("Pool smart types require a bounded multi-camera event diagnostic")
    value["controller_ip"] = _private_ipv4(value["controller_ip"])
    value["device_ip"] = _private_ipv4(value["device_ip"])
    mac = value["mac"]
    if not isinstance(mac, str) or not _MAC.fullmatch(mac) or int(mac[:2], 16) & 3 != 2:
        raise CandidateError("Candidate requires a distinct locally administered unicast MAC")
    value["mac"] = mac.upper()
    pin = value["controller_pin"]
    if not isinstance(pin, str) or not _PIN.fullmatch(pin):
        raise CandidateError("Candidate requires a controller SHA-256 certificate pin")
    value["controller_pin"] = pin.lower()
    version = value["firmware_version"]
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise CandidateError("Invalid candidate firmware version")
    for name in ("device.crt", "device.key", "controller-ca.pem"):
        _private_file(path.parent / name, 16384)
    return value


def _object_shape(raw: bytes) -> dict:
    try:
        body = _strict_json_object(raw)
    except (ValueError, UnicodeError, RecursionError):
        return {"valid_json_object": False}
    mgmt = body.get("mgmt")
    return {"valid_json_object": True,
            "recognized_fields": sorted(set(body) & _ALLOWED_TOP_LEVEL),
            "other_field_count": len(set(body) - _ALLOWED_TOP_LEVEL),
            "mgmt_recognized_fields": sorted(set(mgmt) & _ALLOWED_MGMT) if isinstance(mgmt, dict) else [],
            "mgmt_other_field_count": len(set(mgmt) - _ALLOWED_MGMT) if isinstance(mgmt, dict) else 0}


def _strict_json_object(raw: bytes) -> dict:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    body = json.loads(raw, object_pairs_hook=unique_pairs)
    if not isinstance(body, dict):
        raise ValueError("JSON object required")
    return body


class CandidateService:
    def __init__(self, config: dict, state_dir: Path, *, control_port: int = 7442,
                 disconnect_grace_seconds: float = _DISCONNECT_GRACE_SECONDS):
        if not 0 < disconnect_grace_seconds <= _DISCONNECT_GRACE_SECONDS:
            raise CandidateError("Invalid diagnostic disconnect grace")
        self.config = config
        self.state_dir = Path(state_dir)
        live_config = config.get("live_pool_detector", config.get("live_detector"))
        self._event_budget = (EventBudget(
            self.state_dir, limit=live_config["max_events_per_hour"])
            if live_config is not None else None)
        # One Package event per camera per 30 minutes, across restarts.
        self._package_cooldown = (EventBudget(
            self.state_dir, limit=1, namespace="package-cooldown",
            window_seconds=int(_PACKAGE_COOLDOWN_SECONDS))
            if live_config is not None else None)
        self.control_port = control_port
        self.disconnect_grace_seconds = disconnect_grace_seconds
        self.runner: web.AppRunner | None = None
        self.task: asyncio.Task | None = None
        self.connected = False
        self.upgrades = 0
        self.last_result: str | None = None
        self.manage_requests = 0
        self.last_manage_shape: dict | None = None
        self.ws_binary_frames = 0
        self.ws_text_frames = 0
        self.ws_last_frame_bytes: int | None = None
        self.hello_sent = 0
        self.hello_replies = 0
        self.param_agreements = 0
        self.stream_lists_answered = 0
        self.stream_controls_started = 0
        self.stream_controls_stopped = 0
        self.stream_controls_rejected = 0
        self.stream_status_events_sent = 0
        self.stream_reconnects_preserved = 0
        self.stream_grace_closures = 0
        self.stream_resets_answered = 0
        self.stream_resets_rejected = 0
        self.provision_video_replies = 0
        self.provision_isp_replies = 0
        self.ssh_stop_replies = 0
        self.ssh_start_rejections = 0
        self.credential_rotations = 0
        self.credential_rotations_rejected = 0
        self.sound_led_replies = 0
        self.sound_led_rejections = 0
        self.timezone_replies = 0
        self.timezone_rejections = 0
        self.face_db_requests_rejected = 0
        self.smart_settings_requests_rejected = 0
        self.smart_settings_subset_matches = 0
        self.smart_settings_probe_requests = 0
        self._smart_settings_probe_shape: dict[str, int | bool] | None = None
        self.smart_package_events = 0
        self.smart_objects_joined = 0
        self._pool_sessions: dict[str, dict] = {}
        self.smart_motion_settings_acks = 0
        self.smart_motion_settings_rejected = 0
        self.smart_motion_events_started = 0
        self.smart_motion_events_stopped = 0
        self._pool_motion: dict[str, MotionDetector] = {}
        self.smart_motion_probe_requests = 0
        self.smart_motion_probe_acks = 0
        self.smart_motion_probe_zones = 0
        self.smart_feature_probe_events = 0
        self.smart_settings_probe_acks = 0
        self.smart_settings_repeats = 0
        self.smart_settings_lpr_acks = 0
        self.smart_settings_lpr_requested = 0
        self.smart_settings_rejection_reasons: dict[str, int] = {}
        self.smart_events_entered = 0
        self.smart_events_moved = 0
        self.smart_events_left = 0
        self.smart_events_closed_on_stop = 0
        self.synthetic_probe_claimed = 0
        self.synthetic_probe_errors = 0
        self.recorded_probe_claimed = 0
        self.recorded_probe_errors = 0
        self.recorded_probe_qualified = 0
        self.recorded_probe_attempts = 0
        self.recorded_probe_phase = "idle"
        self.recorded_probe_zone_status = "not_evaluated"
        self._recorded_probe_task: asyncio.Task | None = None
        self._smart_policy: SmartPolicy | None = None
        self._event_track: TrackChange | None = None
        self._event_last_moving_at: float | None = None
        self._event_zone_ids: tuple[int, ...] = ()
        self._event_snapshot: SmartSnapshot | None = None
        self._event_snapshot_expires: float | None = None
        self._pending_snapshot: tuple[SmartSnapshot, float] | None = None
        self._pending_full_fov: tuple[SmartSnapshot, float] | None = None
        self._pool_event_snapshots: dict[
            tuple[str, int], tuple[SmartSnapshot, float]] = {}
        self._pool_pending_snapshots: dict[str, _PendingPoolSnapshot] = {}
        self._pool_snapshot_number = 0
        self._snapshot_cleanup_task: asyncio.Task | None = None
        self._live_event_times: deque[float] = deque()
        self.snapshot_requests = 0
        self.snapshot_uploads = 0
        self.snapshot_rejections = 0
        self.snapshot_rejection_reasons: dict[str, int] = {}
        self.last_stream_error: str | None = None
        self.last_control_command: str | None = None
        self.observed_function_counts: dict[str, int] = {}
        self.unlisted_function_frames = 0
        self.unlisted_envelope_counts = {"request": 0, "response": 0, "other": 0}
        self.unlisted_function_fingerprints: dict[str, int] = {}
        self.unparsed_binary_frames = 0
        self.websocket_close_codes: dict[str, int] = {}
        self.last_disconnect_origin: str | None = None
        self._next_message_id = 2
        self._hello_agreed = False
        self._params_agreed = False
        self._ingress_close_task: asyncio.Task | None = None
        self.started = time.monotonic()
        self.detector_frames_attempted = 0
        self.detector_frames_succeeded = 0
        self.detector_objects_seen = 0
        self.detector_objects_enabled = 0
        self.detector_objects_score_eligible = 0
        self.detector_objects_zone_eligible = 0
        self.detector_tracks_entered = 0
        self.detector_tracks_left = 0
        self.detector_error: str | None = None
        self._detector: RFDetrNanoDetector | None = None
        self._tracker = (TemporalTracker() if "live_detector" in config
                         or "diagnostic_detector" in config
                         or "diagnostic_native_event_probe" in config
                         or "diagnostic_recorded_event_probe" in config else None)
        self._camera_engine: CameraPolicyEngine | None = None
        self._inference: FairInference | None = None
        self._pool_camera_order: tuple[str, ...] = ()
        self._pool_policy_errors: dict[str, str] = {}
        self._pool_secondary_lens_shapes: dict[str, dict[str, int | bool]] = {}
        self._pool_recognition_accuracy_shapes: dict[
            str, dict[str, int | bool | str]] = {}
        if "diagnostic_pool_detector" in config or "live_pool_detector" in config:
            live_pool = "live_pool_detector" in config
            detector = config["live_pool_detector" if live_pool
                              else "diagnostic_pool_detector"]
            cameras = [stream["camera_mac"] for stream in config[
                "paired_streams" if live_pool else "diagnostic_streams"]]
            self._pool_camera_order = tuple(normalize_mac(camera) for camera in cameras)
            self._camera_engine = CameraPolicyEngine(
                cameras,
                max_events_per_camera=(detector["max_events_per_hour"] if live_pool
                                       else len(self._pool_smart_types())),
                event_window_seconds=3600 if live_pool else None,
                event_budget=self._event_budget if live_pool else None,
                package_cooldown=self._package_cooldown if live_pool else None,
                max_track_gap_seconds=(20 if live_pool and
                                       detector.get("inference_backend") == "vision_api"
                                       else 3),
                # Paid API frames are seconds apart; a walking person rarely
                # keeps box overlap between the two confirming samples.
                max_center_distance=(1.5 if live_pool and
                                     detector.get("inference_backend") == "vision_api"
                                     else None))
            self._inference = FairInference(
                cameras,
                load_detector=(
                    (lambda: ApiObjectDetector(
                        detector["provider_config"], self.state_dir,
                        threshold=detector["threshold"],
                        max_requests_per_hour=detector.get("max_requests_per_hour")))
                    if live_pool and detector.get("inference_backend") == "vision_api" else
                    (lambda: OnnxRFDetrNanoDetector.from_model(
                        detector["model_path"], detector["model_sha256"],
                        backend=detector["inference_backend"],
                        threshold=detector["threshold"]))
                    if live_pool and "inference_backend" in detector else
                    (lambda: RFDetrNanoDetector.from_checkpoint(
                        detector["checkpoint_path"], detector["checkpoint_sha256"],
                        threshold=detector["threshold"]))),
                on_result=self._observe_pool_result,
                on_unavailable=self._pool_camera_unavailable,
                preserve_first_pending=(live_pool and
                                        detector.get("inference_backend") == "vision_api"),
                max_frames_per_camera=(None if live_pool else
                                       detector["max_frames_per_camera"]))
        self.credentials = CredentialStore(self.state_dir)
        self.virtual_sound_led = VirtualSoundLedStore(self.state_dir)
        self.virtual_timezone = VirtualTimezoneStore(self.state_dir)
        self.sessions: dict[str, float] = {}
        self._auth_failures: dict[str, list[float]] = {}
        self._auth_lock = asyncio.Lock()
        self.adoption = AdoptionStore(self.state_dir, config["controller_ip"],
                                     config["controller_pin"], control_port)
        self._current_ws: aiohttp.ClientWebSocketResponse | None = None
        self._send_lock = asyncio.Lock()
        self.ingress: AiPortIngress | AiPortIngressPool | None = None
        if "paired_stream" in config:
            self.ingress = AiPortIngress(
                **config["paired_stream"],
                frame_observer=(self._observe_frame
                                if "diagnostic_detector" in config
                                or "live_detector" in config else None))
        elif "paired_streams" in config:
            self.ingress = AiPortIngressPool(
                config["paired_streams"],
                frame_observer_factory=lambda camera: (
                    lambda frame: self._observe_pool_frame(camera, frame)))
        elif config.get("diagnostic_hello_until", 0) > time.time():
            if "diagnostic_stream" in config:
                self.ingress = AiPortIngress(
                    **config["diagnostic_stream"],
                    frame_observer=(self._observe_frame
                                    if "diagnostic_detector" in config else None))
            elif "diagnostic_streams" in config:
                self.ingress = AiPortIngressPool(
                    config["diagnostic_streams"],
                    frame_observer_factory=(
                        (lambda camera: lambda frame: self._observe_pool_frame(camera, frame))
                        if self._inference is not None else None))

    def _pool_event_enabled(self) -> bool:
        return ("live_pool_detector" in self.config
                or time.time() < self.config.get("diagnostic_pool_event_until", 0))

    async def _observe_pool_frame(self, camera_mac: str, frame: bytes) -> None:
        detector = self._pool_motion.get(camera_mac)
        if detector is not None and self._pool_event_enabled():
            try:
                edges = await asyncio.to_thread(
                    detector.observe, frame, now=time.monotonic())
            except MotionSettingsError:
                edges = ()
            await self._publish_motion_edges(camera_mac, edges)
        engine, inference = self._camera_engine, self._inference
        if (engine is None or inference is None
                or not self._pool_event_enabled()
                or not engine.has_policy(camera_mac)):
            return
        await inference.observe(
            camera_mac, frame, generation=engine.policy_generation(camera_mac))

    async def _publish_motion_edges(self, camera_mac: str, edges: tuple) -> None:
        ws = self._current_ws
        if (not edges or ws is None or not self._params_agreed
                or not isinstance(self.ingress, AiPortIngressPool)):
            return
        active = {stream["deviceID"] for stream in self.ingress.list_streams()}
        for edge in edges:
            if edge.edge == "start" and camera_mac not in active:
                continue
            await self._send_control_event(ws, "EventSmartMotion", motion_event_payload(
                camera_mac, edge, clock_wall_ms=int(time.time() * 1000)))
            if edge.edge == "start":
                self.smart_motion_events_started += 1
            else:
                self.smart_motion_events_stopped += 1

    async def _pool_camera_unavailable(self, camera_mac: str) -> None:
        # A failed or exhausted model cannot keep an event open. Revoke only
        # this camera's policy before announcing its unavailable status.
        await self._revoke_pool_policy(camera_mac)
        ws = self._current_ws
        if (ws is None or not self._params_agreed
                or not isinstance(self.ingress, AiPortIngressPool)
                or not self._pool_event_enabled()):
            return
        if any(stream["deviceID"] == camera_mac
               for stream in self.ingress.list_streams()):
            await self._send_stream_status(ws, streaming=True, camera_mac=camera_mac)

    async def _observe_pool_result(self, camera_mac: str,
                                   observations: tuple[ObjectObservation, ...],
                                   generation: int, frame: bytes | None = None) -> None:
        engine = self._camera_engine
        if (engine is None
                or not self._pool_event_enabled()
                or generation != engine.policy_generation(camera_mac)):
            return
        candidates = engine.observe(
            camera_mac, observations, now=time.monotonic(),
            infrared=(frame is not None and any(item.kind == "package" for item in observations)
                      and _frame_mode(frame) == "ir"))
        # A confirming paid sample only helps a new, unconfirmed object. When
        # every sampled object already belongs to an active track, keep the
        # bounded hourly allowance for later arrivals such as a passing cat.
        if (observations and self._inference is not None
                and not engine.needs_confirmation(camera_mac)):
            self._inference.skip_confirmation(camera_mac)
        await self._publish_pool_candidates(candidates, frame=frame)

    async def _publish_pool_candidates(
            self, candidates: tuple[CameraEventCandidate, ...],
            *, frame: bytes | None = None) -> None:
        self._prune_snapshots()
        ws = self._current_ws
        if (ws is None or not self._params_agreed
                or not isinstance(self.ingress, AiPortIngressPool)
                or not self._pool_event_enabled()):
            return
        active = {stream["deviceID"] for stream in self.ingress.list_streams()}
        for candidate in candidates:
            camera, change = candidate.camera_mac, candidate.change
            if change.edge in {"enter", "moving"} and camera not in active:
                continue
            # Protect keeps one ongoing smart event per camera: a second
            # enter is dropped and any leave closes the event. Report every
            # object of a camera inside one event instead.
            session = self._pool_sessions.setdefault(
                camera, {"active": {}, "seen": {}, "snapshots": []})
            track = (change, candidate.zone_ids)
            if change.edge == "enter":
                opening = not session["active"]
                if opening:
                    session["seen"], session["snapshots"] = {}, []
                session["active"][change.track_id] = track
                session["seen"][change.track_id] = track
                if frame is not None and len(session["snapshots"]) < 4:
                    try:
                        self._pool_snapshot_number += 1
                        session["snapshots"].append(await asyncio.to_thread(
                            make_smart_snapshot, frame, change, int(time.time() * 1000),
                            filename_track_id=self._pool_snapshot_number))
                    except SnapshotError:
                        pass
                edge = "enter" if opening else "moving"
                tracks = tuple(session["active"].values())
                if not opening:
                    self.smart_objects_joined += 1
            elif change.edge == "moving":
                if change.track_id not in session["active"]:
                    continue
                session["active"][change.track_id] = track
                session["seen"][change.track_id] = track
                edge, tracks = "moving", tuple(session["active"].values())
            else:
                if change.track_id not in session["seen"]:
                    continue
                session["active"].pop(change.track_id, None)
                session["seen"][change.track_id] = track
                if session["active"]:
                    edge, tracks = "moving", tuple(session["active"].values())
                else:
                    edge, tracks = "leave", tuple(session["seen"].values())
            try:
                payload = camera_event_payload(
                    camera, edge, tracks, clock_wall_ms=int(time.time() * 1000))
            except SmartEventError:
                continue
            if edge == "leave":
                snapshots = session["snapshots"]
                self._pool_sessions.pop(camera, None)
                if snapshots:
                    snapshots[0].add_to_event(payload)
                    payload["smartDetectSnapshots"] = [item.metadata for item in snapshots]
                    for item in snapshots:
                        self._remember_pool_snapshot(camera, item)
            await self._send_control_event(ws, "EventSmartDetect", payload)
            if change.kind == "package" and change.edge == "enter":
                self.smart_package_events += 1
            if edge == "enter":
                self.smart_events_entered += 1
            elif edge == "moving":
                self.smart_events_moved += 1
            else:
                self.smart_events_left += 1

    def _remember_pool_snapshot(self, camera: str, snapshot) -> None:
        now = time.monotonic()
        for filename, pending in tuple(self._pool_pending_snapshots.items()):
            if pending.expires <= now:
                del self._pool_pending_snapshots[filename]
        if len(self._pool_pending_snapshots) >= 16:
            self._pool_pending_snapshots.pop(next(iter(self._pool_pending_snapshots)))
        self._pool_pending_snapshots[snapshot.filename] = (
            _PendingPoolSnapshot(camera, snapshot, now + 75))

    async def _handle_pool_motion_settings(
            self, ws: aiohttp.ClientWebSocketResponse, request_id: int,
            payload: object) -> None:
        """Replace one allowlisted camera's motion zones and timings."""
        engine = self._camera_engine
        try:
            camera = normalize_mac(payload.get("deviceID")
                                   if isinstance(payload, dict) else None)
            engine.has_policy(camera)  # Enforces the private camera allowlist.
            policy = parse_motion_settings(payload, camera_mac=camera)
        except (IngressError, MotionSettingsError):
            await self._reply_control(ws, "ChangeSmartMotionSettings", request_id, 5,
                                      {"description": "invalid_motion_settings"})
            self.smart_motion_settings_rejected += 1
            return
        previous = self._pool_motion.get(camera)
        if previous is not None:
            await self._publish_motion_edges(camera, previous.stop(now=time.monotonic()))
        self._pool_motion[camera] = MotionDetector(policy)
        await self._reply_control(ws, "ChangeSmartMotionSettings", request_id, 0, {})
        self.smart_motion_settings_acks += 1

    def _count_policy_rejection(self, reason: str) -> None:
        """Fixed-code reasons only; never the controller's policy content."""
        self.smart_settings_requests_rejected += 1
        key = reason if len(reason) <= 64 else "other"
        if key in self.smart_settings_rejection_reasons or len(
                self.smart_settings_rejection_reasons) < 16:
            self.smart_settings_rejection_reasons[key] = (
                self.smart_settings_rejection_reasons.get(key, 0) + 1)

    async def _handle_pool_smart_settings(
            self, ws: aiohttp.ClientWebSocketResponse, request_id: int,
            payload: object) -> None:
        engine = self._camera_engine
        if engine is None or not isinstance(payload, dict):
            await self._reply_control(ws, "ChangeSmartDetectSettings", request_id, 501,
                                      {"description": "smart_detection_unavailable"})
            self._count_policy_rejection("engine_or_payload")
            return
        try:
            camera = normalize_mac(payload.get("deviceID"))
            engine.has_policy(camera)  # Enforces the private camera allowlist.
        except IngressError:
            await self._reply_control(ws, "ChangeSmartDetectSettings", request_id, 501,
                                      {"description": "smart_detection_unavailable"})
            self._count_policy_rejection("not_allowlisted")
            return
        if set(payload) == {"deviceID", "isLprCamera"}:
            # Protect 7.3.68 sends every paired camera this separate message
            # on connect. Answering 501 made Protect log "Failed to handle
            # EventAIPortStatus isSmartDetectReady". It carries no smart policy.
            # Live Protect 7.3.68 sends this flag as an integer (0 or 1).
            if not (type(payload["isLprCamera"]) is bool
                    or type(payload["isLprCamera"]) is int
                    and payload["isLprCamera"] in (0, 1)):
                self._count_policy_rejection(
                    "invalid_lpr_flag:" + type(payload["isLprCamera"]).__name__)
                await self._reply_control(ws, "ChangeSmartDetectSettings", request_id, 501,
                                          {"description": "smart_detection_unavailable"})
                return
            # Live health showed every startup rejection was this message
            # (an integer flag, refused as non-boolean before). The AI Port never advertises plate detection,
            # so acknowledging the flag promises no plate events; it is
            # counted so health shows plates were requested but not read.
            self.smart_settings_lpr_acks += 1
            if payload["isLprCamera"]:
                self.smart_settings_lpr_requested += 1
            await self._reply_control(ws, "ChangeSmartDetectSettings", request_id, 0, {})
            return
        try:
            repeat = parse_smart_settings(payload, camera_mac=camera)
        except SmartSettingsError:
            repeat = None
        if (repeat is not None and repeat == engine.current_policy(camera)
                and camera in {stream["deviceID"] for stream in self.ingress.list_streams()}
                and self._inference is not None
                and self._inference.is_available(camera)
                and self._pool_event_enabled()):
            # Protect re-sends identical settings several times after an AI
            # Port connects, while the startup pair samples the scene. That
            # pair is the only sample a stationary object (a package, a
            # sleeping cat) gets; revoking here discarded its confirmation.
            self.smart_settings_repeats += 1
            await self._reply_control(ws, "ChangeSmartDetectSettings", request_id, 0, {})
            self.smart_settings_probe_acks += 1
            return
        if self._inference is not None:
            self._inference.discard_pending(camera)
        await self._publish_pool_candidates(engine.replace_policy(camera, None))
        parsed = None
        rejection_reason = None
        try:
            parsed = parse_smart_settings(payload, camera_mac=camera)
        except SmartSettingsError as exc:
            rejection_reason = str(exc)
        else:
            self.smart_settings_subset_matches += 1
        if rejection_reason == "unsupported_smart_feature:regions:secondLensZones":
            self._pool_secondary_lens_shapes[camera] = summarize_secondary_lens_zones(payload)
        else:
            self._pool_secondary_lens_shapes.pop(camera, None)
        if rejection_reason == "invalid_smart_settings:recognition_accuracy":
            self._pool_recognition_accuracy_shapes[camera] = (
                summarize_recognition_accuracy(payload))
        else:
            self._pool_recognition_accuracy_shapes.pop(camera, None)
        active = {stream["deviceID"] for stream in self.ingress.list_streams()}
        if (parsed is not None and parsed.enabled_types
                and parsed.enabled_types <= set(self._pool_smart_types())
                and camera in active and self._inference is not None
                and self._inference.is_available(camera)
                and self._pool_event_enabled()):
            engine.replace_policy(camera, parsed)
            self._pool_policy_errors.pop(camera, None)
            await self._reply_control(ws, "ChangeSmartDetectSettings", request_id, 0, {})
            self.smart_settings_probe_acks += 1
            return
        if (rejection_reason or "").split(":", 1)[0] not in {
                "invalid_smart_settings", "wrong_camera", "unsupported_smart_feature",
                "invalid_smart_zone", "unsupported_smart_zone",
                "invalid_exclude_zone"}:
            rejection_reason = None
        if rejection_reason is None:
            if parsed is None:
                rejection_reason = "invalid_smart_settings:unclassified"
            elif not parsed.enabled_types:
                rejection_reason = "disabled_policy"
            elif not parsed.enabled_types <= set(self._pool_smart_types()):
                rejection_reason = "unsupported_type"
            elif camera not in active:
                rejection_reason = "inactive_stream"
            elif self._inference is None or not self._inference.is_available(camera):
                rejection_reason = "inference_unavailable"
            else:
                rejection_reason = "event_disabled"
        self._pool_policy_errors[camera] = rejection_reason
        await self._reply_control(ws, "ChangeSmartDetectSettings", request_id, 501,
                                  {"description": "smart_detection_unavailable"})
        self._count_policy_rejection(rejection_reason.split(":", 1)[0] + (
            ":" + rejection_reason.split(":")[1] if rejection_reason.count(":") else ""))

    def _select_pool_snapshot(self, filename: object) -> tuple[_PendingPoolSnapshot | None, bool]:
        if not isinstance(filename, str):
            return None, False
        now = time.monotonic()
        for key, pending in tuple(self._pool_pending_snapshots.items()):
            if pending.expires <= now:
                del self._pool_pending_snapshots[key]
            elif filename == pending.snapshot.filename and pending.crop_available:
                return pending, False
            elif filename == pending.snapshot.full_fov_filename and pending.full_available:
                return pending, True
        return None, False

    def _prune_snapshots(self) -> None:
        now = time.monotonic()
        if self._event_snapshot_expires is not None and self._event_snapshot_expires <= now:
            self._event_snapshot = None
            self._event_snapshot_expires = None
        if self._pending_snapshot is not None and self._pending_snapshot[1] <= now:
            self._pending_snapshot = None
        if self._pending_full_fov is not None and self._pending_full_fov[1] <= now:
            self._pending_full_fov = None
        for key, (_, expires) in tuple(self._pool_event_snapshots.items()):
            if expires <= now:
                del self._pool_event_snapshots[key]
        for key, pending in tuple(self._pool_pending_snapshots.items()):
            if pending.expires <= now:
                del self._pool_pending_snapshots[key]

    async def _snapshot_cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            self._prune_snapshots()

    def _pool_feature_types(self) -> list[str]:
        """Advertised pool capabilities.

        Protect offers Package as a primary-lens zone class only for a device
        that reports ``packageMaincam``. Without it, Package cannot be scoped
        by a detection zone. ``packageSecondcam`` is not reported: a doorbell
        keeps its own package lens, which this device never receives.
        """
        kinds = list(self._pool_smart_types())
        return kinds + ["packageMaincam"] if "package" in kinds else kinds

    def _pool_smart_types(self) -> tuple[str, ...]:
        if "live_pool_detector" in self.config:
            return tuple(self.config["live_pool_detector"]["smart_types"])
        return tuple(self.config.get("diagnostic_pool_smart_types", [
            self.config.get("diagnostic_smart_type", "person")]))

    async def _revoke_pool_policy(self, camera_mac: str) -> None:
        detector = self._pool_motion.get(camera_mac)
        if detector is not None:
            await self._publish_motion_edges(
                camera_mac, detector.stop(now=time.monotonic()))
        engine = self._camera_engine
        if engine is None:
            return
        if self._inference is not None:
            self._inference.discard_pending(camera_mac)
        await self._publish_pool_candidates(engine.replace_policy(camera_mac, None))
        self._pool_sessions.pop(camera_mac, None)
        for key in tuple(self._pool_event_snapshots):
            if key[0] == camera_mac:
                del self._pool_event_snapshots[key]

    async def _observe_frame(self, frame: bytes) -> None:
        live = "live_detector" in self.config
        policy = self.config["live_detector" if live else "diagnostic_detector"]
        until = (float("inf") if live else self.config.get(
            "diagnostic_hello_until", self.config.get("diagnostic_event_until", 0)))
        if (time.time() >= until or self.detector_error is not None
                or not live and self.detector_frames_attempted >= policy["max_frames"]):
            return
        smart_mode = live or "diagnostic_event_until" in self.config
        smart_policy = self._smart_policy
        if smart_mode and smart_policy is None:
            return
        self.detector_frames_attempted += 1
        try:
            if self._detector is None:
                self._detector = await asyncio.to_thread(
                    RFDetrNanoDetector.from_checkpoint, policy["checkpoint_path"],
                    policy["checkpoint_sha256"], threshold=policy["threshold"])
            observations = await asyncio.to_thread(self._detector.detect, frame)
            if smart_mode and self._smart_policy is not smart_policy:
                return
            assert self._tracker is not None
            track_observations = observations
            if smart_mode:
                assert smart_policy is not None
                kind = next(iter(smart_policy.enabled_types))
                # A reverification policy cannot be silently bypassed. Only
                # objects above its upper confidence bound reach the temporal
                # tracker; uncertain observations are dropped, not published.
                enabled = tuple(observation for observation in observations
                                if observation.kind == kind)
                scored = tuple(observation for observation in enabled
                               if smart_policy.allows_score(kind, observation.score))
                track_observations = tuple(
                    observation for observation in scored
                    if smart_policy.zone_ids(kind, observation.box) is not None)
            changes = self._tracker.update(track_observations,
                                           now=time.monotonic())
        except (DetectionError, TrackingError) as exc:
            self.detector_error = str(exc)
            if live:
                ws = self._current_ws
                if ws is not None:
                    await self._revoke_single_policy(ws)
                    if self.ingress is not None and self.ingress.list_streams():
                        await self._send_stream_status(ws, streaming=True)
                return
            raise
        if time.time() < until:
            self.detector_frames_succeeded += 1
            self.detector_objects_seen += len(observations)
            if smart_mode:
                self.detector_objects_enabled += len(enabled)
                self.detector_objects_score_eligible += len(scored)
                self.detector_objects_zone_eligible += len(track_observations)
            self.detector_tracks_entered += sum(change.edge == "enter" for change in changes)
            self.detector_tracks_left += sum(change.edge == "leave" for change in changes)
            if smart_mode and (live or time.time() < self.config.get("diagnostic_event_until", 0)):
                await self._publish_bounded_smart_changes(changes, frame=frame)

    async def _publish_bounded_smart_changes(
            self, changes: tuple[TrackChange, ...], *, frame: bytes | None = None) -> None:
        self._prune_snapshots()
        ws = self._current_ws
        policy = self._smart_policy
        live = "live_detector" in self.config
        if (ws is None or policy is None or len(policy.enabled_types) != 1
                or not live and time.time() >= self.config.get("diagnostic_event_until", 0)
                or not isinstance(self.ingress, AiPortIngress)
                or not self.ingress.list_streams()):
            return
        kind = next(iter(policy.enabled_types))
        for change in changes:
            if change.kind != kind:
                continue
            matched_zone_ids = policy.zone_ids(kind, change.box)
            if live and change.edge == "enter":
                cutoff = time.monotonic() - 3600
                while self._live_event_times and self._live_event_times[0] <= cutoff:
                    self._live_event_times.popleft()
            if (change.edge == "enter" and policy.allows_score(kind, change.score)
                    and matched_zone_ids is not None
                    and self._event_track is None
                    and (len(self._live_event_times) < self.config["live_detector"]["max_events_per_hour"]
                         if live else self.smart_events_entered == 0)):
                edge = "enter"
            elif (change.edge == "moving" and self._event_track is not None
                  and change.track_id == self._event_track.track_id
                  and matched_zone_ids == self._event_zone_ids
                  and self._event_last_moving_at is not None
                  and time.monotonic() - self._event_last_moving_at >= 1):
                edge = "moving"
            elif (change.edge == "leave" and self._event_track is not None
                  and change.track_id == self._event_track.track_id):
                edge = "leave"
            else:
                continue
            try:
                payload = smart_event_payload(
                    self.ingress.camera_mac, change, edge=edge,
                    clock_wall_ms=int(time.time() * 1000),
                    zone_ids=(matched_zone_ids if edge == "enter"
                              else self._event_zone_ids))
            except SmartEventError:
                continue
            if (edge == "enter" and live and self._event_budget is not None
                    and not self._event_budget.claim(self.ingress.camera_mac)):
                continue
            if edge == "enter" and frame is not None:
                try:
                    self._event_snapshot = await asyncio.to_thread(
                        make_smart_snapshot, frame, change, payload["clockWall"])
                    self._event_snapshot_expires = time.monotonic() + 180
                except SnapshotError:
                    self._event_snapshot = None
                    self._event_snapshot_expires = None
            if edge == "leave" and self._event_snapshot is not None:
                self._event_snapshot.add_to_event(payload)
                self._pending_snapshot = (self._event_snapshot, time.monotonic() + 75)
                self._pending_full_fov = (self._event_snapshot, time.monotonic() + 75)
            await self._send_control_event(ws, "EventSmartDetect", payload)
            if edge == "enter":
                if live:
                    self._live_event_times.append(time.monotonic())
                self._event_track = change
                self._event_zone_ids = matched_zone_ids
                self._event_last_moving_at = time.monotonic()
                self.smart_events_entered += 1
            elif edge == "moving":
                self._event_track = change
                self._event_last_moving_at = time.monotonic()
                self.smart_events_moved += 1
            else:
                self._event_track = None
                self._event_zone_ids = ()
                self._event_last_moving_at = None
                self._event_snapshot = None
                self._event_snapshot_expires = None
                self.smart_events_left += 1

    async def _revoke_single_policy(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Close a bounded event before a stream or policy is withdrawn."""
        self._prune_snapshots()
        previous = self._event_track
        zone_ids = self._event_zone_ids
        try:
            if (previous is not None and self._smart_policy is not None
                    and isinstance(self.ingress, AiPortIngress)
                    and self._current_ws is ws and self._params_agreed
                    and ("live_detector" in self.config
                         or time.time() < self.config.get("diagnostic_event_until", 0)
                         or "paired_stream" in self.config)):
                try:
                    payload = smart_event_payload(
                        self.ingress.camera_mac, previous, edge="leave",
                        clock_wall_ms=int(time.time() * 1000), zone_ids=zone_ids)
                except SmartEventError:
                    pass
                else:
                    if self._event_snapshot is not None:
                        self._event_snapshot.add_to_event(payload)
                        self._pending_snapshot = (self._event_snapshot, time.monotonic() + 75)
                        self._pending_full_fov = (self._event_snapshot, time.monotonic() + 75)
                    await self._send_control_event(ws, "EventSmartDetect", payload)
                    self.smart_events_left += 1
        finally:
            self._smart_policy = None
            self._event_track = None
            self._event_zone_ids = ()
            self._event_last_moving_at = None
            self._event_snapshot = None
            self._event_snapshot_expires = None
            if self._tracker is not None:
                self._tracker = TemporalTracker()

    def _claim_native_probe(self, nonce: str) -> bool:
        """Durably claim a diagnostic before writing a synthetic event."""
        try:
            info = self.state_dir.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                raise CandidateError("Native event probe state must be private")
            marker = self.state_dir / f".native-event-probe-{nonce}"
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                         0o600)
        except FileExistsError:
            return False
        except OSError as exc:
            raise CandidateError("Native event probe state unavailable") from exc
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(b"claimed\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise CandidateError("Native event probe state unavailable") from exc
        try:
            directory_fd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise CandidateError("Native event probe state unavailable") from exc
        return True

    async def _run_native_probe(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Send one explicitly configured test enter/leave for wire validation."""
        probe = self.config.get("diagnostic_native_event_probe")
        policy = self._smart_policy
        if (probe is None or policy is None or not isinstance(self.ingress, AiPortIngress)
                or not self._params_agreed or self._current_ws is not ws
                or self.ingress.camera_mac != probe["camera_mac"]
                or not self.ingress.list_streams()
                or time.time() + 4 >= self.config["diagnostic_event_until"]
                or policy.enabled_types != frozenset({"person"})
                or not policy.allows_score("person", 0.99)):
            return
        box = tuple(probe["box"])
        if policy.zone_ids("person", box) is None:
            return
        try:
            if not self._claim_native_probe(probe["nonce"]):
                return
            self.synthetic_probe_claimed += 1
            track = TrackChange("enter", 1, "person", "person", 0.99, box)
            await self._publish_bounded_smart_changes((track,))
            if self._event_track is None:
                return
            await asyncio.sleep(2)
            if (self._current_ws is ws and self._event_track is not None
                    and self._event_track.track_id == track.track_id):
                await self._publish_bounded_smart_changes((TrackChange(
                    "leave", track.track_id, track.kind, track.label,
                    track.score, track.box),))
        except (CandidateError, OSError, aiohttp.ClientError, RuntimeError):
            self.synthetic_probe_errors += 1

    async def _run_recorded_probe(self, ws: aiohttp.ClientWebSocketResponse,
                                  policy: SmartPolicy) -> None:
        """Send one model-confirmed historical person event with its capture time."""
        self.recorded_probe_attempts += 1
        self.recorded_probe_phase = "initial_gate"
        try:
            if (not isinstance(self.ingress, AiPortIngress)
                    or self._current_ws is not ws or not self._params_agreed
                    or not self.ingress.list_streams()
                    or time.time() + 8 >= self.config["diagnostic_event_until"]
                    or policy.enabled_types != frozenset({"person"})):
                return
            self.recorded_probe_phase = "validating"
            probe = parse_recorded_probe(
                self.config["diagnostic_recorded_event_probe"],
                state_dir=self.state_dir, camera_mac=self.ingress.camera_mac)
            if (self.recorded_probe_claimed
                    or os.path.lexists(self.state_dir / f".native-event-probe-{probe.nonce}")):
                self.recorded_probe_phase = "already_claimed"
                return
            self.recorded_probe_phase = "inference_running"
            track = await asyncio.to_thread(infer_recorded_person, probe)
            self.recorded_probe_phase = "inference_finished"
            frame_gap = (probe.frames[1].captured_ms
                         - probe.frames[0].captured_ms) / 1000
            if (self._current_ws is not ws or not self.ingress.list_streams()
                    or self._smart_policy is None):
                self.recorded_probe_phase = "control_or_stream_changed"
                return
            # Protect can resend the same camera policy while model inference
            # runs. Recheck its current values rather than object identity.
            policy = self._smart_policy
            if time.time() + frame_gap + 3 >= self.config["diagnostic_event_until"]:
                self.recorded_probe_phase = "expired_after_inference"
                return
            if (not policy.allows_score("person", track.enter.score)
                    or not policy.allows_score("person", track.moving.score)):
                self.recorded_probe_phase = "score_gate"
                return
            zone_ids = policy.zone_ids("person", track.enter.box)
            self.recorded_probe_zone_status = (
                "not_configured" if not policy.zones_configured else
                "no_match" if not zone_ids else "matched")
            # A recorded Person probe must match a validated Person zone before
            # it claims the one-use permit or publishes an event.
            if (not zone_ids
                    or zone_ids != policy.zone_ids("person", track.moving.box)):
                self.recorded_probe_phase = "zone_gate"
                return
            self.recorded_probe_phase = "qualified"
            self.recorded_probe_qualified += 1
            if not self._claim_native_probe(probe.nonce):
                self.recorded_probe_phase = "permit_unclaimable"
                return
            self.recorded_probe_claimed += 1
            enter = smart_event_payload(
                probe.camera_mac, track.enter, edge="enter",
                clock_wall_ms=probe.frames[0].captured_ms, zone_ids=zone_ids,
                first_shown_ms=probe.frames[0].captured_ms)
            moving = smart_event_payload(
                probe.camera_mac, track.moving, edge="moving",
                clock_wall_ms=probe.frames[1].captured_ms, zone_ids=zone_ids,
                first_shown_ms=probe.frames[0].captured_ms)
            leave = smart_event_payload(
                probe.camera_mac, track.moving, edge="leave",
                clock_wall_ms=probe.frames[1].captured_ms + 2000,
                zone_ids=zone_ids, first_shown_ms=probe.frames[0].captured_ms)
            try:
                snapshot = await asyncio.to_thread(
                    lambda: make_smart_snapshot(
                        read_recorded_frame(probe.frames[0]), track.enter,
                        probe.frames[0].captured_ms))
            except (RecordedProbeError, SnapshotError):
                snapshot = None
            if snapshot is not None:
                snapshot.add_to_event(leave)
            await self._send_control_event(ws, "EventSmartDetect", enter)
            self.recorded_probe_phase = "enter_sent"
            self.smart_events_entered += 1
            # Each descriptor comes from its own recorded model observation.
            # Deliver the track updates at the same spacing as the frames.
            await asyncio.sleep(frame_gap)
            current_policy = self._smart_policy
            if (self._current_ws is not ws
                    or current_policy is None
                    or current_policy.enabled_types != frozenset({"person"})
                    or current_policy.zone_ids("person", track.enter.box) != zone_ids
                    or current_policy.zone_ids("person", track.moving.box) != zone_ids
                    or not current_policy.allows_score("person", track.moving.score)
                    or not self.ingress.list_streams()
                    or time.time() + 2 >= self.config["diagnostic_event_until"]):
                self.recorded_probe_errors += 1
                self.recorded_probe_phase = "interrupted_after_enter"
                return
            await self._send_control_event(ws, "EventSmartDetect", moving)
            self.recorded_probe_phase = "moving_sent"
            self.smart_events_moved += 1
            await asyncio.sleep(2)
            current_policy = self._smart_policy
            if (self._current_ws is ws and current_policy is not None
                    and current_policy.enabled_types == frozenset({"person"})
                    and current_policy.zone_ids("person", track.moving.box) == zone_ids
                    and current_policy.allows_score("person", track.moving.score)
                    and self.ingress.list_streams()
                    and time.time() < self.config["diagnostic_event_until"]):
                if snapshot is not None:
                    self._pending_snapshot = (snapshot, time.monotonic() + 75)
                    self._pending_full_fov = (snapshot, time.monotonic() + 75)
                await self._send_control_event(ws, "EventSmartDetect", leave)
                self.recorded_probe_phase = "leave_sent"
                self.smart_events_left += 1
            else:
                self.recorded_probe_errors += 1
                self.recorded_probe_phase = "interrupted_after_moving"
        except (CandidateError, RecordedProbeError, DetectionError, TrackingError,
                SmartEventError, OSError, aiohttp.ClientError, RuntimeError):
            self.recorded_probe_errors += 1
            self.recorded_probe_phase = "error"
        except asyncio.CancelledError:
            self.recorded_probe_phase = "cancelled"
            raise

    def app(self) -> web.Application:
        app = web.Application(client_max_size=_MAX_MANAGE)
        app.router.add_get("/healthz", self._health)
        app.router.add_post("/api/1.2/login", self._login)
        app.router.add_post("/api/1.2/manage", self._manage)
        return app

    async def _health(self, request: web.Request) -> web.Response:
        live_budget_remaining = None
        live_budget_healthy = None
        if "live_detector" in self.config:
            live_budget_remaining = max(0,
                self.config["live_detector"]["max_events_per_hour"] - sum(
                    entered > time.monotonic() - 3600
                    for entered in self._live_event_times))
            try:
                live_budget_remaining = min(
                    live_budget_remaining,
                    self._event_budget.remaining(self.ingress.camera_mac))
                live_budget_healthy = True
            except EventBudgetError:
                live_budget_remaining = 0
                live_budget_healthy = False
        return web.json_response({"service": "aiport-candidate", "adopted": self.adoption.adopted,
            "adoption_pending": self.adoption.pending_token is not None,
            "control_connected": self.connected, "websocket_upgrades": self.upgrades,
            "last_result": self.last_result, "manage_requests": self.manage_requests,
            "last_manage_shape": self.last_manage_shape,
            "ws_binary_frames": self.ws_binary_frames,
            "ws_text_frames": self.ws_text_frames,
            "ws_last_frame_bytes": self.ws_last_frame_bytes,
            "hello_sent": self.hello_sent,
            "hello_replies": self.hello_replies,
            "param_agreements": self.param_agreements,
            "stream_lists_answered": self.stream_lists_answered,
            "stream_controls_started": self.stream_controls_started,
            "stream_controls_stopped": self.stream_controls_stopped,
            "stream_controls_rejected": self.stream_controls_rejected,
            "stream_status_events_sent": self.stream_status_events_sent,
            "stream_reconnects_preserved": self.stream_reconnects_preserved,
            "stream_grace_closures": self.stream_grace_closures,
            "stream_resets_answered": self.stream_resets_answered,
            "stream_resets_rejected": self.stream_resets_rejected,
            "provision_video_replies": self.provision_video_replies,
            "provision_isp_replies": self.provision_isp_replies,
            "ssh_stop_replies": self.ssh_stop_replies,
            "ssh_start_rejections": self.ssh_start_rejections,
            "credential_rotations": self.credential_rotations,
            "credential_rotations_rejected": self.credential_rotations_rejected,
            "sound_led_replies": self.sound_led_replies,
            "sound_led_rejections": self.sound_led_rejections,
            "timezone_replies": self.timezone_replies,
            "timezone_rejections": self.timezone_rejections,
            "face_db_requests_rejected": self.face_db_requests_rejected,
            "smart_settings_requests_rejected": self.smart_settings_requests_rejected,
            "smart_settings_subset_matches": self.smart_settings_subset_matches,
            "smart_settings_probe_requests": self.smart_settings_probe_requests,
            "smart_package_events": self.smart_package_events,
            "smart_objects_joined": self.smart_objects_joined,
            "smart_motion_settings_acks": self.smart_motion_settings_acks,
            "smart_motion_settings_rejected": self.smart_motion_settings_rejected,
            "smart_motion_events_started": self.smart_motion_events_started,
            "smart_motion_events_stopped": self.smart_motion_events_stopped,
            "smart_motion_probe_requests": self.smart_motion_probe_requests,
            "smart_motion_probe_acks": self.smart_motion_probe_acks,
            "smart_motion_probe_zones": self.smart_motion_probe_zones,
            "smart_feature_probe_events": self.smart_feature_probe_events,
            "smart_settings_probe_acks": self.smart_settings_probe_acks,
            "smart_settings_repeats": self.smart_settings_repeats,
            "smart_settings_lpr_acks": self.smart_settings_lpr_acks,
            "smart_settings_lpr_requested": self.smart_settings_lpr_requested,
            "smart_settings_rejection_reasons": dict(self.smart_settings_rejection_reasons),
            "package_cooldown_skips": (self._camera_engine.package_cooldown_skips
                                       if self._camera_engine is not None else 0),
            "package_ir_held": (self._camera_engine.package_ir_held
                                  if self._camera_engine is not None else 0),
            "package_ir_confirmed": (self._camera_engine.package_ir_confirmed
                                  if self._camera_engine is not None else 0),
            "package_ir_as_animal": (self._camera_engine.package_ir_as_animal
                                  if self._camera_engine is not None else 0),
            "package_ir_dropped": (self._camera_engine.package_ir_dropped
                                  if self._camera_engine is not None else 0),
            "smart_events_entered": self.smart_events_entered,
            "smart_events_moved": self.smart_events_moved,
            "smart_events_left": self.smart_events_left,
            "smart_events_closed_on_stop": self.smart_events_closed_on_stop,
            "snapshot_requests": self.snapshot_requests,
            "snapshot_uploads": self.snapshot_uploads,
            "snapshot_rejections": self.snapshot_rejections,
            "snapshot_rejection_reasons": dict(self.snapshot_rejection_reasons),
            "synthetic_probe_claimed": self.synthetic_probe_claimed,
            "synthetic_probe_errors": self.synthetic_probe_errors,
            "recorded_probe_qualified": self.recorded_probe_qualified,
            "recorded_probe_claimed": self.recorded_probe_claimed,
            "recorded_probe_errors": self.recorded_probe_errors,
            "recorded_probe_attempts": self.recorded_probe_attempts,
            "recorded_probe_phase": self.recorded_probe_phase,
            "recorded_probe_zone_status": self.recorded_probe_zone_status,
            "smart_settings_probe_shape": (self._smart_settings_probe_shape
                if time.time() < self.config.get("diagnostic_smart_probe_until", 0)
                else None),
            "stream_frames_decoded": self.ingress.frame_count if self.ingress else 0,
            "active_streams": len(self.ingress.list_streams()) if self.ingress else 0,
            "stream_restart_attempts": self.ingress.restart_attempts if self.ingress else 0,
            "stream_restart_successes": self.ingress.restart_successes if self.ingress else 0,
            "streams_with_decoded_frames": (
                self.ingress.streams_with_decoded_frames if self.ingress else 0),
            "stream_frames_decoded_total": (
                self.ingress.total_frames_decoded + self.ingress.frame_count
                if self.ingress else 0),
            "stream_frames_observed": self.ingress.frames_observed if self.ingress else 0,
            "stream_frames_skipped": self.ingress.frames_skipped if self.ingress else 0,
            "stream_observer_failed": self.ingress.observer_failed if self.ingress else False,
            "detector_frames_attempted": self.detector_frames_attempted,
            "detector_frames_succeeded": self.detector_frames_succeeded,
            "detector_objects_seen": self.detector_objects_seen,
            "detector_objects_enabled": self.detector_objects_enabled,
            "detector_objects_score_eligible": self.detector_objects_score_eligible,
            "detector_objects_zone_eligible": self.detector_objects_zone_eligible,
            "detector_tracks_entered": self.detector_tracks_entered,
            "detector_tracks_left": self.detector_tracks_left,
            "detector_error": self.detector_error,
            "detection_mode": ("live_pool" if "live_pool_detector" in self.config else
                               "live" if "live_detector" in self.config else
                               "diagnostic_pool" if "diagnostic_pool_detector" in self.config else
                               "diagnostic" if "diagnostic_detector" in self.config else
                               "passive"),
            "live_event_budget_remaining": live_budget_remaining,
            "live_event_budget_healthy": live_budget_healthy,
            "pool_inference": (self._inference.snapshot()
                               if self._inference is not None else None),
            "pool_cameras": ([dict(inference, **policy, **stream,
                                    policy_rejection=self._pool_policy_errors.get(
                                        self._pool_camera_order[index]),
                                    secondary_lens_shape=self._pool_secondary_lens_shapes.get(
                                        self._pool_camera_order[index]),
                                    recognition_accuracy_shape=(
                                        self._pool_recognition_accuracy_shapes.get(
                                            self._pool_camera_order[index])),
                                    motion=(self._pool_motion[
                                        self._pool_camera_order[index]].snapshot()
                                        if self._pool_camera_order[index]
                                        in self._pool_motion else None))
                              for index, (inference, policy, stream) in enumerate(zip(
                                  self._inference.camera_snapshot(),
                                  self._camera_engine.camera_snapshot(now=time.monotonic()),
                                  self.ingress.camera_diagnostics(self._pool_camera_order),
                                  strict=True))]
                             if self._inference is not None
                             and self._camera_engine is not None
                             and isinstance(self.ingress, AiPortIngressPool) else None),
            "last_stream_error": self.last_stream_error,
            "last_decoder_exit_code": (self.ingress.last_decoder_exit_code
                                       if self.ingress else None),
            "last_decoder_stderr_seen": (self.ingress.last_decoder_stderr_seen
                                         if self.ingress else False),
            "last_decoder_error_markers": (list(self.ingress.last_decoder_error_markers)
                                           if self.ingress else []),
            "last_decoder_error_terms": (list(self.ingress.last_decoder_error_terms)
                                         if self.ingress else []),
            "stream_ingest_enabled": (self.ingress is not None and (
                "paired_stream" in self.config or "paired_streams" in self.config
                or time.time() < self.config.get("diagnostic_hello_until", 0))),
            "last_control_command": self.last_control_command,
            "observed_function_counts": dict(self.observed_function_counts),
            "unlisted_function_frames": self.unlisted_function_frames,
            "unlisted_envelope_counts": dict(self.unlisted_envelope_counts),
            "unlisted_function_fingerprints": (
                dict(self.unlisted_function_fingerprints)
                if time.time() < self.config.get("diagnostic_function_fingerprints_until", 0)
                else {}),
            "unparsed_binary_frames": self.unparsed_binary_frames,
            "websocket_close_codes": dict(self.websocket_close_codes),
            "last_disconnect_origin": self.last_disconnect_origin,
            "uptime_seconds": int(time.monotonic() - self.started)})

    def _record_close(self, code: int | None, *, diagnostic_expired: bool) -> None:
        # WebSocket close reasons can contain private device data. Retain only
        # bounded numeric codes and whether our own diagnostic timer fired.
        key = str(code) if type(code) is int and 1000 <= code <= 4999 else "unknown"
        if key not in self.websocket_close_codes and len(self.websocket_close_codes) >= 16:
            key = "other"
        self.websocket_close_codes[key] = min(
            self.websocket_close_codes.get(key, 0) + 1, 1_000_000)
        self.last_disconnect_origin = (
            "diagnostic_expiry" if diagnostic_expired else "peer_or_transport")

    def _control_enabled(self) -> bool:
        return self.adoption.adopted or time.time() < self.config.get("diagnostic_hello_until", 0)

    def _stream_control_enabled(self) -> bool:
        return ("paired_stream" in self.config or "paired_streams" in self.config
                or time.time() < self.config.get("diagnostic_hello_until", 0))

    async def _manage(self, request: web.Request) -> web.Response:
        self.manage_requests += 1
        if not request.secure:
            return web.json_response({"error": "HTTPS required"}, status=400)
        if request.content_type != "application/json":
            return web.json_response({"error": "JSON required"}, status=415)
        try:
            raw = await request.read()
        except web.HTTPRequestEntityTooLarge:
            return web.json_response({"error": "Request too large"}, status=413)
        self.last_manage_shape = _object_shape(raw)
        if self.credentials.credential is None:
            return web.json_response({"error": "Adoption requires rotated credentials"}, status=503)
        try:
            body = _strict_json_object(raw)
        except (ValueError, UnicodeError, RecursionError):
            body = None
        if (not isinstance(body, dict) or not await self._verify_management(
                request, body.get("username"), body.get("password"))):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if self.adoption.adopted:
            return web.json_response({"error": "Already adopted"}, status=409)
        until = self.config.get("diagnostic_adoption_until", 0)
        if until <= time.time():
            return web.json_response({"error": "Adoption window closed"}, status=503)
        try:
            token = validate_management(body.get("mgmt"), self.config["controller_ip"],
                                        self.control_port, body.get("username"),
                                        body.get("password"))
            self.adoption.begin(token, until)
        except AdoptionError:
            return web.json_response({"error": "Invalid or unavailable adoption"}, status=400)
        if self._current_ws is not None:
            await self._current_ws.close()
        return web.json_response({})

    async def _login(self, request: web.Request) -> web.Response:
        if not request.secure:
            return web.json_response({"error": "HTTPS required"}, status=400)
        if request.content_type != "application/json":
            return web.json_response({"error": "JSON required"}, status=415)
        try:
            body = await request.json()
        except (ValueError, UnicodeError, web.HTTPRequestEntityTooLarge):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if (not isinstance(body, dict) or not await self._verify_management(
                request, body.get("username"), body.get("password"))):
            return web.json_response({"error": "Unauthorized"}, status=401)
        token = secrets.token_urlsafe(32)
        self.sessions = {key: expiry for key, expiry in self.sessions.items()
                         if expiry > time.monotonic()}
        if len(self.sessions) >= 256:
            self.sessions.pop(next(iter(self.sessions)))
        self.sessions[token] = time.monotonic() + 600
        response = web.json_response({})
        response.set_cookie("AISESSION", token, secure=True, httponly=True,
                            samesite="Strict", max_age=600)
        return response

    async def _verify_management(self, request: web.Request,
                                 username: object, password: object) -> bool:
        peer = request.remote or "unknown"
        async with self._auth_lock:
            now = time.monotonic()
            if len(self._auth_failures) >= 256:
                self._auth_failures = {
                    source: [at for at in attempts if now - at < 60]
                    for source, attempts in self._auth_failures.items()
                    if any(now - at < 60 for at in attempts)
                }
            failures = [at for at in self._auth_failures.get(peer, []) if now - at < 60]
            if len(failures) >= 10 or (peer not in self._auth_failures
                                       and len(self._auth_failures) >= 256):
                return False
            valid = await asyncio.to_thread(self.credentials.verify, username, password)
            if valid:
                self._auth_failures.pop(peer, None)
            else:
                failures.append(now)
                self._auth_failures[peer] = failures
            return valid

    def _client_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(cafile=str(self.state_dir / "controller-ca.pem"))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.load_cert_chain(str(self.state_dir / "device.crt"), str(self.state_dir / "device.key"))
        return context

    def _server_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(self.state_dir / "device.crt"), str(self.state_dir / "device.key"))
        return context

    async def _send_diagnostic_hello(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._next_message_id = 2
        self._hello_agreed = False
        self._params_agreed = False
        message = {"from": "ubnt_avclient", "to": "UniFiVideo",
                   "responseExpected": True, "functionName": "ubnt_avclient_hello",
                   "messageId": 1, "inResponseTo": 0,
                   "payload": {"fwVersion": self.config["firmware_version"],
                               "protocolVersion": 1,
                               "uptime": int(time.monotonic() - self.started),
                               "ip": self.config["device_ip"],
                               "connectionSecurePort": 443,
                               "features": {}}}
        await ws.send_bytes(json.dumps(message, separators=(",", ":")).encode())
        self.hello_sent += 1

    async def _reply_control(self, ws: aiohttp.ClientWebSocketResponse, function: str,
                             request_id: int, status: int, payload: dict) -> None:
        async with self._send_lock:
            response = {"from": "ubnt_avclient", "to": "UniFiVideo",
                        "responseExpected": False, "functionName": function,
                        "messageId": self._next_message_id, "inResponseTo": request_id,
                        "statusCode": status, "payload": payload}
            await ws.send_bytes(json.dumps(response, separators=(",", ":")).encode())
            self._next_message_id += 1

    async def _send_control_event(self, ws: aiohttp.ClientWebSocketResponse,
                                  function: str, payload: dict) -> None:
        async with self._send_lock:
            event = {"from": "ubnt_avclient", "to": "UniFiVideo",
                     "responseExpected": False, "functionName": function,
                     "messageId": self._next_message_id, "inResponseTo": 0,
                     "payload": payload}
            if function in {"EventSmartDetect", "EventSmartMotion"}:
                # Protect reads this envelope field when routing a smart
                # detection; keep it aligned with the payload's clockWall.
                event["timeStamp"] = datetime.fromtimestamp(
                    payload["clockWall"] / 1000, timezone.utc
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            await ws.send_bytes(json.dumps(event, separators=(",", ":")).encode())
            self._next_message_id += 1

    async def _send_stream_status(self, ws: aiohttp.ClientWebSocketResponse,
                                  *, streaming: bool,
                                  camera_mac: str | None = None) -> None:
        assert self.ingress is not None
        if camera_mac is None:
            camera_mac = getattr(self.ingress, "camera_mac", None)
            if camera_mac is None:
                raise CandidateError("Camera identity required for multi-camera status")
        # Smart readiness requires an explicitly configured detector. Passive
        # pairing remains available without claiming an AI capability.
        smart_ready = (streaming and (
            ("live_detector" in self.config and self.detector_error is None)
            or "live_pool_detector" in self.config
            or time.time() < (
                self.config.get("diagnostic_pool_event_until", 0)
                if isinstance(self.ingress, AiPortIngressPool)
                else self.config.get("diagnostic_smart_probe_until", 0))))
        if smart_ready and isinstance(self.ingress, AiPortIngressPool):
            smart_ready = (self._inference is not None
                           and self._inference.is_available(camera_mac))
        if smart_ready:
            flags: dict[str, object] = {
                "deviceID": camera_mac,
                "smartDetect": (self._pool_feature_types()
                                if isinstance(self.ingress, AiPortIngressPool)
                                else [self.config["live_detector"]["smart_type"]]
                                if "live_detector" in self.config else
                                [self.config.get("diagnostic_smart_type", "person")])}
            if (isinstance(self.ingress, AiPortIngressPool)
                    and "live_pool_detector" in self.config):
                # Protect drops a paired camera's own motion events. Reporting
                # enhanced motion lets Protect send the camera's motion zones
                # so this device can send zone-scoped motion instead.
                flags["motionDetect"] = ["enhanced"]
            await self._send_control_event(ws, "EventFeatureFlagsUpdated", flags)
            self.smart_feature_probe_events += 1
        await self._send_control_event(
            ws, "EventAIPortStatus",
            {"deviceID": camera_mac, "isStreaming": streaming,
             "isSmartDetectReady": smart_ready, "isAudioEventReady": False})
        self.stream_status_events_sent += 1

    async def _handle_diagnostic_frame(self, ws: aiohttp.ClientWebSocketResponse,
                                       raw: bytes) -> None:
        try:
            message = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            self.unparsed_binary_frames = min(self.unparsed_binary_frames + 1, 1_000_000)
            return
        if not isinstance(message, dict):
            self.unparsed_binary_frames = min(self.unparsed_binary_frames + 1, 1_000_000)
            return
        function = message.get("functionName")
        if isinstance(function, str) and function in _OBSERVABLE_FUNCTIONS:
            self.observed_function_counts[function] = min(
                self.observed_function_counts.get(function, 0) + 1, 1_000_000)
        elif isinstance(function, str):
            self.unlisted_function_frames = min(self.unlisted_function_frames + 1, 1_000_000)
            in_response_to = message.get("inResponseTo")
            kind = ("response" if type(in_response_to) is int and in_response_to > 0
                    else "request" if message.get("responseExpected") is True
                    else "other")
            self.unlisted_envelope_counts[kind] = min(
                self.unlisted_envelope_counts[kind] + 1, 1_000_000)
            if (time.time() < self.config.get("diagnostic_function_fingerprints_until", 0)
                    and len(function) <= 96):
                fingerprint = hashlib.sha256(
                    function.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]
                if (fingerprint in self.unlisted_function_fingerprints
                        or len(self.unlisted_function_fingerprints) < 8):
                    self.unlisted_function_fingerprints[fingerprint] = min(
                        self.unlisted_function_fingerprints.get(fingerprint, 0) + 1,
                        1_000_000)
        else:
            self.unparsed_binary_frames = min(self.unparsed_binary_frames + 1, 1_000_000)
        if function == "ubnt_avclient_hello" and message.get("inResponseTo") == 1:
            self.hello_replies += 1
            self._hello_agreed = True
            return
        if function == "ubnt_avclient_paramAgreement" and self._hello_agreed:
            request_id = message.get("messageId")
            if type(request_id) is not int or request_id < 0:
                return
            await self._reply_control(ws, function, request_id, 0, {})
            self.param_agreements += 1
            self._params_agreed = True
            if self.ingress is not None:
                streams = self.ingress.list_streams()
                had_disconnect_grace = self._ingress_close_task is not None
                if had_disconnect_grace:
                    self._cancel_ingress_close()
                if streams:
                    self.stream_reconnects_preserved += 1
                    for stream in streams:
                        await self._send_stream_status(
                            ws, streaming=True,
                            camera_mac=stream["deviceID"])
                elif had_disconnect_grace:
                    await self.ingress.close()
            return
        if function == "ResetAIPortStreams":
            self.last_control_command = function
            request_id = message.get("messageId")
            if not self._params_agreed or type(request_id) is not int or request_id < 0:
                return
            if message.get("payload") != {}:
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "invalid_reset_command"})
                self.stream_resets_rejected += 1
                return
            if self.ingress is not None:
                streams = self.ingress.list_streams()
                if isinstance(self.ingress, AiPortIngress):
                    await self._revoke_single_policy(ws)
                await self.ingress.close()
                if isinstance(self.ingress, AiPortIngressPool):
                    for stream in streams:
                        await self._revoke_pool_policy(stream["deviceID"])
                if self._stream_control_enabled():
                    for stream in streams:
                        await self._send_stream_status(
                            ws, streaming=False,
                            camera_mac=stream["deviceID"])
            await self._reply_control(ws, function, request_id, 0, {})
            self.stream_resets_answered += 1
            return
        if function in {"ChangeVideoSettings", "ChangeIspSettings"}:
            self.last_control_command = function
            request_id = message.get("messageId")
            if not self._params_agreed or type(request_id) is not int or request_id < 0:
                return
            if (not self._control_enabled()
                    or message.get("payload") != {}):
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "unsupported_settings_change"})
                return
            if function == "ChangeVideoSettings":
                payload = {"video": {"videoMode": "default"}, "audio": {"volume": 0}}
                self.provision_video_replies += 1
            else:
                payload = {"irLedLevel": 255}
                self.provision_isp_replies += 1
            await self._reply_control(ws, function, request_id, 0, payload)
            return
        if function in {"StartService", "StopService"}:
            self.last_control_command = function
            request_id = message.get("messageId")
            if not self._params_agreed or type(request_id) is not int or request_id < 0:
                return
            if (not self._control_enabled()
                    or message.get("payload") != {"service": "ssh"}):
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "unsupported_service_command"})
            elif function == "StopService":
                # This image has no SSH server, so it is already stopped.
                await self._reply_control(ws, function, request_id, 0, {})
                self.ssh_stop_replies += 1
            else:
                await self._reply_control(ws, function, request_id, 501,
                                          {"description": "ssh_unavailable"})
                self.ssh_start_rejections += 1
            return
        if function == "UpdateUsernamePassword":
            self.last_control_command = function
            request_id = message.get("messageId")
            if not self._params_agreed or type(request_id) is not int or request_id < 0:
                return
            if not self._control_enabled():
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "diagnostic_expired"})
                self.credential_rotations_rejected += 1
                return
            try:
                await asyncio.to_thread(self.credentials.rotate, message.get("payload"))
            except CredentialError:
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "credential_rotation_rejected"})
                self.credential_rotations_rejected += 1
                return
            self.sessions.clear()
            await self._reply_control(ws, function, request_id, 0, {})
            self.credential_rotations += 1
            return
        if function == "ChangeSoundLedSettings":
            self.last_control_command = function
            request_id = message.get("messageId")
            if not self._params_agreed or type(request_id) is not int or request_id < 0:
                return
            if (not self._control_enabled()
                    or self.credentials.credential is None):
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "diagnostic_unavailable"})
                self.sound_led_rejections += 1
                return
            try:
                await asyncio.to_thread(self.virtual_sound_led.apply, message.get("payload"))
            except VirtualHardwareError:
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "settings_rejected"})
                self.sound_led_rejections += 1
                return
            await self._reply_control(ws, function, request_id, 0, {})
            self.sound_led_replies += 1
            return
        if function == "ChangeDeviceSettings":
            self.last_control_command = function
            request_id = message.get("messageId")
            if not self._params_agreed or type(request_id) is not int or request_id < 0:
                return
            if (not self._control_enabled()
                    or self.credentials.credential is None):
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "diagnostic_unavailable"})
                self.timezone_rejections += 1
                return
            try:
                await asyncio.to_thread(self.virtual_timezone.apply, message.get("payload"))
            except VirtualHardwareError:
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "settings_rejected"})
                self.timezone_rejections += 1
                return
            await self._reply_control(ws, function, request_id, 0, {})
            self.timezone_replies += 1
            return
        if function == "UpdateFaceDBRequest":
            self.last_control_command = function
            request_id = message.get("messageId")
            if not self._params_agreed or type(request_id) is not int or request_id < 0:
                return
            # The payload can contain a private database URL. Do not fetch,
            # persist, log, or reflect it while face recognition is unsupported.
            await self._reply_control(ws, function, request_id, 501,
                                      {"description": "face_database_unavailable"})
            self.face_db_requests_rejected += 1
            return
        if function == "ChangeSmartMotionSettings":
            self.last_control_command = function
            request_id = message.get("messageId")
            if not self._params_agreed or type(request_id) is not int or request_id < 0:
                return
            self.smart_motion_probe_requests += 1
            if (isinstance(self.ingress, AiPortIngressPool)
                    and self._camera_engine is not None
                    and "live_pool_detector" in self.config):
                await self._handle_pool_motion_settings(
                    ws, request_id, message.get("payload"))
                return
            if (not isinstance(self.ingress, AiPortIngress)
                    or time.time() >= self.config.get(
                    "diagnostic_smart_probe_until", 0)):
                await self._reply_control(ws, function, request_id, 501,
                                          {"description": "smart_motion_unavailable"})
                return
            try:
                zone_count = parse_motion_probe(message.get("payload"),
                                                camera_mac=self.ingress.camera_mac)
            except SmartSettingsError:
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "invalid_motion_probe"})
                return
            # A probe-only acknowledgement lets Protect reveal its next
            # command. It does not enable or claim enhanced motion detection.
            await self._reply_control(ws, function, request_id, 0, {})
            self.smart_motion_probe_acks += 1
            self.smart_motion_probe_zones = zone_count
            return
        if function == "ChangeSmartDetectSettings":
            self.last_control_command = function
            request_id = message.get("messageId")
            if not self._params_agreed or type(request_id) is not int or request_id < 0:
                return
            if isinstance(self.ingress, AiPortIngressPool):
                await self._handle_pool_smart_settings(
                    ws, request_id, message.get("payload"))
                return
            # A disabled or unsupported policy also closes any prior event.
            await self._revoke_single_policy(ws)
            # Accept only this camera's validated single-class object policy.
            parsed_policy = None
            if (isinstance(self.ingress, AiPortIngress)
                    and ("live_detector" in self.config
                         or time.time() < self.config.get("diagnostic_hello_until", 0)
                         or time.time() < self.config.get("diagnostic_smart_probe_until", 0))):
                if time.time() < self.config.get("diagnostic_smart_probe_until", 0):
                    self._smart_settings_probe_shape = summarize_smart_request(
                        message.get("payload"), camera_mac=self.ingress.camera_mac)
                    self.smart_settings_probe_requests += 1
                try:
                    parsed_policy = parse_smart_settings(
                        message.get("payload"), camera_mac=self.ingress.camera_mac)
                except SmartSettingsError:
                    pass
                else:
                    self.smart_settings_subset_matches += 1
            if (parsed_policy is not None
                    and parsed_policy.enabled_types == frozenset({
                        self.config["live_detector"]["smart_type"]
                        if "live_detector" in self.config else
                        self.config.get("diagnostic_smart_type", "person")})
                    and ("live_detector" in self.config and self.detector_error is None
                         or time.time() < self.config.get("diagnostic_event_until", 0))
                    and self._tracker is not None):
                self._smart_policy = parsed_policy
                await self._reply_control(ws, function, request_id, 0, {})
                self.smart_settings_probe_acks += 1
                if "diagnostic_native_event_probe" in self.config:
                    await self._run_native_probe(ws)
                if ("diagnostic_recorded_event_probe" in self.config
                        and (self._recorded_probe_task is None
                             or self._recorded_probe_task.done())):
                    self._recorded_probe_task = asyncio.create_task(
                        self._run_recorded_probe(ws, parsed_policy))
                return
            # Never echo or retain the controller's nested camera policy.
            await self._reply_control(ws, function, request_id, 501,
                                      {"description": "smart_detection_unavailable"})
            self.smart_settings_requests_rejected += 1
            return
        if function in {"GetStreamList", "UiStreamControl", "OnvifStreamControl"}:
            self.last_control_command = function
            request_id = message.get("messageId")
            if not self._params_agreed or type(request_id) is not int or request_id < 0:
                return
            if function == "GetStreamList":
                await self._reply_control(ws, function, request_id, 0,
                                          {"list": self.ingress.list_streams() if self.ingress else []})
                self.stream_lists_answered += 1
            elif function == "UiStreamControl" and self.ingress is not None:
                try:
                    if not self._stream_control_enabled():
                        raise IngressError("diagnostic_expired")
                    result = await self.ingress.control(message.get("payload"))
                    if not self._stream_control_enabled():
                        await self.ingress.close()
                        raise IngressError("diagnostic_expired")
                except IngressError as exc:
                    await self._reply_control(ws, function, request_id, 5,
                                              {"description": exc.code})
                    self.stream_controls_rejected += 1
                    self.last_stream_error = exc.code
                else:
                    if (result["status"] == "stopped"
                            and isinstance(self.ingress, AiPortIngress)):
                        await self._revoke_single_policy(ws)
                    await self._reply_control(ws, function, request_id, 0, result)
                    self.last_stream_error = None
                    if result["status"] == "started":
                        self.stream_controls_started += 1
                    else:
                        self.stream_controls_stopped += 1
                    if self._stream_control_enabled():
                        payload = message.get("payload")
                        camera_mac = (normalize_mac(payload["deviceID"])
                                      if isinstance(payload, dict) and "deviceID" in payload
                                      else getattr(self.ingress, "camera_mac", None))
                        if (result["status"] == "stopped"
                                and isinstance(self.ingress, AiPortIngressPool)):
                            await self._revoke_pool_policy(camera_mac)
                        await self._send_stream_status(
                            ws, streaming=result["status"] == "started",
                            camera_mac=camera_mac)
            else:
                await self._reply_control(ws, function, request_id, 501,
                                          {"description": "stream_ingest_unavailable"})
                self.stream_controls_rejected += 1
            return
        if function == "GetRequest":
            self.last_control_command = function
            request_id = message.get("messageId")
            if not self._params_agreed or type(request_id) is not int or request_id < 0:
                return
            if not self.adoption.adopted:
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "snapshot_unavailable"})
                self.snapshot_rejections += 1
                self.snapshot_rejection_reasons["unadopted"] = (
                    self.snapshot_rejection_reasons.get("unadopted", 0) + 1)
                return
            request_payload = message.get("payload")
            requested_filename = (request_payload.get("filename")
                                  if isinstance(request_payload, dict) else None)
            pool_pending = None
            if isinstance(self.ingress, AiPortIngressPool):
                pool_pending, full_fov = self._select_pool_snapshot(requested_filename)
                pending = ((pool_pending.snapshot, pool_pending.expires)
                           if pool_pending is not None else None)
            else:
                full_fov = (self._pending_full_fov is not None
                            and requested_filename == self._pending_full_fov[0].full_fov_filename)
                pending = self._pending_full_fov if full_fov else self._pending_snapshot
            if pending is None or time.monotonic() > pending[1]:
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "snapshot_unavailable"})
                self.snapshot_rejections += 1
                self.snapshot_rejection_reasons["no_pending_snapshot"] = (
                    self.snapshot_rejection_reasons.get("no_pending_snapshot", 0) + 1)
                if pending is not None and pool_pending is None:
                    if full_fov:
                        self._pending_full_fov = None
                    else:
                        self._pending_snapshot = None
                return
            snapshot = pending[0]
            filename = snapshot.full_fov_filename if full_fov else snapshot.filename
            what = ("smartDetectZoneSnapshotFullFoV" if full_fov
                    else "smartDetectZoneSnapshot")
            try:
                if (pool_pending is not None and isinstance(request_payload, dict)
                        and "deviceID" in request_payload):
                    try:
                        camera = normalize_mac(request_payload["deviceID"])
                    except IngressError as exc:
                        raise SnapshotError("unexpected_snapshot_camera") from exc
                    if camera != pool_pending.camera_mac:
                        raise SnapshotError("unexpected_snapshot_camera")
                url = validated_upload_url(
                    message.get("payload"), controller_ip=self.config["controller_ip"],
                    filename=filename, what=what)
            except SnapshotError as exc:
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "snapshot_request_invalid"})
                self.snapshot_rejections += 1
                reason = str(exc)
                self.snapshot_rejection_reasons[reason] = (
                    self.snapshot_rejection_reasons.get(reason, 0) + 1)
                return
            if pool_pending is not None:
                if full_fov:
                    pool_pending.full_available = False
                else:
                    pool_pending.crop_available = False
                if not pool_pending.crop_available and not pool_pending.full_available:
                    self._pool_pending_snapshots.pop(pool_pending.snapshot.filename, None)
            elif full_fov:
                self._pending_full_fov = None
            else:
                self._pending_snapshot = None
            self.snapshot_requests += 1
            try:
                connector = VerifiedConnector(
                    ssl_context=self._client_context(),
                    expected_fingerprint=self.config["controller_pin"])
                timeout = aiohttp.ClientTimeout(total=10, connect=5, sock_connect=5)
                async with aiohttp.ClientSession(
                        connector=connector, timeout=timeout, trust_env=False) as session:
                    form = aiohttp.FormData()
                    form.add_field("payload", (snapshot.full_fov_jpeg if full_fov
                                               else snapshot.jpeg),
                                   filename=filename, content_type="image/jpeg")
                    async with session.post(url, data=form, allow_redirects=False) as response:
                        if response.status != 200:
                            raise aiohttp.ClientError("snapshot_upload_rejected")
                        await response.content.read(1024)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ssl.SSLError):
                await self._reply_control(ws, function, request_id, 5,
                                          {"description": "snapshot_upload_failed"})
                self.snapshot_rejections += 1
                self.snapshot_rejection_reasons["upload_failed"] = (
                    self.snapshot_rejection_reasons.get("upload_failed", 0) + 1)
            else:
                await self._reply_control(ws, function, request_id, 0, {})
                self.snapshot_uploads += 1
            return

    @staticmethod
    async def _expire_diagnostic(ws: aiohttp.ClientWebSocketResponse, until: int) -> None:
        await asyncio.sleep(max(0, until - time.time()))
        await ws.close()

    async def _expire_paired_event_probe(
            self, ws: aiohttp.ClientWebSocketResponse, until: int) -> None:
        """Stop AI events at expiry while keeping the paired stream connected."""
        await asyncio.sleep(max(0, until - time.time()))
        if self._current_ws is not ws or not self._params_agreed:
            return
        await self._revoke_single_policy(ws)
        if isinstance(self.ingress, AiPortIngress) and self.ingress.list_streams():
            await self._send_stream_status(
                ws, streaming=True, camera_mac=self.ingress.camera_mac)

    def _cancel_ingress_close(self) -> None:
        if self._ingress_close_task is not None:
            self._ingress_close_task.cancel()
            self._ingress_close_task = None

    async def _schedule_ingress_close(self) -> None:
        if self.ingress is None:
            return
        self._cancel_ingress_close()
        until = self.config.get("diagnostic_hello_until", 0)
        persistent = ("paired_stream" in self.config
                      or "paired_streams" in self.config)
        delay = (self.disconnect_grace_seconds if persistent
                 else min(self.disconnect_grace_seconds, max(0, until - time.time())))
        if delay <= 0 or not self.ingress.list_streams():
            await self.ingress.close()
            if time.time() >= until and "live_pool_detector" not in self.config:
                self._detector = None
                if self._inference is not None:
                    await self._inference.close()
            return

        async def close_after_grace() -> None:
            await asyncio.sleep(delay)
            await self.ingress.close()
            if time.time() >= until and "live_pool_detector" not in self.config:
                self._detector = None
                if self._inference is not None:
                    await self._inference.close()
            self.stream_grace_closures += 1

        self._ingress_close_task = asyncio.create_task(close_after_grace())

    async def start(self, *, bind: str = "0.0.0.0", port: int = 8443):
        if self.runner is not None:
            return
        self.runner = web.AppRunner(self.app(), access_log=None)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, bind, port, ssl_context=self._server_context()).start()
            self._snapshot_cleanup_task = asyncio.create_task(
                self._snapshot_cleanup_loop(), name="aiport-snapshot-cleanup")
            self.task = asyncio.create_task(self._connect_loop(), name="aiport-candidate-control")
        except BaseException:
            await self.stop()
            raise

    async def _close_open_events_on_stop(self) -> None:
        """Send a leave for each open native event before the link closes.

        Without it Protect keeps the event open until its own timeout (about
        six minutes after a redeploy, Esszimmer 26 Sep 03:46). The leave has
        no snapshots: a stopping AI Port cannot serve their upload.
        """
        ws = self._current_ws
        if ws is None or not self._params_agreed:
            return
        for camera in tuple(self._pool_sessions):
            session = self._pool_sessions.pop(camera)
            if not session["active"]:
                continue
            try:
                payload = camera_event_payload(
                    camera, "leave", tuple(session["seen"].values()),
                    clock_wall_ms=int(time.time() * 1000))
            except SmartEventError:
                continue
            await self._send_control_event(ws, "EventSmartDetect", payload)
            self.smart_events_left += 1
            self.smart_events_closed_on_stop += 1

    async def stop(self):
        try:
            await asyncio.wait_for(self._close_open_events_on_stop(),
                                   _STOP_CLOSE_SECONDS)
        except (asyncio.TimeoutError, aiohttp.ClientError, ConnectionError, RuntimeError):
            pass  # best effort; Protect's own timeout still closes the event
        if self._snapshot_cleanup_task is not None:
            self._snapshot_cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._snapshot_cleanup_task
            self._snapshot_cleanup_task = None
        if self._recorded_probe_task is not None:
            self._recorded_probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recorded_probe_task
            self._recorded_probe_task = None
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        ingress_close_task = self._ingress_close_task
        self._cancel_ingress_close()
        if ingress_close_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await ingress_close_task
        if self.ingress is not None:
            await self.ingress.close()
        if self._inference is not None:
            await self._inference.close()
        self._detector = None
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None

    async def _connect_loop(self):
        connector = VerifiedConnector(ssl_context=self._client_context(),
                                      expected_fingerprint=self.config["controller_pin"])
        trace = aiohttp.TraceConfig()

        async def reject_redirect(session, trace_context, params):
            raise aiohttp.ClientError("Control WebSocket redirect refused")

        trace.on_request_redirect.append(reject_redirect)
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout,
                                         trust_env=False, trace_configs=[trace]) as session:
            while True:
                pending_token = self.adoption.pending_token
                adopted = self.adoption.adopted
                resume_existing = (not adopted and pending_token is None
                                   and time.time() < self.config.get("diagnostic_resume_until", 0))
                headers = {"Camera-MAC": self.config["mac"], "Camera-IP": self.config["device_ip"],
                           "Camera-Model": "0xa5f1", "Camera-Firmware": self.config["firmware_version"],
                           "Adopted": "true" if adopted or pending_token or resume_existing else "false"}
                url = (f"wss://{self.config['controller_ip']}:{self.control_port}/camera/1.0/ws")
                if pending_token:
                    url += f"?token={quote(pending_token, safe='')}"
                try:
                    async with session.ws_connect(
                        url,
                        protocols=["secure_transfer"], headers=headers,
                        heartbeat=30, max_msg_size=64 * 1024,
                    ) as ws:
                        if ws.protocol not in (None, "secure_transfer"):
                            self.last_result = "websocket_protocol_mismatch"
                            await ws.close()
                        else:
                            if pending_token:
                                self.adoption.confirm()
                            elif resume_existing:
                                if time.time() >= self.config["diagnostic_resume_until"]:
                                    await ws.close()
                                    continue
                                self.adoption.resume_existing()
                            self.upgrades += 1
                            self.connected = True
                            self._current_ws = ws
                            self.last_result = "websocket_101"
                            until = self.config.get("diagnostic_hello_until", 0)
                            diagnostic = until > time.time()
                            active_control = diagnostic or self.adoption.adopted
                            expiry_task = None
                            event_expiry_task = None
                            try:
                                if active_control:
                                    await self._send_diagnostic_hello(ws)
                                if diagnostic:
                                    expiry_task = asyncio.create_task(
                                        self._expire_diagnostic(ws, until))
                                if "paired_stream" in self.config:
                                    event_until = self.config.get("diagnostic_event_until", 0)
                                    if event_until > time.time():
                                        event_expiry_task = asyncio.create_task(
                                            self._expire_paired_event_probe(ws, event_until))
                                async for message in ws:
                                    if message.type == aiohttp.WSMsgType.BINARY:
                                        self.ws_binary_frames += 1
                                        self.ws_last_frame_bytes = len(message.data)
                                        if active_control:
                                            await self._handle_diagnostic_frame(ws, message.data)
                                    elif message.type == aiohttp.WSMsgType.TEXT:
                                        self.ws_text_frames += 1
                                        self.ws_last_frame_bytes = len(message.data.encode("utf-8"))
                                    if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                                                        aiohttp.WSMsgType.ERROR):
                                        break
                            finally:
                                self._current_ws = None
                                if isinstance(self.ingress, AiPortIngress):
                                    await self._revoke_single_policy(ws)
                                elif isinstance(self.ingress, AiPortIngressPool):
                                    for stream in self.ingress.list_streams():
                                        await self._revoke_pool_policy(stream["deviceID"])
                                diagnostic_expired = (
                                    expiry_task is not None and expiry_task.done()
                                    and not expiry_task.cancelled())
                                if expiry_task is not None:
                                    expiry_task.cancel()
                                    with contextlib.suppress(asyncio.CancelledError):
                                        await expiry_task
                                if event_expiry_task is not None:
                                    event_expiry_task.cancel()
                                    with contextlib.suppress(asyncio.CancelledError):
                                        await event_expiry_task
                                if self.ingress is not None:
                                    await self._schedule_ingress_close()
                                self._record_close(ws.close_code,
                                                   diagnostic_expired=diagnostic_expired)
                            self.last_result = "websocket_closed"
                except (aiohttp.ClientError, TimeoutError, ssl.SSLError) as exc:
                    self.last_result = type(exc).__name__
                finally:
                    self.connected = False
                await asyncio.sleep(5)


async def _serve(path: Path, bind: str, port: int):
    config = load_config(path)
    service = CandidateService(config, path.parent)
    await service.start(bind=bind, port=port)
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, done.set)
    try:
        await done.wait()
    finally:
        await service.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an unadopted AI Port candidate with no camera access")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args(argv)
    if args.bind not in ("0.0.0.0", "127.0.0.1") or not 0 <= args.port <= 65535:
        parser.error("Invalid candidate bind address or port")
    try:
        asyncio.run(_serve(args.config, args.bind, args.port))
    except (CandidateError, OSError, ssl.SSLError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
