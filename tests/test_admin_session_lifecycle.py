"""Logout and process lifetimes end administrator authority, not just a cookie jar."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from yarl import URL

from aikey.control_site import _COOKIE
from test_admin_security import ORIGIN, PASSWORD, policy
from test_control_site_secrets import signed_in
from test_speech_provider_control import configured_site


@pytest.mark.parametrize("action", ["read", "mutate"])
async def test_cookie_copied_before_logout_cannot_be_replayed(tmp_path, action):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        csrf = site.security.csrf_token(cookie)
        revision = site.aikey.snapshot().revision
        before = path.read_bytes()
        response = await client.post("/logout", data={"csrf": csrf},
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        # Simulate a copied cookie, not the browser's correctly deleted cookie.
        client.session.cookie_jar.update_cookies({_COOKIE: cookie}, response_url=URL(site.origin))
        if action == "read":
            response = await client.get("/", allow_redirects=False)
            assert response.status == 303 and response.headers["Location"] == "/login"
        else:
            response = await client.post("/role-model", data={"csrf": csrf, "revision": revision,
                "profile": "aikey", "role": "caption", "model": "synthetic-replayed-session"},
                headers={"Origin": site.origin}, allow_redirects=False)
            assert response.status == 403
        assert path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


def test_restarting_the_policy_does_not_revive_a_signed_session():
    clock = [1_700_000_000]
    original = policy(clock)
    record = original.create_password_record(PASSWORD)
    credentials = original.login("fixture", PASSWORD, record, ORIGIN).credentials
    assert original.authorize("GET", ORIGIN, credentials.cookie).allowed
    restarted = policy(clock)  # same persistent signing key and origin
    assert not restarted.authorize("GET", ORIGIN, credentials.cookie).allowed
    assert restarted.csrf_token(credentials.cookie) is None


def test_revoke_is_scoped_and_never_revokes_another_valid_session():
    clock = [1_700_000_000]
    security = policy(clock)
    record = security.create_password_record(PASSWORD)
    first = security.login("first", PASSWORD, record, ORIGIN).credentials
    second = security.login("second", PASSWORD, record, ORIGIN).credentials
    assert security.revoke(first.cookie)
    assert not security.revoke(first.cookie) and not security.revoke("not-a-cookie")
    assert not security.authorize("GET", ORIGIN, first.cookie).allowed
    assert security.csrf_token(first.cookie) is None
    assert security.authorize("POST", ORIGIN, second.cookie, second.csrf_token).allowed


def test_session_capacity_fails_closed_without_eviction_and_logout_frees_a_slot():
    clock = [1_700_000_000]
    security = policy(clock, max_sessions=2)
    record = security.create_password_record(PASSWORD)
    first = security.login("first", PASSWORD, record, ORIGIN).credentials
    second = security.login("second", PASSWORD, record, ORIGIN).credentials
    full = security.login("third", PASSWORD, record, ORIGIN)
    assert not full.allowed and full.reason == "session_capacity" and full.credentials is None
    assert security.authorize("GET", ORIGIN, first.cookie).allowed
    assert security.authorize("GET", ORIGIN, second.cookie).allowed
    assert security.revoke(first.cookie)
    assert security.login("third", PASSWORD, record, ORIGIN).allowed
    assert not security.authorize("GET", ORIGIN, first.cookie).allowed


def test_observed_expiry_cannot_be_reversed_by_clock_rollback():
    clock = [1_700_000_000]
    security = policy(clock, session_ttl_seconds=60, max_sessions=1)
    record = security.create_password_record(PASSWORD)
    old = security.login("first", PASSWORD, record, ORIGIN).credentials
    clock[0] += 61
    assert not security.authorize("GET", ORIGIN, old.cookie).allowed
    clock[0] -= 60
    assert not security.authorize("GET", ORIGIN, old.cookie).allowed
    assert security.login("new", PASSWORD, record, ORIGIN).allowed


def test_concurrent_successful_logins_cannot_overbook_session_capacity():
    clock = [1_700_000_000]
    security = policy(clock, max_sessions=1)
    record = security.create_password_record(PASSWORD)
    start = threading.Barrier(4)
    def login(index):
        start.wait(timeout=3)
        return security.login(str(index), PASSWORD, record, ORIGIN)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(login, range(4)))
    assert sum(result.allowed for result in results) == 1
    assert sum(result.reason == "session_capacity" for result in results) == 3


@pytest.mark.parametrize("bad", ["origin", "csrf"])
async def test_rejected_logout_does_not_revoke_the_valid_session(tmp_path, bad):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        response = await client.post("/logout", data={"csrf":
            "wrong" if bad == "csrf" else site.security.csrf_token(cookie)},
            headers={"Origin": "https://other.invalid" if bad == "origin" else site.origin},
            allow_redirects=False)
        assert response.status == 403
        assert site.security.authorize("GET", site.origin, cookie).allowed
        assert (await client.get("/", allow_redirects=False)).status == 200
    finally:
        await client.close()
        await server.close()


async def test_full_session_registry_is_not_misreported_as_a_wrong_password(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        site.security.max_sessions = 1
        response = await client.post("/login", data={"password": PASSWORD},
                                     headers={"Origin": site.origin}, allow_redirects=False)
        body = await response.text()
        assert response.status == 200 and "active session limit" in body
        assert "Check the password" not in body and cookie not in body
        assert site.security.authorize("GET", site.origin, cookie).allowed
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("end", ["expire", "revoke"])
async def test_audio_form_does_not_fail_or_render_new_grants_if_session_ends_during_inventory(tmp_path, end):
    site, server, client, cookie, path = await configured_site(tmp_path)
    async def inventory():
        if end == "expire":
            site.security._clock = lambda: 10**12
        else:
            site.security.revoke(cookie)
        return {"schema": "aikey-camera-preflight/1", "cameras": []}
    site.inventory_loader = inventory
    try:
        before = path.read_bytes()
        response = await client.get("/speech-permissions", allow_redirects=False)
        assert response.status == 303 and response.headers["Location"] == "/login"
        assert path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("end", ["expire", "revoke"])
async def test_audio_save_is_refused_if_session_ends_while_loading_snapshot(tmp_path, monkeypatch, end):
    site, server, client, cookie, path = await configured_site(tmp_path)
    try:
        data = {"csrf": site.security.csrf_token(cookie), "revision": site.aikey.snapshot().revision,
                "mode": "disable_all", "confirm": "on"}
        real = site.aikey.snapshot
        def load_then_end():
            snapshot = real()
            if end == "expire":
                site.security._clock = lambda: 10**12
            else:
                site.security.revoke(cookie)
            return snapshot
        monkeypatch.setattr(site.aikey, "snapshot", load_then_end)
        before = path.read_bytes()
        response = await client.post("/speech-permissions", data=data,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 403 and path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


async def test_expiry_between_authorization_and_main_form_token_redirects_without_server_error(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        now = int(time.time())
        calls = 0
        def crossing_expiry():
            nonlocal calls
            calls += 1
            return now if calls == 1 else now + 5000
        site.security._clock = crossing_expiry
        before = path.read_bytes()
        response = await client.get("/", allow_redirects=False)
        assert response.status == 303 and response.headers["Location"] == "/login"
        assert path.read_bytes() == before
    finally:
        await client.close()
        await server.close()
