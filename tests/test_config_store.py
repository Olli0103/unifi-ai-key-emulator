"""Transactional admin configuration tests use only synthetic local state."""

import json

import pytest

from aikey.config import initialize
from aikey.config_store import (
    ConfigurationStore,
    ConfigurationStoreError,
    RevisionConflict,
    UnsafeConfigurationChange,
)
from aikey.embedding_profile import ensure_embedding_profile
from aikey.search import EmbeddingService


def store(tmp_path):
    config_path = tmp_path / "config.json"
    initialize(config_path, tmp_path / "state", controller_host="127.0.0.1")
    raw = json.loads(config_path.read_text())
    raw["inference"].update({
        "provider": "openai", "base_url": "https://api.openai.com/v1",
        "model": "synthetic-vision-model", "allow_remote": True,
        "api_key_file": str(tmp_path / "state/provider-key"),
    })
    raw["embeddings"]["bearer_token_file"] = str(tmp_path / "state/embedding-key")
    config_path.write_text(json.dumps(raw, indent=2) + "\n")
    return ConfigurationStore(config_path), config_path


def test_snapshot_redacts_all_secret_references(tmp_path):
    config_store, config_path = store(tmp_path)
    raw = json.loads(config_path.read_text())
    raw["inference"]["authorization_header"] = "synthetic-secret-value"
    config_path.write_text(json.dumps(raw))
    snapshot = config_store.snapshot()
    encoded = json.dumps(snapshot.configuration)
    assert len(snapshot.revision) == 64
    assert "provider-key" not in encoded
    assert "embedding-key" not in encoded
    assert "management-password" not in encoded
    assert "database-password" not in encoded
    assert "synthetic-secret-value" not in encoded
    assert snapshot.configuration["inference"]["api_key_file"] == {
        "configured": True, "write_only": True,
    }


def test_preview_has_no_write_and_apply_archives_previous_revision(tmp_path):
    config_store, config_path = store(tmp_path)
    original = config_path.read_bytes()
    before = config_store.snapshot()
    preview = config_store.preview(before.revision, {"device": {"name": "Synthetic processor"}})
    assert config_path.read_bytes() == original
    assert preview.changed_fields == ("device.name",)
    assert preview.restart_required
    assert preview.resulting_revision != before.revision

    after = config_store.apply(before.revision, {"device": {"name": "Synthetic processor"}})
    assert after.revision == preview.resulting_revision
    assert after.configuration["device"]["name"] == "Synthetic processor"
    assert (tmp_path / ".aikey-config-history" / f"{before.revision}.json").read_bytes() == original
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_stale_or_invalid_apply_preserves_current_bytes(tmp_path):
    config_store, config_path = store(tmp_path)
    before = config_store.snapshot()
    first = config_store.apply(before.revision, {"device": {"name": "First writer"}})
    current = config_path.read_bytes()
    with pytest.raises(RevisionConflict, match="changed since"):
        config_store.apply(before.revision, {"device": {"name": "Stale writer"}})
    with pytest.raises(UnsafeConfigurationChange, match="vision provider"):
        config_store.apply(first.revision, {"inference": {"provider": "unknown"}})
    assert config_path.read_bytes() == current


@pytest.mark.parametrize("patch", [
    {"device": {"mac": "020000000099"}},
    {"runtime": {"state_dir": "/tmp/other-state"}},
    {"device": {"management_password_file": "/tmp/replacement"}},
    {"inference": {"api_key_file": "/tmp/replacement"}},
    {"inference": {"provider_secret": "must-not-be-stored"}},
])
def test_identity_and_secret_changes_require_separate_operations(tmp_path, patch):
    config_store, config_path = store(tmp_path)
    snapshot = config_store.snapshot()
    original = config_path.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        config_store.preview(snapshot.revision, patch)
    assert config_path.read_bytes() == original


def test_existing_search_profile_blocks_embedding_identity_change(tmp_path):
    config_store, config_path = store(tmp_path)
    raw = json.loads(config_path.read_text())
    ensure_embedding_profile(
        tmp_path / "state", EmbeddingService(raw["embeddings"]).identity
    )
    snapshot = config_store.snapshot()
    with pytest.raises(UnsafeConfigurationChange, match="search-index migration"):
        config_store.apply(snapshot.revision, {"embeddings": {"revision": "different"}})


def test_corrupt_search_profile_blocks_unrelated_configuration_change(tmp_path):
    config_store, config_path = store(tmp_path)
    raw = json.loads(config_path.read_text())
    ensure_embedding_profile(
        tmp_path / "state", EmbeddingService(raw["embeddings"]).identity
    )
    profile = tmp_path / "state/search-profile.json"
    corrupted = json.loads(profile.read_text())
    corrupted["fingerprint"] = "0" * 64
    profile.write_text(json.dumps(corrupted))
    snapshot = config_store.snapshot()
    with pytest.raises(UnsafeConfigurationChange, match="profile is invalid"):
        config_store.preview(snapshot.revision, {"device": {"name": "No write"}})


def test_rollback_restores_an_archived_revision_without_losing_newer_version(tmp_path):
    config_store, _ = store(tmp_path)
    original = config_store.snapshot()
    changed = config_store.apply(original.revision, {"device": {"name": "Changed"}})
    restored = config_store.rollback(changed.revision, original.revision)
    assert restored.revision == original.revision
    assert restored.configuration["device"]["name"] == original.configuration["device"]["name"]
    newer = tmp_path / ".aikey-config-history" / f"{changed.revision}.json"
    assert newer.is_file()


def test_config_symlink_and_unknown_rollback_revision_fail_closed(tmp_path):
    config_store, config_path = store(tmp_path)
    target = tmp_path / "actual.json"
    config_path.replace(target)
    config_path.symlink_to(target)
    with pytest.raises(ConfigurationStoreError, match="regular configuration"):
        config_store.snapshot()

    safe_store = ConfigurationStore(target)
    snapshot = safe_store.snapshot()
    with pytest.raises(ConfigurationStoreError, match="regular configuration"):
        safe_store.rollback(snapshot.revision, "0" * 64)


def test_history_directory_symlink_fails_before_configuration_replace(tmp_path):
    config_store, config_path = store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".aikey-config-history").symlink_to(outside, target_is_directory=True)
    snapshot = config_store.snapshot()
    original = config_path.read_bytes()
    with pytest.raises(ConfigurationStoreError, match="history directory is unsafe"):
        config_store.apply(snapshot.revision, {"device": {"name": "No write"}})
    assert config_path.read_bytes() == original
    assert list(outside.iterdir()) == []
