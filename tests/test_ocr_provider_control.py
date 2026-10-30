"""Set up a separate OCR route without touching existing camera permission."""

from copy import deepcopy
import json
import socket

import pytest

from aikey.aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from test_aiport_provider_rollback import saved_twice, digest


def ready(root, *, explicit=False):
    store, path, _, _ = saved_twice(root)
    raw = json.loads(path.read_text())
    raw["live_pool_detector"]["plate_cameras"] = [raw["paired_streams"][0]["camera_mac"]]
    if explicit:
        raw["live_pool_detector"]["ocr_provider_config"] = {
            **selection(), "api_key_file": str(root / "ocr-old-key")}
        (root / "ocr-old-key").write_text("synthetic-old-ocr-key")
        (root / "ocr-old-key").chmod(0o600)
    path.write_text(json.dumps(raw) + "\n")
    return store, path


def selection():
    return {"provider": "ollama", "model": "synthetic-ocr", "base_url": "http://127.0.0.1:11435",
            "allow_remote": False, "allow_insecure_http": False, "max_output_tokens": 256}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail("OCR administration must not contact models or resolve endpoints")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.mark.parametrize("explicit", [False, True])
def test_preview_save_and_exact_undo_change_only_ocr(tmp_path, explicit):
    store, path = ready(tmp_path, explicit=explicit)
    before = path.read_bytes()
    original = json.loads(before)
    state = digest(tmp_path)
    revision = store.snapshot().revision
    preview = store.preview_ocr_provider(revision, {**selection(), "model": "synthetic-ocr-next"})
    assert digest(tmp_path) == state
    result = store.apply_ocr_provider(revision, {**selection(), "model": "synthetic-ocr-next"})
    assert result.revision == preview.resulting_revision
    current = json.loads(path.read_text())
    ocr = current["live_pool_detector"].pop("ocr_provider_config")
    if explicit:
        original["live_pool_detector"].pop("ocr_provider_config")
        assert ocr["api_key_file"] == str(tmp_path / "ocr-old-key")
    else:
        assert "api_key_file" not in ocr  # never inherit the detection credential
    assert current == original
    store.rollback_provider(result.revision)
    assert path.read_bytes() == before


def test_changed_destination_drops_only_existing_ocr_key(tmp_path):
    store, path = ready(tmp_path, explicit=True)
    old = json.loads(path.read_text())["live_pool_detector"]["provider_config"]
    store.apply_ocr_provider(store.snapshot().revision,
                             {**selection(), "base_url": "http://127.0.0.1:11436"})
    raw = json.loads(path.read_text())["live_pool_detector"]
    assert "api_key_file" not in raw["ocr_provider_config"]
    assert raw["provider_config"] == old


def test_noop_preserves_raw_bytes(tmp_path):
    store, path = ready(tmp_path, explicit=True)
    raw = json.loads(path.read_text())
    path.write_text(json.dumps(raw, indent=1) + "\n")
    before = digest(tmp_path)
    revision = store.snapshot().revision
    preview = store.preview_ocr_provider(revision, selection())
    assert not preview.restart_required and preview.resulting_revision == revision
    assert store.apply_ocr_provider(revision, selection()).revision == revision
    assert digest(tmp_path) == before


def test_sibling_slot_status_has_no_credential_reference(tmp_path):
    store, path = ready(tmp_path / "first", explicit=True)
    _, sibling = ready(tmp_path / "second", explicit=True)
    before = digest(sibling.parent)
    status = store.ocr_provider_status()
    assert status["key_configured"] and status["assignment"] == "explicit"
    assert "api_key_file" not in json.dumps(status) and "ocr-old-key" not in json.dumps(status)
    store.apply_ocr_provider(store.snapshot().revision, {**selection(), "model": "changed-ocr"})
    assert digest(sibling.parent) == before


@pytest.mark.parametrize("fault", ["scope", "revision", "path", "policy", "cloud_key"])
def test_ocr_refusal_precedes_any_writes(tmp_path, fault):
    store, path = ready(tmp_path)
    choice = deepcopy(selection())
    revision = store.snapshot().revision
    if fault == "scope":
        raw = json.loads(path.read_text())
        del raw["live_pool_detector"]["plate_cameras"]
        path.write_text(json.dumps(raw) + "\n")
        revision = store.snapshot().revision
    elif fault == "revision":
        revision = "0" * 64
    elif fault == "path":
        choice["api_key_file"] = str(tmp_path.parent / "outside-key")
    elif fault == "policy":
        choice["plate_cameras"] = ["2A1122334456"]
    else:
        choice.update(provider="openai", base_url="https://api.openai.com/v1", allow_remote=True)
    before = digest(tmp_path)
    with pytest.raises((AiPortConfigurationError, AiPortRevisionConflict)):
        store.apply_ocr_provider(revision, choice)
    assert digest(tmp_path) == before
