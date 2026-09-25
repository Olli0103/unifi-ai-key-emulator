"""Provider changes preserve adopted AI Port identity and camera pairing."""

import hashlib
import json
import ssl
import sys

import pytest

from aikey.aiport_config_store import (
    AiPortConfigurationError, AiPortConfigurationStore, AiPortRevisionConflict,
)
from aikey.tls import ensure_identity_certificate


CAMERAS = ("2A1122334455", "2A1122334456")


def fixture(tmp_path):
    certificate, _ = ensure_identity_certificate(tmp_path, "2A1100F0A55E")
    (tmp_path / "controller-ca.pem").write_bytes(certificate.read_bytes())
    (tmp_path / "controller-ca.pem").chmod(0o600)
    config = {
        "controller_ip": "192.168.10.1", "device_ip": "192.168.10.20",
        "mac": "2A1100F0A55E",
        "controller_pin": hashlib.sha256(ssl.PEM_cert_to_DER_cert(
            certificate.read_text())).hexdigest(),
        "firmware_version": "5.1.12",
        "paired_streams": [{"camera_mac": mac, "source_ip": "192.168.10.1",
                            "ffmpeg_path": sys.executable} for mac in CAMERAS],
        "live_pool_detector": {
            "checkpoint_path": str(tmp_path / "model.pth"), "checkpoint_sha256": "a" * 64,
            "threshold": 0.3, "smart_types": ["person"],
            "max_events_per_hour": 12},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config) + "\n")
    path.chmod(0o600)
    return AiPortConfigurationStore(path), path, config


def settings(tmp_path):
    return {"provider": "openai", "model": "gpt-6-luna",
            "base_url": "https://api.openai.com/v1", "allow_remote": True,
            "allow_insecure_http": False, "max_output_tokens": 256,
            "api_key_file": str(tmp_path / "openai-key"),
            "threshold": 0.8, "smart_types": ["person"],
            "max_events_per_hour": 12, "max_requests_per_hour": 24}


def test_preview_and_apply_change_only_detector_and_redact_key_path(tmp_path):
    store, path, original = fixture(tmp_path)
    before = path.read_bytes()
    snapshot = store.snapshot()
    assert snapshot.camera_count == 2 and snapshot.backend == "local"
    preview = store.preview(snapshot.revision, settings(tmp_path))
    assert path.read_bytes() == before
    assert preview.restart_required and preview.resulting_revision != snapshot.revision
    assert "openai-key" not in repr(preview.settings)
    after = store.apply(snapshot.revision, settings(tmp_path))
    assert after.revision == preview.resulting_revision
    assert after.backend == "vision_api" and after.model == "gpt-6-luna"
    assert after.key_configured is True and after.max_requests_per_hour == 24
    saved = json.loads(path.read_text())
    assert saved["paired_streams"] == original["paired_streams"]
    for name in ("mac", "controller_ip", "device_ip", "controller_pin",
                 "firmware_version"):
        assert saved[name] == original[name]
    assert (tmp_path / ".aiport-config-history" / f"{snapshot.revision}.json").read_bytes() == before


def test_request_cap_is_optional_and_off_by_default(tmp_path):
    store, path, _ = fixture(tmp_path)
    uncapped = settings(tmp_path)
    del uncapped["max_requests_per_hour"]
    after = store.apply(store.snapshot().revision, uncapped)
    assert after.max_requests_per_hour is None
    assert "max_requests_per_hour" not in json.loads(path.read_text())["live_pool_detector"]
    capped = store.apply(after.revision, settings(tmp_path))
    assert capped.max_requests_per_hour == 24
    cleared = store.apply(capped.revision, {**settings(tmp_path),
                                            "max_requests_per_hour": None})
    assert cleared.max_requests_per_hour is None


def test_stale_revision_and_invalid_provider_do_not_change_config(tmp_path):
    store, path, _ = fixture(tmp_path)
    revision = store.snapshot().revision
    first = store.apply(revision, settings(tmp_path))
    current = path.read_bytes()
    with pytest.raises(AiPortRevisionConflict):
        store.apply(revision, settings(tmp_path))
    bad = settings(tmp_path)
    bad["max_requests_per_hour"] = 0
    with pytest.raises(AiPortConfigurationError):
        store.apply(first.revision, bad)
    assert path.read_bytes() == current


def test_no_camera_pool_or_key_path_outside_state_is_rejected(tmp_path):
    store, path, _ = fixture(tmp_path)
    before = store.snapshot().revision
    bad = settings(tmp_path)
    bad["api_key_file"] = "/tmp/unrelated-key"
    with pytest.raises(AiPortConfigurationError, match="state_directory"):
        store.preview(before, bad)
    raw = json.loads(path.read_text())
    del raw["paired_streams"]
    del raw["live_pool_detector"]
    path.write_text(json.dumps(raw) + "\n")
    with pytest.raises(AiPortConfigurationError, match="incomplete"):
        store.preview(store.snapshot().revision, settings(tmp_path))


def test_host_editor_validates_container_paths_without_changing_pairing(tmp_path):
    _, path, _ = fixture(tmp_path)
    raw = json.loads(path.read_text())
    for stream in raw["paired_streams"]:
        stream["ffmpeg_path"] = "/container-only/bin/ffmpeg"
    path.write_text(json.dumps(raw) + "\n")
    with pytest.raises(AiPortConfigurationError, match="unavailable"):
        AiPortConfigurationStore(path).snapshot()
    store = AiPortConfigurationStore(path, runtime_state_dir=tmp_path / "runtime-state")
    revision = store.snapshot().revision
    selected = settings(tmp_path)
    selected["api_key_file"] = str(store.runtime_state_dir / "provider-key")
    store.apply(revision, selected)
    saved = json.loads(path.read_text())
    assert saved["paired_streams"] == raw["paired_streams"]
    assert saved["live_pool_detector"]["provider_config"]["api_key_file"] == (
        str(store.runtime_state_dir / "provider-key"))
