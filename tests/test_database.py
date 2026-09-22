"""Offline credential-rotation state tests with real psycopg SQL composition."""

import asyncio
import stat

import pytest

from aikey import database
from aikey.config import atomic_private
from aikey.database import DatabaseRotationError, PgCredentialRotator, _password_statement


class FakeDatabase:
    def __init__(self, password, pending_path):
        self.password = password
        self.pending_path = pending_path
        self.events = []
        self.fail = None

    async def connect(self, **kwargs):
        assert self.pending_path.exists(), "The recovery credential must precede database I/O"
        self.events.append(("connect", kwargs))
        if self.fail == "connect" or kwargs["password"] != self.password:
            raise RuntimeError("Rejected fixture credential: " + kwargs["password"])
        owner = self

        class Connection:
            staged = None

            async def execute(self, statement):
                owner.events.append(("execute", statement.as_string()))
                if owner.fail == "execute":
                    raise RuntimeError("SQL failure containing fixture-only-sensitive-text")
                self.staged = owner.pending_path.read_text().removesuffix("\n")

            async def commit(self):
                owner.events.append(("commit",))
                if owner.fail == "commit_before":
                    raise RuntimeError("Commit rejected")
                owner.password = self.staged
                self.staged = None
                if owner.fail == "commit_after":
                    raise RuntimeError("Connection lost after commit")

            async def rollback(self):
                owner.events.append(("rollback",))
                self.staged = None

            async def close(self):
                owner.events.append(("close",))

        return Connection()


@pytest.fixture
def rotation(tmp_path):
    current = tmp_path / "database-password"
    atomic_private(current, "old-fixture-password\n")
    pending = tmp_path / "database-password.pending"
    fake = FakeDatabase("old-fixture-password", pending)
    config = {"database": {"enabled": True, "host": "127.0.0.1", "password_file": str(current)}}
    rotator = PgCredentialRotator(config, tmp_path, connection_factory=fake.connect)
    return rotator, fake, current, pending


async def test_rotation_stages_before_sql_and_commits_before_persisting(rotation, monkeypatch):
    rotator, fake, current, pending = rotation
    real_write = database._write_secret

    def check_write(path, password):
        if path == current:
            assert fake.password == password
            assert any(event[0] == "commit" for event in fake.events)
        real_write(path, password)

    monkeypatch.setattr(database, "_write_secret", check_write)
    await rotator('management-name"; DROP ROLE someone; --', "new-fixture-password")
    assert current.read_text() == "new-fixture-password\n"
    assert stat.S_IMODE(current.stat().st_mode) == 0o600
    assert not pending.exists()
    assert [event[0] for event in fake.events] == ["connect", "execute", "commit", "close"]
    assert fake.events[0][1]["user"] == "unifi-protect"
    assert fake.events[0][1]["sslmode"] == "require"
    assert fake.events[1][1] == 'ALTER ROLE "unifi-protect" PASSWORD \'new-fixture-password\''


def test_sql_uses_real_psycopg_quoting_for_password():
    statement = _password_statement("abc'; DROP ROLE other; --")
    assert statement.as_string() == 'ALTER ROLE "unifi-protect" PASSWORD \'abc\'\'; DROP ROLE other; --\''


async def test_disabled_rotator_performs_no_io(tmp_path):
    async def forbidden(**kwargs):
        pytest.fail("Disabled rotation attempted a connection")

    rotator = PgCredentialRotator({}, tmp_path, connection_factory=forbidden)
    with pytest.raises(DatabaseRotationError, match="disabled"):
        await rotator("user", "fixture-password")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", ["execute", "commit_before", "commit_after", "connect"])
async def test_failures_preserve_pending_and_retry_recovers(rotation, failure):
    rotator, fake, current, pending = rotation
    fake.fail = failure
    with pytest.raises(DatabaseRotationError) as captured:
        await rotator("user", "new-fixture-password")
    assert "fixture-password" not in str(captured.value)
    assert "fixture-only-sensitive-text" not in str(captured.value)
    assert current.read_text() == "old-fixture-password\n"
    assert pending.read_text() == "new-fixture-password\n"
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
    fake.fail = None
    await rotator("user", "new-fixture-password")
    assert fake.password == "new-fixture-password"
    assert current.read_text() == "new-fixture-password\n"
    assert not pending.exists()


