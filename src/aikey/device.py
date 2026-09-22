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


class CommandFailure(Exception):
    """A bounded local command error represented in the UCP response envelope."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


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

    ``job_handler`` admits a complete RequestAI body and returns promptly after
    reservation. It owns inference and result uploads. Returning an object means
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
        self._state = self._load_state()
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
        self._clock_offset_ms: float | None = None
        self._time_sync_id: str | None = None
        self._last_t0: int | None = None
        self._active_admissions = 0

    @property
    def control_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"wss://{host}:{self.port}/"

    @property
    def status(self) -> dict:
        return {"adopted": bool(self._state.get("adopted")), "connected": self._ws is not None and not self._ws.closed,
                "connections": self._connections, "last_error": self._last_error,
                "clock_offset_ms": self._clock_offset_ms, "discovery": "unsupported",
                "supported_commands": ["getInfo", "getTaskQueueInfo", "setConsoleInfo", "setInfo", "updateTimezone", "changeUserPassword", "RequestAI"]}

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

    def get_info(self) -> dict:
        flags = {name: {"enabled": False, "version": "v1"} for name in _OBJECT_CAPABILITIES}
        flags.update({"supportDeepMode": False, "supportVlm": False, "aiMode": "basic"})
        overrides = self.device.get("feature_flags", {})
        if not isinstance(overrides, dict):
            raise ContractError("feature_flags must be an object")
        flags.update(_finite_json(overrides))
        return {"type": self.device.get("model", "UP-AI-KEY"), "sysid": self.device.get("sysid", "0xa5f0"),
                "version": self.device.get("firmware_version", "2.2.8"), "mac": self.mac,
                "uptime": int(time.monotonic() - self._started_at), "poeType": self.device.get("poe_type", "unknown"),
                "storageSize": self.device.get("storage_size", 0), "featureFlags": flags}

    def create_app(self) -> web.Application:
        app = web.Application(client_max_size=_MAX_STATE_BYTES)
        app.router.add_get("/api/info", self._http_info)
        app.router.add_post("/api/adopt", self._http_adopt)
        return app

    async def _http_info(self, request: web.Request) -> web.Response:
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

    async def _http_adopt(self, request: web.Request) -> web.Response:
        if self.mode == "device" and not request.secure:
            return web.json_response({"error": "Adoption requires HTTPS"}, status=400)
        if request.content_type != "application/json":
            return web.json_response({"error": "JSON content type required"}, status=415)
        try:
            body = _object_json(await request.read())
        except ContractError:
            return web.json_response({"error": "Invalid JSON"}, status=422)
        async with self._lock:
            if not await asyncio.to_thread(self._password_matches, body.get("username"), body.get("password")):
                return web.json_response({"error": "Invalid credentials"}, status=401)
            try:
                management = self._validate_adoption(body)
            except ContractError as exc:
                return web.json_response({"error": str(exc)}, status=400)
            except CommandFailure as exc:
                return web.json_response({"error": str(exc)}, status=exc.code)
            self._state["management"] = management
            self._save_state()
            self._wake.set()
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
        handlers: set[asyncio.Task] = set()
        try:
            async with self._session.ws_connect(self.control_url, protocols=("ucp4",), headers=self._headers(),
                                                heartbeat=20, max_msg_size=_MAX_MESSAGE_BYTES,
                                                autoclose=True, autoping=True) as ws:
                if ws.protocol != "ucp4":
                    raise ContractError("Controller did not negotiate UCP4")
                self._ws = ws
                self._connections += 1
                self._last_error = None
                async with self._lock:
                    self._state["adopted"] = True
                    self._state.setdefault("management", {}).pop("token", None)
                    self._save_state()
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
                        raise ContractError("Control WebSocket failed")
        finally:
            for task in handlers:
                task.cancel()
            if handlers:
                await asyncio.gather(*handlers, return_exceptions=True)
            self._ws = None

    async def _respond(self, ws, wire: bytes) -> None:
        try:
            response = await self.handle_message(wire)
            if response is not None and not ws.closed:
                await ws.send_bytes(response)
        except (ContractError, ValueError):
            await ws.close(code=1002, message=b"Invalid UCP4 message")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.log.warning("Control response failed (%s)", type(exc).__name__)
            await ws.close(code=1011, message=b"Control processing failed")

    async def handle_message(self, wire: bytes) -> bytes | None:
        message = decode_message(wire)
        header, body = message.header, message.body
        kind = header.get("type")
        if kind == "response":
            if header.get("id") == self._time_sync_id and not header.get("errorCode"):
                if all(type(body.get(k)) is int for k in ("t0", "t1", "t2")) and body["t0"] == self._last_t0:
                    self._clock_offset_ms = ((body["t1"] - body["t0"]) + (body["t2"] - int(time.time() * 1000))) / 2
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
        try:
            try:
                result = await self._command(action, body)
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

    async def _command(self, action: str, body: dict) -> dict:
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
        if action in {"setConsoleInfo", "setInfo", "updateTimezone", "changeUserPassword"}:
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
                    if not await asyncio.to_thread(self._password_matches, username, old):
                        raise CommandFailure(13, "Invalid current credentials")
                    if self.config.get("search", {}).get("enabled") and self.credential_handler is None:
                        raise CommandFailure(95, "Search requires a database credential rotation handler")
                    if self.credential_handler is not None:
                        await self.credential_handler(username, new)
                    salt = secrets.token_bytes(16)
                    hashed = await asyncio.to_thread(hashlib.pbkdf2_hmac, "sha256", new.encode(), salt, 200_000)
                    self._state["credential"] = {"username": username, "salt": salt.hex(), "hash": hashed.hex()}
                self._save_state()
            return body
        raise CommandFailure(95, f"Unsupported command: {action}")
