"""Full saved Port policy edits retain identity/pairings and role credentials."""

import json

import pytest

from aikey.aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from test_aiport_config_store import fixture, settings
from test_aiport_permission_restrictions import roles
from test_aiport_provider_rollback import digest


def test_threshold_preview_apply_and_full_rollback_are_exact_and_private(tmp_path):
    store, path, original = fixture(tmp_path)
    before = path.read_bytes()
    revision = store.snapshot().revision
    patch = {"live_pool_detector": {"threshold": .72}}
    preview = store.preview_patch(revision, patch)
    assert path.read_bytes() == before and not store.history_dir.exists()
    assert preview.changed_fields == ("live_pool_detector.threshold",)
    result = store.apply_patch(revision, patch)
    assert result.revision == preview.resulting_revision
    saved = json.loads(path.read_text())
    expected = dict(original)
    expected["live_pool_detector"] = {**original["live_pool_detector"], "threshold": .72}
    assert saved == expected
    assert (store.history_dir / (revision + ".json")).read_bytes() == before
    restored = store.rollback(result.revision, revision)
    assert restored.revision == revision and path.read_bytes() == before


def test_full_role_removal_preserves_raw_other_permissions_and_templates(tmp_path):
    store, path, _ = fixture(tmp_path)
    original = roles(path)
    before = path.read_bytes()
    revision = store.snapshot().revision
    result = store.apply_patch(revision, {"live_face": None})
    expected = dict(original)
    del expected["live_face"]
    assert json.loads(path.read_text()) == expected
    assert store.rollback(result.revision, revision).revision == revision
    assert path.read_bytes() == before


@pytest.mark.parametrize("patch", [
    {"mac": "020000000077"}, {"controller_ip": "192.168.10.2"},
    {"device_ip": "192.168.10.99"}, {"controller_pin": "b" * 64},
    {"paired_streams": []}, {"firmware_version": "99.0"},
    {"live_pool_detector": {"threshold": float("inf")}},
    {"live_pool_detector": {"provider_config": {"api_key_file": "/private/canary"}}},
])
def test_immutable_or_secret_or_invalid_patches_write_nothing(tmp_path, patch):
    store, path, _ = fixture(tmp_path)
    # Coordination lock creation is not a configuration/history mutation.
    with store._locked():
        pass
    before = digest(tmp_path)
    revision = store.snapshot().revision
    for method in (store.preview_patch, store.apply_patch):
        with pytest.raises(AiPortConfigurationError):
            method(revision, patch)
    assert digest(tmp_path) == before and not store.history_dir.exists()


def test_a_generic_provider_route_change_cannot_reuse_its_key(tmp_path):
    store, path, _ = fixture(tmp_path)
    key = tmp_path / "openai-key"
    key.write_text("fictional-never-issued-provider-key")
    key.chmod(0o600)
    store.apply(store.snapshot().revision, settings(tmp_path))
    before = digest(tmp_path)
    revision = store.snapshot().revision
    patch = {"live_pool_detector": {"provider_config": {"provider": "openai-compatible",
        "base_url": "https://different.example/v1"}}}
    with pytest.raises(AiPortConfigurationError):
        store.apply_patch(revision, patch)
    assert digest(tmp_path) == before


def test_stale_apply_and_rollback_never_overwrite_a_later_revision(tmp_path):
    store, path, _ = fixture(tmp_path)
    revision = store.snapshot().revision
    current = store.apply_patch(revision, {"live_pool_detector": {"threshold": .61}})
    before = digest(tmp_path)
    with pytest.raises(AiPortRevisionConflict):
        store.apply_patch(revision, {"live_pool_detector": {"threshold": .8}})
    with pytest.raises(AiPortRevisionConflict):
        store.rollback(revision, revision)
    assert store.snapshot().revision == current.revision and digest(tmp_path) == before


def test_missing_archived_key_blocks_full_rollback_before_configuration_write(tmp_path):
    store, path, _ = fixture(tmp_path)
    key = tmp_path / "openai-key"
    key.write_text("fictional-never-issued-key")
    key.chmod(0o600)
    store.apply(store.snapshot().revision, settings(tmp_path))
    prior = store.snapshot().revision
    current = store.apply_patch(prior, {"live_pool_detector": {"threshold": .62}})
    key.unlink()
    before = digest(tmp_path)
    with pytest.raises(AiPortConfigurationError, match="prior_key_missing"):
        store.rollback(current.revision, prior)
    assert digest(tmp_path) == before
