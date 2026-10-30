"""Reject embedding configurations before persistence, not at runtime startup."""

import copy
import json
import socket

import pytest

from aikey.config import ConfigError, defaults, validate_config
from aikey.config_store import ConfigurationStore, UnsafeConfigurationChange


@pytest.fixture(autouse=True)
def no_network_or_model_load(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("configuration validation must remain offline")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    from aikey.search import EmbeddingService
    monkeypatch.setattr(EmbeddingService, "_local_encode", forbidden)


def configuration(tmp_path):
    raw = defaults(tmp_path / "state", "020000000001")
    raw["controller"]["host"] = "127.0.0.1"
    return raw


@pytest.mark.parametrize("backend", ["sentence-transformers", "disabled"])
@pytest.mark.parametrize("model", ["synthetic-wrong-encoder", None, [], 384])
def test_every_backend_refuses_a_runtime_incompatible_model(tmp_path, backend, model):
    raw = configuration(tmp_path)
    raw["embeddings"].update(backend=backend, model=model)
    with pytest.raises(ConfigError, match="Only multilingual-e5-small"):
        validate_config(raw, base=tmp_path)


@pytest.mark.parametrize("backend", ["synthetic-unsupported", "HTTP", "", None, False, 12, [], {}])
def test_unsupported_backends_are_domain_errors_even_with_search_disabled(tmp_path, backend):
    raw = configuration(tmp_path)
    raw["embeddings"]["backend"] = backend
    with pytest.raises(ConfigError, match="embeddings.backend"):
        validate_config(raw, base=tmp_path)


@pytest.mark.parametrize("missing", [False, True])
def test_enabled_e5_search_requires_a_real_encoder_before_activation(tmp_path, missing):
    raw = configuration(tmp_path)
    raw["search"]["enabled"] = True
    if missing:
        raw["embeddings"].pop("backend")
    else:
        raw["embeddings"]["backend"] = "disabled"
    with pytest.raises(ConfigError, match="requires a real embedding backend"):
        validate_config(raw, base=tmp_path)


@pytest.mark.parametrize("operation", ["preview", "apply"])
@pytest.mark.parametrize("patch", [
    {"embeddings": {"backend": "sentence-transformers", "model": "synthetic-wrong-encoder"}},
    {"embeddings": {"backend": "synthetic-unsupported"}},
    {"embeddings": {"backend": "disabled"}, "search": {"enabled": True}},
])
def test_rejected_administration_changes_keep_previous_bytes_and_history(tmp_path, operation, patch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(configuration(tmp_path)) + "\n")
    path.chmod(0o600)
    store = ConfigurationStore(path)
    revision = store.snapshot().revision
    before = path.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        getattr(store, operation)(revision, copy.deepcopy(patch))
    assert path.read_bytes() == before
    assert store.snapshot().revision == revision
    assert not store.history_dir.exists()
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("backend", ["http", "sentence-transformers", "disabled"])
@pytest.mark.parametrize("model", ["multilingual-e5-small", "intfloat/multilingual-e5-small"])
def test_supported_models_and_disabled_search_remain_valid_without_io(tmp_path, backend, model):
    raw = configuration(tmp_path)
    raw["embeddings"].update(backend=backend, model=model)
    # The deployment host may mount a local checkpoint at this path; validation
    # must not try loading it, downloading it or checking its presence here.
    if backend == "sentence-transformers":
        raw["embeddings"]["model_path"] = str(tmp_path / "not-mounted-here")
    before = copy.deepcopy(raw)
    checked = validate_config(raw, base=tmp_path)
    assert checked["embeddings"]["model"] == model
    assert raw == before and not (tmp_path / "state").exists()


def test_clip_search_does_not_require_the_unselected_e5_backend(tmp_path):
    raw = configuration(tmp_path)
    raw["embeddings"]["backend"] = "disabled"
    raw["search"].update(enabled=True, profile="clip-basic-v1")
    raw["find_anything"] = {"clip_server": "http://127.0.0.1:8178"}
    checked = validate_config(raw, base=tmp_path)
    assert checked["embeddings"]["backend"] == "disabled"
    assert not (tmp_path / "state").exists()
