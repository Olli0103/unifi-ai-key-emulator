"""Authentication policy tests use no HTTP server or real credentials."""

import json

import pytest

from aikey.admin_security import AdminSecurity, AdminSecurityError, SESSION_COOKIE_NAME


PASSWORD = "synthetic-admin-passphrase"
ORIGIN = "https://admin.example:8443"


def policy(clock, **options):
    return AdminSecurity(b"s" * 32, ORIGIN, clock=lambda: clock[0], **options)


def test_password_record_is_salted_and_rejects_bad_input():
    first = AdminSecurity.create_password_record(PASSWORD)
    second = AdminSecurity.create_password_record(PASSWORD)
    assert first != second
    assert AdminSecurity.verify_password(PASSWORD, first)
    assert not AdminSecurity.verify_password("wrong-password-value", first)
    assert not AdminSecurity.verify_password(PASSWORD, first + "corrupt")
    assert PASSWORD not in first
    with pytest.raises(AdminSecurityError, match="14 to 1024"):
        AdminSecurity.create_password_record("too-short")


def test_login_session_requires_origin_and_csrf_for_mutations():
    clock = [1_700_000_000]
    security = policy(clock)
    record = security.create_password_record(PASSWORD)
    rejected = security.login("198.51.100.12", PASSWORD, record, "https://other.example")
    assert not rejected.allowed and rejected.reason == "origin_rejected"

    login = security.login("198.51.100.12", PASSWORD, record, ORIGIN)
    assert login.allowed and login.credentials and login.subject == "administrator"
    assert SESSION_COOKIE_NAME.startswith("__Host-")
    read = security.authorize("GET", ORIGIN, login.credentials.cookie)
    assert read.allowed and read.subject == "administrator"
    missing = security.authorize("POST", ORIGIN, login.credentials.cookie)
    assert not missing.allowed and missing.reason == "csrf_rejected"
    mutation = security.authorize(
        "POST", ORIGIN, login.credentials.cookie, login.credentials.csrf_token
    )
    assert mutation.allowed


def test_modified_expired_and_cross_origin_sessions_fail_closed():
    clock = [1_700_000_000]
    security = policy(clock, session_ttl_seconds=60)
    record = security.create_password_record(PASSWORD)
    credentials = security.login("fixture-client", PASSWORD, record, ORIGIN).credentials
    assert credentials is not None
    modified = credentials.cookie[:-1] + ("A" if credentials.cookie[-1] != "A" else "B")
    assert security.authorize("GET", ORIGIN, modified).reason == "invalid_session"
    assert security.authorize("GET", "https://other.example", credentials.cookie).reason == "origin_rejected"
    clock[0] += 61
    assert security.authorize("GET", ORIGIN, credentials.cookie).reason == "invalid_session"


def test_signing_key_rotation_revokes_existing_sessions():
    clock = [1_700_000_000]
    original = policy(clock)
    record = original.create_password_record(PASSWORD)
    credentials = original.login("fixture-client", PASSWORD, record, ORIGIN).credentials
    assert credentials is not None
    rotated = AdminSecurity(b"r" * 32, ORIGIN, clock=lambda: clock[0])
    assert rotated.authorize("GET", ORIGIN, credentials.cookie).reason == "invalid_session"


def test_login_rate_limit_uses_hashed_actor_and_resets_after_window():
    clock = [1_700_000_000]
    security = policy(clock, max_login_attempts=2, login_window_seconds=10)
    record = security.create_password_record(PASSWORD)
    first = security.login("private-client-address", "bad-password-value", record, ORIGIN)
    second = security.login("private-client-address", "bad-password-value", record, ORIGIN)
    limited = security.login("private-client-address", PASSWORD, record, ORIGIN)
    assert first.reason == second.reason == "invalid_credentials"
    assert limited.reason == "rate_limited"
    encoded = json.dumps(limited.audit)
    assert "private-client-address" not in encoded
    assert PASSWORD not in encoded and "bad-password" not in encoded
    clock[0] += 11
    assert security.login("private-client-address", PASSWORD, record, ORIGIN).allowed


@pytest.mark.parametrize("origin", [
    "http://admin.example", "https://user:secret@admin.example", "https://admin.example/path",
    "https://admin.example?token=secret", "https://admin.example/%2fhidden",
])
def test_admin_origin_rejects_plain_remote_http_credentials_and_paths(origin):
    with pytest.raises(AdminSecurityError, match="HTTPS origin"):
        AdminSecurity(b"s" * 32, origin)


def test_loopback_http_requires_explicit_lab_permission():
    with pytest.raises(AdminSecurityError):
        AdminSecurity(b"s" * 32, "http://127.0.0.1:8080")
    security = AdminSecurity(
        b"s" * 32, "http://127.0.0.1:8080", allow_loopback_http=True
    )
    assert security.allowed_origin == "http://127.0.0.1:8080"


def test_audit_fields_are_fixed_and_never_contain_credentials():
    clock = [1_700_000_000]
    security = policy(clock)
    record = security.create_password_record(PASSWORD)
    login = security.login("private-client", PASSWORD, record, ORIGIN)
    assert set(login.audit) == {"action", "actor", "result", "timestamp"}
    encoded = json.dumps(login.audit)
    for secret in (PASSWORD, record, login.credentials.cookie, login.credentials.csrf_token):
        assert secret not in encoded
