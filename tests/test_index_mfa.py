"""Real pinned loopback TLS: explicit challenge, single code, denial and cleanup."""

import asyncio

from aiohttp import web
import pytest

from aikey.index_native import NativeIndexError, NativeIndexSession
from test_native_camera_status import controller


def challenge(method="totp"):
    return {
        "code": "MFA_AUTH_REQUIRED",
        "data": {
            "mfaCookie": "PRIVATE-CHALLENGE",
            "authenticators": [{"id": "method-a", "type": method, "blocked_reason": None}],
            "user": {"default_mfa": "method-a"},
        },
    }


def success():
    response = web.json_response({})
    response.set_cookie("TOKEN", "FICTIONAL-SESSION", secure=True, httponly=True)
    response.headers["X-CSRF-Token"] = "FICTIONAL-CSRF"
    return response


def session(reader, method="totp", provider=None):
    return NativeIndexSession(
        reader.host,
        username_file=reader.username_file,
        password_file=reader.password_file,
        trust_file=reader.trust_file,
        cert_file=reader.cert_file,
        mfa_method=method,
        token_provider=provider,
    )


@pytest.mark.parametrize("status", [200, 401, 403, 429, 499, 500, 302])
async def test_single_code_submission_and_no_retry(tmp_path, monkeypatch, status):
    calls = []

    async def login(request):
        payload = await request.json()
        calls.append(payload)
        if len(calls) == 1:
            assert "token" not in payload
            return web.json_response(challenge(), status=499)
        assert payload == {**calls[0], "token": "123456"}
        return (
            success()
            if status == 200
            else web.Response(status=status, headers={"Location": "/leak"})
        )

    async def code():
        return "123456"

    async with controller(tmp_path, monkeypatch, login_hook=login) as (reader, seen, _):
        native = session(reader, provider=code)
        if status == 200:
            async with native:
                assert native.protect_version == "7.3.74"
            assert native.logout_acknowledged
        else:
            with pytest.raises(NativeIndexError):
                async with native:
                    pytest.fail("must refuse")
            assert ("GET", "/proxy/protect/api/bootstrap") not in seen
        assert len(calls) == 2 and native._session is None


@pytest.mark.parametrize(
    "kind",
    [
        "no_opt_in",
        "bad_challenge",
        "wrong_method",
        "blocked",
        "duplicate",
        "bad_code",
        "unicode",
        "cancel",
        "timeout",
    ],
)
async def test_challenge_admission_or_input_failure_never_submits_code(tmp_path, monkeypatch, kind):
    calls = []
    data = challenge()
    if kind == "bad_challenge":
        data["data"]["mfaCookie"] = "\nprivate"
    if kind == "wrong_method":
        data["data"]["authenticators"][0]["type"] = "unknown"
    if kind == "blocked":
        data["data"]["authenticators"][0]["blocked_reason"] = "blocked"
    if kind == "duplicate":
        data["data"]["authenticators"] *= 2

    async def login(request):
        calls.append(await request.json())
        return web.json_response(data, status=499)

    async def code():
        if kind == "cancel":
            raise asyncio.CancelledError
        if kind == "timeout":
            await asyncio.sleep(10)
        return "１２３４５６" if kind == "unicode" else "secret-invalid"

    monkeypatch.setattr("aikey.index_mfa.MFA_TIMEOUT", 0.05)
    async with controller(tmp_path, monkeypatch, login_hook=login) as (reader, seen, _):
        native = session(reader, method=None if kind == "no_opt_in" else "totp", provider=code)
        with pytest.raises(asyncio.CancelledError if kind == "cancel" else NativeIndexError) as exc:
            async with native:
                pytest.fail("must refuse")
        assert "PRIVATE-CHALLENGE" not in str(exc.value) and "secret-invalid" not in str(exc.value)
        assert len(calls) == 1 and native._session is None
        assert ("GET", "/proxy/protect/api/bootstrap") not in seen


