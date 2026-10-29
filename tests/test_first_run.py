"""Real offline CLI preparation; all addresses, names and keys are fictional."""

import json
import socket
import stat

import pytest

from aikey.cli import main
from aikey.config import load_config


@pytest.fixture(autouse=True)
def forbid_network_and_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline setup attempted network, runtime or trust import")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr("aikey.cli._run", forbidden)
    monkeypatch.setattr("aikey.tls.import_controller_trust", forbidden)


def arguments(root, *extra):
    return ["setup", "--directory", str(root), "--controller", "192.0.2.1",
            "--device-ip", "192.0.2.110", "--protect-version", "7.3.74",
            "--provider", "ollama", "--model", "fictional-vision", *extra]


def test_prepares_config_identity_and_safe_next_steps(tmp_path, capsys):
    root = tmp_path / "fresh"
    assert main(arguments(root)) == 0
    report = json.loads(capsys.readouterr().out)
    config = load_config(root / "config.json")
    assert report["network_contacted"] is False
    assert report["service_started"] is False
    assert report["controller_trust_imported"] is False
    assert report["adoption"] == "not_started"
    assert report["model_availability"] == "not_tested"
    assert report["additional_features_enabled"] is False
    assert "cost" in report["next_steps"][-1]
    assert config["controller"]["host"] == "192.0.2.1"
    assert config["device"]["ip"] == config["runtime"]["bind"] == "192.0.2.110"
    assert config["controller"]["protect_version"] == "7.3.74"
    assert config["controller"]["verify_hostname"] is True
    assert config["inference"]["provider"] == "ollama"
    assert config["inference"]["model"] == "fictional-vision"
    assert not config["worker"]["request_mp4_exports"]
    assert not any(key in config["worker"] for key in ("continuous", "test_scope", "test_scopes"))
    assert all(config[key]["enabled"] is False for key in ("discovery", "database", "search"))
    assert "speech_to_text" not in config
    identity = json.loads((root / "state" / "identity.json").read_text())
    assert config["device"]["mac"] == identity["mac"]
    assert identity["mac"] != "020000000000"
    assert (root / "state" / "device.crt").is_file()
    assert (root / "state" / "device.key").is_file()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600
               for path in root.rglob("*") if path.is_file())
    assert main(["check", "--config", str(root / "config.json")]) == 2
    assert not json.loads(capsys.readouterr().out)["checks"]["controller_trust_file"]


def test_explicit_nat_pin_mp4_and_hosted_key_reference_without_read(tmp_path, capsys):
    root, key = tmp_path / "fresh", tmp_path / "provider-key"
    # A FIFO is intentionally not read: setup only saves a reference. check/run
    # perform the bounded private-secret validation separately.
    import os
    os.mkfifo(key, 0o600)
    assert main(arguments(root, "--bind", "0.0.0.0", "--https-port", "9443",
                          "--provider", "openai", "--api-key-file", str(key),
                          "--controller-fingerprint", "ab" * 32,
                          "--pin-only-hostname", "--request-mp4-exports",
                          "--ffmpeg-path", "/usr/bin/ffmpeg")) == 0
    config = load_config(root / "config.json")
    assert config["runtime"]["bind"] == "0.0.0.0"
    assert config["runtime"]["https_port"] == 9443
    assert config["controller"]["verify_hostname"] is False
    assert config["controller"]["expected_fingerprint"] == "ab" * 32
    assert config["worker"]["ffmpeg_path"] == "/usr/bin/ffmpeg"
    assert config["worker"]["request_mp4_exports"] is True
    assert config["inference"]["api_key_file"] == str(key)
    assert "api_key" not in config["inference"]
    report = json.loads(capsys.readouterr().out)
    assert report["vision_destination"] == "https://api.openai.com/v1"
    assert "not verified" in report["media_policy"]


@pytest.mark.parametrize("extra", [
    ["--controller", "https://example.invalid"], ["--controller", ""],
    ["--device-ip", "not-an-ip"], ["--bind", "no-ip"], ["--https-port", "0"],
    ["--protect-version", "7.x"], ["--pin-only-hostname"],
    ["--controller-fingerprint", "z" * 64], ["--controller-fingerprint", "ab"],
    ["--ffmpeg-path", "relative/ffmpeg"], ["--model", " "],
    ["--provider", "openai"], ["--provider", "openai-compatible"],
    ["--provider", "openai", "--api-key-file", "missing", "--base-url", "https://wrong.invalid/v1"],
    ["--base-url", "http://remote.invalid"],
    ["--base-url", "http://192.0.2.2", "--allow-remote"],
    ["--base-url", "http://user:password@example.invalid"],
])
def test_invalid_options_leave_no_identity_or_directory(tmp_path, extra):
    root = tmp_path / "fresh"
    assert main(arguments(root, *extra)) == 2
    assert not root.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["empty", "partial", "adopted", "symlink", "dangling", "file"])
def test_existing_targets_never_reused_or_modified(tmp_path, kind):
    root, target = tmp_path / "fresh", tmp_path / "other"
    if kind in {"empty", "partial", "adopted"}:
        root.mkdir()
        if kind != "empty":
            (root / "identity.json").write_text('{"fictional":"retained"}')
        if kind == "adopted":
            (root / "adoption.json").write_text('{"fictional":"adopted"}')
    elif kind in {"symlink", "dangling"}:
        if kind == "symlink":
            target.mkdir()
        root.symlink_to(target, target_is_directory=True)
    else:
        root.write_text("retained")
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert main(arguments(root)) == 2
    after = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
    if kind in {"symlink", "dangling"}:
        assert root.is_symlink()


def test_repeat_and_racing_setup_do_not_replace_identity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    root = tmp_path / "fresh"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: main(arguments(root)), range(2)))
    assert sorted(results) == [0, 2]
    original = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert main(arguments(root)) == 2
    assert original == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_partial_certificate_failure_retained_and_never_repaired(tmp_path, monkeypatch):
    root = tmp_path / "fresh"
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic certificate failure")
    monkeypatch.setattr("aikey.tls.ensure_identity_certificate", fail)
    assert main(arguments(root)) == 2
    assert (root / "state" / "identity.json").is_file()
    assert not (root / "config.json").exists()
    original = (root / "state" / "identity.json").read_bytes()
    assert main(arguments(root)) == 2
    assert (root / "state" / "identity.json").read_bytes() == original


def test_unknown_numeric_version_is_not_compatibility_claim(tmp_path, capsys):
    assert main(arguments(tmp_path / "fresh", "--protect-version", "99.1.2")) == 0
    assert json.loads(capsys.readouterr().out)["operator_reported_version_evidence"] == "unknown"


def test_explicit_lan_provider_requires_both_network_opt_ins(tmp_path, capsys):
    assert main(arguments(tmp_path / "fresh", "--base-url", "http://192.168.50.2:11434",
                          "--allow-remote", "--allow-insecure-http")) == 0
    assert json.loads(capsys.readouterr().out)["network_contacted"] is False
