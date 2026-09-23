"""Persistent logical settings for hardware this AI Port candidate does not have."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Callable, TypeVar


_FLAGS = frozenset(("ledFaceEnabled", "ledFaceAlwaysOnWhenManaged",
                    "speakerEnabled", "systemSoundsEnabled"))
_FIELDS = _FLAGS | {"speakerVolume", "welcomeType"}
_TIMEZONE = re.compile(r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*\Z")


class VirtualHardwareError(ValueError):
    """The requested virtual hardware state is invalid or could not be saved."""


T = TypeVar("T")


def _load_private(path: Path, limit: int, validate: Callable[[object], T]) -> T | None:
    if not os.path.lexists(path):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > limit:
                raise VirtualHardwareError("Unsafe virtual hardware file")
            return validate(json.loads(source.read(limit + 1)))
    except (OSError, ValueError, UnicodeError) as exc:
        raise VirtualHardwareError("Unsafe virtual hardware file") from exc


def _save_private(path: Path, value: dict) -> None:
    raw = json.dumps(value, separators=(",", ":")).encode()
    temporary = path.with_name(f".{path.stem}-{secrets.token_hex(8)}")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise VirtualHardwareError("Virtual hardware state could not be persisted") from exc


def validate_sound_led(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise VirtualHardwareError("Invalid sound and LED settings")
    if any(type(payload[name]) is not int or payload[name] not in (0, 1) for name in _FLAGS):
        raise VirtualHardwareError("Invalid sound and LED settings")
    volume = payload["speakerVolume"]
    if (type(volume) is not int or not 0 <= volume <= 100
            or payload["welcomeType"] not in ("text", "image")):
        raise VirtualHardwareError("Invalid sound and LED settings")
    return dict(payload)


class VirtualSoundLedStore:
    def __init__(self, directory: Path):
        self.path = Path(directory) / "virtual-sound-led.json"
        self.settings = _load_private(self.path, 512, validate_sound_led)

    def apply(self, payload: object) -> None:
        settings = validate_sound_led(payload)
        _save_private(self.path, settings)
        self.settings = settings


def validate_timezone(payload: object) -> str:
    if not isinstance(payload, dict) or set(payload) != {"timezone"}:
        raise VirtualHardwareError("Invalid timezone setting")
    timezone = payload["timezone"]
    if (not isinstance(timezone, str) or len(timezone) > 64
            or not _TIMEZONE.fullmatch(timezone) or timezone in (".", "..")):
        raise VirtualHardwareError("Invalid timezone setting")
    return timezone


class VirtualTimezoneStore:
    def __init__(self, directory: Path):
        self.path = Path(directory) / "virtual-timezone.json"
        self.timezone = _load_private(self.path, 128, validate_timezone)

    def apply(self, payload: object) -> None:
        timezone = validate_timezone(payload)
        _save_private(self.path, {"timezone": timezone})
        self.timezone = timezone
