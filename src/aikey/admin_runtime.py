"""Bounded read-only observations from explicitly configured own runtime peers.

No inference, discovery, caller-selected URL, credential or controller request.
CA validation plus the profile's own public leaf pin precede HTTP headers.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
from pathlib import Path
import ssl
import time
from urllib.parse import urlsplit

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from .config_store import ConfigurationStore, ConfigurationStoreError
from .device import VerifiedConnector
from .protocol import _json_object


_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "100.64.0.0/10", "fc00::/7"))
_MAX_BYTES = 256 * 1024


def _target(value):
    try:
        if (not isinstance(value, str) or len(value) > 256
                or any(c.isspace() for c in value) or any(c in value for c in "\\%?#")):
            raise ValueError
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        if (parsed.scheme != "https" or parsed.path != "/healthz" or parsed.query or parsed.fragment
                or parsed.username is not None or parsed.password is not None
                or not 1 <= (parsed.port or 443) <= 65535 or address.is_unspecified
                or not any(address in n for n in _NETWORKS)):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Runtime health requires a literal private HTTPS healthz target") from None
    return value


def _flag(value, name):
    result = value.get(name)
    if result is not None and type(result) is not bool:
        raise ValueError("Invalid runtime flag")
    return result


def _count(value, name):
    result = value.get(name)
    if result is not None and (type(result) is not int or not 0 <= result <= 2 ** 63 - 1):
        raise ValueError("Invalid runtime counter")
    return result


def _object(value, name):
    result = value.get(name, {})
    if not isinstance(result, dict):
        raise ValueError("Invalid runtime namespace")
    return result


def _normalize(kind, value):
    expected = "local-aikey" if kind == "aikey" else "aiport-candidate"
    if value.get("service") != expected:
        raise ValueError("Unexpected runtime service")
    if kind == "aikey":
        device, worker, search = (_object(value, key) for key in ("device", "worker", "search"))
        result = {"adopted": _flag(device, "adopted"), "connected": _flag(device, "connected"),
                  "search_connected": _flag(search, "connected")}
        result.update({name: _count(worker, name) for name in ("queued", "active", "pending", "ledger", "capacity")})
    else:
        result = {name: _flag(value, name) for name in ("adopted", "control_connected", "adoption_pending")}
        result.update({name: _count(value, name) for name in (
            "active_streams", "streams_with_decoded_frames", "stream_frames_decoded_total",
            "detector_frames_attempted", "detector_frames_succeeded", "snapshot_uploads",
            "live_event_budget_remaining")})
        result["live_event_budget_healthy"] = _flag(value, "live_event_budget_healthy")
    if result["adopted"] is None:
        raise ValueError("Runtime adoption flag is unavailable")
    return result


def _trust(directory):
    pem = ConfigurationStore._read_regular_bytes(Path(directory) / "device.crt", 262144)
    certificate = x509.load_pem_x509_certificate(pem)
    der = certificate.public_bytes(serialization.Encoding.DER)
    context = ssl.create_default_context(cadata=pem.decode("ascii"))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False  # The exact configured profile leaf is pinned too.
    context.verify_mode = ssl.CERT_REQUIRED
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    return context, hashlib.sha256(der).hexdigest()


class RuntimeObserver:
    def __init__(self, profiles: dict, targets: dict):
        if (len(profiles) > 32 or set(targets) - set(profiles)
                or any(kind not in {"aikey", "aiport"} for kind, _ in profiles.values())):
            raise ValueError("Runtime health profiles are invalid")
        self.profiles = dict(profiles)
        self.targets = {profile: _target(value) for profile, value in targets.items()}
        self.locks = {profile: asyncio.Lock() for profile in profiles}
        self.connections = asyncio.Semaphore(2)
        self.cache = {}

    @staticmethod
    def unavailable():
        return {"observation": "unavailable", "runtime": None,
                "runtime_configuration_revision": None}

    async def _fetch(self, profile):
        kind, directory = self.profiles[profile]
        context, fingerprint = await asyncio.to_thread(_trust, directory)
        connector = VerifiedConnector(ssl_context=context, expected_fingerprint=fingerprint)
        async with aiohttp.ClientSession(connector=connector, trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
                timeout=aiohttp.ClientTimeout(total=3, connect=2),
                headers={"Accept": "application/json", "Accept-Encoding": "identity"}) as session:
            async with session.get(self.targets[profile], allow_redirects=False) as response:
                if (response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity"
                        or response.content_type != "application/json"):
                    raise ValueError("Runtime reply refused")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    if len(raw) > _MAX_BYTES:
                        raise ValueError("Runtime reply exceeds byte limit")
        runtime = _normalize(kind, _json_object(bytes(raw)))
        return {"observation": "runtime_health", "runtime": runtime,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "tls_ca_and_leaf_pin_verified": True, "runtime_configuration_revision": None}

    async def read(self, profile):
        if profile not in self.profiles:
            raise ValueError("Unknown runtime profile")
        if profile not in self.targets:
            return {"observation": "not_configured", "runtime": None,
                    "runtime_configuration_revision": None}
        try:
            async with asyncio.timeout(4):
                async with self.locks[profile]:
                    cached = self.cache.get(profile)
                    if cached and cached[0] > time.monotonic():
                        return deepcopy(cached[1])
                    try:
                        async with self.connections:
                            result = await self._fetch(profile)
                    except (aiohttp.ClientError, OSError, ValueError, ConfigurationStoreError, asyncio.TimeoutError):
                        result = self.unavailable()
                    self.cache[profile] = (time.monotonic() + 2, result)
                    return deepcopy(result)
        except asyncio.TimeoutError:
            return self.unavailable()
