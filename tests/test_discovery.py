import asyncio
from copy import deepcopy
import socket
import struct

import pytest

from aikey.discovery import DiscoveryService, build_discovery_response, parse_discovery_query
from aikey.protocol import ContractError


INFO = {"type": "UP-AI-KEY", "mac": "020000000001", "sysid": "0xa5f0", "version": "2.2.8", "uptime": 7}
CONFIG = {"runtime": {"mode": "lab"}, "device": {"mac": INFO["mac"], "ip": "127.0.0.1", "name": "lab"},
          "controller": {"host": "127.0.0.1"}, "discovery": {"enabled": True, "bind": "127.0.0.1", "port": 0}}


def test_query_parser_accepts_only_observed_read_only_opcodes():
    assert parse_discovery_query(bytes.fromhex("01000000"), INFO["mac"]) == 0
    assert parse_discovery_query(bytes.fromhex("01040006020000000001"), INFO["mac"]) == 4
    for raw in ("", "0100", "02000000", "01010000", "01020000", "0100000100", "01040006020000000002", "0100000000"):
        assert parse_discovery_query(bytes.fromhex(raw), INFO["mac"]) is None


def test_discovery_encoder_matches_static_byte_contract():
    wire = build_discovery_response(INFO, ip="192.0.2.5", hostname="lab", adopted=False)
    # Independent explicit fixture from native builder types and endian stores.
    payload = bytes.fromhex(
        "02000a020000000001c0000205"  # interface MAC + IPv4
        "010006020000000001"          # identity MAC
        "0a000400000007"              # BE uptime
        "0b00036c6162"                # hostname
        "0c000955502d41492d4b4559"    # platform
        "17000400000001"              # unadopted/default
        "030005322e322e38"            # version
        "100002f0a5"                  # LE sysid
    )
    assert wire == b"\x01\x00" + struct.pack(">H", len(payload)) + payload
    adopted = build_discovery_response(INFO, ip="192.0.2.5", hostname="lab", adopted=True, command=4)
    assert adopted[1] == 4
    assert bytes.fromhex("17000400000000") in adopted


def test_discovery_rejects_nonidentity_addresses_and_oversized_names():
    with pytest.raises(ContractError):
        build_discovery_response(INFO, ip="0.0.0.0", hostname="lab", adopted=False)
    with pytest.raises(ContractError):
        build_discovery_response(INFO, ip="127.0.0.1", hostname="x" * 100, adopted=False)
    with pytest.raises(ContractError):
        build_discovery_response({**INFO, "mac": "010000000001"}, ip="127.0.0.1", hostname="lab", adopted=False)


async def test_disabled_discovery_opens_no_socket():
    config = deepcopy(CONFIG)
    config["discovery"]["enabled"] = False
    service = DiscoveryService(config, lambda: INFO, lambda: False)
    await service.start()
    assert not service.status["listening"]
    await service.stop()


async def test_lab_discovery_cannot_bind_lan():
    config = deepcopy(CONFIG)
    config["discovery"]["bind"] = "0.0.0.0"
    service = DiscoveryService(config, lambda: INFO, lambda: False)
    with pytest.raises(ValueError, match="loopback"):
        await service.start()


async def test_real_loopback_udp_query_reply_and_mutation_silence():
    service = DiscoveryService(deepcopy(CONFIG), lambda: INFO, lambda: False)
    await service.start()
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.setblocking(False)
    peer.bind(("127.0.0.1", 0))
    try:
        address = service.transport.get_extra_info("sockname")
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(peer, bytes.fromhex("01000000"), address)
        reply, _ = await asyncio.wait_for(loop.sock_recvfrom(peer, 1400), 1)
        assert reply == build_discovery_response(INFO, ip="127.0.0.1", hostname="lab", adopted=False)
        await loop.sock_sendto(peer, bytes.fromhex("01020000"), address)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(loop.sock_recvfrom(peer, 1400), .05)
        assert service.responses == 1 and service.rejected == 1
    finally:
        peer.close()
        await service.stop()
