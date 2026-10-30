"""Unsupported provider parameters cannot be silently persisted or ignored."""

import json

import pytest

from aikey.config import ConfigError, defaults, validate_config
from aikey.config_store import ConfigurationStore, UnsafeConfigurationChange
from aikey.providers import ProviderError, VisionProvider
from aikey.search import EmbeddingError, EmbeddingService
from aikey.speech import SpeechError, SpeechProvider


@pytest.mark.parametrize("parameter", ["model_dimensions", "response_format", "reasoning_effort",
                                       "authorization_header"])
def test_unsupported_vision_parameters_fail_at_the_adapter_boundary(parameter):
    with pytest.raises(ProviderError):
        VisionProvider({"provider": "ollama", "model": "synthetic-vision", parameter: "not-supported"})


@pytest.mark.parametrize("parameter", ["dimensions", "temperature", "api_key"])
def test_unsupported_embedding_parameters_fail_before_any_encoder_use(parameter):
    with pytest.raises(EmbeddingError):
        EmbeddingService({"backend": "disabled", "model": "multilingual-e5-small",
                          parameter: "not-supported"})


@pytest.mark.parametrize("parameter", ["temperature", "response_format", "dimensions"])
def test_fixed_speech_request_parameters_cannot_be_overridden_silently(parameter):
    with pytest.raises(SpeechError):
        SpeechProvider({"provider": "openai-compatible", "model": "synthetic-whisper",
                        "base_url": "http://127.0.0.1:8181/v1", parameter: "not-supported"})


@pytest.mark.parametrize("section,parameter", [("inference", "reasoning_effort"),
                                                ("embeddings", "dimensions"),
                                                ("speech_to_text", "temperature")])
def test_configuration_and_apply_refuse_unsupported_role_fields_without_history(tmp_path, section, parameter):
    raw = defaults(tmp_path / "state", "020000000001")
    raw["controller"]["host"] = "127.0.0.1"
    raw["speech_to_text"] = {"provider": "openai-compatible", "model": "synthetic-whisper",
        "base_url": "http://127.0.0.1:8181/v1", "camera_ids": ["synthetic-camera"]}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw) + "\n")
    path.chmod(0o600)
    store = ConfigurationStore(path)
    revision = store.snapshot().revision
    before = path.read_bytes()
    candidate = json.loads(before)
    candidate[section][parameter] = "not-supported"
    with pytest.raises(ConfigError):
        validate_config(candidate, base=tmp_path)
    with pytest.raises(UnsafeConfigurationChange):
        store.apply(revision, {section: {parameter: "not-supported"}})
    assert path.read_bytes() == before and store.snapshot().revision == revision
    assert not store.history_dir.exists()
