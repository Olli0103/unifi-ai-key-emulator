"""Real frozen state runbook, synthetic Docker inventory, no runtime mutation."""

import hashlib
import json
import sys

import pytest

from aikey.admin_maintenance import Bridge, work_once
from aikey.maintenance_runbooks import prepare_state
from test_state_snapshot import source, contents


def engine_fixture(tmp_path, directory, *, running=False, becomes_running=False, restarted_between=False):
    state = tmp_path / "engine-state.json"
    state.write_text(json.dumps({"running": running, "source": str(directory),
                                "becomes_running": becomes_running, "restarted_between": restarted_between, "lists": 0}))
    engine = tmp_path / "docker-fixture"
    engine.write_text(f"#!{sys.executable}\n" + '''import json,pathlib,sys
root=pathlib.Path(__file__).parent
path=root/"engine-state.json"
state=json.loads(path.read_text())
args=sys.argv[1:]
with (root/"engine-calls.jsonl").open("a") as log:log.write(json.dumps(args)+"\\n")
if args==["ps","--all","--quiet","--no-trunc"]:
    state["lists"]+=1
    if state["becomes_running"] and state["lists"]>1:state["running"]=True
    print("f"*64)
elif args==["inspect","f"*64]:
    stamp="2026-10-30T00:01:00Z" if state["restarted_between"] and state["lists"]>1 else "2026-10-30T00:00:00Z"
    print(json.dumps([{"Id":"f"*64,"Name":"/synthetic-profile","Image":"sha256:"+"a"*64,"RestartCount":0,
        "State":{"Running":state["running"],"StartedAt":stamp,"FinishedAt":stamp},
        "Mounts":[{"Type":"bind","Source":state["source"],"Destination":"/state"}]}]))
else:raise RuntimeError("fixture forbids runtime mutations")
path.write_text(json.dumps(state))
''')
    engine.chmod(0o700)
    return engine


@pytest.mark.parametrize("kind", ["aikey", "aiport"])
def test_whole_device_state_export_and_fresh_restore_via_one_use_owner(tmp_path, kind):
    directory = source(tmp_path, kind)
    engine = engine_fixture(tmp_path, directory)
    host, bridge, archive = tmp_path / "authority", tmp_path / "bridge", tmp_path / "device.tar"
    before = contents(directory)
    operation = prepare_state(host, bridge, kind="backup", profile_kind=kind,
        directory=directory, archive=archive, container="synthetic-profile", engine=engine,
        profile=kind)
    client = Bridge(bridge)
    revision = hashlib.sha256((directory / "config.json").read_bytes()).hexdigest()
    client.submit(operation, revision, "a" * 32)
    work_once(host, bridge)
    assert client.receipt("a" * 32)["state"] == "succeeded"
    assert all((directory / name).read_bytes() == value for name, value in before.items())
    assert archive.is_file()
    # Source can be running during a restore to a NEW, unmounted offline path.
    state = json.loads((tmp_path / "engine-state.json").read_text())
    state["running"] = True
    (tmp_path / "engine-state.json").write_text(json.dumps(state))
    destination = tmp_path / "offline-restored-profile"
    restore = prepare_state(host, bridge, kind="restore", profile_kind=kind,
        directory=directory, archive=archive, container="synthetic-profile", engine=engine,
        destination=destination, profile=kind)
    client.submit(restore, revision, "b" * 32)
    work_once(host, bridge)
    assert client.receipt("b" * 32)["state"] == "succeeded"
    assert contents(destination) == contents(directory)
    assert all((directory / name).read_bytes() == value for name, value in before.items())
    calls = [json.loads(raw) for raw in (tmp_path / "engine-calls.jsonl").read_text().splitlines()]
    assert all(args[0] in {"ps", "inspect"} for args in calls)
    public = json.dumps(client.catalog())
    assert "fictional-never-issued-key" not in public and str(directory) not in public


def test_running_source_refuses_before_snapshot_export(tmp_path):
    directory = source(tmp_path, "aikey")
    engine = engine_fixture(tmp_path, directory, running=True)
    host, bridge, archive = tmp_path / "authority", tmp_path / "bridge", tmp_path / "must-not-exist.tar"
    operation = prepare_state(host, bridge, kind="backup", profile_kind="aikey",
        directory=directory, archive=archive, container="synthetic-profile", engine=engine)
    client = Bridge(bridge)
    client.submit(operation, hashlib.sha256((directory / "config.json").read_bytes()).hexdigest(), "c" * 32)
    work_once(host, bridge)
    assert client.receipt("c" * 32)["state"] == "rejected"
    assert not archive.exists()


def test_new_writer_after_snapshot_prevents_durable_success_and_keeps_recovery(tmp_path):
    directory = source(tmp_path, "aikey")
    engine = engine_fixture(tmp_path, directory, becomes_running=True)
    host, bridge, archive = tmp_path / "authority", tmp_path / "bridge", tmp_path / "candidate.tar"
    operation = prepare_state(host, bridge, kind="backup", profile_kind="aikey",
        directory=directory, archive=archive, container="synthetic-profile", engine=engine)
    client = Bridge(bridge)
    client.submit(operation, hashlib.sha256((directory / "config.json").read_bytes()).hexdigest(), "d" * 32)
    work_once(host, bridge)
    assert client.receipt("d" * 32)["state"] == "uncertain"
    assert archive.is_file()  # Not falsely erased/reported complete; reconcile it.


def test_restart_then_stop_between_checks_is_not_mistaken_for_a_cold_snapshot(tmp_path):
    directory = source(tmp_path, "aikey")
    engine = engine_fixture(tmp_path, directory, restarted_between=True)
    host, bridge, archive = tmp_path / "authority", tmp_path / "bridge", tmp_path / "candidate.tar"
    operation = prepare_state(host, bridge, kind="backup", profile_kind="aikey",
        directory=directory, archive=archive, container="synthetic-profile", engine=engine)
    client = Bridge(bridge)
    client.submit(operation, hashlib.sha256((directory / "config.json").read_bytes()).hexdigest(), "e" * 32)
    work_once(host, bridge)
    assert client.receipt("e" * 32)["state"] == "uncertain"
    assert json.loads((tmp_path / "engine-state.json").read_text())["running"] is False
    assert archive.is_file()
