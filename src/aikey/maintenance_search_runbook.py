"""Isolated entrypoint; prepared with frozen maintained search_backup source."""

import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys


def main():
    data = json.load(sys.stdin)
    engine = Path(data["engine"])
    if hashlib.sha256(engine.read_bytes()).hexdigest() != data["engine_sha256"]:
        return "rejected"
    backend = runpy.run_path(str(Path(__file__).with_name("search_backup_backend.py")),
                            run_name="_aikey_search_maintenance_backend")

    def run(command, **kwargs):
        if len(command) < 3 or command[:2] != ["container", "exec"]:
            raise ValueError("unsupported_engine_operation")
        # No shell, no credential arguments; database authority stays on its host.
        return subprocess.run([str(engine), *command[1:]],
                              pass_fds=(int(os.environ["AIKEY_MAINTENANCE_LEASE_FD"]),), **kwargs)

    if data["kind"] == "backup":
        value = backend["backup"](run, data["container"], Path(data["output"]),
                                   Path(data["profile"]) if data["profile"] is not None else None)
        manifest = Path(data["output"]) / value["dump"].replace(".dump", ".json")
    elif data["kind"] == "restore":
        manifest = Path(data["manifest"])
    else:
        return "rejected"
    # This function drops only the scratch DB it created; never the live DB.
    result = backend["verify"](run, data["container"], manifest)
    return "succeeded" if result["verified"] is True else "uncertain"


if __name__ == "__main__":
    try:
        outcome = main()
    except Exception:
        outcome = "uncertain"
    print(json.dumps({"schema": "aikey-maintenance-result/1", "outcome": outcome}))
