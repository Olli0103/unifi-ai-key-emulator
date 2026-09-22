"""Independent, bounded AI Key management and UCP4 control implementation.

No vendor firmware is imported or executed. Network startup is explicit. The
caller supplies identity, controller trust, and an async job admission handler.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import contextlib
from copy import deepcopy
import hashlib
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import ssl
import tempfile
import time
from typing import Awaitable, Callable
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from .protocol import ContractError, decode_message, encode_message
from .config import validate_factory_enrollment_deadline


_OBJECT_CAPABILITIES = (
    "supportFaceEnhancement", "supportRetroactiveProcessing", "supportAiSummary",
    "supportRecognizeAnything", "supportFaceRecognition", "supportLicensePlateRecognition",
)
_QUEUE_FIELDS = (
    "UI_AUDIO_RAM", "UI_AUTO_FACE_ENHANCE", "UI_AUTO_RAM", "UI_AUTO_STT",
    "UI_MANUAL_FACE_ENHANCE", "UI_TASK_NUM",
)
_MAX_STATE_BYTES = 64 * 1024
_MAX_MESSAGE_BYTES = 2 * 1024 * 1024 + 16
_FACTORY_CONFIRMATION_TIMEOUT = 5
_RESULT_CODES = (0, 5, 13, 22, 95, 110, 409)
_COUNTER_LIMIT = 2 ** 31 - 1
_JSON_SHAPES = ("missing", "null", "string", "boolean", "number", "array", "object", "other")
_RAM_TYPES = ("video", "videoWithRecognition", "image", "multipleImages")
# Exact local validation messages only. Never expose an arbitrary exception string.
_WORKER_REJECTION_REASONS = {
    "Invalid or oversized RequestAI command": "command_size_or_shape",
    "Job must contain finite JSON": "invalid_json",
    "recognizeKeyFrames requires its explicit single-use test scope": "scope_kind",
    "Unsupported recognizeKeyFrames payload fields": "payload_fields",
    "recognizeKeyFrames is limited to captioned, muted target-camera video": "video_contract",
    "recognizeKeyFrames video must span at most 10 seconds": "video_interval",
    "recognizeKeyFrames video exceeds configured duration bound": "video_interval",
    "recognizeKeyFrames requires bounded distinct timestamps inside the video": "key_moments",
    "recognizeKeyFrames requires at most 128 integer timestamps inside the video": "key_moments",
    "recognizeKeyFrames requires the observed RAM callback": "callback_path",
    "recognizeKeyFrames requires the AI processor video export route": "media_path",
    "recognizeKeyFrames export query is malformed": "export_query",
    "recognizeKeyFrames export must exactly match the command camera and interval": "export_query_match",
    "Invalid callback URL": "callback_url",
    "Invalid media URL": "media_url",
    "Invalid HTTP origin or URL": "http_origin",
    "callback URL is outside configured controller origins": "callback_origin",
    "media URL is outside configured controller origins": "media_origin",
    "Invalid callback path": "callback_path",
    "Invalid media path": "media_path",
    "Unsupported callback path": "callback_path",
    "Unsupported controller media path": "media_path",
    "MP4 adaptation is restricted to the verified AI processor export route": "mp4_path",
    "MP4 adaptation cannot rewrite signed or unknown query fields": "mp4_query",
    "MP4 adaptation requires a bounded start/end interval": "mp4_interval",
    "MP4 adaptation requires a literal format=ubv component": "mp4_format",
    "Task identity reused with different input": "job_identity_conflict",
    "Callback outcome is uncertain; review journal before retrying": "callback_uncertain",
    "Test scope permit is already consumed; no further media or inference is allowed": "permit_consumed",
    "Test scope permit is already consumed": "permit_consumed",
    "Invalid test scope reservation; inspect it without resetting the permit": "permit_state",
    "Cannot persist test scope reservation; no work was admitted": "permit_persistence",
    "Worker queue is full": "queue_full",
    "Worker journal is full; archive reviewed entries": "journal_full",
    "Worker has stopped": "worker_stopped",
}


def _increment(counter, key):
    counter[key] = min(counter[key] + 1, _COUNTER_LIMIT)


def _result_counts():
    return dict.fromkeys([str(code) for code in _RESULT_CODES] + ["other"], 0)


def _field_shape(body, field):
    if field not in body:
        return "missing"
    value = body[field]
    if value is None:
        return "null"
    return {str: "string", bool: "boolean", int: "number", float: "number",
            list: "array", dict: "object"}.get(type(value), "other")


class CommandFailure(Exception):
    """A bounded local command error represented in the UCP response envelope."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class AbnormalControlClosure(ConnectionError):
    """The control transport ended without a WebSocket close handshake."""


