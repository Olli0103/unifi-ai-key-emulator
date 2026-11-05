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
    monkeypatch.setattr(socket.socket, "bind", forbidden)
    monkeypatch.setattr(socket.socket, "sendto", forbidden)
    monkeypatch.setattr("aikey.cli._run", forbidden)
    monkeypatch.setattr("aikey.tls.import_controller_trust", forbidden)


def arguments(root, *extra):
    return ["setup", "--directory", str(root), "--controller", "192.0.2.1",
            "--device-ip", "192.0.2.110", "--protect-version", "7.3.74",
            "--provider", "ollama", "--model", "fictional-vision", *extra]


@pytest.mark.parametrize("worker,caption,images", [(300, 240, 1), (600, 600, 16), (1, 1, 1)])
def test_explicit_caption_runtime_limits_persist_without_enabling_processing(tmp_path, worker, caption, images):
    root = tmp_path / "fresh"
    assert main(arguments(root, "--worker-timeout-seconds", str(worker),
                          "--caption-timeout-seconds", str(caption), "--max-images", str(images))) == 0
    config = load_config(root / "config.json")
    assert {key: config["worker"][key] for key in ("timeout_s", "caption_timeout_s", "max_images")} == {
        "timeout_s": worker, "caption_timeout_s": caption, "max_images": images}
    assert config["worker"]["max_queue"] == 8 and config["worker"]["max_concurrency"] == 1
    assert "continuous" not in config["worker"] and "feature_flags" not in config["device"]


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
    assert report["factory_enrollment_until"] == 0
    assert "cost" in report["next_steps"][-1]
    assert config["controller"]["host"] == "192.0.2.1"
    assert config["device"]["ip"] == config["runtime"]["bind"] == "192.0.2.110"
    assert config["controller"]["protect_version"] == "7.3.74"
    assert config["controller"]["verify_hostname"] is True
    assert config["inference"]["provider"] == "ollama"
    assert config["inference"]["model"] == "fictional-vision"
    assert not config["worker"]["request_mp4_exports"]
    assert config["worker"]["timeout_s"] == 120
    assert "caption_timeout_s" not in config["worker"] and "max_images" not in config["worker"]
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


def test_linux_discovery_is_prepared_offline_and_scoped_to_controller(tmp_path, capsys):
    root = tmp_path / "fresh"
    assert main(arguments(root, "--linux-discovery")) == 0
    report = json.loads(capsys.readouterr().out)
    config = load_config(root / "config.json")
    assert report["discovery_enabled"] is report["additional_features_enabled"] is True
    assert report["network_contacted"] is report["service_started"] is False
    assert report["adoption"] == "not_started"
    assert config["discovery"] == {
        "enabled": True, "bind": "0.0.0.0", "port": 10001,
        "multicast": True, "interface_ip": "192.0.2.110",
        "allowed_controller_ips": ["192.0.2.1"],
    }
    assert not config["database"]["enabled"] and not config["search"]["enabled"]
    assert "factory_enrollment_until" not in config["device"]
    assert "continuous" not in config["worker"]


@pytest.mark.parametrize("option,value", [
    ("--controller", "controller.invalid"), ("--controller", "::1"),
    ("--controller", "0.0.0.0"), ("--controller", "239.1.1.1"),
    ("--controller", "127.0.0.1"), ("--controller", "8.8.8.8"),
    ("--device-ip", "0.0.0.0"), ("--device-ip", "127.0.0.1"),
    ("--device-ip", "239.1.1.1"), ("--device-ip", "::1"),
    ("--device-ip", "255.255.255.255"),
])
def test_discovery_invalid_address_refused_before_identity(tmp_path, option, value):
    root = tmp_path / "fresh"
    assert main(arguments(root, "--linux-discovery", option, value)) == 2
    assert not root.exists()


