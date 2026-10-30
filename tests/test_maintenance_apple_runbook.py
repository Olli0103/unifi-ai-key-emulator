"""Actual frozen Apple tool/engine processes; synthetic host and owned TLS only."""

import asyncio
import hashlib
import json
from pathlib import Path
import sys

import pytest

from aikey.admin_maintenance import Bridge, MaintenanceError, work_once
from aikey.maintenance_runbooks import prepare_apple_update
from aikey import apple_supervisor as supervisor
from aikey.tls import ensure_identity_certificate
from test_apple_upgrade import Host, _entry, _image, OLD, OLD_IMAGE
from test_admin_runtime_observation import target


IMAGE = "sha256:" + "a" * 64


def host_fixture(tmp_path, run_rc=0):
    root = tmp_path / "supervisor"
    root.mkdir(mode=0o700)
    entry = _entry()
    entry["configuration"]["mounts"][1]["source"] = str(tmp_path)
    supervisor.pin(root, "aiport-mac", OLD, run=Host([entry]))
    # The source is a synthetic container engine fixture, not a real engine.
    (tmp_path / "engine_fixture.py").write_bytes(Path(__file__).with_name("test_apple_upgrade.py").read_bytes())
    state = tmp_path / "engine-state.json"
    state.write_text(json.dumps({"entries": [entry], "images": {OLD_IMAGE: _image(), IMAGE: _image()}, "run_rc": run_rc}))
    engine = tmp_path / "engine"
    engine.write_text(f'#!{sys.executable}\n' + '''import json, pathlib, sys
root=pathlib.Path(__file__).parent
sys.path.insert(0,str(root))
from engine_fixture import Host
path=root/"engine-state.json"
data=json.loads(path.read_text())
host=Host(data["entries"],data["images"],run_rc=data["run_rc"])
with (root/"engine-calls.jsonl").open("a") as log: log.write(json.dumps(sys.argv[1:])+"\\n")
code,out=host(["/usr/local/bin/container",*sys.argv[1:]])
data["entries"]=list(host.entries.values())
path.write_text(json.dumps(data))
print(out)
sys.exit(code)
''')
    engine.chmod(0o700)
    config = tmp_path / "config.json"
    config.write_bytes(b'{"fictional":"configuration-and-identity-unchanged"}\n')
    archive = tmp_path / "recovery.tar"
    archive.write_bytes(b"fictional-retained-recovery-bytes-not-a-live-restore-drill")
    return root, engine, config, archive


@pytest.mark.parametrize("run_rc", [0, 1])
async def test_owned_apple_swap_or_verified_rollback_preserves_identity_config(tmp_path, run_rc):
    root, engine, config, archive = host_fixture(tmp_path, run_rc)
    health = {"service": "aiport-candidate", "adopted": True, "control_connected": True,
              "smart_events_entered": 0, "smart_events_left": 0,
              "active_streams": 2, "streams_with_decoded_frames": 2}
    peer, requests, url = await target(tmp_path / "tls", health)
    (config.parent / "device.crt").write_bytes((tmp_path / "tls" / "device.crt").read_bytes())
    host, bridge = tmp_path / "authority", tmp_path / "bridge"
    before, recovery = config.read_bytes(), archive.read_bytes()
    try:
        operation = await asyncio.to_thread(prepare_apple_update, host, bridge, config=config,
            state_dir=root, service="aiport-mac", new_name="aiport-new", image=IMAGE,
            kind="aiport", health_url=url, certificate=tmp_path / "tls" / "device.crt",
            recovery_archive=archive, streams=2, engine=engine)
        Bridge(bridge).submit(operation, hashlib.sha256(before).hexdigest(), "c" * 32)
        await asyncio.to_thread(work_once, host, bridge)
        assert Bridge(bridge).receipt("c" * 32)["state"] == ("succeeded" if run_rc == 0 else "rolled_back")
        state = json.loads((tmp_path / "engine-state.json").read_text())
        running = [row for row in state["entries"] if row["status"]["state"] == "running"]
        assert len(running) == 1 and running[0]["id"] == ("aiport-new" if run_rc == 0 else OLD)
        assert config.read_bytes() == before and archive.read_bytes() == recovery
        assert len(requests) >= 3
        assert all(not any(name.lower() in {"authorization", "cookie"} for name in row) for row in requests)
        pin = json.loads((root / supervisor.SPEC_FILE).read_text())["services"][0]
        assert pin["container"] == running[0]["id"]
    finally:
        await peer.close()


