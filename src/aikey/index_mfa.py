"""Opt-in native UniFi MFA, with no persisted challenge or token."""

from __future__ import annotations

import asyncio
import os
import re
import termios

from .camera_inventory import _json
from .index_native import NativeIndexError

MFA_TIMEOUT = 180


def _text(value, limit):
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and all(ord(c) >= 32 and ord(c) != 127 for c in value)
    )


async def authenticate(native, raw, credentials):
    method = native.mfa_method
    if method is None:
        raise NativeIndexError("Native MFA requires an explicit operator-selected method")
    try:
        challenge = _json(raw)
        data = challenge["data"]
        cookie = data["mfaCookie"]
        authenticators = data["authenticators"]
        if (
            not _text(cookie, 16384)
            or not isinstance(authenticators, list)
            or not 1 <= len(authenticators) <= 32
        ):
            raise ValueError
        choices = [
            a
            for a in authenticators
            if isinstance(a, dict) and a.get("type") == method and not a.get("blocked_reason")
        ]
        if len(choices) != 1 or not _text(choices[0].get("id"), 256):
            raise ValueError
        authenticator = choices[0]["id"]
    except (ValueError, KeyError, TypeError):
        raise NativeIndexError("Native MFA challenge or selected method is unsupported") from None
    async with asyncio.timeout(MFA_TIMEOUT):
        if method in {"email", "sms"}:
            status, _ = await native._login_post(
                "/api/sso/login/mfa/send",
                {"id": authenticator, "method": method, "mfaCookie": cookie},
            )
            if status != 200:
                raise NativeIndexError("Native MFA code delivery was refused")
        if method == "push":
            # Initial login triggers the selected default push, as in the native UI.
            # A nondefault push must be explicitly sent once.
            if data.get("user", {}).get("default_mfa") != authenticator:
                status, response = await native._login_post(
                    "/api/sso/login/mfa/send", {"method": "push", "mfaCookie": cookie}
                )
                try:
                    cookie = _json(response)["mfaCookie"] if status == 200 else None
                except (ValueError, KeyError, TypeError):
                    cookie = None
                if not _text(cookie, 16384):
                    raise NativeIndexError("Native MFA push was refused")
            for _ in range(90):
                status, _ = await native._login_post(
                    "/api/auth/login/poll", {"mfaCookie": cookie, "rememberMe": False}
                )
                if status != 202:
                    return status
                await asyncio.sleep(2)
            raise NativeIndexError("Native MFA approval expired")
        if native.token_provider is None:
            raise NativeIndexError("Native MFA requires a private code input")
        token = await native.token_provider()
        if not isinstance(token, str) or not re.fullmatch(r"[0-9]{6}", token):
            raise NativeIndexError("Native MFA code must contain six digits")
        # Exactly one submission; invalid/expired codes never cause an automatic retry.
        status, _ = await native._login_post("/api/auth/login", {**credentials, "token": token})
        return status


async def prompt_token():
    """Read a single canonical line from the controlling TTY with echo disabled.

    Unlike a worker-thread getpass, cancellation closes the reader and restores
    terminal flags before the authentication scope can finish.
    """
    fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    previous = None
    loop = asyncio.get_running_loop()
    try:
        previous = termios.tcgetattr(fd)
        attrs = termios.tcgetattr(fd)
        attrs[3] &= ~(termios.ECHO | termios.ECHONL)
        attrs[3] |= termios.ICANON
        termios.tcflush(fd, termios.TCIFLUSH)
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        os.write(fd, b"UniFi 2FA code (hidden, six digits): ")
        future = loop.create_future()

        def ready():
            try:
                value = os.read(fd, 64)
                if not future.done():
                    future.set_result(value)
            except BlockingIOError:
                pass
            except OSError:
                if not future.done():
                    future.set_exception(NativeIndexError("Native MFA input unavailable"))

        loop.add_reader(fd, ready)
        raw = await future
        if not re.fullmatch(rb"[0-9]{6}\n", raw):
            raise NativeIndexError("Native MFA code must contain six digits")
        return raw[:-1].decode("ascii")
    finally:
        loop.remove_reader(fd)
        try:
            if previous is not None:
                termios.tcflush(fd, termios.TCIFLUSH)
                termios.tcsetattr(fd, termios.TCSANOW, previous)
            os.write(fd, b"\n")
        finally:
            os.close(fd)
