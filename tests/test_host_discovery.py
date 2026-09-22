import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
import pytest

from aikey.discovery import build_discovery_response
from aikey.host_discovery import HostDiscoveryCompanion
from aikey.tls import ensure_identity_certificate, server_context


INFO = {
    "type": "UP-AI-KEY", "mac": "020000000001", "sysid": "0xa5f0",
    "version": "2.2.8", "uptime": 7,
}
QUERY = bytes.fromhex("01000000")


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class RecordingTransport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, address):
        self.sent.append((data, address))

    def close(self):
        pass


@asynccontextmanager
async def https_device(tmp_path):
    state_dir = tmp_path / "state"
    ensure_identity_certificate(state_dir, INFO["mac"])
    fixture = SimpleNamespace(
        state_dir=state_dir, info=deepcopy(INFO),
        health={"service": "local-aikey", "device": {"adopted": False}},
        info_status=200, health_status=200, redirect=None, fragmented=None,
        requests=[], redirect_visits=0,
    )

    async def get(request):
        fixture.requests.append((request.method, request.path, dict(request.headers)))
        if request.path == fixture.redirect:
            raise web.HTTPFound("/redirect-target")
        if request.path == fixture.fragmented:
            payload = json.dumps(
                fixture.info if request.path == "/api/info" else fixture.health).encode()
            response = web.StreamResponse(headers={"Content-Type": "application/json"})
            await response.prepare(request)
            halfway = len(payload) // 2
            await response.write(payload[:halfway])
            await asyncio.sleep(0.01)
            await response.write(payload[halfway:])
            await response.write_eof()
            return response
        if request.path == "/api/info":
            return web.json_response(fixture.info, status=fixture.info_status)
        return web.json_response(fixture.health, status=fixture.health_status)

    async def redirect_target(request):
        fixture.redirect_visits += 1
        return web.json_response(fixture.info)

    app = web.Application()
    app.router.add_get("/api/info", get)
    app.router.add_get("/healthz", get)
    app.router.add_get("/redirect-target", redirect_target)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_context(state_dir))
    await site.start()
    fixture.port = site._server.sockets[0].getsockname()[1]
    fixture.config = {
        "runtime": {"mode": "lab", "https_port": fixture.port,
                    "bind": "127.0.0.1", "state_dir": str(state_dir)},
        "device": {"ip": "127.0.0.1", "mac": INFO["mac"], "name": "lab"},
        "controller": {"host": "127.0.0.1"},
        "discovery": {"enabled": True, "bind": "127.0.0.1", "port": 0},
    }
    try:
        yield fixture
    finally:
        await runner.cleanup()
        await asyncio.sleep(0)


def probe(companion):
    """Exercise the UDP receive path without sending any network datagram."""
    discovery = companion.discovery
    transport = RecordingTransport()
    discovery.transport = transport
    discovery._allowed = {"127.0.0.1"}
    discovery._last_response.clear()
    discovery.datagram_received(QUERY, ("127.0.0.1", 12345))
    discovery.transport = None
    return transport.sent


async def test_refresh_uses_pinned_https_gets_and_live_adoption_state(tmp_path):
    async with https_device(tmp_path) as fixture:
        companion = HostDiscoveryCompanion(fixture.config, fixture.state_dir)
        try:
            assert companion.status["health_fresh"] is False
            assert probe(companion) == []
            assert await companion.refresh() is True
            assert companion.status["health_fresh"] is True
            assert companion.status["health_successes"] == 1
            assert probe(companion) == [(
                build_discovery_response(INFO, ip="127.0.0.1", hostname="lab", adopted=False),
                ("127.0.0.1", 12345),
            )]
            fixture.health["device"]["adopted"] = True
            fixture.info["uptime"] = 11
            assert await companion.refresh() is True
            assert probe(companion) == [(
                build_discovery_response(fixture.info, ip="127.0.0.1", hostname="lab", adopted=True),
                ("127.0.0.1", 12345),
            )]
            assert {path for _, path, _ in fixture.requests} == {"/api/info", "/healthz"}
            assert all(method == "GET" for method, _, _ in fixture.requests)
            assert all("Authorization" not in headers for _, _, headers in fixture.requests)
        finally:
            await companion.stop()


async def test_cached_health_expires_after_ten_seconds(tmp_path):
    async with https_device(tmp_path) as fixture:
        clock = Clock()
        companion = HostDiscoveryCompanion(fixture.config, fixture.state_dir, clock=clock)
        try:
            assert await companion.refresh() is True
            clock.now += 9.99
            assert len(probe(companion)) == 1
            clock.now += 0.02
            assert companion.status["health_fresh"] is False
            assert probe(companion) == []
            assert await companion.refresh() is True
            assert len(probe(companion)) == 1
        finally:
            await companion.stop()


@pytest.mark.parametrize("endpoint", ["info", "health"])
async def test_failed_poll_immediately_invalidates_previous_success(tmp_path, endpoint):
    async with https_device(tmp_path) as fixture:
        companion = HostDiscoveryCompanion(fixture.config, fixture.state_dir)
        try:
            assert await companion.refresh() is True
            assert len(probe(companion)) == 1
            setattr(fixture, f"{endpoint}_status", 503)
            assert await companion.refresh() is False
            assert companion.status["health_fresh"] is False
            assert companion.status["health_failures"] == 1
            assert companion.status["last_error"] == "ContractError"
            assert probe(companion) == []
            setattr(fixture, f"{endpoint}_status", 200)
            assert await companion.refresh() is True
            assert companion.status["last_error"] is None
            assert len(probe(companion)) == 1
        finally:
            await companion.stop()


