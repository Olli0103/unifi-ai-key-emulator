"""Revision and settings must come from the same bounded private descriptor."""

import hashlib
from copy import deepcopy
import json
import os

import pytest

from aikey import aiport_config_store as module
from aikey.aiport_candidate import CandidateError, load_config, parse_config
from aikey.aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from test_aiport_config_store import fixture, settings


@pytest.mark.parametrize("replacement", ["config", "symlink", "fifo", "invalid", "oversized"])
def test_path_replacement_after_read_cannot_change_snapshot(tmp_path, monkeypatch, replacement):
    store, path, original = fixture(tmp_path)
    before = path.read_bytes()
    target = tmp_path / "replacement"
    changed = deepcopy(original)
    changed["live_pool_detector"]["threshold"] = 0.9
    if replacement == "fifo":
        os.mkfifo(target, 0o600)
    elif replacement == "symlink":
        canary = tmp_path / "synthetic-canary.json"
        canary.write_text(json.dumps(changed))
        canary.chmod(0o600)
        target.symlink_to(canary)
    else:
        target.write_bytes((json.dumps(changed).encode() if replacement == "config" else
                            b"private-synthetic-invalid" if replacement == "invalid" else b" " * 4097))
        target.chmod(0o600)
    inode = path.stat().st_ino
    real_fdopen = module.os.fdopen
    swapped = False

    class SwapAfterRead:
        def __init__(self, source):
            self.source = source

        def __enter__(self):
            self.source.__enter__()
            return self

        def __exit__(self, *args):
            return self.source.__exit__(*args)

        def fileno(self):
            return self.source.fileno()

        def read(self, size):
            nonlocal swapped
            result = self.source.read(size)
            assert not swapped
            target.replace(path)
            swapped = True
            return result

    def fdopen(fd, *args, **kwargs):
        is_original = os.fstat(fd).st_ino == inode
        source = real_fdopen(fd, *args, **kwargs)
        return SwapAfterRead(source) if is_original else source

    monkeypatch.setattr(module.os, "fdopen", fdopen)
    snapshot = store.snapshot()
    assert swapped
    assert snapshot.revision == hashlib.sha256(before).hexdigest()
    assert snapshot.camera_count == 2
    assert snapshot.threshold == 0.3
    assert not store.lock_path.exists() and not store.history_dir.exists()
    if replacement == "config":
        later = store.snapshot()
        assert later.threshold == 0.9 and later.revision != snapshot.revision
        saved = path.read_bytes()
        with pytest.raises(AiPortRevisionConflict):
            store.preview(snapshot.revision, settings(tmp_path))
        assert path.read_bytes() == saved
        assert not store.lock_path.exists() and not store.history_dir.exists()
    elif replacement == "symlink":
        assert path.is_symlink() and canary.read_text() == json.dumps(changed)


def test_parser_uses_captured_bytes_not_supplied_state_directory_config(tmp_path):
    store, path, _ = fixture(tmp_path)
    captured = path.read_bytes()
    expected = load_config(path)
    path.write_bytes(b"synthetic-invalid-current-path")
    assert parse_config(captured, state_dir=tmp_path) == expected
    assert path.read_bytes() == b"synthetic-invalid-current-path"
    with pytest.raises(AiPortConfigurationError, match="^aiport_config_unavailable$"):
        store.snapshot()


@pytest.mark.parametrize("raw", [b" " * 4097, bytearray(b"{}"), "{}", None])
def test_parser_rejects_bad_snapshot_before_any_referenced_file_read(tmp_path, monkeypatch, raw):
    from aikey import aiport_candidate

    def no_file_read(*args, **kwargs):
        pytest.fail("unexpected referenced file read")

    monkeypatch.setattr(aiport_candidate, "_private_file", no_file_read)
    with pytest.raises(CandidateError, match="^Invalid candidate configuration size$"):
        parse_config(raw, state_dir=tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("reference", ["device.crt", "device.key", "controller-ca.pem"])
def test_snapshot_parser_still_validates_private_identity_references(tmp_path, reference):
    _, path, _ = fixture(tmp_path)
    raw = path.read_bytes()
    (tmp_path / reference).chmod(0o644)
    with pytest.raises(CandidateError, match="private"):
        parse_config(raw, state_dir=tmp_path)


def test_valid_and_invalid_provider_preview_create_no_validation_files(tmp_path, monkeypatch):
    store, path, _ = fixture(tmp_path)
    revision = store.snapshot().revision
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    real_open = module.os.open

    def no_create(path, flags, *args, **kwargs):
        assert not flags & os.O_CREAT
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", no_create)
    assert store.preview(revision, settings(tmp_path)).restart_required
    invalid = dict(settings(tmp_path), threshold=2)
    with pytest.raises(AiPortConfigurationError, match="invalid"):
        store.preview(revision, invalid)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
