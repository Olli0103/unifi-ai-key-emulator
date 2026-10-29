"""Private, certificate-bound adoption state for the AI Port profile."""

from __future__ import annotations

import ipaddress
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import time


class AdoptionError(ValueError):
    """An adoption request or stored state is invalid."""


def _controller_host(entry: object, controller_ip: str, control_port: int) -> bool:
    if not isinstance(entry, str) or len(entry) > 64 or entry.count(":") != 1:
        raise AdoptionError("Invalid controller host")
    address, port = entry.split(":", 1)
    try:
        ipaddress.IPv4Address(address)
        parsed_port = int(port)
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise AdoptionError("Invalid controller host") from exc
    if not port.isdecimal() or not 1 <= parsed_port <= 65535:
        raise AdoptionError("Invalid controller host")
    return address == controller_ip and parsed_port == control_port


_REQUIRED = {"token", "hosts", "protocol"}
_OPTIONAL = {"username", "password", "mode", "nvr", "controller",
             "consoleId", "consoleName"}


def validate_management(value: object, controller_ip: str, control_port: int,
                        username: str | None = None, password: str | None = None) -> str:
    if (not isinstance(value, dict) or not _REQUIRED <= set(value)
            or not set(value) <= _REQUIRED | _OPTIONAL):
        raise AdoptionError("Invalid management fields")
    token = value["token"]
    hosts = value["hosts"]
    if (not isinstance(token, str) or not 16 <= len(token) <= 512
            or any(ord(character) < 33 or ord(character) > 126 for character in token)
            or value["protocol"] != "wss"):
        raise AdoptionError("Invalid management token or protocol")
    if not isinstance(hosts, list) or not 1 <= len(hosts) <= 64:
        raise AdoptionError("Invalid management hosts")
    mode = value.get("mode", 0)
    if not ((type(mode) is int and mode == 0) or mode == "0"):
        raise AdoptionError("Unsupported management mode")
    for name in ("nvr", "controller", "consoleId", "consoleName"):
        if name in value and (not isinstance(value[name], str)
                              or not 1 <= len(value[name]) <= 256):
            raise AdoptionError("Invalid management metadata")
    for name, expected in (("username", username), ("password", password)):
        if name in value and (not isinstance(value[name], str)
                              or expected is None
                              or not hmac.compare_digest(value[name], expected)):
            raise AdoptionError("Conflicting management credentials")
    matches = [_controller_host(entry, controller_ip, control_port) for entry in hosts]
    if not any(matches):
        raise AdoptionError("The pinned controller is missing")
    return token


class AdoptionStore:
    """A pending token becomes an adopted record only after a verified WebSocket 101."""

    def __init__(self, directory: Path, controller_ip: str,
                 controller_pin: str, control_port: int):
        self.path = Path(directory) / "aiport-adoption.json"
        self.binding = {"controller_ip": controller_ip, "controller_pin": controller_pin,
                        "control_port": control_port}
        self.state: dict | None = None
        if not os.path.lexists(self.path):
            return
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 1024:
                    raise AdoptionError("Unsafe adoption file")
                value = json.loads(source.read(1025))
            if not isinstance(value, dict) or value.get("phase") not in ("pending", "adopted"):
                raise AdoptionError("Unsafe adoption file")
            if any(value.get(key) != expected for key, expected in self.binding.items()):
                raise AdoptionError("Adoption state belongs to a different controller")
            if value["phase"] == "pending":
                if (set(value) != set(self.binding) | {"phase", "token", "expires_at"}
                        or not isinstance(value["token"], str)
                        or not 16 <= len(value["token"]) <= 512
                        or any(ord(character) < 33 or ord(character) > 126
                               for character in value["token"])
                        or type(value["expires_at"]) is not int):
                    raise AdoptionError("Unsafe adoption file")
                self.state = value if value["expires_at"] > int(time.time()) else None
            elif set(value) == set(self.binding) | {"phase"}:
                self.state = value
            else:
                raise AdoptionError("Unsafe adoption file")
        except (OSError, ValueError, UnicodeError) as exc:
            raise AdoptionError("Unsafe adoption file") from exc

    @property
    def adopted(self) -> bool:
        return self.state == {**self.binding, "phase": "adopted"}

    @property
    def pending_token(self) -> str | None:
        state = self.state
        if (state is not None and state.get("phase") == "pending"
                and state["expires_at"] > int(time.time())):
            return state["token"]
        return None

    def _save(self, value: dict) -> None:
        raw = json.dumps(value, separators=(",", ":")).encode()
        temporary = self.path.with_name(f".aiport-adoption-{secrets.token_hex(8)}")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600)
            with os.fdopen(fd, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise AdoptionError("Adoption state could not be persisted") from exc
        self.state = value

    def begin(self, token: str, expires_at: int) -> None:
        if self.adopted:
            raise AdoptionError("Already adopted")
        if not int(time.time()) < expires_at <= int(time.time()) + 600:
            raise AdoptionError("Adoption window expired")
        self._save({**self.binding, "phase": "pending", "token": token,
                    "expires_at": expires_at})

    def confirm(self) -> None:
        if self.pending_token is None:
            raise AdoptionError("No pending token")
        self._save({**self.binding, "phase": "adopted"})
