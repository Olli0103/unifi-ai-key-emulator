"""Opt-in, pinned, session-local Protect metadata reader; no camera controls.

Only explicit operator-supplied credential files are used. Browser cookies are
never imported/exported. No login retry, background poll, media or PTZ request.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import ipaddress
import os
from pathlib import Path
import stat
import time

import aiohttp

from . import camera_inventory as inventory
from .aiport_ingest import IngressError, normalize_mac

SCHEMA = "aikey-native-camera-status/1"
_STATES = {"CONNECTED", "CONNECTING", "DISCONNECTED", "UPDATING", "ADOPTING",
           "PROVISIONING", "UNKNOWN"}


class NativeStatusError(RuntimeError):
    """Fixed diagnostic, never a credential, URL, response or transport error."""

    def __init__(self, code="native_status_unavailable"):
        if code not in {"native_status_unavailable", "native_status_backoff",
                        "native_session_cleanup_unconfirmed", "native_authentication_refused"}:
            raise ValueError("Invalid native status diagnostic")
        self.code = code
        super().__init__(code)


def _boolean(value):
    if value is not None and type(value) is not bool:
        raise ValueError
    return value


def _integer(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError
    return value


def _camera(row):
    if not isinstance(row, dict):
        raise ValueError
    camera_id = row.get("id")
    if (not isinstance(camera_id, str) or not inventory._CAMERA_ID.fullmatch(camera_id)
            or row.get("modelKey") != "camera" or row.get("state") not in _STATES):
        raise ValueError
    flags = row.get("featureFlags", {})
    if not isinstance(flags, dict):
        raise ValueError
    channels = row.get("channels", [])
    if not isinstance(channels, list) or len(channels) > 16:
        raise ValueError
    clean = []
    for channel in channels:
        if not isinstance(channel, dict):
            raise ValueError
        entry = {"id": _integer(channel.get("id"), 255)}
        for field, maximum in (("width", 16384), ("height", 16384), ("fps", 240)):
            if field in channel:
                entry[field] = _integer(channel[field], maximum)
        if "isRtspEnabled" in channel:
            entry["rtsp_enabled"] = _boolean(channel["isRtspEnabled"])
        clean.append(entry)
    if len({c["id"] for c in clean}) != len(clean):
        raise ValueError
    return {"id": camera_id, "mac": normalize_mac(row.get("mac")),
            "name": inventory._label(row.get("name"), "name"), "state": row["state"],
            "recording_active": _boolean(row.get("isRecording")),
            "ptz_capable": _boolean(flags.get("isPtz")), "channels": clean}


def parse_bootstrap(value, *, observed_at):
    try:
        if (not isinstance(value, dict) or not isinstance(value.get("nvr"), dict)
                or not inventory.is_supported_protect_version(value["nvr"].get("version"))
                or not isinstance(value.get("cameras"), list)
                or len(value["cameras"]) > 256):
            raise ValueError
        cameras = [_camera(row) for row in value["cameras"]]
        if (len({r["id"] for r in cameras}) != len(cameras)
                or len({r["mac"] for r in cameras}) != len(cameras)):
            raise ValueError
        return {"schema": SCHEMA, "protect_version": value["nvr"]["version"],
                "fetched_at": observed_at, "source": "authenticated_protect_bootstrap",
                "control_operations_enabled": False,
                "cameras": sorted(cameras, key=lambda r: r["id"])}
    except (ValueError, TypeError, KeyError, IngressError, inventory.InventoryError):
        raise NativeStatusError("native_status_unavailable") from None


def public_snapshot(value):
    """Revalidate trusted-loader output at the authenticated publication boundary."""
    try:
        if (not isinstance(value, dict) or set(value) != {
                "schema", "protect_version", "fetched_at", "source",
                "control_operations_enabled", "cameras", "session_logout_acknowledged"}
                or value["schema"] != SCHEMA
                or value["source"] != "authenticated_protect_bootstrap"
                or value["control_operations_enabled"] is not False
                or value["session_logout_acknowledged"] is not True
                or type(value["fetched_at"]) is not int
                or not 0 <= time.time() - value["fetched_at"] <= 30
                or not isinstance(value["cameras"], list) or len(value["cameras"]) > 256):
            raise ValueError
        wire = []
        for row in value["cameras"]:
            if not isinstance(row, dict) or set(row) != {
                    "id", "mac", "name", "state", "recording_active", "ptz_capable", "channels"}:
                raise ValueError
            channels = []
            if not isinstance(row["channels"], list) or len(row["channels"]) > 16:
                raise ValueError
            for c in row["channels"]:
                if not isinstance(c, dict) or set(c) - {"id", "width", "height", "fps", "rtsp_enabled"}:
                    raise ValueError
                channels.append({("isRtspEnabled" if k == "rtsp_enabled" else k): v
                                 for k, v in c.items()})
            wire.append({"id": row["id"], "mac": row["mac"], "name": row["name"],
                         "state": row["state"], "modelKey": "camera",
                         "isRecording": row["recording_active"],
                         "featureFlags": {"isPtz": row["ptz_capable"]}, "channels": channels})
        clean = parse_bootstrap({"nvr": {"version": value["protect_version"]}, "cameras": wire},
                                observed_at=value["fetched_at"])
        return {**clean, "session_logout_acknowledged": True}
    except (ValueError, TypeError, KeyError):
        raise NativeStatusError("native_status_unavailable") from None


def _credential(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        meta = os.fstat(stream.fileno())
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                or meta.st_mode & 0o077 or not 0 < meta.st_size <= limit):
            raise ValueError
        value = stream.read(limit + 1).decode("utf-8").removesuffix("\n")
    if not value or len(value.encode()) > limit or any(ord(c) < 32 for c in value):
        raise ValueError
    return value


async def _body(response, limit):
    parts, size = [], 0
    async for chunk in response.content.iter_chunked(min(limit + 1, 65536)):
        size += len(chunk)
        if size > limit:
            raise NativeStatusError("native_status_unavailable")
        parts.append(chunk)
    return b"".join(parts)


class NativeStatusReader:
    """One bounded observation, fresh in-memory session, explicit logout each time."""

    def __init__(self, host, *, username_file: Path, password_file: Path,
                 trust_file: Path, cert_file: Path):
        try:
            if not isinstance(host, str):
                raise ValueError
            address = ipaddress.ip_address(host)
            if (not isinstance(address, ipaddress.IPv4Address)
                    or not any(address in n for n in inventory._LOCAL_IPV4)):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Native status requires a private IPv4 controller") from None
        self.host, self.username_file, self.password_file = host, username_file, password_file
        self.trust_file, self.cert_file = trust_file, cert_file
        self._lock = asyncio.Lock()
        self._cache = None
        self._next = 0.0
        self._clock = time.monotonic
        self._blocked_credential = None
        self._cleanup_uncertain = False

    async def read(self):
        async with self._lock:
            if self._cleanup_uncertain:
                raise NativeStatusError("native_session_cleanup_unconfirmed")
            now = self._clock()
            if self._cache is not None and now < self._cache[0]:
                return public_snapshot(deepcopy(self._cache[1]))
            self._cache = None  # Never serve stale data after a failed refresh.
            if now < self._next:
                raise NativeStatusError("native_status_backoff")
            self._next = now + 30  # Bound repeated logins, including authentication failure.
            try:
                result = await self._fetch()
            except asyncio.CancelledError:
                raise
            except NativeStatusError:
                raise
            except (OSError, ValueError, TypeError, inventory.InventoryError,
                    aiohttp.ClientError, TimeoutError):
                raise NativeStatusError("native_status_unavailable") from None
            self._cache = (self._next, deepcopy(result))
            return public_snapshot(result)

    async def _fetch(self):
        context, pin = inventory._trusted_web(self.host, self.trust_file, self.cert_file)
        username = _credential(self.username_file, 256)
        password = _credential(self.password_file, 4096)
        credential_version = hashlib.sha256((username + "\0" + password).encode()).digest()
        if credential_version == self._blocked_credential:
            raise NativeStatusError("native_authentication_refused")
        port = inventory._INTEGRATION_PORT
        origin = f"https://{self.host}" + (f":{port}" if port != 443 else "")
        connector = inventory.PinnedWebConnector(context, pin)
        jar = aiohttp.CookieJar(unsafe=True)  # Exact private IP, no domain redirects.
        timeout = aiohttp.ClientTimeout(total=12, connect=4, sock_read=8)
        async with aiohttp.ClientSession(connector=connector, cookie_jar=jar,
                timeout=timeout, trust_env=False, headers={"Origin": origin,
                                                        "Accept": "application/json"}) as session:
            attempted = False
            try:
                async with asyncio.timeout(15):
                    attempted = True
                    async with session.post(origin + "/api/auth/login", allow_redirects=False,
                            json={"username": username, "password": password,
                                  "rememberMe": False}) as response:
                        await _body(response, 262144)
                        if 400 <= response.status < 500:
                            self._blocked_credential = credential_version
                            if not jar:  # Definitive refusal, no returned session to sign out.
                                attempted = False
                            raise NativeStatusError("native_authentication_refused")
                        if response.status != 200:
                            raise NativeStatusError("native_status_unavailable")
                        csrf = response.headers.get("X-CSRF-Token")
                        if csrf:
                            if len(csrf) > 4096 or any(ord(c) < 32 for c in csrf):
                                raise NativeStatusError("native_status_unavailable")
                            session.headers["X-CSRF-Token"] = csrf
                    if not jar:
                        raise NativeStatusError("native_status_unavailable")
                    async with session.get(origin + "/proxy/protect/api/bootstrap",
                                           allow_redirects=False) as response:
                        if response.status in {401, 403}:
                            self._blocked_credential = credential_version
                            raise NativeStatusError("native_authentication_refused")
                        if response.status != 200 or response.content_type != "application/json":
                            raise NativeStatusError("native_status_unavailable")
                        raw = await _body(response, 2097152)
                        value = inventory._json(raw)
                    result = parse_bootstrap(value, observed_at=int(time.time()))
            finally:
                # A login may have been accepted even when its response failed.
                # No retry or server-session-cleanup claim follows uncertain logout.
                cancelled = asyncio.current_task().cancelling() > 0
                try:
                    if attempted:
                        async with asyncio.timeout(3):
                            async with session.post(origin + "/api/auth/logout", allow_redirects=False,
                                    timeout=aiohttp.ClientTimeout(total=3)) as response:
                                await _body(response, 4096)
                                if response.status not in {200, 204}:
                                    raise NativeStatusError("native_session_cleanup_unconfirmed")
                except asyncio.CancelledError:
                    self._cleanup_uncertain = True
                    raise
                except (aiohttp.ClientError, TimeoutError, NativeStatusError):
                    self._cleanup_uncertain = True
                    if cancelled:
                        raise asyncio.CancelledError from None
                    raise NativeStatusError("native_session_cleanup_unconfirmed") from None
                finally:
                    jar.clear()
        return {**result, "session_logout_acknowledged": True}
