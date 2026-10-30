"""A reviewable plan description must remain bound to the frozen host action."""

import json

import pytest

from aikey import admin_maintenance as module
from test_admin_maintenance import prepared


@pytest.mark.parametrize(("field", "changed"), [
    ("profile", "aiport"), ("kind", "update"),
    ("timeout", 6), ("expected_effect", "offline_replace"),
])
def test_changed_published_description_cannot_execute_another_frozen_action(tmp_path, field, changed):
    host, bridge, source, operation, revision = prepared(tmp_path)
    path = bridge / "catalog" / (operation + ".json")
    row = json.loads(path.read_text())
    row[field] = changed
    path.write_text(json.dumps(row))
    client = module.Bridge(bridge)
    client.submit(operation, revision, "1" * 32)
    assert module.work_once(host, bridge) == {"handled": 1}
    assert client.receipt("1" * 32)["state"] == "rejected"
    assert not (tmp_path / "cold-copy.json").exists()
    assert source.read_bytes() == b'{"fictional":"device-identity-unchanged"}\n'


def update_plan(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("fictional-unchanged")
    script = tmp_path / "approved.py"
    script.write_text('import json\nprint(json.dumps({"schema":"aikey-maintenance-result/1","outcome":"succeeded"}))\n')
    import hashlib
    revision = hashlib.sha256(source.read_bytes()).hexdigest()
    host, bridge = tmp_path / "host", tmp_path / "bridge"
    operation = module.prepare(host, bridge, script=script,
        inputs={"image": "private-registry.invalid/local-candidate@sha256:" + "a" * 64},
        kind="update", profile="aikey", revision=revision, timeout=5,
        expected_effect="offline_replace", guard_files=[str(source)])
    return host, bridge, operation, revision


def test_update_target_is_a_fixed_digest_not_a_private_registry_or_host_path(tmp_path):
    host, bridge, operation, revision = update_plan(tmp_path)
    row, = module.Bridge(bridge).catalog()
    assert row["target_image"] == "sha256:" + "a" * 64
    assert "private-registry.invalid" not in json.dumps(row)
    assert str(tmp_path) not in json.dumps(row)
    module.Bridge(bridge).submit(operation, revision, "2" * 32)
    module.work_once(host, bridge)
    assert module.Bridge(bridge).receipt("2" * 32)["state"] == "succeeded"


def test_changed_update_target_is_refused_before_any_runbook(tmp_path):
    host, bridge, operation, revision = update_plan(tmp_path)
    path = bridge / "catalog" / (operation + ".json")
    row = json.loads(path.read_text())
    row["target_image"] = "sha256:" + "b" * 64
    path.write_text(json.dumps(row))
    module.Bridge(bridge).submit(operation, revision, "3" * 32)
    module.work_once(host, bridge)
    assert module.Bridge(bridge).receipt("3" * 32)["state"] == "rejected"
    assert not (host / "staging").exists() or not list((host / "staging").iterdir())


def test_legacy_unlabelled_target_still_uses_its_frozen_api_plan(tmp_path):
    host, bridge, operation, revision = update_plan(tmp_path)
    path = bridge / "catalog" / (operation + ".json")
    row = json.loads(path.read_text())
    row.pop("target_image", None)
    path.write_text(json.dumps(row))
    module.Bridge(bridge).submit(operation, revision, "4" * 32)
    module.work_once(host, bridge)
    assert module.Bridge(bridge).receipt("4" * 32)["state"] == "succeeded"


def test_changed_valid_restore_purpose_is_not_the_same_approved_scope(tmp_path):
    source = tmp_path / 'source.json'
    source.write_text('fictional-config')
    script = tmp_path / 'approved.py'
    script.write_text('import pathlib\npathlib.Path(' + repr(str(tmp_path / 'effect')) + ').touch()\n')
    import hashlib
    revision = hashlib.sha256(source.read_bytes()).hexdigest()
    host, bridge = tmp_path / 'host', tmp_path / 'bridge'
    operation = module.prepare(host, bridge, script=script, inputs={}, kind='restore',
        profile='aikey', revision=revision, timeout=5, expected_effect='isolated_restore',
        guard_files=[str(source)], purpose='device_file_restore')
    path = bridge / 'catalog' / (operation + '.json')
    row = json.loads(path.read_text())
    row['purpose'] = 'search_index_restore'
    path.write_text(json.dumps(row))
    client = module.Bridge(bridge)
    assert client.catalog()[0]['purpose'] == 'search_index_restore'
    client.submit(operation, revision, '5' * 32)
    module.work_once(host, bridge)
    assert client.receipt('5' * 32)['state'] == 'rejected'
    assert not (tmp_path / 'effect').exists()


@pytest.mark.parametrize(('purpose', 'kind', 'effect', 'inputs', 'error'), [
    ('device_file_backup', 'restore', 'isolated_restore', {}, 'invalid_maintenance_purpose'),
    ('shell', 'backup', 'read_only', {}, 'invalid_maintenance_purpose'),
    ('apple_image_update', 'update', 'offline_replace', {'image': 'mutable:latest'},
     'invalid_maintenance_update_target'),
])
def test_host_cannot_publish_misleading_typed_scope(tmp_path, purpose, kind, effect, inputs, error):
    host, bridge, source, _, revision = prepared(tmp_path)
    before = sorted(p.name for p in (bridge / 'catalog').iterdir())
    with pytest.raises(module.MaintenanceError, match=error):
        module.prepare(host, bridge, script=tmp_path / 'approved.py', inputs=inputs,
            kind=kind, profile='aikey', revision=revision, timeout=5, expected_effect=effect,
            guard_files=[str(source)], purpose=purpose)
    assert sorted(p.name for p in (bridge / 'catalog').iterdir()) == before


def test_oversized_request_history_fails_before_opening_any_payload(tmp_path, monkeypatch):
    host, bridge, *_ = prepared(tmp_path)
    for n in range(129):
        (bridge / 'requests' / (f'{n:032x}.json')).write_text('not-parseable')
    def forbidden_read(*args, **kwargs):
        pytest.fail('capacity must be checked before reading payloads')
    monkeypatch.setattr(module, '_read', forbidden_read)
    with pytest.raises(module.MaintenanceError, match='maintenance_queue_full'):
        module.Bridge(bridge).history()
