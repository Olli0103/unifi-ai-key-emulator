"""Saved settings are not runtime facts; owned TLS peers only."""

import asyncio
import json
from functools import partial

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey.tls import ensure_identity_certificate, server_context


def key_health():
    return {"service": "local-aikey", "device": {"adopted": True, "connected": False},
            "worker": {"queued": 2, "active": 1, "pending": 3, "ledger": 17},
            "search": {"connected": True}, "private_image": "fictional-private-media",
            "private_id": "fictional-native-camera"}


async def target(tmp_path, response):
    ensure_identity_certificate(tmp_path, "020000000099")
    requests = []
    async def health(request):
        requests.append(dict(request.headers))
        return response() if callable(response) else web.json_response(response)
    app = web.Application()
    app.router.add_get("/healthz", health)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server(ssl=server_context(tmp_path))
    return server, requests, str(server.make_url("/healthz"))


async def test_tls_runtime_counts_are_separate_from_saved_configuration_and_cached(tmp_path):
    from aikey.admin_runtime import RuntimeObserver
    server, calls, url = await target(tmp_path, key_health())
    observer = RuntimeObserver({"aikey": ("aikey", tmp_path)}, {"aikey": url})
    try:
        results = await asyncio.gather(*(observer.read("aikey") for _ in range(12)))
        result = results[0]
        assert all(item == result for item in results)
        assert result["observation"] == "runtime_health"
        assert result["runtime"]["adopted"] is True and result["runtime"]["connected"] is False
        assert result["runtime"]["queued"] == 2 and result["runtime"]["pending"] == 3
        assert result["runtime"]["search_connected"] is True
        assert result["runtime_configuration_revision"] is None
        assert result["tls_ca_and_leaf_pin_verified"] is True
        assert result["observed_at"]
        assert "fictional" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
        assert len(calls) == 1 and not any(k.lower() in {"authorization", "cookie", "x-token"} for k in calls[0])
        assert await observer.read("aikey") == result and len(calls) == 1
    finally:
        await server.close()


async def test_no_target_means_unknown_not_an_offline_or_disabled_device(tmp_path):
    from aikey.admin_runtime import RuntimeObserver
    observer = RuntimeObserver({"aikey": ("aikey", tmp_path)}, {})
    result = await observer.read("aikey")
    assert result["observation"] == "not_configured" and result["runtime"] is None
    assert "adopted" not in result


@pytest.mark.parametrize("trust_other_certificate", [False, True])
async def test_a_different_certificate_is_refused_before_health_headers(tmp_path, trust_other_certificate):
    from aikey.admin_runtime import RuntimeObserver
    first, second = tmp_path / "first", tmp_path / "second"
    ensure_identity_certificate(first, "020000000098")
    server, calls, url = await target(second, key_health())
    if trust_other_certificate:
        # Even when the CA trust bundle contains the other public certificate,
        # the configured profile's first leaf pin must still reject that peer.
        own = first / "device.crt"
        own.write_bytes(own.read_bytes() + (second / "device.crt").read_bytes())
    observer = RuntimeObserver({"aikey": ("aikey", first)}, {"aikey": url})
    try:
        result = await observer.read("aikey")
        assert result["observation"] == "unavailable" and result["runtime"] is None
        assert calls == [] and "fictional" not in json.dumps(result)
    finally:
        await server.close()


async def test_port_counts_keep_zero_and_unknown_distinct_without_camera_identifiers(tmp_path):
    from aikey.admin_runtime import RuntimeObserver
    value = {"service": "aiport-candidate", "adopted": True, "control_connected": True,
             "active_streams": 0, "streams_with_decoded_frames": 0,
             "live_event_budget_remaining": None,
             "pool_cameras": [{"camera": "fictional-private-camera", "description": "fictional-private"}]}
    server, calls, url = await target(tmp_path, value)
    observer = RuntimeObserver({"aiport:test": ("aiport", tmp_path)}, {"aiport:test": url})
    try:
        result = await observer.read("aiport:test")
        assert result["runtime"]["active_streams"] == 0
        assert result["runtime"]["live_event_budget_remaining"] is None
        assert "fictional" not in json.dumps(result) and len(calls) == 1
    finally:
        await server.close()


async def test_slow_health_is_bounded_and_its_owned_server_is_collected(tmp_path):
    from aikey.admin_runtime import RuntimeObserver
    ensure_identity_certificate(tmp_path, "020000000099")
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow(request):
        entered.set()
        await release.wait()
        return web.json_response(key_health())
    app = web.Application()
    app.router.add_get("/healthz", slow)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server(ssl=server_context(tmp_path))
    observer = RuntimeObserver({"aikey": ("aikey", tmp_path)}, {"aikey": str(server.make_url('/healthz'))})
    try:
        async with asyncio.timeout(5):
            result = await observer.read("aikey")
        assert entered.is_set() and result["observation"] == "unavailable"
        assert result["runtime"] is None
    finally:
        release.set()
        await server.close()


@pytest.mark.parametrize("case", ["redirect", "duplicate", "wrong_service", "oversize", "wrong_counter"])
async def test_untrusted_health_cannot_become_valid_runtime_or_echoed_output(tmp_path, case):
    from aikey.admin_runtime import RuntimeObserver
    followed = []
    async def stolen(request):
        followed.append(True)
        return web.json_response(key_health())
    app = web.Application()
    app.router.add_get("/stolen", stolen)
    other = TestServer(app, host="127.0.0.1")
    await other.start_server()
    response = key_health()
    if case == "redirect":
        response = partial(web.Response, status=302, headers={"Location": str(other.make_url('/stolen'))})
    elif case == "duplicate":
        response = partial(web.Response, text='{"service":"local-aikey","service":"fictional-private"}',content_type="application/json")
    elif case == "wrong_service":
        response["service"] = "fictional-private"
    elif case == "oversize":
        response["private_image"] = "fictional-private" * 30000
    else:
        response["worker"]["queued"] = True
    server, calls, url = await target(tmp_path, response)
    observer = RuntimeObserver({"aikey": ("aikey", tmp_path)}, {"aikey": url})
    try:
        result = await observer.read("aikey")
        assert result["observation"] == "unavailable" and result["runtime"] is None
        assert "fictional" not in json.dumps(result) and not followed and len(calls) == 1
    finally:
        await server.close()
        await other.close()


@pytest.mark.parametrize("url", ["http://127.0.0.1:1/healthz", "https://example.com/healthz",
    "https://8.8.8.8/healthz", "https://127.0.0.1/other", "https://127.0.0.1/healthz?secret=fictional",
    "https://u:p@127.0.0.1/healthz", "https://0.0.0.0/healthz"])
def test_only_server_owned_literal_private_https_health_targets_are_accepted(tmp_path, url):
    from aikey.admin_runtime import RuntimeObserver
    with pytest.raises(ValueError):
        RuntimeObserver({"aikey": ("aikey", tmp_path)}, {"aikey": url})
