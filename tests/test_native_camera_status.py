"""Real owned TLS/HTTP metadata boundaries, without a controller or real credential."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
import os
import ssl
import time

from aiohttp import web
import pytest

from aikey import camera_inventory
from aikey.native_camera_status import (
    NativeStatusError,
    NativeStatusReader,
    parse_bootstrap,
    public_snapshot,
)
from aikey.tls import ensure_identity_certificate, server_context
from test_camera_inventory import camera, private_file


def bootstrap():
    return {
        "nvr": {"version": "7.3.74", "private": "DO-NOT-PUBLISH"},
        "cameras": [
            {
                **camera(),
                "isRecording": True,
                "featureFlags": {"isPtz": False},
                "channels": [
                    {
                        "id": 0,
                        "width": 1920,
                        "height": 1080,
                        "fps": 20,
                        "rtspAlias": "DO-NOT-PUBLISH",
                        "isRtspEnabled": True,
                    }
                ],
                "rtspPassword": "DO-NOT-PUBLISH",
            }
        ],
    }


@asynccontextmanager
async def controller(
    tmp_path,
    monkeypatch,
    *,
    login_status=200,
    logout_status=204,
    native_status=200,
    payload=None,
    raw_payload=None,
    segmented=False,
    gate=None,
    login_gate=None,
    logout_hook=None,
):
    ensure_identity_certificate(tmp_path, "020000000001")
    der = ssl.PEM_cert_to_DER_cert((tmp_path / "device.crt").read_text())
    seen = []
    app = web.Application()

    async def login(request):
        seen.append((request.method, request.path))
        assert await request.json() == {
            "username": "fictional-user",
            "password": "fictional-password",
            "rememberMe": False,
        }
        if login_gate:
            login_gate[0].set()
            await login_gate[1].wait()
        if login_status != 200:
            return web.Response(status=login_status)
        response = web.json_response({"private": "DO-NOT-PUBLISH"})
        response.set_cookie("TOKEN", "FICTIONAL-SESSION", secure=True, httponly=True)
        response.headers["X-CSRF-Token"] = "FICTIONAL-CSRF"
        return response

    async def native(request):
        seen.append((request.method, request.path))
        assert request.cookies.get("TOKEN") == "FICTIONAL-SESSION"
        assert request.headers.get("X-CSRF-Token") == "FICTIONAL-CSRF"
        if gate:
            gate[0].set()
            await gate[1].wait()
        if native_status != 200:
            return web.Response(status=native_status, headers={"Location": "/leak"})
        raw = (
            raw_payload
            if raw_payload is not None
            else json.dumps(bootstrap() if payload is None else payload).encode()
        )
        if not segmented:
            return web.Response(body=raw, content_type="application/json")
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        for start in range(0, len(raw), 47):
            await response.write(raw[start : start + 47])
            await asyncio.sleep(0.001)
        await response.write_eof()
        return response

    async def logout(request):
        seen.append((request.method, request.path))
        if request.cookies.get("TOKEN") != "FICTIONAL-SESSION":
            return web.Response(status=401)
        if logout_hook:
            logout_hook()
        return web.Response(status=logout_status)

    async def leak(request):
        pytest.fail("redirect must not be followed")

    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/proxy/protect/api/bootstrap", native)
    app.router.add_post("/api/auth/logout", logout)
    app.router.add_route("*", "/leak", leak)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_context(tmp_path))
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(camera_inventory, "_INTEGRATION_PORT", port)
    user = private_file(tmp_path / "native-user", "fictional-user\n")
    password = private_file(tmp_path / "native-password", "fictional-password\n")
    trust = private_file(
        tmp_path / "web-trust",
        json.dumps({"host": "127.0.0.1", "port": port, "sha256": hashlib.sha256(der).hexdigest()}),
    )
    cert = private_file(tmp_path / "web-leaf", (tmp_path / "device.crt").read_bytes())
    reader = NativeStatusReader(
        "127.0.0.1", username_file=user, password_file=password, trust_file=trust, cert_file=cert
    )
    try:
        yield reader, seen, password
    finally:
        if gate:
            gate[1].set()
        if login_gate:
            login_gate[1].set()
        await runner.cleanup()


def test_parser_strips_private_data_and_preserves_false_versus_unknown():
    value = bootstrap()
    result = parse_bootstrap(value, observed_at=int(time.time()))
    row = result["cameras"][0]
    assert row["recording_active"] is True and row["ptz_capable"] is False
    assert "DO-NOT-PUBLISH" not in json.dumps(result)
    del value["cameras"][0]["isRecording"]
    del value["cameras"][0]["featureFlags"]["isPtz"]
    unknown = parse_bootstrap(value, observed_at=int(time.time()))["cameras"][0]
    assert unknown["recording_active"] is None and unknown["ptz_capable"] is None
    snapshot = {**result, "session_logout_acknowledged": True}
    assert public_snapshot(snapshot) == snapshot


@pytest.mark.parametrize(
    "host",
    [
        None,
        True,
        2130706433,
        "::1",
        "8.8.8.8",
        "example.invalid",
        "https://127.0.0.1",
        "127.0.0.1:8443",
        "127.0.0.1/other",
    ],
)
def test_caller_cannot_turn_the_fixed_controller_into_an_arbitrary_url(tmp_path, host):
    with pytest.raises(ValueError, match="private IPv4 controller"):
        NativeStatusReader(
            host,
            username_file=tmp_path / "missing-user",
            password_file=tmp_path / "missing-password",
            trust_file=tmp_path / "missing-trust",
            cert_file=tmp_path / "missing-cert",
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "field,bad",
    [
        ("isRecording", 0),
        ("isRecording", "false"),
        ("state", []),
        ("featureFlags", None),
        ("channels", {}),
        ("id", "url"),
        ("mac", "invalid"),
        ("name", "line\nbreak"),
    ],
)
def test_invalid_metadata_is_never_silently_a_false_status(field, bad):
    value = bootstrap()
    value["cameras"][0][field] = bad
    with pytest.raises(NativeStatusError, match="^native_status_unavailable$"):
        parse_bootstrap(value, observed_at=int(time.time()))


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "duplicate",
        "mac_duplicate",
        "channels_duplicate",
        "channels_overflow",
        "bad_fps",
        "bad_ptz",
    ],
)
def test_bootstrap_identity_version_and_shape_boundaries(mutation):
    value = bootstrap()
    row = value["cameras"][0]
    if mutation == "version":
        value["nvr"]["version"] = "unqualified"
    elif mutation == "duplicate":
        value["cameras"].append(deepcopy(row))
    elif mutation == "mac_duplicate":
        value["cameras"].append({**row, "id": "2" * 24})
    elif mutation == "channels_duplicate":
        row["channels"] *= 2
    elif mutation == "channels_overflow":
        row["channels"] *= 17
    elif mutation == "bad_fps":
        row["channels"][0]["fps"] = True
    elif mutation == "bad_ptz":
        row["featureFlags"]["isPtz"] = 1
    with pytest.raises(NativeStatusError):
        parse_bootstrap(value, observed_at=int(time.time()))


@pytest.mark.parametrize(
    "field,bad",
    [
        ("fetched_at", 1),
        ("fetched_at", True),
        ("session_logout_acknowledged", False),
        ("source", "invented"),
        ("control_operations_enabled", True),
        ("secret", "DO-NOT-PUBLISH"),
    ],
)
def test_publication_refuses_stale_unconfirmed_or_extra_fields(field, bad):
    result = {
        **parse_bootstrap(bootstrap(), observed_at=int(time.time())),
        "session_logout_acknowledged": True,
        field: bad,
    }
    with pytest.raises(NativeStatusError):
        public_snapshot(result)


async def test_real_segmented_tls_response_cookie_isolation_cache_and_logout(tmp_path, monkeypatch):
    async with controller(tmp_path, monkeypatch, segmented=True) as (reader, seen, _):
        first, other = await asyncio.gather(reader.read(), reader.read())
        assert first == other and first["session_logout_acknowledged"] is True
        first["cameras"].clear()
        assert len((await reader.read())["cameras"]) == 1
        assert seen == [
            ("POST", "/api/auth/login"),
            ("GET", "/proxy/protect/api/bootstrap"),
            ("POST", "/api/auth/logout"),
        ]
        assert all(
            s not in json.dumps(other)
            for s in ["fictional-password", "fictional-user", "FICTIONAL-SESSION", "FICTIONAL-CSRF"]
        )


@pytest.mark.parametrize("status", [302, 401, 403, 500])
async def test_native_http_error_or_redirect_still_signs_out_without_retry(
    tmp_path, monkeypatch, status
):
    async with controller(tmp_path, monkeypatch, native_status=status) as (reader, seen, _):
        with pytest.raises(NativeStatusError):
            await reader.read()
        assert len(seen) == 3 and seen[-1][1] == "/api/auth/logout"
        with pytest.raises(NativeStatusError, match="backoff"):
            await reader.read()
        assert len(seen) == 3


async def test_authentication_refusal_is_latched_beyond_backoff(tmp_path, monkeypatch):
    async with controller(tmp_path, monkeypatch, login_status=401) as (reader, seen, _):
        with pytest.raises(NativeStatusError, match="authentication_refused"):
            await reader.read()
        reader._clock = lambda: time.monotonic() + 100
        with pytest.raises(NativeStatusError, match="authentication_refused"):
            await reader.read()
        assert seen == [("POST", "/api/auth/login")]


async def test_uncertain_logout_blocks_new_sessions_not_stale_success(tmp_path, monkeypatch):
    async with controller(tmp_path, monkeypatch, logout_status=500) as (reader, seen, _):
        with pytest.raises(NativeStatusError, match="cleanup_unconfirmed"):
            await reader.read()
        reader._clock = lambda: time.monotonic() + 100
        with pytest.raises(NativeStatusError, match="cleanup_unconfirmed"):
            await reader.read()
        assert len(seen) == 3 and reader._cache is None


async def test_cancelled_native_read_attempts_logout_and_preserves_cancellation(
    tmp_path, monkeypatch
):
    gate = asyncio.Event(), asyncio.Event()
    async with controller(tmp_path, monkeypatch, gate=gate) as (reader, seen, _):
        task = asyncio.create_task(reader.read())
        await asyncio.wait_for(gate[0].wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert seen[-1] == ("POST", "/api/auth/logout") and reader._cache is None


async def test_cancelled_login_with_uncertain_cleanup_still_preserves_cancellation(
    tmp_path, monkeypatch
):
    gate = asyncio.Event(), asyncio.Event()
    async with controller(tmp_path, monkeypatch, login_gate=gate) as (reader, seen, _):
        task = asyncio.create_task(reader.read())
        await asyncio.wait_for(gate[0].wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert reader._cleanup_uncertain is True and reader._cache is None
        assert seen == [("POST", "/api/auth/login"), ("POST", "/api/auth/logout")]


@pytest.mark.parametrize("kind", ["world_readable", "symlink", "fifo"])
async def test_unsafe_credential_file_is_refused_before_any_http(tmp_path, monkeypatch, kind):
    async with controller(tmp_path, monkeypatch) as (reader, seen, password):
        if kind == "world_readable":
            password.chmod(0o644)
        elif kind == "symlink":
            actual = password.with_name("other-private")
            password.rename(actual)
            password.symlink_to(actual)
        else:
            password.unlink()
            os.mkfifo(password, 0o600)
        with pytest.raises(NativeStatusError):
            await asyncio.wait_for(reader.read(), 1)
        assert not seen


async def test_wrong_valid_leaf_pin_releases_no_credential_to_server(tmp_path, monkeypatch):
    async with controller(tmp_path, monkeypatch) as (reader, seen, _):
        other = tmp_path / "other"
        other.mkdir()
        ensure_identity_certificate(other, "020000000002")
        der = ssl.PEM_cert_to_DER_cert((other / "device.crt").read_text())
        value = json.loads(reader.trust_file.read_text())
        value["sha256"] = hashlib.sha256(der).hexdigest()
        private_file(reader.trust_file, json.dumps(value))
        private_file(reader.cert_file, (other / "device.crt").read_bytes())
        with pytest.raises(NativeStatusError):
            await reader.read()
        assert not seen


@pytest.mark.parametrize(
    "raw", [b'{"nvr":{},"nvr":{},"cameras":[]}', b'{"cameras":NaN}', b" " * 2097153]
)
async def test_malformed_duplicate_nonfinite_or_oversized_wire_data_signs_out(
    tmp_path, monkeypatch, raw
):
    async with controller(tmp_path, monkeypatch, raw_payload=raw) as (reader, seen, _):
        with pytest.raises(NativeStatusError):
            await reader.read()
        assert seen[-1] == ("POST", "/api/auth/logout") and len(seen) == 3


async def test_refresh_failure_drops_cached_success_instead_of_serving_old_state(
    tmp_path, monkeypatch
):
    payload = bootstrap()
    async with controller(tmp_path, monkeypatch, payload=payload) as (reader, seen, _):
        assert (await reader.read())["cameras"][0]["recording_active"] is True
        reader._clock = lambda: time.monotonic() + 100
        payload["cameras"][0]["isRecording"] = "invalid"
        with pytest.raises(NativeStatusError):
            await reader.read()
        assert reader._cache is None and len(seen) == 6
        with pytest.raises(NativeStatusError, match="backoff"):
            await reader.read()
        assert len(seen) == 6
