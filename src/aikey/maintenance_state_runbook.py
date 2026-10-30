"""Host-only cold device-state export and fresh offline restore adapter."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from datetime import datetime


def main():
    data = json.load(sys.stdin)
    root = Path(__file__).parent
    sys.path.insert(0, str(root))
    from aikey import state_snapshot as backend
    from aikey.file_lock import acquire
    from aikey.maintenance_hash import artifact_digest as digest
    if digest(Path(data["engine"])) != data["engine_sha256"]:
        return "rejected"

    def engine(*args):
        result = subprocess.run([data["engine"], *args], capture_output=True, timeout=30, check=False,
                                pass_fds=(int(os.environ["AIKEY_MAINTENANCE_LEASE_FD"]),))
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            raise ValueError("container_inventory_unavailable")
        return result.stdout

    directory = Path(data["directory"])
    def preflight():
        identifiers = engine("ps", "--all", "--quiet", "--no-trunc").decode().splitlines()
        if (not identifiers or len(identifiers) > 256 or len(set(identifiers)) != len(identifiers)
                or any(not re.fullmatch(r"[0-9a-f]{64}", identifier) for identifier in identifiers)):
            raise ValueError("container_inventory_unavailable")
        entries = json.loads(engine("inspect", *identifiers))
        if (not isinstance(entries, list) or len(entries) != len(identifiers)
                or any(not isinstance(item, dict) for item in entries)
                or {item.get("Id") for item in entries} != set(identifiers)):
            raise ValueError("container_inventory_unavailable")
        selected, lifecycle, protected = False, [], (directory if data["kind"] == "backup" else Path(data["destination"]))
        protected = protected.resolve()
        for item in entries:
            running = (item.get("State") or {}).get("Running")
            if type(running) is not bool or not isinstance(item.get("Mounts"), list):
                raise ValueError("container_inventory_unavailable")
            own = item.get("Name") == "/" + data["container"]
            bound, overlaps = False, False
            for mount in item["Mounts"]:
                if not isinstance(mount, dict) or not isinstance(mount.get("Source"), str):
                    raise ValueError("container_inventory_unavailable")
                path = Path(mount["Source"]).resolve()
                bound |= path == directory.resolve()
                overlapping = path == protected or path in protected.parents or protected in path.parents
                overlaps |= overlapping
                if running and overlapping:
                    raise ValueError("state_is_not_offline")
            if overlaps:
                state = item["State"]
                for name in ("StartedAt", "FinishedAt"):
                    value = state.get(name)
                    if (not isinstance(value, str) or not re.fullmatch(
                            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z", value)):
                        raise ValueError("state_lifecycle_unavailable")
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                if (type(item.get("RestartCount")) is not int or not 0 <= item["RestartCount"] < 2 ** 63
                        or not isinstance(item.get("Image"), str)
                        or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["Image"])):
                    raise ValueError("state_lifecycle_unavailable")
                lifecycle.append((item["Id"], item["Image"], item["RestartCount"], state["StartedAt"], state["FinishedAt"]))
            if own:
                selected = bound
        if not selected:
            raise ValueError("profile_runtime_binding_unavailable")
        return sorted(lifecycle)

    lock_path = directory / (".config.json.lock" if data["profile_kind"] == "aikey" else ".config.json.admin.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            return "rejected"
        acquire(fd)
        if hashlib.sha256((directory / "config.json").read_bytes()).hexdigest() != data["revision"]:
            return "rejected"
        try:
            before = preflight()
        except (OSError, ValueError, subprocess.SubprocessError):
            return "rejected"  # No export/destination has been created.
        if data["kind"] == "backup":
            result = backend.backup(directory, Path(data["archive"]), kind=data["profile_kind"],
                                        expected_revision=data["revision"])
            if result["identity"] != data["identity"]:
                return "uncertain"
        else:
            backend.restore(Path(data["archive"]), Path(data["destination"]),
                expected_sha256=data["archive_sha256"], expected_identity=data["identity"],
                expected_revision=data["backup_revision"])
        if preflight() != before:
            raise ValueError("state_lifecycle_changed")
        return "succeeded"
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


if __name__ == "__main__":
    try:
        outcome = main()
    except Exception:
        outcome = "uncertain"
    print(json.dumps({"schema": "aikey-maintenance-result/1", "outcome": outcome}))
