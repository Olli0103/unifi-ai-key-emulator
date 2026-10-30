"""Explicit direct-TLS administration; never infer an origin from proxy headers."""

from __future__ import annotations

from contextlib import ExitStack
from collections.abc import Callable
from dataclasses import dataclass, field
import ipaddress
import os
from pathlib import Path
import ssl
import stat
import threading
from urllib.parse import urlsplit


_PRIVATE_BINDS = tuple(ipaddress.ip_network(value) for value in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "100.64.0.0/10", "fc00::/7",
))


@dataclass(frozen=True)
class AdminListener:
    host: str
    ssl_context: ssl.SSLContext | None
    _contexts: _ReloadableContexts | None = field(default=None, repr=False, compare=False)

    @property
    def loopback_only(self) -> bool:
        return ipaddress.ip_address(self.host).is_loopback

    @property
    def tls_generation(self) -> int:
        return self._contexts.generation if self._contexts is not None else 0

    def reload_tls(self) -> bool:
        """Publish a fully loaded context for new handshakes, not active sessions."""
        if self._contexts is None:
            return False
        self._contexts.reload()
        return True


class _ReloadableContexts:
    def __init__(self, load: Callable[[], ssl.SSLContext], initial: ssl.SSLContext):
        self._load = load
        self.current = initial
        self.generation = 0
        self._lock = threading.Lock()

    def reload(self) -> None:
        with self._lock:
            candidate = self._load()
            self.current = candidate
            self.generation += 1

    def select(self, connection, _server_name, _initial_context) -> None:
        # Also called for an absent SNI extension. No peer-provided name becomes
        # a path and no disk work takes place during a handshake.
        connection.context = self.current


def _no_password() -> str:
    raise ValueError("Encrypted administrator TLS keys are not supported")


def _server_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _load_context(certificate: Path, private_key: Path) -> ssl.SSLContext:
    context = _server_context()
    # Keep validated descriptors open while OpenSSL reads /dev/fd on Mac/Linux.
    # It cannot re-open a replaced/symlinked input pathname; no key copy or
    # interactive password prompt is needed.
    with ExitStack() as stack:
        descriptors = []
        for path, limit in ((certificate, 262144), (private_key, 65536)):
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, descriptor)
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or not 0 < info.st_size <= limit):
                raise ValueError("Administrator TLS inputs must be private owned regular files")
            descriptors.append(f"/dev/fd/{descriptor}")
        context.load_cert_chain(*descriptors, password=_no_password)
    return context


def configure_listener(port: int, bind: str, canonical_origin: str,
                       certificate: Path | None, private_key: Path | None) -> AdminListener:
    address = ipaddress.ip_address(bind)
    if not any(address in network for network in _PRIVATE_BINDS):
        raise ValueError("Bind administration to one explicit private or loopback address")
    origin = urlsplit(canonical_origin)
    if (origin.port or (443 if origin.scheme == "https" else 80)) != port:
        raise ValueError("Administrator origin and listener ports must agree")
    if certificate is None and private_key is None:
        if bind != "127.0.0.1" or canonical_origin != f"http://127.0.0.1:{port}":
            raise ValueError("Non-default administration requires direct HTTPS")
        return AdminListener(bind, None)
    if certificate is None or private_key is None or origin.scheme != "https":
        raise ValueError("HTTPS needs an explicit origin, certificate and private key")
    contexts = _ReloadableContexts(lambda: _load_context(certificate, private_key),
                                   _load_context(certificate, private_key))
    primary = _server_context()
    primary.sni_callback = contexts.select
    return AdminListener(str(address), primary, contexts)
