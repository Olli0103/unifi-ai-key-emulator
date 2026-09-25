"""Read-only Protect camera inventory for operator preflight.

This module never grants processing scope. A camera's advertised smart types
describe its own capabilities, not proven AI Key callback eligibility.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from html import escape
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import stat
import time

import aiohttp
from cryptography import x509

from .aiport_ingest import IngressError, normalize_mac
from .device import verify_peer_pin


_MAX_RESPONSE = 2 * 1024 * 1024
_MAX_CAMERAS = 256
_INTEGRATION_PORT = 443
_LOCAL_IPV4 = tuple(ipaddress.ip_network(value) for value in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_CAMERA_ID = re.compile(r"[0-9a-fA-F]{24}")
_FINGERPRINT = re.compile(r"[0-9a-fA-F]{64}")
_SMART_TYPES = {"person", "vehicle", "package", "licensePlate", "face", "animal"}
_AUDIO_TYPES = {"alrmSmoke", "alrmCmonx", "alrmSiren", "alrmBabyCry", "alrmSpeak",
                "alrmBark", "alrmBurglar", "alrmCarHorn", "alrmGlassBreak",
                "smoke_cmonx"}  # Seen in a 7.3.60 inventory, absent from its published enum.
_VALIDATED_VERSIONS = frozenset({"7.3.60", "7.3.68"})


class InventoryError(RuntimeError):
    """The inventory cannot be trusted or is unavailable."""


@dataclass(frozen=True)
class Camera:
    id: str
    mac: str
    name: str | None
    model: str | None
    state: str
    smart_detect_types: tuple[str, ...]
    audio_detect_types: tuple[str, ...]
    processing_class: str
    reason: str

    def public(self) -> dict:
        value = asdict(self)
        value["smart_detect_types"] = list(self.smart_detect_types)
        value["audio_detect_types"] = list(self.audio_detect_types)
        return value


def _read_private(path: Path, limit: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise InventoryError("Inventory credential or trust file is unavailable") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            meta = os.fstat(handle.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077:
                raise InventoryError("Inventory credential or trust file must be private")
            if meta.st_size > limit:
                raise InventoryError("Inventory credential or trust file is too large")
            content = handle.read(limit + 1)
            if len(content) > limit:
                raise InventoryError("Inventory credential or trust file is too large")
            return content
    except OSError as exc:
        raise InventoryError("Inventory credential or trust file is unavailable") from exc


def _json(raw: bytes):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InventoryError("Invalid inventory JSON") from exc


def _label(value, field: str) -> str | None:
    if value is None:
        return None
    if (not isinstance(value, str) or len(value) > 128
            or any(ord(ch) < 32 for ch in value)):
        raise InventoryError(f"Invalid camera {field}")
    return value


def _types(value, allowed: set[str], field: str) -> tuple[str, ...]:
    if (not isinstance(value, list) or len(value) > len(allowed)
            or any(not isinstance(item, str) or item not in allowed for item in value)
            or len(value) != len(set(value))):
        raise InventoryError(f"Invalid camera {field}")
    return tuple(value)


def parse_cameras(value) -> tuple[Camera, ...]:
    """Validate a complete observed integration response before publishing it."""
    if not isinstance(value, list) or len(value) > _MAX_CAMERAS:
        raise InventoryError("Invalid camera inventory list")
    cameras = []
    ids = set()
    for row in value:
        if not isinstance(row, dict):
            raise InventoryError("Invalid camera row")
        camera_id = row.get("id")
        if not isinstance(camera_id, str) or not _CAMERA_ID.fullmatch(camera_id):
            raise InventoryError("Invalid camera ID")
        if camera_id in ids:
            raise InventoryError("Duplicate camera ID")
        ids.add(camera_id)
        try:
            mac = normalize_mac(row.get("mac"))
        except IngressError as exc:
            raise InventoryError("Invalid camera MAC") from exc
        if row.get("modelKey") != "camera":
            raise InventoryError("Unexpected inventory model key")
        state = row.get("state")
        if state not in {"CONNECTED", "CONNECTING", "DISCONNECTED"}:
            raise InventoryError("Unknown camera state")
        flags = row.get("featureFlags")
        if not isinstance(flags, dict):
            raise InventoryError("Missing camera feature flags")
        smart = _types(flags.get("smartDetectTypes"), _SMART_TYPES, "smart types")
        audio = _types(flags.get("smartDetectAudioTypes"), _AUDIO_TYPES, "audio types")
        name = _label(row.get("name"), "name")
        model = _label(row.get("type"), "model")
        if state != "CONNECTED":
            processing_class = "offline"
            reason = "Camera is not connected."
        elif smart:
            processing_class = "smart_event_candidate"
            reason = "Camera reports smart detections; AI Key event delivery still needs a live trial."
        else:
            processing_class = "legacy_ingress_needed"
            reason = "No onboard smart detections; an AI Port or verified ingress path is needed."
        cameras.append(Camera(camera_id, mac, name, model, state, smart, audio,
                              processing_class, reason))
    return tuple(sorted(cameras, key=lambda item: item.id))


def render_html(report: dict) -> str:
    """Render a private static preview with no script or external resources."""
    counts = report["summary"]
    labels = {"smart_event_candidate": "Smart-event candidate",
              "legacy_ingress_needed": "Legacy ingress needed", "offline": "Offline"}
    rows = []
    for camera in sorted(report["cameras"], key=lambda item: (
            (item["name"] or "").casefold(), item["id"])):
        cells = [camera["name"] or "Unnamed", camera["model"] or "Unknown model",
                 labels[camera["processing_class"]], camera["state"],
                 ", ".join(camera["smart_detect_types"]) or "None", camera["reason"]]
        rows.append("<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in cells) + "</tr>")
    fetched = datetime.fromtimestamp(report["fetched_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Camera inventory preflight</title><style>
body{{font:16px system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#17212f;background:#f7f9fc}}
h1{{font-size:1.8rem}}p{{line-height:1.5}}.notice{{padding:1rem;border:1px solid #c17c00;background:#fff5d8;border-radius:8px}}
.counts{{display:flex;gap:1rem;flex-wrap:wrap;margin:1.5rem 0}}.counts div{{background:white;border:1px solid #d5dce5;border-radius:8px;padding:1rem;min-width:130px}}
.counts strong{{display:block;font-size:1.5rem}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:.7rem;text-align:left;border-bottom:1px solid #d5dce5;vertical-align:top}}
th{{background:#e9eff8}}.scroll{{overflow-x:auto}}small{{color:#44546a}}
</style></head><body><main>
<h1>Camera inventory preflight</h1>
<p><small>Protect {escape(report['protect_version'])} · Fetched {escape(fetched)} · Local integration API</small></p>
<p class="notice"><strong>Processing is off.</strong> This report is read-only. A smart-event candidate has not passed a native AI Key caption test. Legacy cameras still need a verified ingress path.</p>
<div class="counts"><div><strong>{counts['total']}</strong>All cameras</div><div><strong>{counts['smart_event_candidates']}</strong>Smart-event candidates</div><div><strong>{counts['legacy_ingress_needed']}</strong>Legacy ingress needed</div><div><strong>{counts['offline']}</strong>Offline</div></div>
<div class="scroll"><table><thead><tr><th>Camera</th><th>Model</th><th>Preflight class</th><th>State</th><th>Onboard smart types</th><th>Reason</th></tr></thead><tbody>
{''.join(rows)}
</tbody></table></div></main></body></html>\n"""


