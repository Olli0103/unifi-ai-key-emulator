"""Address policy for async model clients, separate from controller trust.

Private backends use explicit unicast IPs or localhost. Other DNS names may
resolve only to global unicast addresses. The checked resolution records are
the records aiohttp connects to; there is no validation-then-second-lookup.
"""

from __future__ import annotations

import ipaddress
import re

import aiohttp

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_DNS = re.compile(r"(?=.{1,253}\Z)" + _LABEL + r"(?:\." + _LABEL + r")*\.?\Z")
MAX_ADDRESSES = 64
_PRIVATE = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"))
_TRANSLATION = tuple(ipaddress.ip_network(value) for value in (
    "64:ff9b::/96", "64:ff9b:1::/48"))


class ProviderAddressError(aiohttp.ClientConnectionError):
    """Fixed diagnostic: never include a hostname, address, URL or credential."""

    def __init__(self):
        super().__init__("provider_address_policy_refused")


def validate_provider_host(host: str, *, authority: str | None = None) -> None:
    """Require an explicit IP or ASCII DNS name (IDNs use their A-label form).

    This check happens before URL canonicalization can turn compatibility
    Unicode into localhost or a private IP. It performs no DNS or other I/O.
    """
    if (not isinstance(host, str) or not host.isascii() or "%" in host
            or authority is not None and not authority.isascii()):
        raise ProviderAddressError()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _DNS.fullmatch(host) or re.fullmatch(r"[0-9.]+", host):
            raise ProviderAddressError() from None


def _address(value):
    if not isinstance(value, str) or "%" in value:
        raise ProviderAddressError()
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ProviderAddressError() from None
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _unicast(address) -> bool:
    if address.is_unspecified or address.is_multicast or address.is_link_local:
        return False
    if address.is_reserved and not address.is_loopback:
        return False
    if address.version == 6 and (address.sixtofour is not None or address.teredo is not None
                                or any(address in net for net in _TRANSLATION)):
        return False
    return (address.is_loopback or address.is_global
            or any(address.version == net.version and address in net for net in _PRIVATE))


class ProviderConnector(aiohttp.TCPConnector):
    async def _resolve_host(self, host, port, traces=None):
        validate_provider_host(host)
        try:
            literal = _address(host)
        except ProviderAddressError:
            literal = None
        try:
            records = await super()._resolve_host(host, port, traces)
        except (OSError, RuntimeError):
            # Includes a resolver returning an empty cached answer. Do not
            # expose resolver diagnostics or turn cancellation into a retry.
            raise ProviderAddressError() from None
        if not records or len(records) > MAX_ADDRESSES:
            raise ProviderAddressError()
        checked = []
        for record in records:
            address = _address(record.get("host"))
            if (record.get("port") != port or record.get("hostname") != host
                    or not _unicast(address)
                    or (literal is not None and address != literal)
                    or (literal is None and not (
                        address.is_loopback if host.lower().rstrip(".") == "localhost"
                        else address.is_global))):
                raise ProviderAddressError()
            checked.append(dict(record))
        return checked
