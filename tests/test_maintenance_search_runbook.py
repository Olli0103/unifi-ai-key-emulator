"""The host adapter invokes maintained backup/restore logic through real exec."""

import hashlib
import json
import sys

import pytest

from aikey.admin_maintenance import Bridge, MaintenanceError, work_once
from aikey.maintenance_runbooks import prepare_search


def fixture(tmp_path, *, fail_restore=False):
    config = tmp_path / "config.json"
    config.write_bytes(b'{"fictional":"configuration-only"}\n')
    engine = tmp_path / "engine"
    engine.write_text(f'#!{sys.executable}\n' + '''import json, pathlib, sys
root=pathlib.Path(__file__).parent
args=sys.argv[1:]
with (root/"calls.jsonl").open("a") as log: log.write(json.dumps(args)+"\\n")
assert args[0]=="exec"
verb=next(a for a in args if a in {"psql","pg_dump","createdb","pg_restore","dropdb"})
if verb=="psql":
    print(json.dumps({"tables":{"ramDetections":2},"embeddings":2,"dimensions":[768]}))
elif verb=="pg_dump":
    sys.stdout.buffer.write(b"fictional-postgres-dump-fixture")
elif verb=="createdb":
    assert args[-1].startswith("aikey_restore_check_")
    (root/"scratch-created").write_text(args[-1])
elif verb=="pg_restore":
    assert (root/"scratch-created").exists()
    assert sys.stdin.buffer.read()==b"fictional-postgres-dump-fixture"
    if (root/"fail-restore").exists(): sys.exit(1)
else:
    assert args[-1]==(root/"scratch-created").read_text()
    (root/"scratch-created").unlink()
''')
    engine.chmod(0o700)
    if fail_restore:
        (tmp_path / "fail-restore").touch()
    return config, engine


@pytest.mark.parametrize("fail_restore", [False, True])
def test_backup_runs_actual_maintained_restore_check_and_collects_scratch(tmp_path, fail_restore):
    config, engine = fixture(tmp_path, fail_restore=fail_restore)
    host, bridge, output = tmp_path / "host", tmp_path / "bridge", tmp_path / "backups"
    before = config.read_bytes()
    operation = prepare_search(host, bridge, kind="backup", config=config,
                               engine=engine, container="synthetic-pg", output=output)
    client = Bridge(bridge)
    client.submit(operation, hashlib.sha256(before).hexdigest(), "a" * 32)
    work_once(host, bridge)
    assert client.receipt("a" * 32)["state"] == ("uncertain" if fail_restore else "succeeded")
    calls = [json.loads(raw) for raw in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert sum("createdb" in args for args in calls) == 1
    assert sum("dropdb" in args for args in calls) == 1
    assert not (tmp_path / "scratch-created").exists()
    assert config.read_bytes() == before
    if not fail_restore:
        manifest = next(output.glob("search-*.json"))
        assert json.loads(manifest.read_text())["verified"]["restore_counts_match"] is True
        restore = prepare_search(host, bridge, kind="restore", config=config,
            engine=engine, container="synthetic-pg", output=output, manifest=manifest)
        client.submit(restore, hashlib.sha256(before).hexdigest(), "b" * 32)
        work_once(host, bridge)
        assert client.receipt("b" * 32)["state"] == "succeeded"
        assert not (tmp_path / "scratch-created").exists()
    assert all(not ("dropdb" in args and args[-1] == "unifi-protect") for args in calls)


def test_restore_refuses_external_manifest_dump_path_before_publication(tmp_path):
    config, engine = fixture(tmp_path)
    output = tmp_path / "backups"
    output.mkdir()
    manifest = output / "untrusted.json"
    manifest.write_text(json.dumps({"dump":"../config.json","sha256":"0"*64}))
    with pytest.raises(MaintenanceError):
        prepare_search(tmp_path / "host", tmp_path / "bridge", kind="restore", config=config,
            engine=engine, container="synthetic-pg", output=output, manifest=manifest)
    assert not (tmp_path / "bridge").exists()
