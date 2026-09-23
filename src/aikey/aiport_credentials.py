"""Private AI Port management credentials received over the pinned control channel."""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess


_USERNAME = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
_SHA512_CRYPT = re.compile(r"\$6\$([A-Za-z0-9./]{1,16})\$[A-Za-z0-9./]{86}\Z")


class CredentialError(ValueError):
    """A rotation could not be safely stored or verified."""


def parse_rotation(payload: object) -> tuple[str, str]:
    if not isinstance(payload, dict) or set(payload) != {"username", "hashedPassword"}:
        raise CredentialError("Invalid credential rotation")
    username = payload["username"]
    digest = payload["hashedPassword"]
    if (not isinstance(username, str) or not _USERNAME.fullmatch(username)
            or not isinstance(digest, str) or not _SHA512_CRYPT.fullmatch(digest)):
        raise CredentialError("Invalid credential rotation")
    return username, digest


class CredentialStore:
    """Atomically persist the controller's password hash, never its plain password."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "management-credential.json"
        self.credential: tuple[str, str] | None = None
        if os.path.lexists(self.path):
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as source:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 256:
                        raise CredentialError("Unsafe management credential file")
                    raw = source.read(257)
                self.credential = parse_rotation(json.loads(raw))
            except (OSError, ValueError, UnicodeError) as exc:
                raise CredentialError("Unsafe management credential file") from exc

    def rotate(self, payload: object) -> None:
        username, digest = parse_rotation(payload)
        raw = json.dumps({"username": username, "hashedPassword": digest},
                         separators=(",", ":")).encode()
        temporary = self.directory / f".management-credential-{secrets.token_hex(8)}"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise CredentialError("Management credential could not be persisted") from exc
        self.credential = username, digest

    def verify(self, username: object, password: object) -> bool:
        if self.credential is None or not isinstance(username, str) or not isinstance(password, str):
            return False
        if (not hmac.compare_digest(username, self.credential[0]) or len(password) > 1024
                or any(character in password for character in ("\n", "\r", "\x00"))):
            return False
        match = _SHA512_CRYPT.fullmatch(self.credential[1])
        if match is None:
            return False
        openssl = shutil.which("openssl")
        if openssl is None:
            return False
        try:
            completed = subprocess.run(
                [openssl, "passwd", "-6", "-salt", match.group(1), "-stdin"],
                input=password.encode("utf-8") + b"\n", capture_output=True,
                timeout=3, check=True,
            )
        except (OSError, UnicodeError, subprocess.SubprocessError):
            return False
        return hmac.compare_digest(completed.stdout.strip().decode("ascii", "ignore"),
                                   self.credential[1])