async def test_nonidle_runtime_is_refused_before_hold_or_stop(tmp_path):
    root, engine, config, archive = host_fixture(tmp_path)
    health = {"service": "aiport-candidate", "adopted": True, "control_connected": True,
              "smart_events_entered": 1, "smart_events_left": 0,
              "active_streams": 2, "streams_with_decoded_frames": 2}
    peer, requests, url = await target(tmp_path / "tls", health)
    (config.parent / "device.crt").write_bytes((tmp_path / "tls" / "device.crt").read_bytes())
    host, bridge = tmp_path / "authority", tmp_path / "bridge"
    try:
        operation = await asyncio.to_thread(prepare_apple_update, host, bridge, config=config,
            state_dir=root, service="aiport-mac", new_name="aiport-new", image=IMAGE,
            kind="aiport", health_url=url, certificate=tmp_path / "tls" / "device.crt",
            recovery_archive=archive, streams=2, engine=engine)
        before = (root / supervisor.SPEC_FILE).read_bytes()
        Bridge(bridge).submit(operation, hashlib.sha256(config.read_bytes()).hexdigest(), "d" * 32)
        await asyncio.to_thread(work_once, host, bridge)
        assert Bridge(bridge).receipt("d" * 32)["state"] == "rejected"
        calls = [json.loads(row) for row in (tmp_path / "engine-calls.jsonl").read_text().splitlines()]
        assert not any(row[0] in {"stop", "start", "run", "delete"} for row in calls)
        assert (root / supervisor.SPEC_FILE).read_bytes() == before and len(requests) == 1
    finally:
        await peer.close()


def test_mutable_image_is_not_published_as_an_update(tmp_path):
    with pytest.raises(MaintenanceError, match="invalid_apple_update_plan"):
        prepare_apple_update(tmp_path / "authority", tmp_path / "bridge", config=tmp_path / "config",
            state_dir=tmp_path / "state", service="aiport-mac", new_name="new", image="local:latest",
            kind="aiport", health_url="https://127.0.0.1/healthz", certificate=tmp_path / "cert",
            recovery_archive=tmp_path / "backup", streams=2)
    assert not (tmp_path / "bridge").exists()


async def test_trusted_but_wrong_health_leaf_is_refused_before_http_and_stop(tmp_path):
    root, engine, config, archive = host_fixture(tmp_path)
    own = tmp_path / "own-tls"
    ensure_identity_certificate(own, "020000000098")
    peer, requests, url = await target(tmp_path / "other-tls", {
        "service": "aiport-candidate", "adopted": True, "control_connected": True,
        "smart_events_entered": 0, "smart_events_left": 0,
        "active_streams": 2, "streams_with_decoded_frames": 2})
    certificate = own / "device.crt"
    certificate.write_bytes(certificate.read_bytes() + (tmp_path / "other-tls" / "device.crt").read_bytes())
    (config.parent / "device.crt").write_bytes(certificate.read_bytes())
    host, bridge = tmp_path / "authority", tmp_path / "bridge"
    try:
        operation = await asyncio.to_thread(prepare_apple_update, host, bridge, config=config,
            state_dir=root, service="aiport-mac", new_name="aiport-new", image=IMAGE,
            kind="aiport", health_url=url, certificate=certificate,
            recovery_archive=archive, streams=2, engine=engine)
        Bridge(bridge).submit(operation, hashlib.sha256(config.read_bytes()).hexdigest(), "e" * 32)
        await asyncio.to_thread(work_once, host, bridge)
        assert Bridge(bridge).receipt("e" * 32)["state"] == "rejected" and requests == []
        calls = [json.loads(row) for row in (tmp_path / "engine-calls.jsonl").read_text().splitlines()]
        assert not any(row[0] in {"stop", "start", "run", "delete"} for row in calls)
    finally:
        await peer.close()
