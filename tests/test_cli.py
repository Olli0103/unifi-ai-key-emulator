import json
import socket

import pytest

from aikey.cli import main
from aikey.config import load_config


def test_nas_init_uses_configured_addresses_without_network(tmp_path, capsys):
    config = tmp_path / "config.json"
    state = tmp_path / "state"
    assert main(["init", "--config", str(config), "--state-dir", str(state),
                 "--controller", "192.0.2.1", "--device-ip", "192.0.2.110"]) == 0
    assert json.loads(capsys.readouterr().out)["network_contacted"] is False
    loaded = load_config(config)
    assert loaded["controller"]["host"] == "192.0.2.1"
    assert loaded["runtime"]["bind"] == "192.0.2.110"
    assert loaded["controller_origins"][0] == "https://192.0.2.1:7444"
    assert main(["check", "--config", str(config)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["checks"]["vision_model_configured"]


def test_provider_switch_resets_old_key_and_preserves_device_identity(tmp_path, capsys):
    config = tmp_path / "config.json"
    assert main(["init", "--config", str(config), "--state-dir", str(tmp_path / "state")]) == 0
    identity = load_config(config)["device"]
    key_path = tmp_path / "openai-key"
    # Configure only a file reference. No key or hosted call is necessary.
    assert main(["provider", "openai", "--config", str(config), "--model", "chosen-vision-model",
                 "--api-key-file", str(key_path)]) == 0
    openai = load_config(config)["inference"]
    assert openai["provider"] == "openai" and openai["allow_remote"] is True
    assert openai["api_key_file"] == str(key_path)
    assert main(["provider", "ollama", "--config", str(config), "--model", "local-vision-model"]) == 0
    changed = load_config(config)
    assert changed["device"] == identity
    assert changed["inference"]["base_url"] == "http://127.0.0.1:11434"
    assert "api_key_file" not in changed["inference"]
    assert all("provider_contacted" not in line or '"provider_contacted": false' in line
               for line in capsys.readouterr().out.splitlines())


def test_missing_provider_fields_do_not_modify_existing_config(tmp_path):
    config = tmp_path / "config.json"
    main(["init", "--config", str(config), "--state-dir", str(tmp_path / "state")])
    original = config.read_bytes()
    assert main(["provider", "openai", "--config", str(config), "--model", "fixture"]) == 2
    assert config.read_bytes() == original
    assert main(["provider", "openai-compatible", "--config", str(config), "--model", "fixture"]) == 2
    assert config.read_bytes() == original


@pytest.mark.parametrize("arguments", [
    ["ollama", "--model", "fixture", "--base-url", "http://127.0.0.1:11434/v1"],
    ["openai", "--model", "fixture", "--base-url", "https://wrong.example/v1",
     "--api-key-file", "not-provisioned-yet"],
    ["openai-compatible", "--model", "fixture", "--base-url", "https://remote.example/v1"],
    ["openai-compatible", "--model", "fixture", "--base-url", "http://192.0.2.2/v1", "--allow-remote"],
    ["ollama", "--model", " "],
])
def test_invalid_provider_settings_never_replace_config(tmp_path, arguments, monkeypatch):
    config = tmp_path / "config.json"
    main(["init", "--config", str(config), "--state-dir", str(tmp_path / "state")])
    original = config.read_bytes()

    def forbidden_network(*args, **kwargs):
        pytest.fail("Config-only provider selection attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_network)
    assert main(["provider", *arguments, "--config", str(config)]) == 2
    assert config.read_bytes() == original