class PinnedWebConnector(aiohttp.TCPConnector):
    """Authenticate a self-signed web leaf before any API-key header is sent."""

    def __init__(self, context: ssl.SSLContext, pin: bytes):
        if context.verify_mode != ssl.CERT_NONE or len(pin) != 32:
            raise InventoryError("Invalid pinned web TLS configuration")
        self._pin = pin
        super().__init__(ssl=context, limit=2)

    async def _wrap_create_connection(self, *args, **kwargs):
        transport, protocol = await super()._wrap_create_connection(*args, **kwargs)
        try:
            verify_peer_pin(transport.get_extra_info("ssl_object"), self._pin)
        except Exception:
            transport.close()
            raise
        return transport, protocol


def _trusted_web(host: str, trust_file: Path, cert_file: Path) -> tuple[ssl.SSLContext, bytes]:
    trust = _json(_read_private(trust_file, 4096))
    if not isinstance(trust, dict):
        raise InventoryError("Invalid web trust record")
    pin = trust.get("sha256")
    if (trust.get("host") != host or trust.get("port") != _INTEGRATION_PORT
            or not isinstance(pin, str) or not _FINGERPRINT.fullmatch(pin)):
        raise InventoryError("Web trust record does not match the configured controller")
    pem = _read_private(cert_file, 16384)
    if b"-----BEGIN CERTIFICATE-----" not in pem:
        raise InventoryError("Invalid pinned web certificate")
    try:
        der = ssl.PEM_cert_to_DER_cert(pem.decode("ascii"))
        certificate = x509.load_der_x509_certificate(der)
        if (not hmac.compare_digest(bytes.fromhex(pin), hashlib.sha256(der).digest())
                or not certificate.not_valid_before_utc <= datetime.now(timezone.utc)
                <= certificate.not_valid_after_utc):
            raise ValueError
    except (UnicodeError, ssl.SSLError, ValueError) as exc:
        raise InventoryError("Pinned web certificate does not match trust or is expired") from exc
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    # UDM web leaf is self-signed without CA authority. Exact leaf pinning below
    # authenticates it before aiohttp can write the API-key header.
    context.verify_mode = ssl.CERT_NONE
    return context, bytes.fromhex(pin)


