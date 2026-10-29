"""Stdlib model transport: no proxies/redirects or validation/connection DNS race."""

import http.client
import ipaddress
import math
import socket
import time
from urllib.request import (BaseHandler, HTTPHandler, HTTPSHandler, HTTPRedirectHandler,
                            ProxyHandler, build_opener)

from .provider_network import ProviderAddressError, checked_addresses, validate_provider_host


class ProviderLookupError(socket.gaierror):
    def __init__(self):
        # Preserve a fixed DNS-outage subtype without resolver payloads.
        super().__init__("provider_dns_unavailable")


def _deadline(timeout):
    try:
        valid = type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ProviderAddressError()
    return time.monotonic() + timeout


def _connect(address, timeout, source_address=None):
    host, port = address
    validate_provider_host(host)
    deadline = _deadline(timeout)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        peers = [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '',
                  (host, port, 0, 0) if literal.version == 6 else (host, port))]
    else:
        try:
            peers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        except OSError:
            raise ProviderLookupError() from None
    peers = tuple(peers)
    records = []
    for family, kind, proto, _, peer in peers:
        if (family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM
                or proto != socket.IPPROTO_TCP or len(peer) not in (2, 4)
                or len(peer) == 4 and (peer[2] != 0 or peer[3] != 0)):
            raise ProviderAddressError()
        records.append({'hostname': host, 'host': peer[0], 'port': peer[1]})
    checked_addresses(host, port, records)
    # No create_connection(hostname): connect to the exact checked numeric
    # sockaddr. One remaining budget covers all candidate connection attempts.
    for family, kind, proto, _, peer in peers:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("provider_connection_unavailable")
        connection = socket.socket(family, kind, proto)
        try:
            connection.settimeout(remaining)
            if source_address:
                connection.bind(source_address)
            connection.connect(peer)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("provider_connection_unavailable")
            connection.settimeout(remaining)
            return connection
        except OSError:
            connection.close()
        except BaseException:
            connection.close()
            raise
    raise OSError("provider_connection_unavailable")


class ProviderHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect

    def connect(self):
        if self._tunnel_host:
            raise ProviderAddressError()
        deadline = _deadline(self.timeout)
        try:
            super().connect()
            if time.monotonic() >= deadline:
                raise TimeoutError("provider_connection_unavailable")
        except BaseException:
            self.close()
            raise


class ProviderHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect

    def connect(self):
        if self._tunnel_host:
            raise ProviderAddressError()
        deadline = _deadline(self.timeout)
        try:
            super().connect()
            if time.monotonic() >= deadline:
                raise TimeoutError("provider_connection_unavailable")
        except BaseException:
            self.close()
            raise


class ProviderHTTPHandler(HTTPHandler):
    def http_open(self, request):
        return self.do_open(ProviderHTTPConnection, request)


class ProviderHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(ProviderHTTPSConnection, request, context=self._context)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class HTTPOnly(BaseHandler):
    def default_open(self, request):
        if request.type not in {'http', 'https'}:
            raise ProviderAddressError()


def provider_opener():
    return build_opener(HTTPOnly(), ProxyHandler({}), NoRedirect(),
                        ProviderHTTPHandler(), ProviderHTTPSHandler())