async def test_commit_followed_by_disk_failure_recovers_with_pending(rotation, monkeypatch):
    rotator, fake, current, pending = rotation
    real_write = database._write_secret

    def fail_current(path, password):
        if path == current:
            raise OSError("Cannot persist fixture-only-sensitive-text")
        real_write(path, password)

    monkeypatch.setattr(database, "_write_secret", fail_current)
    with pytest.raises(DatabaseRotationError):
        await rotator("user", "new-fixture-password")
    assert fake.password == "new-fixture-password"
    assert current.read_text() == "old-fixture-password\n"
    assert pending.exists()
    monkeypatch.setattr(database, "_write_secret", real_write)
    await rotator("user", "new-fixture-password")
    candidates = [event[1]["password"] for event in fake.events if event[0] == "connect"]
    assert candidates == ["old-fixture-password", "old-fixture-password", "new-fixture-password"]
    assert current.read_text() == "new-fixture-password\n"


async def test_different_request_first_recovers_previous_pending(rotation):
    rotator, fake, current, pending = rotation
    atomic_private(pending, "previous-pending-password\n")
    fake.password = "previous-pending-password"
    await rotator("user", "next-fixture-password")
    assert [event[1]["password"] for event in fake.events if event[0] == "connect"] == [
        "old-fixture-password", "previous-pending-password",
    ]
    assert len([event for event in fake.events if event[0] == "commit"]) == 2
    assert current.read_text() == "next-fixture-password\n"
    assert fake.password == "next-fixture-password"
    assert not pending.exists()


async def test_missing_current_file_recovers_from_pending(rotation):
    rotator, fake, current, pending = rotation
    current.unlink()
    atomic_private(pending, fake.password + "\n")
    await rotator("user", fake.password)
    assert current.read_text() == fake.password + "\n"
    assert not pending.exists()


async def test_private_secret_permissions_are_required(rotation):
    rotator, fake, current, _ = rotation
    current.chmod(0o644)
    with pytest.raises(DatabaseRotationError, match="private permissions"):
        await rotator("user", "next-fixture-password")
    assert not fake.events


async def test_connection_timeout_leaves_recovery_credential(rotation):
    rotator, _, current, pending = rotation

    async def stalled(**kwargs):
        await asyncio.sleep(30)

    rotator._connection_factory = stalled
    rotator.options["timeout_s"] = 0.01
    with pytest.raises(DatabaseRotationError, match="timed out"):
        await rotator("user", "next-fixture-password")
    assert pending.read_text() == "next-fixture-password\n"
    assert current.read_text() == "old-fixture-password\n"


async def test_remote_database_requires_verified_tls(tmp_path):
    rotator = PgCredentialRotator({"database": {"enabled": True, "host": "192.0.2.8"}}, tmp_path)
    with pytest.raises(DatabaseRotationError, match="verified TLS"):
        await rotator("user", "fixture-password")
    assert not list(tmp_path.iterdir())
    ca = tmp_path / "postgres-ca.pem"
    ca.write_text("certificate fixture path, no connection is performed")
    rotator.options["sslrootcert"] = str(ca)
    assert rotator._connection_options()["sslmode"] == "verify-full"
    rotator.options["sslmode"] = "disable"
    with pytest.raises(DatabaseRotationError, match="cannot weaken"):
        rotator._connection_options()


def test_explicit_unix_socket_and_fixed_role(tmp_path):
    rotator = PgCredentialRotator({"database": {"host": "/var/run/postgresql"}}, tmp_path)
    assert rotator._connection_options()["sslmode"] == "disable"
    rotator.options["user"] = "postgres"
    with pytest.raises(DatabaseRotationError, match="restricted"):
        rotator._connection_options()


@pytest.mark.parametrize("password", ["", "a\nb", "a\x00b", "x" * 4097, "\ud800"])
async def test_invalid_new_password_has_no_db_io(rotation, password):
    rotator, fake, _, pending = rotation
    with pytest.raises(DatabaseRotationError):
        await rotator("user", password)
    assert not fake.events
    assert not pending.exists()
