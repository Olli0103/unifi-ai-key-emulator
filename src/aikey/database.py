"""Optional PostgreSQL credential rotation with a durable recovery credential.

Construction performs no network I/O. The hook is called before management
credentials are changed, so a database failure leaves management login intact.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import ipaddress
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Awaitable, Callable

from .config import atomic_private


ROLE = "unifi-protect"
MAX_SECRET_BYTES = 4096


class DatabaseRotationError(RuntimeError):
    """A rotation failed or needs recovery. Messages never contain credentials."""


def _validate_password(password: str) -> None:
    if not isinstance(password, str) or not password or any(char in password for char in "\x00\r\n"):
        raise DatabaseRotationError("Database password must be nonempty and contain no line breaks or NUL")
    try:
        size = len(password.encode("utf-8"))
    except UnicodeError:
        raise DatabaseRotationError("Database password must be valid UTF-8") from None
    if size > MAX_SECRET_BYTES:
        raise DatabaseRotationError("Database password exceeds the local size limit")


def _password_statement(password: str):
    """Utility statements need SQL literals, not server-side bind parameters."""
    try:
        from psycopg import sql
    except ImportError:
        raise DatabaseRotationError("Install the optional PostgreSQL dependency before enabling rotation") from None
    return sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(ROLE), sql.Literal(password))


async def _default_connect(**kwargs):
    try:
        from psycopg import AsyncConnection
    except ImportError:
        raise DatabaseRotationError("Install the optional PostgreSQL dependency before enabling rotation") from None
    return await AsyncConnection.connect(**kwargs)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_secret(path: Path, password: str) -> None:
    atomic_private(path, password + "\n")
    _sync_directory(path.parent)


def _read_secret(path: Path, *, optional: bool = False) -> str | None:
    if optional and not path.exists():
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise DatabaseRotationError("Database secret files must be regular files with private permissions")
            raw = handle.read(MAX_SECRET_BYTES + 2)
        if len(raw) > MAX_SECRET_BYTES + 1:
            raise DatabaseRotationError("Database secret file exceeds the local size limit")
        password = raw.decode("utf-8")
        if password.endswith("\n"):
            password = password[:-1]
        _validate_password(password)
        return password
    except DatabaseRotationError:
        raise
    except (OSError, UnicodeError):
        raise DatabaseRotationError("Cannot read the private database credential file") from None


class PgCredentialRotator:
    """Async DeviceService credential_handler; always rotates unifi-protect.

    A connection factory can be injected for local tests. It receives libpq
    keyword arguments and returns an async connection with execute/commit/
    rollback/close methods. No shell command is used.
    """

    def __init__(
        self, config: dict[str, Any], state_dir: Path,
        *, connection_factory: Callable[..., Awaitable[Any]] | None = None,
    ):
        self.options = dict(config.get("database", {}))
        self.state_dir = Path(state_dir)
        self.password_path = Path(self.options.get("password_file", self.state_dir / "database-password"))
        self.pending_path = self.password_path.with_name(self.password_path.name + ".pending")
        self.lock_path = self.password_path.with_name(self.password_path.name + ".lock")
        self._connection_factory = connection_factory or _default_connect
        self._lock = asyncio.Lock()

    def _connection_options(self) -> dict[str, Any]:
        if self.options.get("user", ROLE) != ROLE or self.options.get("dbname", ROLE) != ROLE:
            raise DatabaseRotationError("Rotation is restricted to the dedicated unifi-protect user and database")
        host = self.options.get("host", "127.0.0.1")
        if not isinstance(host, str) or not host or any(char in host for char in "\r\n\x00, "):
            raise DatabaseRotationError("Configure one explicit database host or Unix socket directory")
        local_socket = host.startswith("/")
        if not local_socket and not re.fullmatch(r"[A-Za-z0-9_.:\-]+", host):
            raise DatabaseRotationError("Database host must be a hostname or IP address")
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        port = self.options.get("port", 5432)
        if type(port) is not int or not 1 <= port <= 65535:
            raise DatabaseRotationError("Database port is invalid")
        result = {
            "host": host, "port": port, "dbname": ROLE, "user": ROLE,
            "connect_timeout": 5, "autocommit": False,
            "application_name": "local-aikey-credential-rotation",
            "options": "-c statement_timeout=10000 -c lock_timeout=5000",
        }
        root_cert = self.options.get("sslrootcert")
        if local_socket:
            result["sslmode"] = "disable"
        elif root_cert:
            if not Path(root_cert).is_file():
                raise DatabaseRotationError("Database CA file is missing")
            result.update(sslmode="verify-full", sslrootcert=str(root_cert))
        elif loopback:
            result["sslmode"] = "require"
        else:
            raise DatabaseRotationError("Remote database rotation requires sslrootcert and verified TLS")
        if self.options.get("sslmode", result["sslmode"]) != result["sslmode"]:
            raise DatabaseRotationError("Database TLS mode cannot weaken the rotation profile")
        return result

    async def _connect_candidates(self, options: dict, candidates: list[str | None]):
        seen = set()
        for password in candidates:
            if password is None or password in seen:
                continue
            seen.add(password)
            try:
                connection = await self._connection_factory(**options, password=password)
                return connection, password
            except asyncio.CancelledError:
                raise
            except DatabaseRotationError:
                raise
            except Exception:
                # Connect failures can lack SQLSTATE, including libpq auth errors.
                # Only the bounded local current/pending candidates are tried.
                continue
        raise DatabaseRotationError("Database connection failed using the recorded recovery credentials") from None

    def _remove_pending(self) -> None:
        self.pending_path.unlink(missing_ok=True)
        _sync_directory(self.pending_path.parent)

    async def __call__(self, username: str, new_password: str) -> None:
        if self.options.get("enabled") is not True:
            raise DatabaseRotationError("Database credential rotation is disabled")
        # Management usernames never select a PostgreSQL role.
        if not isinstance(username, str) or not username:
            raise DatabaseRotationError("Management username must be nonempty")
        _validate_password(new_password)
        options = self._connection_options()
        timeout = self.options.get("timeout_s", 30)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise DatabaseRotationError("Database rotation timeout must be between 0 and 120 seconds")
        async with self._lock:
            self.password_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise DatabaseRotationError("Another database credential rotation is running") from None
                try:
                    async with asyncio.timeout(timeout):
                        await self._rotate_locked(options, new_password)
                except TimeoutError:
                    raise DatabaseRotationError("Database rotation timed out; retain its pending credential for retry") from None
            finally:
                os.close(descriptor)

    async def _rotate_locked(self, options: dict, new_password: str) -> None:
        connection = None
        committed = False
        try:
            pending = _read_secret(self.pending_path, optional=True)
            current = _read_secret(self.password_path, optional=pending is not None)
            if pending is not None and pending != new_password:
                connection, _ = await self._connect_candidates(options, [current, pending])
                # Resolve an earlier uncertain commit before replacing its only
                # possible recovery credential. Reapplying it also works with an
                # explicitly configured trusted Unix socket, where successful
                # login does not prove that a supplied password was checked.
                await connection.execute(_password_statement(pending))
                await connection.commit()
                _write_secret(self.password_path, pending)
                self._remove_pending()
                current = pending
            _write_secret(self.pending_path, new_password)
            if connection is None:
                connection, _ = await self._connect_candidates(options, [current, new_password])
            await connection.execute(_password_statement(new_password))
            await connection.commit()
            committed = True
            _write_secret(self.password_path, new_password)
            self._remove_pending()
        except asyncio.CancelledError:
            raise
        except DatabaseRotationError:
            raise
        except Exception:
            raise DatabaseRotationError("Database rotation was not finalized; its pending credential was retained for retry") from None
        finally:
            if connection is not None:
                if not committed:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(connection.rollback(), timeout=2)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(connection.close(), timeout=2)
