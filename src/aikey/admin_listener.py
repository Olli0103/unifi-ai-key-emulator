"""Explicit direct-TLS administration; never infer an origin from proxy headers."""

from contextlib import ExitStack
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import ssl
import stat
from urllib.parse import urlsplit


_PRIVATE_BINDS = tuple(ipaddress.ip_network(value) for value in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "100.64.0.0/10", "fc00::/7",
))


@dataclass(frozen=True)
class AdminListener:
    host: str
    ssl_context: ssl.SSLContext | None

    @property
    def loopback_only(self) -> bool:
        return ipaddress.ip_address(self.host).is_loopback


def _no_password() -> str:
    raise ValueError("Encrypted administrator TLS keys are not supported")


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
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
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
    return AdminListener(str(address), context)