class VerifiedConnector(aiohttp.TCPConnector):
    """Check an optional leaf pin before aiohttp can write HTTP headers.

    The SSLContext still performs CA verification. The hook is intentionally
    covered by a transport test because it is an aiohttp internal extension.
    """

    def __init__(self, *, ssl_context: ssl.SSLContext, expected_fingerprint: str | None = None):
        if ssl_context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("Controller TLS must require certificate verification")
        self._pin = _parse_pin(expected_fingerprint)
        if not ssl_context.check_hostname and self._pin is None:
            raise ValueError("Controller TLS needs hostname verification or an explicit SHA256 pin")
        super().__init__(ssl=ssl_context, limit=4)

    async def _wrap_create_connection(self, *args, **kwargs):
        transport, protocol = await super()._wrap_create_connection(*args, **kwargs)
        try:
            verify_peer_pin(transport.get_extra_info("ssl_object"), self._pin)
        except Exception:
            transport.close()
            raise
        return transport, protocol


def _parse_pin(value: str | None) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Controller pin must be a SHA256 hexadecimal fingerprint")
    value = value.replace(":", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Controller pin must be a SHA256 hexadecimal fingerprint")
    return bytes.fromhex(value)


def verify_peer_pin(ssl_object, expected_pin: bytes | None) -> None:
    """The caller invokes this after TLS verification and before HTTP writes."""
    if ssl_object is None:
        raise ssl.SSLError("A TLS connection is required")
    cert = ssl_object.getpeercert(binary_form=True)
    if not cert:
        raise ssl.SSLError("Controller did not present a certificate")
    if expected_pin is not None and not hmac.compare_digest(hashlib.sha256(cert).digest(), expected_pin):
        raise ssl.SSLError("Controller certificate fingerprint mismatch")


def _finite_json(value):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ContractError("Expected finite JSON") from exc


def _object_json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError("object required")
        return _finite_json(value)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ContractError("Invalid JSON object") from exc


def _text(value, name: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ContractError(f"Invalid {name}")
    return value


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class DeviceService:
    """Management routes and a reconnecting control client, without an HTTP runner.

    ``job_handler`` admits a complete RequestAI body, or an explicit
    ``{command: recognizeKeyFrames, payload: body}`` wrapper, after reservation.
    It owns inference and result uploads. Returning an object means
    admission succeeded; raising means admission failed. No shell dispatch is used.
    """

    def __init__(self, config: dict, state_dir: Path,
                 job_handler: Callable[[dict], Awaitable[dict]],
                 tls_context: ssl.SSLContext | None = None, logger=None,
                 queue_status: Callable[[], dict] | None = None,
                 credential_handler: Callable[[str, str], Awaitable[None]] | None = None):
        self.config = deepcopy(config)
        self.device = self.config.get("device", {})
        self.controller = self.config.get("controller", {})
        self.runtime = self.config.get("runtime", {})
        self.state_dir = Path(state_dir)
        self.state_path = self.state_dir / "device-state.json"
        self.job_handler = job_handler
        self.queue_status = queue_status
        self.credential_handler = credential_handler
        self.tls_context = tls_context
        self.log = logger or logging.getLogger(__name__)
        self.mac = _text(self.device.get("mac"), "device MAC").replace(":", "").replace("-", "").upper()
        if not re.fullmatch(r"[0-9A-F]{12}", self.mac):
            raise ValueError("A stable 12-digit MAC identity is required")
        if int(self.mac[:2], 16) & 1:
            raise ValueError("Device identity must use a unicast MAC")
        self.mode = self.runtime.get("mode", "lab")
        if self.mode not in {"lab", "device"}:
            raise ValueError("Runtime mode must be lab or device")
        self.host = _text(self.controller.get("host", "127.0.0.1"), "controller host")
        if any(c in self.host for c in "/@?#\\") or self.host.strip() != self.host:
            raise ValueError("Controller host must be a hostname or IP address")
        self.port = self.controller.get("control_port", 7442)
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Invalid control port")
        bind = self.runtime.get("bind", "127.0.0.1")
        if self.mode == "lab" and (not _loopback(self.host) or not _loopback(bind)):
            raise ValueError("Lab mode is restricted to loopback addresses")
        self.username = _text(self.device.get("management_username"), "management username")
        self._initial_password = _text(self.device.get("management_password"), "management password", 1024)
        self._factory_until = validate_factory_enrollment_deadline(self.device.get("factory_enrollment_until", 0))
        self._factory_monotonic_until = time.monotonic() + max(0, self._factory_until - time.time())
        if self._factory_until > time.time() and _parse_pin(self.controller.get("expected_fingerprint")) is None:
            raise ValueError("Active factory enrollment requires an explicit controller SHA-256 pin")
        self._state = self._load_state()
        self._confirmed_control_connection = None
        self._confirmation_waiting_connection = None
        self._confirmation_event = asyncio.Event()
        self._started_at = time.monotonic()
        self._task: asyncio.Task | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._pending: dict[str, tuple[bytes, asyncio.Future]] = {}
        self._completed: OrderedDict[str, tuple[bytes, bytes]] = OrderedDict()
        self._connections = 0
        self._last_error: str | None = None
        self._last_close_code: int | None = None
        self._clock_offset_ms: float | None = None
        self._time_sync_id: str | None = None
        self._last_t0: int | None = None
        self._adoption_generation = 0
        self._connection_generation: int | None = None
        self._connection_token: str | None = None
        self._active_admissions = 0
        self._control_diagnostics = {name: {"count": 0, "last_result_code": None,
                                           "result_code_counts": _result_counts()} for name in (
            "getInfo", "getTaskQueueInfo", "setConsoleInfo", "setInfo", "updateTimezone",
            "changeUserPassword", "RequestAI", "recognizeKeyFrames", "changeAiInferAgentSettings",
            "changeDescribePrompts", "networkStatus", "sshService", "unknown")}
        self._recognize_diagnostics = {
            "camera_shape_counts": dict.fromkeys(_JSON_SHAPES, 0),
            "cameraId_shape_counts": dict.fromkeys(_JSON_SHAPES, 0),
            "camera_match_counts": dict.fromkeys(("matches", "different", "not_comparable"), 0),
            "cameraId_match_counts": dict.fromkeys(("matches", "different", "not_comparable"), 0),
            "ram_type_counts": dict.fromkeys((*_RAM_TYPES, "missing", "invalid_type", "other_string"), 0),
            "metadata_presence_counts": dict.fromkeys(("personMeta", "faceMeta", "vehicleMeta"), 0),
            "video_interval_counts": dict.fromkeys(("missing", "invalid_type", "invalid_order_or_range",
                                                    "up_to_10_seconds", "over_10_seconds"), 0),
            "duration_limit_counts": dict.fromkeys(("within", "exceeds", "not_comparable"), 0),
            "key_moments_counts": dict.fromkeys(("missing", "invalid_type", "empty", "at_or_below_sampling_limit",
                "above_sampling_limit", "over_128_inputs", "duplicates", "non_integer", "outside_interval",
                "interval_not_comparable"), 0),
            "matching_camera_result_code_counts": _result_counts(),
            "matching_cameraId_result_code_counts": _result_counts(),
            "phase_counts": dict.fromkeys(("scope_disabled", "camera_mismatch", "worker_admission",
                "admitted", "worker_rejected", "admission_timeout", "admission_cancelled",
                "admission_exception", "invalid_admission_result"), 0),
            "worker_rejection_counts": dict.fromkeys(
                sorted(set(_WORKER_REJECTION_REASONS.values()) | {"unclassified_worker_error"}), 0),
        }
        # Process-local counts only; never retain request bodies or credentials.
        self._management_diagnostics = {
            "info_post_requests": 0, "info_credential_rejections": 0,
            "adopt_requests": 0, "adopt_credential_rejections": 0,
            "adopt_invalid_payloads": 0, "adopt_accepted": 0,
            "last_adoption_result": None,
        }

    @property
    def control_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"wss://{host}:{self.port}/"

    @property
    def status(self) -> dict:
        scope = self.config.get("worker", {}).get("test_scope")
        basic_enabled = isinstance(scope, dict) and scope.get("kind") == "recognizeKeyFrames"
        return {"adopted": bool(self._state.get("adopted")), "connected": self._ws is not None and not self._ws.closed,
                "connections": self._connections, "last_error": self._last_error,
                "last_close_code": self._last_close_code,
                "management": dict(self._management_diagnostics),
                "control_commands": deepcopy(self._control_diagnostics),
                "recognize_key_frames": deepcopy(self._recognize_diagnostics),
                "clock_offset_ms": self._clock_offset_ms, "discovery": "unsupported",
                "supported_commands": ["getInfo", "getTaskQueueInfo", "setConsoleInfo", "setInfo", "updateTimezone", "changeUserPassword", "RequestAI"]
                    + (["recognizeKeyFrames"] if basic_enabled else [])}

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {"schema": 1, "mac": self.mac, "adopted": False}
        if self.state_path.is_symlink() or not self.state_path.is_file():
            raise ValueError("State must be a regular file")
        if self.state_path.stat().st_size > _MAX_STATE_BYTES:
            raise ValueError("Device state is too large")
        state = _object_json(self.state_path.read_bytes())
        if state.get("schema") != 1 or state.get("mac") != self.mac or type(state.get("adopted")) is not bool:
            raise ValueError("Invalid or different device identity in state")
        return state

    def _save_state(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.state_dir.is_symlink() or self.state_path.is_symlink():
            raise ValueError("Refusing symlinked state")
        raw = json.dumps(self._state, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > _MAX_STATE_BYTES:
            raise ValueError("Device state is too large")
        fd, name = tempfile.mkstemp(prefix=".device-state-", dir=self.state_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.state_path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name)

    def _password_matches(self, username: str, password: str) -> bool:
        if not isinstance(username, str) or not isinstance(password, str):
            return False
        if len(password) > 1024:
            return False
        credential = self._state.get("credential")
        if credential:
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(credential["salt"]), 200_000).hex()
            return hmac.compare_digest(username.encode(), credential["username"].encode()) and hmac.compare_digest(actual, credential["hash"])
        return hmac.compare_digest(username.encode(), self.username.encode()) and hmac.compare_digest(password.encode(), self._initial_password.encode())

    def _factory_window_active(self) -> bool:
        return (self.username == "ui" and time.time() < self._factory_until
                and time.monotonic() < self._factory_monotonic_until)

    async def _http_credentials_match(self, username, password) -> bool:
        """Called under the state lock; HTTP factory credentials end at adoption."""
        if await asyncio.to_thread(self._password_matches, username, password):
            return True
        if (not self._state.get("adopted") and not self._state.get("credential")
                and self._factory_window_active() and username == "ui" and password == "ui"):
            previous = self._state.get("factory_enrollment_used")
            self._state["factory_enrollment_used"] = True
            try:
                self._save_state()
            except Exception:
                if previous is None:
                    self._state.pop("factory_enrollment_used", None)
                else:
                    self._state["factory_enrollment_used"] = previous
                raise
            return True
        return False

    def _factory_rotation_allowed(self, username, password, connection) -> bool:
        return (username == "ui" and password == "ui" and self._factory_window_active()
                and self._state.get("factory_enrollment_used") is True
                and self._state.get("adopted") is True and not self._state.get("credential")
                and connection is not None and connection is self._ws and not connection.closed
                and connection is self._confirmed_control_connection)

    async def _await_factory_confirmation(self, body: dict, connection) -> None:
        """Allow startup ordering without holding the lock needed by timeSync."""
        if (body.get("username") != "ui" or body.get("passwordOld") != "ui"
                or not self._factory_window_active()
                or self._state.get("factory_enrollment_used") is not True
                or self._state.get("credential") or connection is None
                or connection is not self._ws or connection.closed
                or connection is self._confirmed_control_connection):
            return
        if not self._state.get("adopted") and not (
                self._connection_token and self._connection_generation == self._adoption_generation
                and self._state.get("management", {}).get("token") == self._connection_token):
            return
        if self._confirmation_waiting_connection is not connection:
            self._confirmation_waiting_connection = connection
            self._confirmation_event = asyncio.Event()
        timeout = min(_FACTORY_CONFIRMATION_TIMEOUT, self._factory_until - time.time(),
                      self._factory_monotonic_until - time.monotonic())
        if timeout > 0:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._confirmation_event.wait(), timeout=timeout)

    def get_info(self) -> dict:
        flags = {name: {"enabled": False, "version": "v1"} for name in _OBJECT_CAPABILITIES}
        flags.update({"supportDeepMode": False, "supportVlm": False, "aiMode": "basic"})
        overrides = self.device.get("feature_flags", {})
        if not isinstance(overrides, dict):
            raise ContractError("feature_flags must be an object")
        flags.update(_finite_json(overrides))
        # The summary flag remains an explicit opt-in and requires the local
        # caption path. It says nothing about deep mode, tags or search.
        summary = flags.get("supportAiSummary")
        if isinstance(summary, dict) and summary.get("enabled") is True:
            worker = self.config.get("worker", {})
            inference = self.config.get("inference", {})
            scope = worker.get("test_scope")
            scope_supported = scope is None or (
                isinstance(scope, dict) and scope.get("kind", "on_demand") in
                ("on_demand", "recognizeKeyFrames"))
            decoder = worker.get("ffmpeg_path")
            configured = (isinstance(inference.get("model"), str) and bool(inference["model"])
                          and worker.get("callback_mode", "enabled") == "enabled"
                          and isinstance(decoder, str) and Path(decoder).is_absolute()
                          and Path(decoder).is_file() and scope_supported)
            if not configured:
                flags["supportAiSummary"] = {**summary, "enabled": False}
        return {"type": self.device.get("model", "UP-AI-KEY"), "sysid": self.device.get("sysid", "0xa5f0"),
                "version": self.device.get("firmware_version", "2.2.8"), "mac": self.mac,
                "uptime": int(time.monotonic() - self._started_at), "poeType": self.device.get("poe_type", "unknown"),
                "storageSize": self.device.get("storage_size", 0), "featureFlags": flags}

    def create_app(self) -> web.Application:
        app = web.Application(client_max_size=_MAX_STATE_BYTES)
        app.router.add_get("/api/info", self._http_info)
        app.router.add_post("/api/info", self._http_info)
        app.router.add_post("/api/adopt", self._http_adopt)
        return app

    async def _http_info(self, request: web.Request) -> web.Response:
        if request.method == "POST":
            self._management_diagnostics["info_post_requests"] += 1
            if self.mode == "device" and not request.secure:
                return web.json_response({"error": "Credentialed information requests require HTTPS"}, status=400)
            if request.content_type != "application/json":
                return web.json_response({"error": "JSON content type required"}, status=415)
            try:
                body = _object_json(await request.read())
            except ContractError:
                return web.json_response({"error": "Invalid JSON"}, status=422)
            async with self._lock:
                if not await self._http_credentials_match(body.get("username"), body.get("password")):
                    self._management_diagnostics["info_credential_rejections"] += 1
                    return web.json_response({"error": "Invalid credentials"}, status=401)
        return web.json_response(self.get_info())

    def _validate_adoption(self, body: dict) -> dict:
        if body.get("protocol") != "wss" or body.get("mode", 0) not in (0, "0"):
            raise ContractError("Only WSS mode 0 adoption is supported")
        token = _text(body.get("token"), "adoption token", 512)
        hosts = body.get("hosts")
        if not isinstance(hosts, list) or not hosts or len(hosts) > 64:
            raise ContractError("Adoption requires a hosts list")
        matched = False
        for entry in hosts:
            _text(entry, "controller host entry", 512)
            try:
                parsed = urlsplit("wss://" + entry)
                if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                    raise ValueError()
                if parsed.hostname and parsed.hostname.lower() == self.host.lower() and (parsed.port or 7442) == self.port:
                    matched = True
            except ValueError as exc:
                raise ContractError("Invalid controller host entry") from exc
        if not matched:
            raise ContractError("Adoption hosts must include the configured controller")
        if self._state.get("adopted"):
            raise CommandFailure(409, "Device is already adopted; local reset is required")
        result = {"hosts": [self.control_url.removeprefix("wss://").removesuffix("/")],
                  "protocol": "wss", "mode": 0, "token": token}
        for name in ("nvr", "controller", "consoleId", "consoleName"):
            if name in body:
                result[name] = _text(body[name], name)
        return result

    def _adoption_result(self, result: str) -> None:
        self._management_diagnostics["last_adoption_result"] = result
        counter = {"invalid_credentials": "adopt_credential_rejections",
                   "invalid_payload": "adopt_invalid_payloads", "accepted": "adopt_accepted"}.get(result)
        if counter is not None:
            self._management_diagnostics[counter] += 1

    async def _http_adopt(self, request: web.Request) -> web.Response:
        self._management_diagnostics["adopt_requests"] += 1
        self._adoption_result("received")
        if self.mode == "device" and not request.secure:
            self._adoption_result("https_required")
            return web.json_response({"error": "Adoption requires HTTPS"}, status=400)
        if request.content_type != "application/json":
            self._adoption_result("invalid_payload")
            return web.json_response({"error": "JSON content type required"}, status=415)
        try:
            body = _object_json(await request.read())
        except web.HTTPRequestEntityTooLarge:
            self._adoption_result("invalid_payload")
            return web.json_response({"error": "Request exceeds size limit"}, status=413)
        except ContractError:
            self._adoption_result("invalid_payload")
            return web.json_response({"error": "Invalid JSON"}, status=422)
        async with self._lock:
            if not await self._http_credentials_match(body.get("username"), body.get("password")):
                self._adoption_result("invalid_credentials")
                return web.json_response({"error": "Invalid credentials"}, status=401)
            try:
                management = self._validate_adoption(body)
            except ContractError as exc:
                self._adoption_result("invalid_payload")
                return web.json_response({"error": str(exc)}, status=400)
            except CommandFailure as exc:
                self._adoption_result("already_adopted")
                return web.json_response({"error": str(exc)}, status=exc.code)
            self._state["management"] = management
            try:
                self._save_state()
            except Exception:
                self._adoption_result("state_error")
                raise
            self._adoption_generation += 1
            self._wake.set()
            self._adoption_result("accepted")
        if self._ws is not None:
            await self._ws.close()
        # Stock firmware echoes the request. Omit credentials and token here.
        return web.json_response({key: value for key, value in management.items() if key != "token"})

    def _headers(self) -> dict[str, str]:
        headers = {"x-ident": self.mac, "x-type": self.device.get("model", "UP-AI-KEY"),
                   "x-sysid": self.device.get("sysid", "0xa5f0"), "x-ip": self.device.get("ip", "127.0.0.1"),
                   "x-version": self.device.get("firmware_version", "2.2.8"), "x-mode": "0",
                   "x-adopted": str(bool(self._state.get("adopted"))).lower()}
        token = self._state.get("management", {}).get("token")
        if token:
            headers["x-token"] = token
        return headers

    async def start(self) -> None:
        if self._task is not None:
            return
        if self.tls_context is None:
            raise ValueError("A verified controller TLS context with a client certificate is required")
        connector = VerifiedConnector(ssl_context=self.tls_context, expected_fingerprint=self.controller.get("expected_fingerprint"))
        trace = aiohttp.TraceConfig()
        async def reject_redirect(session, trace_context, params):
            raise ContractError("Control WebSocket redirects are forbidden")
        trace.on_request_redirect.append(reject_redirect)
        self._session = aiohttp.ClientSession(connector=connector, trust_env=False,
                                              trace_configs=[trace],
                                              timeout=aiohttp.ClientTimeout(total=20))
        self._task = asyncio.create_task(self._run(), name="aikey-control")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _run(self) -> None:
        delay = 1.0
        while True:
            self._wake.clear()
            try:
                await self._connect_once()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Exception text may contain transport/request secrets.
                self._last_error = type(exc).__name__
                self.log.warning("Control connection failed (%s)", self._last_error)
                delay = min(30.0, delay * 2)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _connect_once(self) -> None:
        assert self._session is not None
        profile = self.controller.get("control_profile", "ucp4")
        if profile not in {"ucp4", "device-service"}:
            raise ContractError("Unsupported controller control profile")
        if profile == "device-service" and _parse_pin(self.controller.get("expected_fingerprint")) is None:
            raise ContractError("Device Service control profile requires an explicit controller certificate pin")
        handlers: set[asyncio.Task] = set()
        ws = None
        async with self._lock:
            headers = self._headers()
            generation = self._adoption_generation
        try:
            async with self._session.ws_connect(self.control_url, protocols=("ucp4",), headers=headers,
                                                heartbeat=20, max_msg_size=_MAX_MESSAGE_BYTES,
                                                autoclose=True, autoping=True) as ws:
                device_service = (profile == "device-service" and ws.protocol is None
                                  and "Sec-WebSocket-Protocol" not in ws._response.headers
                                  and (bool(headers.get("x-token")) or headers.get("x-adopted") == "true"))
                if ws.protocol != "ucp4" and not device_service:
                    raise ContractError("Controller did not negotiate UCP4")
                self._ws = ws
                self._connections += 1
                self._connection_generation = generation
                self._connection_token = headers.get("x-token")
                self._time_sync_id = secrets.token_hex(16)
                self._last_t0 = int(time.time() * 1000)
                await ws.send_bytes(encode_message({"type": "request", "action": "timeSync", "id": self._time_sync_id,
                                                   "timestamp": self._last_t0}, {"t0": self._last_t0}))
                async for frame in ws:
                    if frame.type == aiohttp.WSMsgType.BINARY:
                        if len(handlers) >= 32:
                            await ws.close(code=1013, message=b"Too many pending requests")
                            break
                        task = asyncio.create_task(self._respond(ws, frame.data))
                        handlers.add(task)
                        task.add_done_callback(handlers.discard)
                    elif frame.type == aiohttp.WSMsgType.TEXT:
                        await ws.close(code=1003, message=b"Binary UCP4 required")
                        break
                    elif frame.type == aiohttp.WSMsgType.ERROR:
                        if ws.close_code == 1006:
                            raise AbnormalControlClosure("Control transport closed abnormally")
                        raise ContractError("Control WebSocket failed")
                # aiohttp also exposes transport loss as CLOSED/1006, which ends
                # async iteration without raising or yielding an ERROR frame.
                if ws.close_code == 1006:
                    raise AbnormalControlClosure("Control transport closed abnormally")
                if ws.close_code not in (1000, 1001):
                    raise ContractError("Control WebSocket closed unexpectedly")
        finally:
            for task in handlers:
                task.cancel()
            if handlers:
                await asyncio.gather(*handlers, return_exceptions=True)
            if ws is not None:
                code = ws.close_code
                self._last_close_code = int(code) if isinstance(code, int) and 1000 <= code <= 4999 else None
            self._ws = None
            self._time_sync_id = None
            self._last_t0 = None
            self._connection_token = None
            self._connection_generation = None

    async def _respond(self, ws, wire: bytes) -> None:
        try:
            response = await self.handle_message(wire, _connection=ws)
            if response is not None and not ws.closed:
                await ws.send_bytes(response)
        except (ContractError, ValueError):
            await ws.close(code=1002, message=b"Invalid UCP4 message")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.log.warning("Control response failed (%s)", type(exc).__name__)
            await ws.close(code=1011, message=b"Control processing failed")

    async def handle_message(self, wire: bytes, *, _connection=None) -> bytes | None:
        message = decode_message(wire)
        header, body = message.header, message.body
        kind = header.get("type")
        if kind == "response":
            if (_connection is not None and _connection is self._ws and not _connection.closed
                    and self._time_sync_id is not None and header.get("id") == self._time_sync_id
                    and type(header.get("errorCode")) is int and header["errorCode"] == 0
                    and header.get("error") in (None, "")):
                if (all(type(body.get(k)) is int and 0 <= body[k] <= 2 ** 53 - 1 for k in ("t0", "t1", "t2"))
                        and body["t0"] == self._last_t0 and body["t2"] >= body["t1"]):
                    self._clock_offset_ms = ((body["t1"] - body["t0"]) + (body["t2"] - int(time.time() * 1000))) / 2
                    self._last_error = None
                    async with self._lock:
                        if _connection is self._ws and not _connection.closed:
                            self._confirmed_control_connection = _connection
                        if (_connection is self._ws and not _connection.closed
                                and self._connection_token
                                and self._connection_generation == self._adoption_generation
                                and self._state.get("management", {}).get("token") == self._connection_token):
                            previously_adopted = self._state["adopted"]
                            self._state["adopted"] = True
                            self._state["management"].pop("token")
                            try:
                                self._save_state()
                            except Exception:
                                self._state["adopted"] = previously_adopted
                                self._state["management"]["token"] = self._connection_token
                                raise
                            self._connection_token = None
                        if self._confirmation_waiting_connection is _connection:
                            self._confirmation_event.set()
            return None
        if kind == "event":
            # No event contract is implemented; never acknowledge a mutation.
            return None
        if kind != "request":
            raise ContractError("Unsupported message type")
        request_id = _text(header.get("id"), "request id", 128)
        action = _text(header.get("action"), "action", 128)
        digest = hashlib.sha256(wire).digest()
        if request_id in self._completed:
            original, response = self._completed[request_id]
            if original != digest:
                raise ContractError("Request id reused with different content")
            return response
        if request_id in self._pending:
            original, future = self._pending[request_id]
            if original != digest:
                raise ContractError("Pending request id reused with different content")
            return await asyncio.shield(future)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (digest, future)
        diagnostic = self._control_diagnostics.get(action, self._control_diagnostics["unknown"])
        _increment(diagnostic, "count")
        matches = self._record_recognize_shape(body) if action == "recognizeKeyFrames" else ()
        try:
            try:
                result = await self._command(action, body, _connection=_connection)
                error, code = None, 0
            except CommandFailure as exc:
                result, error, code = {}, str(exc), exc.code
            except (ContractError, ValueError, TypeError):
                result, error, code = {}, "Invalid or unsupported command payload", 22
            except asyncio.TimeoutError:
                result, error, code = {}, "Job admission timed out", 110
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log.warning("Command failed (%s)", type(exc).__name__)
                result, error, code = {}, "Command failed", 5
            known_code = type(code) is int and code in _RESULT_CODES
            diagnostic["last_result_code"] = code if known_code else None
            code_bucket = str(code) if known_code else "other"
            _increment(diagnostic["result_code_counts"], code_bucket)
            for field in matches:
                _increment(self._recognize_diagnostics[f"matching_{field}_result_code_counts"], code_bucket)
            response = encode_message({"id": request_id, "type": "response", "timestamp": int(time.time() * 1000),
                                       "error": error, "errorCode": code}, result)
            self._completed[request_id] = (digest, response)
            while len(self._completed) > 128:
                self._completed.popitem(last=False)
            future.set_result(response)
            return response
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    def _record_recognize_shape(self, body: dict) -> tuple[str, ...]:
        """Retain only fixed field names and categories, never request values."""
        scope = self.config.get("worker", {}).get("test_scope")
        target = scope.get("camera_id") if isinstance(scope, dict) else None
        matches = []
        for field in ("camera", "cameraId"):
            _increment(self._recognize_diagnostics[f"{field}_shape_counts"], _field_shape(body, field))
            value = body.get(field)
            if isinstance(value, str) and isinstance(target, str):
                outcome = "matches" if value == target else "different"
            else:
                outcome = "not_comparable"
            _increment(self._recognize_diagnostics[f"{field}_match_counts"], outcome)
            if outcome == "matches":
                matches.append(field)
        value = body.get("ramType")
        category = ("missing" if "ramType" not in body else "invalid_type"
                    if not isinstance(value, str) else value if value in _RAM_TYPES else "other_string")
        _increment(self._recognize_diagnostics["ram_type_counts"], category)
        for field in self._recognize_diagnostics["metadata_presence_counts"]:
            if field in body:
                _increment(self._recognize_diagnostics["metadata_presence_counts"], field)
        start, end = body.get("start"), body.get("end")
        interval_valid = (type(start) is int and type(end) is int
                          and 0 <= start < end <= 2 ** 53 - 1)
        interval_category = (
            "missing" if "start" not in body or "end" not in body else
            "invalid_type" if type(start) is not int or type(end) is not int else
            "invalid_order_or_range" if not interval_valid else
            "over_10_seconds" if end - start > 10000 else "up_to_10_seconds")
        _increment(self._recognize_diagnostics["video_interval_counts"], interval_category)
        duration_limit = self.config.get("worker", {}).get("max_video_duration_ms", 120000)
        duration_limit = duration_limit if type(duration_limit) is int and duration_limit > 0 else 120000
        duration_category = ("not_comparable" if not interval_valid else
                             "exceeds" if end - start > duration_limit else "within")
        _increment(self._recognize_diagnostics["duration_limit_counts"], duration_category)
        moments = body.get("keyMoments")
        moment_counts = self._recognize_diagnostics["key_moments_counts"]
        if "keyMoments" not in body:
            _increment(moment_counts, "missing")
        elif not isinstance(moments, list):
            _increment(moment_counts, "invalid_type")
        elif not moments:
            _increment(moment_counts, "empty")
        else:
            limit = self.config.get("worker", {}).get("max_images", 4)
            limit = limit if type(limit) is int and limit > 0 else 4
            _increment(moment_counts, "above_sampling_limit" if len(moments) > limit else "at_or_below_sampling_limit")
            if len(moments) > 128:
                _increment(moment_counts, "over_128_inputs")
            if any(type(moment) is not int for moment in moments):
                _increment(moment_counts, "non_integer")
            else:
                if len(set(moments)) != len(moments):
                    _increment(moment_counts, "duplicates")
                if not interval_valid:
                    _increment(moment_counts, "interval_not_comparable")
                elif any(not start <= moment < end for moment in moments):
                    _increment(moment_counts, "outside_interval")
        return tuple(matches)

    async def _command(self, action: str, body: dict, *, _connection=None) -> dict:
        if action == "getInfo":
            return self.get_info()
        if action == "getTaskQueueInfo":
            if self.queue_status is None:
                raise CommandFailure(95, "Worker queue status is unavailable")
            result = self.queue_status()
            if isinstance(result, dict) and {"queued", "active"} <= result.keys():
                queued, active = result["queued"], result["active"]
                if type(queued) is not int or type(active) is not int or min(queued, active) < 0:
                    raise ContractError("Worker queue counts must be nonnegative integers")
                result = dict.fromkeys(_QUEUE_FIELDS, 0)
                result["UI_TASK_NUM"] = queued + active
            if not isinstance(result, dict) or set(result) != set(_QUEUE_FIELDS):
                raise ContractError("Queue status provider must return observed fields or worker counts")
            if any(type(value) is not int or value < 0 for value in result.values()):
                raise ContractError("Queue counts must be nonnegative integers")
            return result
        if action == "RequestAI":
            target = _text(body.get("targetUri"), "targetUri", 512)
            if not re.fullmatch(r":\d{1,5}/[A-Za-z0-9_/-]+", target):
                raise ContractError("Unsupported targetUri syntax")
            if not isinstance(body.get("payload"), dict):
                raise ContractError("RequestAI payload must be an object")
            timeout_ms = body.get("timeoutMs")
            if type(timeout_ms) is not int or not 1 <= timeout_ms <= 3_600_000:
                raise ContractError("Invalid timeoutMs")
            if "resUrl" in body and body["resUrl"] is not None:
                _text(body["resUrl"], "resUrl", 2048)
            self._active_admissions += 1
            try:
                async with asyncio.timeout(min(timeout_ms / 1000, 30)):
                    admitted = await self.job_handler(deepcopy(body))
                if not isinstance(admitted, dict):
                    raise ContractError("Job admission must return an object")
            finally:
                self._active_admissions -= 1
            return body
        if action == "recognizeKeyFrames":
            from .worker import WorkerError
            phases = self._recognize_diagnostics["phase_counts"]
            scope = self.config.get("worker", {}).get("test_scope", {})
            if not isinstance(scope, dict) or scope.get("kind") != "recognizeKeyFrames":
                _increment(phases, "scope_disabled")
                raise CommandFailure(95, "recognizeKeyFrames is outside the configured single-use scope")
            if not isinstance(body.get("camera"), str) or body["camera"] != scope.get("camera_id"):
                _increment(phases, "camera_mismatch")
                raise CommandFailure(95, "recognizeKeyFrames is outside the configured single-use scope")
            self._active_admissions += 1
            _increment(phases, "worker_admission")
            try:
                async with asyncio.timeout(30):
                    admitted = await self.job_handler({"command": action, "payload": deepcopy(body)})
            except WorkerError as exc:
                _increment(phases, "worker_rejected")
                reason = _WORKER_REJECTION_REASONS.get(str(exc), "unclassified_worker_error")
                _increment(self._recognize_diagnostics["worker_rejection_counts"], reason)
                raise
            except asyncio.TimeoutError:
                _increment(phases, "admission_timeout")
                raise
            except asyncio.CancelledError:
                _increment(phases, "admission_cancelled")
                raise
            except Exception:
                _increment(phases, "admission_exception")
                raise
            finally:
                self._active_admissions -= 1
            if not isinstance(admitted, dict):
                _increment(phases, "invalid_admission_result")
                raise ContractError("Job admission must return an object")
            _increment(phases, "admitted")
            return body
        if action in {"setConsoleInfo", "setInfo", "updateTimezone", "changeUserPassword"}:
            if action == "changeUserPassword":
                await self._await_factory_confirmation(body, _connection)
            async with self._lock:
                if action == "setConsoleInfo":
                    if not isinstance(body.get("controller"), dict):
                        raise ContractError("controller object required")
                    allowed = {"consoleName", "id", "protectVersion", "supportsDbCredential"}
                    if set(body["controller"]) - allowed:
                        raise ContractError("Unsupported controller info fields")
                    self._state["console_info"] = _finite_json(body["controller"])
                elif action == "setInfo":
                    if set(body) != {"hostname"}:
                        raise ContractError("Only a logical hostname is supported")
                    self._state["name"] = _text(body["hostname"], "hostname")
                elif action == "updateTimezone":
                    if set(body) != {"timezone"}:
                        raise ContractError("timezone required")
                    self._state["timezone"] = _text(body["timezone"], "timezone")
                else:
                    username = _text(body.get("username"), "username")
                    old = _text(body.get("passwordOld"), "old password", 1024)
                    new = _text(body.get("passwordNew"), "new password", 1024)
                    factory_rotation = self._factory_rotation_allowed(username, old, _connection)
                    if not await asyncio.to_thread(self._password_matches, username, old) and not factory_rotation:
                        raise CommandFailure(13, "Invalid current credentials")
                    if username == "ui" and new == "ui":
                        raise CommandFailure(22, "Factory enrollment must rotate to a nonfactory password")
                    if self.config.get("search", {}).get("enabled") and self.credential_handler is None:
                        raise CommandFailure(95, "Search requires a database credential rotation handler")
                    if self.credential_handler is not None:
                        await self.credential_handler(username, new)
                    salt = secrets.token_bytes(16)
                    hashed = await asyncio.to_thread(hashlib.pbkdf2_hmac, "sha256", new.encode(), salt, 200_000)
                    previous_state = deepcopy(self._state)
                    self._state["credential"] = {"username": username, "salt": salt.hex(), "hash": hashed.hex()}
                    self._state.pop("factory_enrollment_used", None)
                    try:
                        self._save_state()
                    except Exception:
                        self._state = previous_state
                        raise
                    return body
                self._save_state()
            return body
        raise CommandFailure(95, f"Unsupported command: {action}")
