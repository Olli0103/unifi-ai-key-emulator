"""Device-state schema migration, refusal, interrupted write and rollback (#24).

Every state here is synthetic and written under tmp_path; no live state is read.
"""

import json
import os

import pytest

from aikey import state_schema
from aikey.device import DeviceService
from aikey.state_schema import (StateSchemaError, backup_path, check_schema, migrate,
                                rollback_state_file, upgrade_state_file)
from test_basic_descriptions import device_config

CREDENTIAL = {"username": "synthetic-admin", "record": "scrypt$synthetic$record"}


def adopted_state(tmp_path):
    """A schema-1 adopted state written by the real DeviceService writer."""
    async def admit(_body):
        return {"accepted": True}
    device = DeviceService(device_config(), tmp_path, admit)
    device._state.update(adopted=True, credential=dict(CREDENTIAL),
                         management={"controller": "192.0.2.1"}, name="Synthetic Key")
    device._save_state()
    return device.state_path, json.loads(device.state_path.read_text())


def as_schema_zero(state):
    """A synthetic older layout: schema 0 kept the name under 'label'."""
    old = dict(state, schema=0, label=state["name"])
    del old["name"]
    return old


def zero_to_one(state):
    upgraded = dict(state, schema=1, name=state.pop("label"))
    return upgraded


MIGRATIONS = {0: zero_to_one}


def test_todays_schema_loads_unchanged_with_identity_and_credential(tmp_path):
    path, state = adopted_state(tmp_path)
    before = path.read_bytes()

    async def admit(_body):
        return {"accepted": True}
    reloaded = DeviceService(device_config(), tmp_path, admit)
    assert reloaded._state["adopted"] is True and reloaded._state["credential"] == CREDENTIAL
    assert path.read_bytes() == before and not list(tmp_path.glob("*.bak"))
    assert upgrade_state_file(path)["migrated"] == []


def test_a_newer_schema_is_refused_and_never_rewritten(tmp_path):
    path, state = adopted_state(tmp_path)
    path.write_text(json.dumps(dict(state, schema=2)))
    before = path.read_bytes()
    with pytest.raises(StateSchemaError, match="newer than this release supports"):
        check_schema(json.loads(before))

    async def admit(_body):
        return {"accepted": True}
    with pytest.raises(StateSchemaError, match="restore device-state.json.schema"):
        DeviceService(device_config(), tmp_path, admit)
    assert path.read_bytes() == before


@pytest.mark.parametrize("schema", [None, "1", 1.0, 0, -1])
def test_a_missing_or_invalid_schema_is_refused(schema):
    with pytest.raises(StateSchemaError):
        check_schema({"schema": schema, "mac": "X", "adopted": False})


def test_a_migration_preserves_identity_and_keeps_one_backup(tmp_path):
    path, state = adopted_state(tmp_path)
    old = as_schema_zero(state)
    path.write_text(json.dumps(old))
    original = path.read_bytes()
    result = upgrade_state_file(path, migrations=MIGRATIONS, oldest=0)
    assert result["migrated"] == [0] and result["backup"] == "device-state.json.schema-0.bak"
    upgraded = json.loads(path.read_text())
    assert upgraded == dict(state)                       # the layout of today, identity intact
    assert upgraded["credential"] == CREDENTIAL and upgraded["adopted"] is True
    assert backup_path(path, 0).read_bytes() == original
    assert oct(path.stat().st_mode)[-3:] == "600" and oct(backup_path(path, 0).stat().st_mode)[-3:] == "600"
    # Idempotent: a second run sees the current schema and changes nothing.
    again = upgrade_state_file(path, migrations=MIGRATIONS, oldest=0)
    assert again["migrated"] == [] and json.loads(path.read_text()) == upgraded


def test_a_migration_that_touches_the_credential_is_refused(tmp_path):
    path, state = adopted_state(tmp_path)
    path.write_text(json.dumps(as_schema_zero(state)))
    before = path.read_bytes()

    def rotate(state):
        return dict(zero_to_one(state), credential={"username": "other", "record": "x"})
    with pytest.raises(StateSchemaError, match="altered the device identity"):
        upgrade_state_file(path, migrations={0: rotate}, oldest=0)
    assert path.read_bytes() == before and not backup_path(path, 0).exists()
    with pytest.raises(StateSchemaError, match="No migration from device state schema 0"):
        migrate(json.loads(before), migrations={}, oldest=0)


def test_an_interrupted_write_keeps_the_original_and_the_retry_converges(tmp_path, monkeypatch):
    path, state = adopted_state(tmp_path)
    path.write_text(json.dumps(as_schema_zero(state)))
    original = path.read_bytes()
    real_replace, calls = os.replace, []

    def crash_on_state(src, dst):
        calls.append(os.path.basename(dst))
        if os.path.basename(dst) == "device-state.json" and len(calls) == 2:
            raise OSError("power lost before the rename")
        return real_replace(src, dst)
    monkeypatch.setattr(state_schema.os, "replace", crash_on_state)
    with pytest.raises(OSError):
        upgrade_state_file(path, migrations=MIGRATIONS, oldest=0)
    assert path.read_bytes() == original                     # the old state still loads
    assert backup_path(path, 0).read_bytes() == original     # the backup was completed first
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".device-state")]
    monkeypatch.setattr(state_schema.os, "replace", real_replace)
    assert upgrade_state_file(path, migrations=MIGRATIONS, oldest=0)["migrated"] == [0]
    assert json.loads(path.read_text()) == dict(state)


def test_rollback_restores_the_pre_upgrade_state_from_a_copy(tmp_path):
    path, state = adopted_state(tmp_path)
    path.write_text(json.dumps(as_schema_zero(state)))
    original = path.read_bytes()
    upgrade_state_file(path, migrations=MIGRATIONS, oldest=0)
    assert rollback_state_file(path, 0, oldest=0) == {
        "restored_schema": 0, "from": "device-state.json.schema-0.bak"}
    assert path.read_bytes() == original
    # A backup from another identity is never restored.
    backup_path(path, 0).write_text(json.dumps(dict(as_schema_zero(state), mac="020000000000")))
    with pytest.raises(StateSchemaError, match="another device identity"):
        rollback_state_file(path, 0, oldest=0)
    with pytest.raises(StateSchemaError, match="No schema 5 backup"):
        rollback_state_file(path, 5)


def test_a_conflicting_existing_backup_blocks_the_upgrade(tmp_path):
    path, state = adopted_state(tmp_path)
    path.write_text(json.dumps(as_schema_zero(state)))
    backup_path(path, 0).write_text(json.dumps({"schema": 0, "mac": "other"}))
    before = path.read_bytes()
    with pytest.raises(StateSchemaError, match="different pre-upgrade backup"):
        upgrade_state_file(path, migrations=MIGRATIONS, oldest=0)
    assert path.read_bytes() == before
