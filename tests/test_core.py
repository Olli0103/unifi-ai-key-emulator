"""Pure provider validation and readiness must agree with runtime requirements."""

import json
from pathlib import Path
import shutil
import socket

import pytest

from aikey.config import ConfigError, initialize, readiness, validate_config
from aikey.providers import ProviderError, VisionProvider, validate_inference_config


def ready_config(tmp_path):
    state = tmp_path / "state"
    config = initialize(tmp_path / "config.json", state, controller_host="127.0.0.1")
    shutil.copyfile(state / "device.crt", state / "controller-ca.pem")
    config["inference"] = {"provider": "ollama", "model": "fixture-model",
                           "base_url": "http://127.0.0.1:11434"}
    return config


def test_readiness_reads_key_without_network_or_secret_output(tmp_path, monkeypatch):
    config = ready_config(tmp_path)
    key_path = tmp_path / "provider-key"
    key_path.write_text("")
    config["inference"] = {
        "provider": "openai", "model": "fixture-model", "allow_remote": True,
        "base_url": "https://api.openai.com/v1", "api_key_file": str(key_path),
    }

    def forbidden_network(*args, **kwargs):
        pytest.fail("Readiness attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_network)
    empty = readiness(config)
    assert empty["ready_for_device_start"] is False
    assert empty["checks"]["vision_configuration_valid"] is False
    assert empty["errors"]["vision_configuration_valid"]
    secret = "synthetic-fixture-key-with spaces"
    key_path.write_text(secret)
    invalid = readiness(config)
    assert invalid["ready_for_device_start"] is False
    assert secret not in json.dumps(invalid)
    key_path.write_text("synthetic-valid-fixture-key")
    valid = readiness(config)
    assert valid["ready_for_device_start"] is True
    assert valid["errors"] == {}
    assert "synthetic-valid-fixture-key" not in json.dumps(valid)


def test_readiness_rejects_invalid_credentials_and_provider_shape(tmp_path):
    config = ready_config(tmp_path)
    config["inference"]["base_url"] = "http://127.0.0.1:11434/v1"
    report = readiness(config)
    assert report["ready_for_device_start"] is False
    assert "server root" in report["errors"]["vision_configuration_valid"]
    Path(config["device"]["management_password_file"]).write_text("short")
    report = readiness(config)
    assert report["checks"]["credentials_readable"] is False


def test_config_validation_and_runtime_share_remote_policy(tmp_path):
    config = ready_config(tmp_path)
    config["inference"]["base_url"] = "https://remote.example"
    with pytest.raises(ConfigError, match="allow_remote"):
        validate_config(config)
    with pytest.raises(ProviderError, match="allow_remote"):
        validate_inference_config(config["inference"])
    config["inference"]["allow_remote"] = True
    validate_config(config)
    validate_inference_config(config["inference"])


def test_config_validation_requires_explicit_remote_embedding_policy(tmp_path):
    config = ready_config(tmp_path)
    config["embeddings"]["base_url"] = "https://embeddings.example/v1"
    with pytest.raises(ConfigError, match="embeddings.allow_remote"):
        validate_config(config)
    config["embeddings"]["allow_remote"] = True
    validate_config(config)
    config["embeddings"]["base_url"] = "http://192.0.2.20:8080/v1"
    with pytest.raises(ConfigError, match="HTTPS"):
        validate_config(config)
    config["embeddings"]["allow_insecure_http"] = True
    validate_config(config)


def test_runtime_openai_still_requires_hydrated_key():
    config = {"provider": "openai", "model": "fixture-model", "allow_remote": True}
    validate_inference_config(config, require_api_key=False)
    with pytest.raises(ProviderError, match="api_key_file"):
        validate_inference_config(config)
    with pytest.raises(ProviderError, match="api_key_file"):
        VisionProvider(config)


def test_readiness_reports_optional_deployment_label(tmp_path):
    config = ready_config(tmp_path)
    assert "deployment" not in config["runtime"]
    assert readiness(config)["target"]["deployment"] == "unspecified"
    config["runtime"]["deployment"] = " Apple container on macOS "
    checked = validate_config(config)
    assert readiness(checked)["target"]["deployment"] == "Apple container on macOS"


@pytest.mark.parametrize("label", [None, True, 12, "", "  ", "x" * 129, "Mac\nLAN", "Mac\x00"])
def test_invalid_deployment_labels_are_rejected(tmp_path, label):
    config = ready_config(tmp_path)
    config["runtime"]["deployment"] = label
    with pytest.raises(ConfigError, match="runtime.deployment"):
        validate_config(config)