async def test_wrong_certificate_pin_never_reaches_http_handler(tmp_path):
    async with https_device(tmp_path) as fixture:
        wrong_state = tmp_path / "wrong-identity"
        ensure_identity_certificate(wrong_state, INFO["mac"])
        companion = HostDiscoveryCompanion(fixture.config, wrong_state)
        try:
            assert await companion.refresh() is False
            assert fixture.requests == []
            assert probe(companion) == []
        finally:
            await companion.stop()


@pytest.mark.parametrize("endpoint", ["/api/info", "/healthz"])
async def test_https_redirects_are_not_followed(tmp_path, endpoint):
    async with https_device(tmp_path) as fixture:
        fixture.redirect = endpoint
        companion = HostDiscoveryCompanion(fixture.config, fixture.state_dir)
        try:
            assert await companion.refresh() is False
            assert fixture.redirect_visits == 0
            assert probe(companion) == []
        finally:
            await companion.stop()


async def test_info_mac_must_match_configured_identity(tmp_path):
    async with https_device(tmp_path) as fixture:
        fixture.info["mac"] = "020000000099"
        companion = HostDiscoveryCompanion(fixture.config, fixture.state_dir)
        try:
            assert await companion.refresh() is False
            assert probe(companion) == []
        finally:
            await companion.stop()


@pytest.mark.parametrize("health", [
    {},
    {"service": "unrelated", "device": {"adopted": False}},
    {"service": "local-aikey", "device": {}},
    {"service": "local-aikey", "device": {"adopted": "false"}},
    {"service": "local-aikey", "device": {"adopted": 0}},
    {"service": "local-aikey", "device": {"adopted": None}},
    {"service": "local-aikey", "device": []},
])
async def test_health_requires_service_identity_and_a_real_adoption_boolean(tmp_path, health):
    async with https_device(tmp_path) as fixture:
        fixture.health = health
        companion = HostDiscoveryCompanion(fixture.config, fixture.state_dir)
        try:
            assert await companion.refresh() is False
            assert probe(companion) == []
        finally:
            await companion.stop()


async def test_polling_never_reads_secret_files(tmp_path, monkeypatch):
    async with https_device(tmp_path) as fixture:
        forbidden = tmp_path / "unreadable-secrets"
        forbidden.mkdir()
        fixture.config["device"]["management_password_file"] = str(forbidden)
        fixture.config["inference"] = {"api_key_file": str(forbidden)}
        fixture.config["controller"]["ca_file"] = str(forbidden)
        original_read_text = Path.read_text
        original_read_bytes = Path.read_bytes
        reads = []

        def checked_text(path, *args, **kwargs):
            reads.append(path)
            assert path == fixture.state_dir / "device.crt"
            return original_read_text(path, *args, **kwargs)

        def checked_bytes(path, *args, **kwargs):
            reads.append(path)
            assert path == fixture.state_dir / "device.crt"
            return original_read_bytes(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", checked_text)
        monkeypatch.setattr(Path, "read_bytes", checked_bytes)
        companion = HostDiscoveryCompanion(fixture.config, fixture.state_dir)
        try:
            assert await companion.refresh() is True
            assert reads
            assert len(probe(companion)) == 1
        finally:
            await companion.stop()


async def test_lab_companion_starts_and_stops_without_macos_opt_in(tmp_path):
    async with https_device(tmp_path) as fixture:
        companion = HostDiscoveryCompanion(fixture.config, fixture.state_dir)
        try:
            await companion.start()
            assert companion.discovery.status["listening"] is True
            assert companion.discovery.transport.get_extra_info("sockname")[0] == "127.0.0.1"
        finally:
            await companion.stop()
        assert companion.discovery.status["listening"] is False


@pytest.mark.parametrize("endpoint", ["/api/info", "/healthz"])
async def test_fragmented_json_response_is_fully_read(tmp_path, endpoint):
    async with https_device(tmp_path) as fixture:
        fixture.fragmented = endpoint
        companion = HostDiscoveryCompanion(fixture.config, fixture.state_dir)
        try:
            assert await companion.refresh() is True
            assert len(probe(companion)) == 1
        finally:
            await companion.stop()


async def test_unhealthy_start_defers_udp_until_polling_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr("aikey.host_discovery.POLL_INTERVAL", 0.01)
    async with https_device(tmp_path) as fixture:
        fixture.health_status = 503
        companion = HostDiscoveryCompanion(fixture.config, fixture.state_dir)
        try:
            await companion.start()
            assert companion.status["running"] is True
            assert companion.status["health_fresh"] is False
            assert companion.discovery.status["listening"] is False
            fixture.health_status = 200
            async with asyncio.timeout(1):
                while not companion.status["responding"]:
                    await asyncio.sleep(0.005)
            assert companion.status["health_successes"] >= 1
            assert companion.status["last_error"] is None
            assert companion.discovery.status["listening"] is True
        finally:
            await companion.stop()


async def test_physical_macos_companion_requires_explicit_opt_in(tmp_path, monkeypatch):
    async with https_device(tmp_path) as fixture:
        fixture.config["runtime"]["mode"] = "device"
        fixture.config["device"]["ip"] = "192.0.2.5"
        fixture.config["controller"]["host"] = "192.0.2.1"
        fixture.config["discovery"].update(bind="192.0.2.5", port=10001)
        monkeypatch.setattr("sys.platform", "darwin")
        companion = None
        try:
            with pytest.raises(ValueError, match="allow.macos.host|macOS|explicit"):
                companion = HostDiscoveryCompanion(fixture.config, fixture.state_dir)
                await companion.start()
            assert fixture.requests == []
        finally:
            if companion is not None:
                await companion.stop()