@pytest.mark.parametrize("extra", [
    ["--controller", "https://example.invalid"], ["--controller", ""],
    ["--device-ip", "not-an-ip"], ["--bind", "no-ip"], ["--https-port", "0"],
    ["--protect-version", "7.x"], ["--pin-only-hostname"],
    ["--control-profile", "device-service"], ["--search-fingerprint", "xyz"],
    ["--controller-fingerprint", ""], ["--search-fingerprint", ""],
    ["--controller-fingerprint", "z" * 64], ["--controller-fingerprint", "ab"],
    ["--ffmpeg-path", "relative/ffmpeg"], ["--model", " "],
    ["--factory-enrollment-seconds", "-1"], ["--factory-enrollment-seconds", "601"],
    ["--factory-enrollment-seconds", "600"],
    ["--worker-timeout-seconds", "0"], ["--worker-timeout-seconds", "601"],
    ["--caption-timeout-seconds", "0"], ["--caption-timeout-seconds", "601"],
    ["--caption-timeout-seconds", "121"], ["--worker-timeout-seconds", "29"],
    ["--max-images", "0"], ["--max-images", "17"],
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


def test_factory_window_is_opt_in_absolute_and_not_renewed_on_reload(tmp_path, monkeypatch, capsys):
    import time
    started = int(time.time())
    monkeypatch.setattr("aikey.first_run.time.time", lambda: started)
    root = tmp_path / "fresh"
    assert main(arguments(root, "--factory-enrollment-seconds", "600",
                          "--controller-fingerprint", "ab" * 32)) == 0
    report = json.loads(capsys.readouterr().out)
    path = root / "config.json"
    original = path.read_bytes()
    assert report["factory_enrollment_until"] == started + 600
    assert load_config(path)["device"]["factory_enrollment_until"] == started + 600
    # The existing runtime accepts an expired configuration, but cannot extend
    # its saved window just by loading/restarting it later.
    monkeypatch.setattr("aikey.first_run.time.time", lambda: started + 601)
    assert load_config(path)["device"]["factory_enrollment_until"] < time.time()
    assert path.read_bytes() == original
    assert main(arguments(root, "--factory-enrollment-seconds", "600",
                          "--controller-fingerprint", "ab" * 32)) == 2
    assert path.read_bytes() == original


def test_new_directory_creation_mode_is_hardened_before_identity(tmp_path, monkeypatch):
    from pathlib import Path
    root = tmp_path / "fresh"
    mkdir = Path.mkdir
    def nas_mkdir(path, *args, **kwargs):
        existed = path.exists()
        result = mkdir(path, *args, **kwargs)
        if path == root and not existed:
            path.chmod(0o777)
        return result
    monkeypatch.setattr(Path, "mkdir", nas_mkdir)
    assert main(arguments(root)) == 0
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert (root / "state/identity.json").is_file()


def test_directory_hardening_failure_keeps_empty_partial_without_identity(tmp_path, monkeypatch):
    root = tmp_path / "fresh"
    def refuse(*args):
        raise PermissionError("fictional NAS refusal")
    monkeypatch.setattr("aikey.first_run.os.fchmod", refuse)
    assert main(arguments(root)) == 2
    assert root.is_dir() and list(root.iterdir()) == []
    assert main(arguments(root)) == 2
    assert list(root.iterdir()) == []


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


def test_explicit_profile_and_distinct_search_trust_remain_offline(tmp_path, capsys):
    root = tmp_path / "fresh"
    assert main(arguments(root, "--control-profile", "device-service",
                          "--controller-fingerprint", "ab" * 32,
                          "--search-fingerprint", "cd" * 32,
                          "--pin-only-hostname")) == 0
    config = load_config(root / "config.json")
    assert config["controller"]["control_profile"] == "device-service"
    assert config["controller"]["expected_fingerprint"] == "ab" * 32
    assert config["controller"]["search_expected_fingerprint"] == "cd" * 32
    assert config["controller"]["ca_file"] != config["controller"]["search_ca_file"]
    assert not (root / "state" / "controller-ca.pem").exists()
    assert not (root / "state" / "controller-search-ca.pem").exists()
    assert config["search"]["enabled"] is False and config["database"]["enabled"] is False
    assert json.loads(capsys.readouterr().out)["network_contacted"] is False


@pytest.mark.parametrize("channel,port,leaf,pin", [
    ("control", 7442, "controller-ca.pem", "ab" * 32),
    ("search", 7443, "controller-search-ca.pem", "cd" * 32),
])
@pytest.mark.parametrize("override", [False, True])
def test_trust_routes_one_explicit_channel_without_rewriting_config(tmp_path, monkeypatch, channel, port, leaf, pin, override):
    from aikey.config import atomic_private
    root = tmp_path / "fresh"
    assert main(arguments(root, "--controller-fingerprint", "ab" * 32,
                          "--search-fingerprint", "cd" * 32)) == 0
    path = root / "config.json"
    before = path.read_bytes()
    seen = []
    def import_fixture(host, selected_port, fingerprint, output):
        seen.append((host, selected_port, fingerprint, output))
        atomic_private(output, "synthetic certificate fixture, not usable TLS")
        return fingerprint
    monkeypatch.setattr("aikey.tls.import_controller_trust", import_fixture)
    extra = ["--port", "9443"] if override else []
    assert main(["trust", "--config", str(path), "--channel", channel,
                 "--fingerprint", pin, *extra]) == 0
    assert seen == [("192.0.2.1", 9443 if override else port, pin, root / "state" / leaf)]
    assert path.read_bytes() == before


@pytest.mark.parametrize("extra", [
    ["--channel", "search"], ["--fingerprint", "cd" * 32],
    ["--port", "0"], ["--port", "65536"],
])
def test_trust_refuses_missing_channel_mismatched_pin_or_invalid_port_before_network(tmp_path, extra):
    root = tmp_path / "fresh"
    assert main(arguments(root, "--controller-fingerprint", "ab" * 32)) == 0
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert main(["trust", "--config", str(root / "config.json"), "--fingerprint", "ab" * 32, *extra]) == 2
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("invalid", [False, 123, ["ab" * 32]])
def test_malformed_saved_control_pin_fails_safely_before_network(tmp_path, invalid):
    root = tmp_path / "fresh"
    assert main(arguments(root)) == 0
    path = root / "config.json"
    value = json.loads(path.read_text())
    value["controller"]["expected_fingerprint"] = invalid
    path.write_text(json.dumps(value) + "\n")
    before = path.read_bytes()
    assert main(["trust", "--config", str(path), "--fingerprint", "ab" * 32]) == 2
    assert path.read_bytes() == before
