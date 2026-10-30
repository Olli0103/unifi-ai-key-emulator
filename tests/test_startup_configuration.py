"""Startup provenance must never silently follow a later saved configuration."""

import hashlib
import json
import os

import pytest

from aikey import startup_configuration as snapshot
from aikey.config import ConfigError, initialize, load_startup_config
from test_aiport_config_store import fixture as port_fixture


@pytest.mark.parametrize("replacement", ["different", "invalid", "symlink", "fifo"])
def test_key_validation_and_digest_use_one_descriptor_even_after_path_replacement(
        tmp_path, monkeypatch, replacement):
    path = tmp_path / "config.json"
    initialize(path, tmp_path)
    raw = path.read_bytes()
    original = json.loads(raw)
    after = tmp_path / "replacement"
    if replacement == "fifo":
        os.mkfifo(after, 0o600)
    elif replacement == "symlink":
        after.symlink_to(tmp_path / "absent")
    else:
        changed = json.loads(raw)
        changed["device"]["name"] = "Different fictional device"
        after.write_bytes(json.dumps(changed).encode() if replacement == "different" else b"invalid")
    read = snapshot.read_snapshot

    def replaced(path, **options):
        result = read(path, **options)
        after.replace(path)
        return result

    monkeypatch.setattr(snapshot, "read_snapshot", replaced)
    config, revision = load_startup_config(path)
    assert config["device"]["name"] == original["device"]["name"]
    assert revision == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("kind", ["fifo", "symlink", "directory", "oversize", "invalid"])
def test_key_startup_refuses_unbounded_or_special_config_without_blocking(tmp_path, kind):
    path = tmp_path / "config.json"
    if kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "symlink":
        path.symlink_to(tmp_path / "not-opened")
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b" " * (1024 * 1024 + 1) if kind == "oversize" else b"invalid")
    with pytest.raises(ConfigError, match="valid startup configuration"):
        load_startup_config(path)


async def test_port_startup_snapshot_is_frozen_and_programmatic_configuration_is_unknown(tmp_path, monkeypatch):
    from aikey import aiport_candidate as candidate
    _, path, _ = port_fixture(tmp_path)
    before = path.read_bytes()
    real_parse = candidate.parse_config

    def parse_then_replace(raw, **options):
        checked = real_parse(raw, **options)
        changed = json.loads(raw)
        changed["live_pool_detector"]["threshold"] = .91
        path.write_text(json.dumps(changed))
        return checked

    monkeypatch.setattr(candidate, "parse_config", parse_then_replace)
    service = candidate.CandidateService.from_configuration_file(path)
    assert service.config["live_pool_detector"]["threshold"] == .3
    value = json.loads((await service._health(None)).text)["startup_configuration"]
    assert value == snapshot.observation(hashlib.sha256(before).hexdigest())
    path.write_bytes(b"later-invalid-config")
    assert json.loads((await service._health(None)).text)["startup_configuration"] == value
    programmatic = candidate.CandidateService(service.config, tmp_path)
    assert json.loads((await programmatic._health(None)).text)["startup_configuration"] is None


async def test_key_health_keeps_startup_revision_even_after_saved_file_changes(tmp_path):
    from aikey.config import hydrate_secrets
    from aikey.runtime import Application
    path = tmp_path / "config.json"
    initialize(path, tmp_path, controller_host="127.0.0.1")
    raw = json.loads(path.read_bytes())
    (tmp_path / "controller-ca.pem").write_bytes((tmp_path / "device.crt").read_bytes())
    raw["runtime"]["mode"] = "lab"
    raw["controller_origins"] = ["https://127.0.0.1:7442"]
    raw["inference"]["model"] = "fictional-uncontacted-vision"
    path.write_text(json.dumps(raw))
    config, revision = load_startup_config(path)
    app = Application(hydrate_secrets(config), startup_revision=revision)
    assert json.loads((await app._health(None)).text)["startup_configuration"] == snapshot.observation(revision)
    path.write_bytes(b"later-invalid-config")
    assert json.loads((await app._health(None)).text)["startup_configuration"] == snapshot.observation(revision)
    unknown = Application(hydrate_secrets(config))
    assert json.loads((await unknown._health(None)).text)["startup_configuration"] is None


@pytest.mark.parametrize("value", ["a" * 63, "A" * 64, "z" * 64, 1, True, {}])
def test_invalid_revision_refused_before_runtime_construction(value, tmp_path):
    from aikey.runtime import Application
    from aikey.aiport_candidate import CandidateService
    with pytest.raises(ValueError, match="invalid_startup_configuration_revision"):
        Application({}, startup_revision=value)
    with pytest.raises(ValueError, match="invalid_startup_configuration_revision"):
        CandidateService({}, tmp_path, startup_revision=value)


def test_cli_run_transports_the_validated_snapshot_not_a_later_reread(tmp_path, monkeypatch):
    from aikey import cli
    path = tmp_path / "config.json"
    initialize(path, tmp_path)
    before = path.read_bytes()
    captured = []
    monkeypatch.setattr(cli, "readiness", lambda config: {"ready_for_device_start": True})

    async def run(config, *, startup_revision):
        captured.append((config, startup_revision))
        path.write_bytes(b"synthetic-later-saved-file")

    monkeypatch.setattr(cli, "_run", run)
    assert cli.main(["run", "--config", str(path)]) == 0
    assert len(captured) == 1
    assert captured[0][1] == hashlib.sha256(before).hexdigest()
    assert captured[0][0]["device"]["mac"] == json.loads(before)["device"]["mac"]
