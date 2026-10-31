"""Native metadata uses the existing private session, never a caller-selected target."""

import asyncio
import time

import pytest

from aikey import control_site
from aikey.config import initialize
from aikey.native_camera_status import NativeStatusError, parse_bootstrap
from test_control_site_secrets import signed_in
from test_native_camera_status import bootstrap

PREFIX = "/api/admin/v1/cameras/native-status"


def snapshot():
    return {
        **parse_bootstrap(bootstrap(), observed_at=int(time.time())),
        "session_logout_acknowledged": True,
    }


async def test_default_is_explicitly_unconfigured_without_native_access(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        response = await client.get(PREFIX)
        value = await response.json()
        assert response.status == 200 and value["configured"] is False
        assert value["cameras"] is None and value["observation"] == "not_configured"
        response = await client.get("/camera-status")
        assert response.status == 200 and "No native login was attempted" in await response.text()
        assert "Native camera status" in await (await client.get("/")).text()
    finally:
        await client.close()
        await server.close()


async def test_authenticated_api_and_browser_keep_unknown_false_and_escaped_names(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    value = snapshot()
    value["cameras"][0]["name"] = '<img src=x onerror="DO-NOT-EXECUTE">'
    value["cameras"][0]["recording_active"] = None
    before = path.read_bytes(), site.aiport.path.read_bytes()
    calls = []

    async def read():
        calls.append(True)
        return value

    site.native_status_loader = read
    try:
        response = await client.get(PREFIX)
        assert response.status == 200
        body = await response.json()
        assert body["snapshot"]["cameras"][0]["recording_active"] is None
        assert body["snapshot"]["cameras"][0]["ptz_capable"] is False
        assert body["snapshot"]["control_operations_enabled"] is False
        response = await client.get("/camera-status")
        page = await response.text()
        assert response.status == 200 and "Reported unsupported" in page and "Unknown" in page
        assert "<img" not in page and "&lt;img" in page
        assert "Temporary session logout acknowledged" in page
        assert len(calls) == 2 and before == (path.read_bytes(), site.aiport.path.read_bytes())
        assert response.headers["Cache-Control"] == "no-store"
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("kind", ["stale", "extra", "unknown_logout", "private_channel"])
async def test_untrusted_loader_shapes_never_publish_private_or_stale_values(tmp_path, kind):
    site, server, client, cookie, path = await signed_in(tmp_path)
    value = snapshot()
    if kind == "stale":
        value["fetched_at"] -= 31
    elif kind == "extra":
        value["password"] = "DO-NOT-PUBLISH"
    elif kind == "unknown_logout":
        value["session_logout_acknowledged"] = False
    else:
        value["cameras"][0]["channels"][0]["rtsp_url"] = "DO-NOT-PUBLISH"

    async def read():
        return value

    site.native_status_loader = read
    try:
        response = await client.get(PREFIX)
        assert response.status == 503 and "DO-NOT-PUBLISH" not in await response.text()
        response = await client.get("/camera-status")
        page = await response.text()
        assert "No stale observation" in page and "DO-NOT-PUBLISH" not in page
    finally:
        await client.close()
        await server.close()


async def test_signed_out_or_query_injection_makes_no_native_request(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    calls = []

    async def read():
        calls.append(True)
        raise AssertionError("Native access forbidden")

    site.native_status_loader = read
    try:
        assert (await client.get(PREFIX + "?url=https://127.0.0.1/private")).status == 400
        assert (await client.get("/camera-status?username=caller")).status == 400
        await client.post(
            "/logout",
            data={"csrf": site.security.csrf_token(cookie)},
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert (await client.get(PREFIX)).status == 401
        assert (await client.get("/camera-status", allow_redirects=False)).status == 303
        assert not calls
    finally:
        await client.close()
        await server.close()


async def test_revoked_session_cannot_receive_an_inflight_snapshot(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def read():
        entered.set()
        await release.wait()
        return snapshot()

    site.native_status_loader = read
    try:
        task = asyncio.create_task(client.get(PREFIX))
        await asyncio.wait_for(entered.wait(), 3)
        site.security.revoke(cookie)
        release.set()
        response = await task
        assert response.status == 401 and "cameras" not in await response.text()
    finally:
        release.set()
        await client.close()
        await server.close()


async def test_revocation_also_hides_a_late_native_authentication_failure(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)

    async def read():
        site.security.revoke(cookie)
        raise NativeStatusError("native_authentication_refused")

    site.native_status_loader = read
    try:
        response = await client.get(PREFIX)
        assert response.status == 401
        assert "native_authentication_refused" not in await response.text()
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "code",
    [
        "native_authentication_refused",
        "native_session_cleanup_unconfirmed",
        "native_status_backoff",
    ],
)
async def test_fixed_failure_subtype_is_retained_without_raw_diagnostics(tmp_path, code):
    site, server, client, cookie, path = await signed_in(tmp_path)

    async def read():
        raise NativeStatusError(code)

    site.native_status_loader = read
    try:
        response = await client.get(PREFIX)
        assert response.status == 503 and (await response.json())["reason"] == code
    finally:
        await client.close()
        await server.close()


def test_cli_native_access_is_opt_in_complete_and_never_initializes_credentials(
    tmp_path, monkeypatch
):
    path = tmp_path / "key" / "config.json"
    initialize(path, path.parent, controller_host="127.0.0.1")
    original = path.read_bytes()
    admin = tmp_path / "admin"
    assert control_site.main(["init", "--state-dir", str(admin), "--generate"]) == 0
    calls = []

    async def no_listener(site, port):
        calls.append(site)

    monkeypatch.setattr(control_site, "_run", no_listener)
    args = ["run", "--state-dir", str(admin), "--aikey-config", str(path)]
    assert control_site.main(args) == 0 and calls[-1].native_status_loader is None
    user, password = tmp_path / "not-created-user", tmp_path / "not-created-password"
    assert control_site.main(args + ["--native-status-username-file", str(user)]) == 2
    assert (
        control_site.main(
            args
            + [
                "--native-status-username-file",
                str(user),
                "--native-status-password-file",
                str(password),
            ]
        )
        == 2
    )
    full = args + [
        "--inventory-controller",
        "127.0.0.1",
        "--inventory-api-key-file",
        str(tmp_path / "key-ref"),
        "--inventory-web-trust-file",
        str(tmp_path / "trust"),
        "--inventory-web-cert-file",
        str(tmp_path / "cert"),
        "--native-status-username-file",
        str(user),
        "--native-status-password-file",
        str(password),
    ]
    assert control_site.main(full) == 0 and calls[-1].native_status_loader is not None
    assert len(calls) == 2 and not user.exists() and not password.exists()
    assert path.read_bytes() == original
