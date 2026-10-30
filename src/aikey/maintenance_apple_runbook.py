"""Frozen Apple same-host upgrade adapter; not a Docker/NAS update adapter."""

import dataclasses
import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
from urllib.parse import urlsplit


def main():
    data = json.load(sys.stdin)
    # Only frozen, host-prepared modules staged in our own private root.
    sys.path.insert(0, str(Path(__file__).parent))
    from aikey import apple_upgrade as upgrade
    from aikey.maintenance_hash import artifact_digest
    if (artifact_digest(data["engine"]) != data["engine_sha256"]
            or artifact_digest(data["recovery_archive"]) != data["recovery_sha256"]):
        return "rejected"

    def run(argv):
        if argv[0] != upgrade.CONTAINER:
            raise ValueError("unsupported_engine_operation")
        result = subprocess.run([data["engine"], *argv[1:]], capture_output=True,
                                text=True, timeout=120, check=False,
                                pass_fds=(int(os.environ["AIKEY_MAINTENANCE_LEASE_FD"]),))
        return result.returncode, result.stdout

    planned = upgrade.plan(Path(data["state_dir"]), data["service"], data["new_name"], data["image"], run=run)
    if hashlib.sha256(json.dumps(dataclasses.asdict(planned), sort_keys=True).encode()).hexdigest() != data["plan_sha256"]:
        return "rejected"
    parsed = urlsplit(data["health_url"])
    context = ssl.create_default_context(cafile=data["certificate"])
    context.check_hostname = False  # Exact saved leaf pin precedes HTTP headers.

    def health():
        peer = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=3, context=context)
        try:
            peer.connect()
            if hashlib.sha256(peer.sock.getpeercert(binary_form=True)).hexdigest() != data["certificate_pin"]:
                raise ValueError("runtime_pin_changed")
            peer.request("GET", "/healthz", headers={"Accept": "application/json"})
            response = peer.getresponse()
            raw = response.read(256 * 1024 + 1)
            if response.status != 200 or len(raw) > 256 * 1024:
                raise ValueError("runtime_health_unavailable")
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError("duplicate_health_field")
                    result[key] = value
                return result
            result = json.loads(raw, object_pairs_hook=pairs,
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            expected = "local-aikey" if data["kind"] == "aikey" else "aiport-candidate"
            if not isinstance(result, dict) or result.get("service") != expected:
                raise ValueError("runtime_service_changed")
            counters = ((result.get("worker"), ("queued", "active", "pending"))
                        if data["kind"] == "aikey" else
                        (result, ("smart_events_entered", "smart_events_left", "active_streams",
                                  "streams_with_decoded_frames")))
            values, names = counters
            if not isinstance(values, dict) or any(type(values.get(name)) is not int
                    or not 0 <= values[name] < 2 ** 63 for name in names):
                raise ValueError("runtime_counters_unavailable")
            return result
        finally:
            peer.close()

    def idle():
        return (upgrade.aikey_idle(health()) if data["kind"] == "aikey"
                else upgrade.aiport_idle(health()))

    def ready():
        return (upgrade.aikey_ready(health()) if data["kind"] == "aikey"
                else upgrade.aiport_ready(health(), data["streams"]))
    try:
        if not idle():
            return "rejected"
    except (OSError, ValueError, http.client.HTTPException):
        return "rejected"  # No service has been stopped or held.
    outcome = upgrade.swap(Path(data["state_dir"]), planned, idle=idle, ready=ready,
                           run=run, idle_timeout=60, ready_timeout=60)
    if outcome == "swapped":
        return "succeeded"
    if outcome.startswith("rolled_back:"):
        return "rolled_back"
    if outcome in {"not_idle", "hold_failed"}:
        return "rejected"
    return "uncertain"


if __name__ == "__main__":
    try:
        outcome = main()
    except Exception:
        outcome = "uncertain"
    print(json.dumps({"schema": "aikey-maintenance-result/1", "outcome": outcome}))
