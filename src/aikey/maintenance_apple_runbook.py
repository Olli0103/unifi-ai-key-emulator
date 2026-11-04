"""Frozen Apple same-host upgrade adapter; not a Docker/NAS update adapter."""

import dataclasses
import hashlib
import http.client
import json
from pathlib import Path
import ssl
import sys
from urllib.parse import urlsplit


def main():
    data = json.load(sys.stdin)
    # Only frozen, host-prepared modules staged in our own private root.
    sys.path.insert(0, str(Path(__file__).parent))
    from aikey import apple_upgrade as upgrade
    from aikey import apple_supervisor as supervisor
    from aikey.maintenance_hash import artifact_digest
    if (artifact_digest(data["engine"]) != data["engine_sha256"]
            or artifact_digest(data["recovery_archive"]) != data["recovery_sha256"]):
        return "rejected"

    def run(argv):
        if argv[0] != upgrade.CONTAINER:
            raise ValueError("unsupported_engine_operation")
        return supervisor._run(argv, engine=(data['engine'], data['engine_sha256']))

    def with_host_lease(bound):
        def leased(argv):
            return supervisor._run(argv, engine=(data['engine'], data['engine_sha256']), lease_fd=bound.lease_fd)
        def effect(argv, context):
            from aikey.host_runtime_command import argv_digest
            # Native executor keeps the normal recovery contract. An explicit
            # alternate executor binds its actual path; normal native recovery
            # refuses a mismatched note rather than trusting a foreign client.
            context = dict(context, argv_sha256=argv_digest([data['engine'], *argv[1:]]))
            return supervisor._run(argv, engine=(data['engine'], data['engine_sha256']), lease_fd=bound.lease_fd,
                                   root_fd=bound.root_fd, effect=context)
        leased.with_effect = effect
        return leased
    run.with_host_lease = with_host_lease

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
            from aikey.startup_configuration import revision
            if (revision(result.get('startup_configuration')) != data['config_sha256']
                    or result.get('host_activation_schema') != 'aikey-host-activation/1'):
                raise ValueError('runtime_configuration_changed')
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
                           run=run, idle_timeout=60, ready_timeout=60,
                           configuration=Path(data['config']))
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
