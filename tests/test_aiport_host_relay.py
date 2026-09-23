"""Synthetic multi-instance HTTPS relay and fail-closed plan checks."""

import asyncio
import hashlib
import ssl

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey.aiport_deployment import plan_ai_ports
from aikey.aiport_host_relay import Endpoint, HostRelayError, HostRelayGroup, endpoints_from_plan
from aikey.aiport_instance_state import provision_slot
from aikey.tls import ensure_identity_certificate


def _provisioned(tmp_path):
    controller = tmp_path / "controller"
    controller.mkdir(mode=0o700)
    cert, _ = ensure_identity_certificate(controller, "2A1100F0A55E")
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    plan = plan_ai_ports({"schema": "aikey-camera-preflight/1", "cameras": [
        {"id": f"{1:024x}", "model": "UVC G3 Instant", "state": "CONNECTED",
         "processing_class": "legacy_ingress_needed"}]},
        device_ips=["192.168.10.20"])
    root = tmp_path / "states"
    root.mkdir(mode=0o700)
    state = root / "slot-1"
    return plan, cert, pin, state


def test_complete_plan_requires_preexisting_identity_and_never_creates_one(tmp_path):
    plan, cert, pin, state = _provisioned(tmp_path)
    state.mkdir(mode=0o700)
    with pytest.raises(HostRelayError, match="missing"):
        endpoints_from_plan(plan, {1: state}, controller_ip="192.168.10.1",
                            controller_pin=pin)
    assert list(state.iterdir()) == []
    state.rmdir()
    provision_slot(plan, 1, state, controller_ip="192.168.10.1",
                   controller_cert_file=cert, controller_pin=pin)
    assert endpoints_from_plan(plan, {1: state}, controller_ip="192.168.10.1",
                               controller_pin=pin) == (
        Endpoint(1, "192.168.10.20", state / "device.crt"),)
    plan["instances"][0]["host_ip"] = None
    with pytest.raises(HostRelayError, match="identity"):
        endpoints_from_plan(plan, {1: state}, controller_ip="192.168.10.1",
                            controller_pin=pin)


@pytest.mark.asyncio
async def test_cert_pinned_relay_forwards_to_its_own_instance(tmp_path):
    state = tmp_path / "slot-1"
    state.mkdir(mode=0o700)
    cert, key = ensure_identity_certificate(state, "2A1100F0A501")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    app = web.Application()

    async def response(_request):
        return web.Response(text="1")

    app.router.add_get("/", response)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server(ssl=context)
    endpoint = Endpoint(1, "127.0.0.1", cert, server.port)
    relay = HostRelayGroup((endpoint,), controller_ip="127.0.0.1",
                           listen_port=0, allow_loopback=True)
    try:
        await relay.start()
        trusted = ssl.create_default_context(cafile=str(cert))
        trusted.check_hostname = False
        trusted.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        connector = aiohttp.TCPConnector(ssl=trusted)
        async with aiohttp.ClientSession(connector=connector, trust_env=False) as client:
            async with client.get(f"https://127.0.0.1:{relay._relays[0].listen_port}/") as result:
                assert result.status == 200
                assert await result.text() == "1"
    finally:
        counters = await relay.stop()
        await server.close()
    assert counters == {"accepted": 1, "rejected": 0}


@pytest.mark.asyncio
async def test_wrong_upstream_certificate_opens_no_listener(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    cert, key = ensure_identity_certificate(state, "2A1100F0A501")
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    wrong_cert, _ = ensure_identity_certificate(other, "2A1100F0A502")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = TestServer(web.Application(), host="127.0.0.1")
    await server.start_server(ssl=context)
    relay = HostRelayGroup((Endpoint(1, "127.0.0.1", wrong_cert, server.port),),
                           controller_ip="127.0.0.1", listen_port=0,
                           allow_loopback=True)
    try:
        with pytest.raises(HostRelayError, match="preflight"):
            await relay.start()
        assert relay._relays == []
    finally:
        await relay.stop()
        await server.close()


@pytest.mark.asyncio
async def test_changed_upstream_certificate_is_denied_after_listener_starts(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    cert, key = ensure_identity_certificate(state, "2A1100F0A501")
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    wrong_cert, _ = ensure_identity_certificate(other, "2A1100F0A502")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    requests = []
    app = web.Application()

    async def response(_request):
        requests.append(1)
        return web.Response(text="unexpected")

    app.router.add_get("/", response)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server(ssl=context)
    group = HostRelayGroup((Endpoint(1, "127.0.0.1", cert, server.port),),
                           controller_ip="127.0.0.1", listen_port=0,
                           allow_loopback=True)
    try:
        await group.start()
        cert.write_bytes(wrong_cert.read_bytes())
        reader, writer = await asyncio.open_connection("127.0.0.1",
                                                       group._relays[0].listen_port)
        writer.write(b"not forwarded")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(1), timeout=3) == b""
        assert requests == []
        writer.close()
        await writer.wait_closed()
    finally:
        await group.stop()
        await server.close()


@pytest.mark.asyncio
async def test_partial_bind_failure_rolls_back_all_listeners(monkeypatch, tmp_path):
    first = Endpoint(1, "127.0.0.2", tmp_path / "one.crt")
    second = Endpoint(2, "127.0.0.3", tmp_path / "two.crt")
    group = HostRelayGroup((first, second), controller_ip="127.0.0.1",
                           listen_port=0, allow_loopback=True)

    async def preflight():
        pass

    monkeypatch.setattr(group, "preflight", preflight)
    from aikey.aiport_relay import BoundedRelay
    started = []
    stopped = []

    async def start(relay):
        if relay.listen_ip == "127.0.0.3":
            raise OSError("synthetic bind conflict")
        started.append(relay.listen_ip)

    async def stop(relay):
        stopped.append(relay.listen_ip)

    monkeypatch.setattr(BoundedRelay, "start", start)
    monkeypatch.setattr(BoundedRelay, "stop", stop)
    with pytest.raises(HostRelayError, match="no new listeners"):
        await group.start()
    assert group._relays == []
    assert started == stopped == ["127.0.0.2"]
