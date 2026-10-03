"""Private, bounded audit trail of control-site actions (#13).

Each entry records when an administrative action happened, which one, for
which profile and with what result. Fields are allowlisted: never a
password, key, file path, setting value or network address. The log is a
0600 JSON-lines file written with O_APPEND and fsync; at 256 KiB it rotates
to a single ``.1`` file, so it stays bounded.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import time

ACTIONS = frozenset({"login", "logout", "provider_save", "provider_rollback", "rollout_apply",
                     "role_model"})
_RESULT = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
_PROFILE = re.compile(r"(aikey|aiport|aiport:[a-z0-9-]{1,32})\Z")
_MAX_BYTES = 256 * 1024
_MAX_LINE = 512


class AuditLog:
    def __init__(self, path: Path, *, clock=time.time, max_bytes: int = _MAX_BYTES):
        self.path = Path(path)
        self.clock = clock
        self.max_bytes = max_bytes
        self.failures = 0

    def record(self, action: str, result: str, *, profile: str | None = None) -> bool:
        """Append one entry; returns False (and counts it) if it could not be written."""
        if action not in ACTIONS or not isinstance(result, str) or not _RESULT.fullmatch(result):
            raise ValueError("Audit entries use a fixed action and a short result code")
        if profile is not None and (not isinstance(profile, str) or not _PROFILE.fullmatch(profile)):
            profile = "unknown"
        entry = {"at": int(self.clock()), "action": action, "result": result}
        if profile is not None:
            entry["profile"] = profile
        line = (json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n").encode()
        try:
            self._rotate_if_needed(len(line))
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT
                                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                    raise OSError("audit log is not a private regular file")
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, line)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            self.failures += 1
            return False
        return True

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode):
            raise OSError("audit log is a symlink")
        if info.st_size + incoming > self.max_bytes:
            os.replace(self.path, self.path.with_name(self.path.name + ".1"))

    def recent(self, limit: int = 50) -> list[dict]:
        """The newest entries first; malformed lines are skipped, never echoed."""
        entries: list[dict] = []
        for path in (self.path, self.path.with_name(self.path.name + ".1")):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                lines = path.read_bytes().splitlines()
            except OSError:
                continue
            for raw in reversed(lines):
                if len(raw) > _MAX_LINE:
                    continue
                try:
                    value = json.loads(raw)
                except ValueError:
                    continue
                if (isinstance(value, dict) and value.get("action") in ACTIONS
                        and isinstance(value.get("result"), str) and _RESULT.fullmatch(value["result"])
                        and type(value.get("at")) is int):
                    entries.append({key: value[key] for key in ("at", "action", "result", "profile")
                                    if key in value})
                if len(entries) >= limit:
                    return entries
        return entries
