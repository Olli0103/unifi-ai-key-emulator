"""Stdlib model transport: no proxies/redirects or validation/connection DNS race."""

import http.client
import io
import ipaddress
import math
import socket
import time
from urllib.request import (BaseHandler, HTTPHandler, HTTPSHandler, HTTPRedirectHandler,
                            ProxyHandler, build_opener)

from .provider_network import ProviderAddressError, checked_addresses, validate_provider_host
from .provider_dns import ProviderLookupError, ProviderDNSTimeout, ProviderResolverError, resolve_addresses


def _deadline(timeout):
    try:
        valid = type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ProviderAddressError()
    return time.monotonic() + timeout


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("provider_request_unavailable")
    return remaining


class _DeadlineReader(io.RawIOBase):
    """A fresh *remaining* budget for each raw receive, including header lines."""

    def __init__(self, raw, connection, deadline):
        super().__init__()
        self.raw, self.connection, self.deadline = raw, connection, deadline

    def readable(self):
        return True

    def fileno(self):
        return self.raw.fileno()

    def readinto(self, target):
        self.connection.settimeout(_remaining(self.deadline))
        count = self.raw.readinto(target)
        _remaining(self.deadline)
        return count

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class _DeadlineSocket:
    """Preserve socket ownership/makefile refs; never reconnect or renew a budget."""

    def __init__(self, connection, deadline):
        self.connection, self.deadline = connection, deadline

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def sendall(self, data, *args, **kwargs):
        self.connection.settimeout(_remaining(self.deadline))
        result = self.connection.sendall(data, *args, **kwargs)
        _remaining(self.deadline)
        return result

    def makefile(self, mode, *args, **kwargs):
        if mode != "rb" or args or kwargs:
            raise ValueError("provider_response_stream_invalid")
        _remaining(self.deadline)
        raw = self.connection.makefile("rb", buffering=0)
        return io.BufferedReader(_DeadlineReader(raw, self.connection, self.deadline))


class _DeadlineResponse(http.client.HTTPResponse):
    def __init__(self, connection, *args, **kwargs):
        self._provider_deadline = connection.deadline
        self._provider_connection = connection
        super().__init__(connection, *args, **kwargs)

    def _bounded(self, method, *args):
        try:
            _remaining(self._provider_deadline)
            result = method(*args)
            _remaining(self._provider_deadline)
            return result
        except BaseException:
            try:
                self.close()
            finally:
                self._provider_connection.close()
            raise

    def begin(self):
        return self._bounded(super().begin)

    def read(self, amt=None):
        return self._bounded(super().read, amt)

    def read1(self, n=-1):
        return self._bounded(super().read1, n)

    def readinto(self, target):
        return self._bounded(super().readinto, target)

    def readline(self, limit=-1):
        return self._bounded(super().readline, limit)

    def peek(self, n=-1):
        return self._bounded(super().peek, n)


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
            peers = resolve_addresses(host, port, _remaining(deadline))
        except (ProviderDNSTimeout, ProviderResolverError):
            raise
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


class _RequestBudget:
    response_class = _DeadlineResponse

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect
        self._provider_deadline = None

    def request(self, *args, **kwargs):
        self._provider_deadline = _deadline(self.timeout)
        if isinstance(self.sock, _DeadlineSocket):
            self.sock.deadline = self._provider_deadline
        try:
            return super().request(*args, **kwargs)
        except BaseException:
            self.close()
            raise

    def connect(self):
        if self._tunnel_host:
            raise ProviderAddressError()
        if self._provider_deadline is None:
            self._provider_deadline = _deadline(self.timeout)
        original_timeout = self.timeout
        try:
            self.timeout = _remaining(self._provider_deadline)
            super().connect()
            self.sock.settimeout(_remaining(self._provider_deadline))
            self.sock = _DeadlineSocket(self.sock, self._provider_deadline)
        except BaseException:
            self.close()
            raise
        finally:
            self.timeout = original_timeout


class ProviderHTTPConnection(_RequestBudget, http.client.HTTPConnection):
    pass


class ProviderHTTPSConnection(_RequestBudget, http.client.HTTPSConnection):
    pass


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
