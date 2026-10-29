"""Bound native hostname resolution without orphan threads or credential inheritance."""

import json
import math
import os
import socket
import subprocess
import sys
import threading
import time

from .provider_network import MAX_ADDRESSES, ProviderAddressError, validate_provider_host


class ProviderLookupError(socket.gaierror):
    def __init__(self):
        super().__init__("provider_dns_unavailable")


class ProviderDNSTimeout(TimeoutError):
    def __init__(self):
        super().__init__("provider_dns_deadline")


class ProviderResolverError(OSError):
    def __init__(self):
        super().__init__("provider_resolver_unavailable")


_SLOTS = threading.BoundedSemaphore(4)
_HELPER = """import json,socket,sys
host,port=json.load(sys.stdin)
try:
 rows=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM,proto=socket.IPPROTO_TCP)
 if not rows or len(rows)>64:
  print(json.dumps({'policy':True}))
 else:
  rows=[[int(f),int(k),int(p),'',list(a)] for f,k,p,_,a in rows]
  text=json.dumps({'rows':rows},separators=(',',':'))
  print(text if len(text)<=32768 else '{"policy":true}')
except OSError:
 print('{"unavailable":true}')
"""


def resolve_addresses(host: str, port: int, timeout: float):
    """One cancellable native getaddrinfo subprocess; no HTTP, keys or retries.

    The caller retains the all-answer address decision and numeric connection.
    Child admission, startup and resolution consume this one remaining budget.
    """
    validate_provider_host(host)
    try:
        valid = (type(timeout) in (int, float) and math.isfinite(timeout)
                 and 0 < timeout <= threading.TIMEOUT_MAX
                 and type(port) is int and 1 <= port <= 65535)
    except OverflowError:
        valid = False
    if not valid:
        raise ProviderAddressError()
    deadline = time.monotonic() + timeout
    if not _SLOTS.acquire(timeout=timeout):
        raise ProviderDNSTimeout()
    process = None
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderDNSTimeout()
        # Isolated stdlib only. Do not inherit application/provider/proxy keys,
        # Python hooks, resolver overrides, controller state or arbitrary env.
        process = subprocess.Popen([sys.executable, "-I", "-S", "-c", _HELPER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"}, close_fds=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderDNSTimeout()
        try:
            output, _ = process.communicate(json.dumps([host, port]).encode(), timeout=remaining)
        except subprocess.TimeoutExpired:
            raise ProviderDNSTimeout() from None
        if time.monotonic() >= deadline:
            raise ProviderDNSTimeout()
        if process.returncode or not 0 < len(output) <= 32769:
            raise ProviderResolverError()
        try:
            value = json.loads(output)
            if value == {"unavailable": True}:
                raise ProviderLookupError()
            if value == {"policy": True}:
                raise ProviderAddressError()
            rows = value["rows"]
            if set(value) != {"rows"} or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ADDRESSES:
                raise ValueError
            peers = []
            for row in rows:
                if (not isinstance(row, list) or len(row) != 5
                        or not all(type(v) is int for v in row[:3])
                        or row[0] not in (socket.AF_INET, socket.AF_INET6)
                        or row[1:3] != [socket.SOCK_STREAM, socket.IPPROTO_TCP]
                        or row[3] != "" or not isinstance(row[4], list)
                        or len(row[4]) != (2 if row[0] == socket.AF_INET else 4)
                        or not isinstance(row[4][0], str) or len(row[4][0]) > 64
                        or type(row[4][1]) is not int or row[4][1] != port
                        or len(row[4]) == 4 and (row[4][2:] != [0, 0]
                                                 or not all(type(v) is int for v in row[4][2:]))):
                    raise ValueError
                peers.append((*row[:4], tuple(row[4])))
            return tuple(peers)
        except (ValueError, TypeError, KeyError, IndexError):
            raise ProviderAddressError() from None
    except OSError as error:
        if isinstance(error, (ProviderLookupError, ProviderDNSTimeout, ProviderResolverError,
                              ProviderAddressError)):
            raise
        raise ProviderResolverError() from None
    finally:
        try:
            if process is not None:
                try:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                finally:
                    for stream in (process.stdin, process.stdout):
                        if stream is not None:
                            stream.close()
        finally:
            _SLOTS.release()
