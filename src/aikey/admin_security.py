"""Framework-independent authentication policy for the planned admin listener."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from typing import Callable
from urllib.parse import urlsplit


SESSION_COOKIE_NAME = "__Host-aikey_admin"
_PASSWORD_VERSION = "scrypt-v1"
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class AdminSecurityError(ValueError):
    """The administration security policy or stored credential is invalid."""


@dataclass(frozen=True)
class AdminCredentials:
    cookie: str
    csrf_token: str
    expires_at: int


@dataclass(frozen=True)
class AdminDecision:
    allowed: bool
    reason: str
    audit: dict[str, str | int]
    credentials: AdminCredentials | None = None
    subject: str | None = None


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin(value: str, *, allow_loopback_http: bool) -> str:
    try:
        if (not isinstance(value, str) or not value or len(value) > 2048
                or any(char.isspace() for char in value)
                or any(char in value for char in "\\%")):
            raise ValueError
        parsed = urlsplit(value)
        port = parsed.port
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError
        if parsed.scheme != "https" and not (
                parsed.scheme == "http" and allow_loopback_http and _loopback(parsed.hostname)):
            raise ValueError
        default = 443 if parsed.scheme == "https" else 80
        host = parsed.hostname.lower()
        authority = f"[{host}]" if ":" in host else host
        if port is not None and port != default:
            authority += f":{port}"
        return f"{parsed.scheme}://{authority}"
    except (TypeError, ValueError) as exc:
        raise AdminSecurityError("Admin origin must be an HTTPS origin without credentials or a path") from exc


class AdminSecurity:
    """Password, session, CSRF, origin and login-rate policy behind one interface.

    The HTTP adapter persists the password record and signing key, sets the
    cookie attributes, and records the returned allowlisted audit fields.
    """

    def __init__(self, signing_key: bytes, allowed_origin: str, *,
                 session_ttl_seconds: int = 3600, login_window_seconds: int = 300,
                 max_login_attempts: int = 5, allow_loopback_http: bool = False,
                 clock: Callable[[], float] = time.time):
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise AdminSecurityError("Admin signing key must contain at least 32 random bytes")
        if (type(session_ttl_seconds) is not int or not 60 <= session_ttl_seconds <= 43200
                or type(login_window_seconds) is not int or not 10 <= login_window_seconds <= 3600
                or type(max_login_attempts) is not int or not 1 <= max_login_attempts <= 20):
            raise AdminSecurityError("Invalid admin session or login-rate policy")
        self._key = signing_key
        self.allowed_origin = _origin(
            allowed_origin, allow_loopback_http=allow_loopback_http
        )
        self.session_ttl = session_ttl_seconds
        self.login_window = login_window_seconds
        self.max_login_attempts = max_login_attempts
        self._allow_loopback_http = allow_loopback_http
        self._clock = clock
        self._attempts: dict[str, deque[int]] = defaultdict(deque)

    @staticmethod
    def create_password_record(password: str) -> str:
        if (not isinstance(password, str) or len(password) < 14 or len(password) > 1024
                or password.strip() != password or "\x00" in password):
            raise AdminSecurityError(
                "Admin password must contain 14 to 1024 characters without outer whitespace"
            )
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
            dklen=32,
        )
        return "$".join((
            _PASSWORD_VERSION, str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P),
            _b64encode(salt), _b64encode(digest),
        ))

    @staticmethod
    def verify_password(password: str, record: str) -> bool:
        try:
            if (not isinstance(password, str) or len(password) > 1024
                    or not isinstance(record, str) or len(record) > 4096):
                raise ValueError
            version, n, r, p, salt, expected = record.split("$")
            if (version != _PASSWORD_VERSION or int(n) != _SCRYPT_N
                    or int(r) != _SCRYPT_R or int(p) != _SCRYPT_P):
                raise ValueError
            salt_bytes = _b64decode(salt)
            expected_bytes = _b64decode(expected)
            if len(salt_bytes) != 16 or len(expected_bytes) != 32:
                raise ValueError
            actual = hashlib.scrypt(
                password.encode("utf-8"), salt=salt_bytes, n=_SCRYPT_N,
                r=_SCRYPT_R, p=_SCRYPT_P, dklen=32,
            )
            return hmac.compare_digest(actual, expected_bytes)
        except (AttributeError, TypeError, ValueError, UnicodeError):
            return False

    def login(self, client_id: str, password: str, password_record: str,
              origin: str) -> AdminDecision:
        actor = self._actor(client_id)
        now = int(self._clock())
        if not self._same_origin(origin):
            return self._decision(False, "origin_rejected", "login", actor, now)
        attempts = self._attempts[actor]
        while attempts and attempts[0] <= now - self.login_window:
            attempts.popleft()
        if len(attempts) >= self.max_login_attempts:
            return self._decision(False, "rate_limited", "login", actor, now)
        if not self.verify_password(password, password_record):
            attempts.append(now)
            self._trim_attempt_buckets()
            return self._decision(False, "invalid_credentials", "login", actor, now)
        self._attempts.pop(actor, None)
        credentials, subject = self._issue(now)
        return self._decision(
            True, "authenticated", "login", actor, now,
            credentials=credentials, subject=subject,
        )

    def authorize(self, method: str, origin: str, cookie: str,
                  csrf_token: str | None = None) -> AdminDecision:
        now = int(self._clock())
        action = "read" if isinstance(method, str) and method.upper() in _SAFE_METHODS else "mutate"
        if not self._same_origin(origin):
            return self._decision(False, "origin_rejected", action, "session", now)
        claims = self._claims(cookie, now)
        if claims is None:
            return self._decision(False, "invalid_session", action, "session", now)
        subject, session_id = claims
        actor = "session:" + session_id[:16]
        if action == "mutate":
            expected = _b64encode(hmac.digest(self._key, b"csrf\0" + cookie.encode(), "sha256"))
            if not isinstance(csrf_token, str) or not hmac.compare_digest(csrf_token, expected):
                return self._decision(False, "csrf_rejected", action, actor, now)
        return self._decision(True, "authorized", action, actor, now, subject=subject)

    def _same_origin(self, origin: str) -> bool:
        try:
            actual = _origin(origin, allow_loopback_http=self._allow_loopback_http)
        except AdminSecurityError:
            return False
        return hmac.compare_digest(actual, self.allowed_origin)

    def _actor(self, client_id: str) -> str:
        if not isinstance(client_id, str) or not client_id or len(client_id) > 256:
            client_id = "invalid"
        digest = hmac.digest(self._key, b"actor\0" + client_id.encode("utf-8", "replace"), "sha256")
        return "client:" + digest.hex()[:16]

    def _issue(self, now: int) -> tuple[AdminCredentials, str]:
        subject = "administrator"
        session_id = secrets.token_hex(16)
        expires = now + self.session_ttl
        payload = json.dumps(
            {"exp": expires, "iat": now, "sid": session_id, "sub": subject, "v": 1},
            sort_keys=True, separators=(",", ":"),
        ).encode()
        body = _b64encode(payload)
        signature = _b64encode(hmac.digest(self._key, b"session\0" + body.encode(), "sha256"))
        cookie = body + "." + signature
        csrf = _b64encode(hmac.digest(self._key, b"csrf\0" + cookie.encode(), "sha256"))
        return AdminCredentials(cookie, csrf, expires), subject

    def _claims(self, cookie: str, now: int) -> tuple[str, str] | None:
        try:
            if not isinstance(cookie, str) or len(cookie) > 2048 or cookie.count(".") != 1:
                raise ValueError
            body, supplied = cookie.split(".")
            expected = _b64encode(hmac.digest(self._key, b"session\0" + body.encode(), "sha256"))
            if not hmac.compare_digest(supplied, expected):
                raise ValueError
            claims = json.loads(_b64decode(body))
            if (not isinstance(claims, dict)
                    or set(claims) != {"exp", "iat", "sid", "sub", "v"}
                    or claims["v"] != 1 or claims["sub"] != "administrator"
                    or type(claims["iat"]) is not int or type(claims["exp"]) is not int
                    or not isinstance(claims["sid"], str)
                    or len(claims["sid"]) != 32
                    or not 0 <= claims["iat"] <= now < claims["exp"]
                    or claims["exp"] - claims["iat"] != self.session_ttl):
                raise ValueError
            return claims["sub"], claims["sid"]
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _decision(self, allowed: bool, reason: str, action: str, actor: str, now: int,
                  *, credentials: AdminCredentials | None = None,
                  subject: str | None = None) -> AdminDecision:
        return AdminDecision(
            allowed=allowed, reason=reason, credentials=credentials, subject=subject,
            audit={"action": action, "actor": actor, "result": reason, "timestamp": now},
        )

    def _trim_attempt_buckets(self) -> None:
        if len(self._attempts) <= 1024:
            return
        for key in list(self._attempts)[:len(self._attempts) - 1024]:
            self._attempts.pop(key, None)