async def _get_json(session: aiohttp.ClientSession, url: str):
    try:
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise InventoryError(f"Protect inventory request returned HTTP {response.status}")
            if response.content_type != "application/json":
                raise InventoryError("Protect inventory response is not JSON")
            body = await response.content.read(_MAX_RESPONSE + 1)
            if len(body) > _MAX_RESPONSE:
                raise InventoryError("Protect inventory response is too large")
            return _json(body)
    except (aiohttp.ClientError, TimeoutError, ssl.SSLError) as exc:
        raise InventoryError(f"Protect inventory transport failed ({type(exc).__name__})") from exc


async def fetch_inventory(host: str, *, api_key_file: Path, trust_file: Path,
                          cert_file: Path) -> dict:
    """Make only two fixed-path GETs after TLS pin validation, then return a preview."""
    try:
        address = ipaddress.ip_address(host)
        if (not isinstance(address, ipaddress.IPv4Address)
                or not any(address in network for network in _LOCAL_IPV4)):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise InventoryError("Inventory requires an explicit private IPv4 controller address") from exc
    try:
        key = _read_private(api_key_file, 4096).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise InventoryError("Invalid Protect integration API key file") from exc
    if not key or len(key) > 512 or any(ch.isspace() for ch in key):
        raise InventoryError("Invalid Protect integration API key file")
    context, pin = _trusted_web(host, trust_file, cert_file)
    connector = PinnedWebConnector(context, pin)
    authority = host if _INTEGRATION_PORT == 443 else f"{host}:{_INTEGRATION_PORT}"
    base = f"https://{authority}/proxy/protect/integration/v1"
    timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout,
                                    trust_env=False, headers={"X-API-Key": key,
                                                              "Accept": "application/json"}) as session:
        meta = await _get_json(session, base + "/meta/info")
        if (not isinstance(meta, dict)
                or meta.get("applicationVersion") not in _VALIDATED_VERSIONS):
            raise InventoryError("Protect version has no validated camera inventory contract")
        rows = await _get_json(session, base + "/cameras")
    cameras = parse_cameras(rows)
    return {"schema": "aikey-camera-preflight/1",
            "protect_version": meta["applicationVersion"],
            "fetched_at": int(time.time()), "source": "local_protect_integration_api",
            "processing_enabled": False,
            "summary": {"total": len(cameras),
                        "smart_event_candidates": sum(c.processing_class == "smart_event_candidate" for c in cameras),
                        "legacy_ingress_needed": sum(c.processing_class == "legacy_ingress_needed" for c in cameras),
                        "offline": sum(c.processing_class == "offline" for c in cameras)},
            "cameras": [camera.public() for camera in cameras]}