@pytest.mark.parametrize("method", ["email", "sms"])
async def test_delivery_uses_fixed_route_and_challenge_before_code(tmp_path, monkeypatch, method):
    order = []

    async def login(request):
        body = await request.json()
        order.append("login")
        return success() if "token" in body else web.json_response(challenge(method), status=499)

    async def send(request):
        order.append("send")
        assert await request.json() == {
            "id": "method-a",
            "method": method,
            "mfaCookie": "PRIVATE-CHALLENGE",
        }
        return web.json_response({})

    async def code():
        order.append("input")
        return "123456"

    async with controller(
        tmp_path,
        monkeypatch,
        login_hook=login,
        additional_routes=[web.post("/api/sso/login/mfa/send", send)],
    ) as (reader, seen, _):
        native = session(reader, method, code)
        async with native:
            pass
        assert order == ["login", "send", "input", "login"] and native.logout_acknowledged


@pytest.mark.parametrize("poll_status", [200, 401, 403, 429, 302])
async def test_push_default_one_poll_denial_or_success(tmp_path, monkeypatch, poll_status):
    calls = []

    async def login(request):
        calls.append("login")
        return web.json_response(challenge("push"), status=499)

    async def poll(request):
        calls.append("poll")
        assert await request.json() == {"mfaCookie": "PRIVATE-CHALLENGE", "rememberMe": False}
        return (
            success()
            if poll_status == 200
            else web.Response(status=poll_status, headers={"Location": "/leak"})
        )

    async with controller(
        tmp_path,
        monkeypatch,
        login_hook=login,
        additional_routes=[web.post("/api/auth/login/poll", poll)],
    ) as (reader, seen, _):
        native = session(reader, "push")
        if poll_status == 200:
            async with native:
                pass
            assert native.logout_acknowledged
        else:
            with pytest.raises(NativeIndexError):
                async with native:
                    pytest.fail("must refuse")
        assert calls == ["login", "poll"] and native._session is None


async def test_tty_restores_echo_and_removes_reader_on_cancellation(monkeypatch):
    import os
    import pty
    import termios
    from aikey.index_mfa import prompt_token

    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    real_open = os.open
    monkeypatch.setattr(
        os,
        "open",
        lambda name, flags: os.dup(slave) if name == "/dev/tty" else real_open(name, flags),
    )
    task = asyncio.create_task(prompt_token())
    try:
        await asyncio.sleep(0.01)
        assert not termios.tcgetattr(slave)[3] & termios.ECHO
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert termios.tcgetattr(slave) == original
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize(
    "raw,valid", [(b"123456\n", True), (b"12345\n", False), (b"1234567\n", False)]
)
async def test_tty_code_is_hidden_and_flags_restored(tmp_path, monkeypatch, raw, valid):
    import os
    import pty
    import termios
    from aikey.index_mfa import prompt_token

    master, slave = pty.openpty()
    os.set_blocking(master, False)
    original = termios.tcgetattr(slave)
    real_open = os.open
    monkeypatch.setattr(
        os,
        "open",
        lambda name, flags: os.dup(slave) if name == "/dev/tty" else real_open(name, flags),
    )
    task = asyncio.create_task(prompt_token())
    try:
        await asyncio.sleep(0.01)
        os.write(master, raw)
        if valid:
            assert await asyncio.wait_for(task, 2) == "123456"
        else:
            with pytest.raises(NativeIndexError):
                await asyncio.wait_for(task, 2)
        assert termios.tcgetattr(slave) == original
        assert raw.strip() not in os.read(master, 4096)
    finally:
        os.close(master)
        os.close(slave)


async def test_push_pending_is_bounded_without_relogin(tmp_path, monkeypatch):
    counts = {"login": 0, "poll": 0}

    async def login(request):
        counts["login"] += 1
        return web.json_response(challenge("push"), status=499)

    async def poll(request):
        counts["poll"] += 1
        return web.Response(status=202)

    monkeypatch.setattr("aikey.index_mfa.MFA_TIMEOUT", 0.1)
    async with controller(
        tmp_path,
        monkeypatch,
        login_hook=login,
        additional_routes=[web.post("/api/auth/login/poll", poll)],
    ) as (reader, seen, _):
        native = session(reader, "push")
        with pytest.raises(NativeIndexError):
            async with native:
                pytest.fail("must expire")
        assert counts == {"login": 1, "poll": 1}
        assert native._session is None
