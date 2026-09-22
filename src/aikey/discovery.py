"""Read-only subset of the version-1 discovery contract in AI Key ubntbox.

Only empty information queries and a query targeting our configured MAC are
answered. No mutation opcode, credential, shell command, or device inventory is
accepted. Real LAN operation requires explicit enablement on Linux.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import ipaddress
import logging
import re
import socket
import struct
import sys
import time

from .protocol import ContractError


DISCOVERY_PORT = 10001
DISCOVERY_GROUP = "233.89.188.1"
_HEADER = struct.Struct(">BBH")
_TLV = struct.Struct(">BH")


def _mac(value: str) -> bytes:
    if not isinstance(value, str):
        raise ContractError("Discovery MAC must be a string")
    value = value.replace(":", "").replace("-", "")
    if not re.fullmatch(r"[0-9A-Fa-f]{12}", value):
        raise ContractError("Invalid discovery MAC")
    result = bytes.fromhex(value)
    if result[0] & 1 or result == b"\0" * 6:
        raise ContractError("Discovery MAC must be a nonzero unicast identity")
    return result


def parse_discovery_query(wire: bytes, mac: str) -> int | None:
    """Return the observed query command, or None for unsupported/malformed data."""
    if not isinstance(wire, bytes) or len(wire) < 4 or len(wire) > 64:
        return None
    version, command, length = _HEADER.unpack_from(wire)
    if version != 1 or len(wire) != 4 + length:
        return None
    if command == 0 and length == 0:
        return command
    if command == 4 and length == 6 and wire[4:] == _mac(mac):
        return command
    return None


def _string(value: str, label: str, limit: int) -> bytes:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ContractError(f"Invalid discovery {label}")
    raw = value.encode("utf-8")
    if len(raw) > limit:
        raise ContractError(f"Discovery {label} is too long")
    return raw


def build_discovery_response(info: dict, *, ip: str, hostname: str, adopted: bool,
                             command: int = 0, platform: str | None = None) -> bytes:
    """Encode only fields traced in ubntbox 0.1.7's fill_info_response.

    sysid uses the firmware's little-endian 16-bit field. Header/TLV lengths,
    uptime and the factory-default flag are big-endian. Platform is configurable
    because firmware can override its board platform through custom.platform.
    """
    if command not in (0, 4) or type(adopted) is not bool:
        raise ContractError("Unsupported discovery response")
    mac = _mac(info.get("mac"))
    address = ipaddress.IPv4Address(ip)
    if address.is_unspecified or address.is_multicast:
        raise ContractError("Discovery requires a concrete unicast device IPv4")
    uptime = info.get("uptime")
    if type(uptime) is not int or not 0 <= uptime <= 0xFFFFFFFF:
        raise ContractError("Invalid discovery uptime")
    try:
        sysid = int(info["sysid"], 16)
    except (KeyError, ValueError, TypeError) as exc:
        raise ContractError("Invalid discovery sysid") from exc
    if not 0 <= sysid <= 65535:
        raise ContractError("Discovery sysid is not 16 bits")
    fields = [
        (0x02, mac + address.packed),
        (0x01, mac),
        (0x0A, struct.pack(">I", uptime)),
        (0x0B, _string(hostname, "hostname", 63)),
        (0x0C, _string(platform or info.get("type"), "platform", 63)),
        (0x17, struct.pack(">I", int(not adopted))),
        (0x03, _string(info.get("version"), "version", 127)),
        (0x10, struct.pack("<H", sysid)),
    ]
    payload = b"".join(_TLV.pack(kind, len(value)) + value for kind, value in fields)
    if len(payload) > 1396:
        raise ContractError("Discovery response exceeds native buffer size")
    return _HEADER.pack(1, command, len(payload)) + payload


class DiscoveryService(asyncio.DatagramProtocol):
    """Opt-in UDP responder, scoped to explicit controller IPv4 addresses.

    Configuration lives in config['discovery']. The caller supplies current
    getInfo/adoption state; start() does nothing while enabled is false.
    """

    def __init__(self, config: dict, info_provider: Callable[[], dict],
                 adopted_provider: Callable[[], bool], logger=None):
        self.config = config
        self.settings = config.get("discovery", {})
        self.info_provider = info_provider
        self.adopted_provider = adopted_provider
        self.log = logger or logging.getLogger(__name__)
        self.transport: asyncio.DatagramTransport | None = None
        self._closed: asyncio.Future | None = None
        self._last_response: dict[str, float] = {}
        self._allowed: set[str] = set()
        self.responses = 0
        self.rejected = 0

    @property
    def status(self) -> dict:
        return {"enabled": bool(self.settings.get("enabled", False)), "listening": self.transport is not None,
                "profile": "ubntbox-0.1.7-v1-info", "responses": self.responses, "rejected": self.rejected,
                "controller_acceptance": "needs_evidence"}

    async def start(self) -> None:
        if not self.settings.get("enabled", False) or self.transport is not None:
            return
        mode = self.config.get("runtime", {}).get("mode", "lab")
        if mode not in {"lab", "device"}:
            raise ValueError("Unknown discovery runtime mode")
        bind = self.settings.get("bind", "127.0.0.1")
        bind_ip = ipaddress.IPv4Address(bind)
        if mode == "lab" and not bind_ip.is_loopback:
            raise ValueError("Lab discovery is restricted to loopback")
        if not bind_ip.is_loopback and sys.platform != "linux":
            raise ValueError("Real LAN discovery is enabled only on Linux")
        peer_values = self.settings.get("allowed_controller_ips")
        if peer_values is None:
            peer_values = [self.config.get("controller", {}).get("host", "127.0.0.1")]
        if not isinstance(peer_values, list) or not 1 <= len(peer_values) <= 16:
            raise ValueError("Specify 1-16 allowed controller IPv4 addresses")
        self._allowed = {str(ipaddress.IPv4Address(value)) for value in peer_values}
        if mode == "lab" and any(not ipaddress.IPv4Address(ip).is_loopback for ip in self._allowed):
            raise ValueError("Lab controller peers must be loopback")
        if any(ipaddress.IPv4Address(ip).is_multicast or ipaddress.IPv4Address(ip).is_unspecified for ip in self._allowed):
            raise ValueError("Controller peers must be concrete unicast IPv4 addresses")
        port = self.settings.get("port", DISCOVERY_PORT)
        if type(port) is not int or not 0 <= port <= 65535 or (port == 0 and mode != "lab"):
            raise ValueError("Invalid discovery port")
        multicast = self.settings.get("multicast", False)
        if multicast and mode != "device":
            raise ValueError("Multicast discovery requires device mode")
        # Validate response data before opening any socket.
        self._response(0)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setblocking(False)
            sock.bind((str(bind_ip), port))
            if multicast:
                interface = ipaddress.IPv4Address(self.settings.get("interface_ip", self.config["device"]["ip"]))
                membership = socket.inet_aton(DISCOVERY_GROUP) + interface.packed
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            self._closed = asyncio.get_running_loop().create_future()
            await asyncio.get_running_loop().create_datagram_endpoint(lambda: self, sock=sock)
        except BaseException:
            sock.close()
            raise

    async def stop(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None
            if self._closed is not None:
                await self._closed

    def connection_made(self, transport):
        self.transport = transport

    def connection_lost(self, exc):
        self.transport = None
        if self._closed is not None and not self._closed.done():
            self._closed.set_result(None)

    def _response(self, command: int) -> bytes:
        device = self.config["device"]
        return build_discovery_response(self.info_provider(), ip=device["ip"],
                                        hostname=device.get("name", "local-aikey"),
                                        adopted=self.adopted_provider(), command=command,
                                        platform=self.settings.get("platform"))

    def datagram_received(self, data, addr):
        if self.transport is None or addr[0] not in self._allowed:
            self.rejected += 1
            return
        command = parse_discovery_query(data, self.config["device"]["mac"])
        if command is None:
            self.rejected += 1
            return
        now = time.monotonic()
        if now - self._last_response.get(addr[0], -10) < .25:
            self.rejected += 1
            return
        self._last_response[addr[0]] = now
        try:
            self.transport.sendto(self._response(command), addr)
            self.responses += 1
        except (ContractError, ValueError) as exc:
            self.log.warning("Discovery response rejected (%s)", type(exc).__name__)
            self.rejected += 1

    def error_received(self, exc):
        self.log.warning("Discovery socket error (%s)", type(exc).__name__)
