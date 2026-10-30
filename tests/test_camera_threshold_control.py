"""Existing-camera score policy only: no inference, enrollment or provider changes."""

import json
import socket

import pytest

from aikey.aiport_config_store import AiPortConfigurationError
from test_aiport_provider_rollback import saved_twice, digest
from test_aiport_config_store import CAMERAS


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Score settings must not contact models or resolve endpoints")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def ready(root):
    store, path, _, _ = saved_twice(root)
    raw = json.loads(path.read_text())
    raw["live_pool_detector"].update(camera_thresholds={CAMERAS[0]: .72},
        plate_cameras=[CAMERAS[0]], fallback={"provider_config": {
            "provider": "ollama", "model": "fixture-fallback", "base_url": "http://127.0.0.1:11434"}})
    path.write_text(json.dumps(raw, indent=1) + "\n")
    return store, path


@pytest.mark.parametrize("gates", [{}, {CAMERAS[0]: .23, CAMERAS[1]: 1}, {CAMERAS[1]: .000001}])
def test_preview_save_exact_undo_preserve_every_unrelated_value(tmp_path, gates):
    store, path = ready(tmp_path / "selected")
    _, sibling = ready(tmp_path / "sibling")
    other = digest(sibling.parent)
    before = path.read_bytes()
    state = digest(path.parent)
    rev = store.snapshot().revision
    preview = store.preview_camera_thresholds(rev, gates)
    assert digest(path.parent) == state
    result = store.apply_camera_thresholds(rev, gates)
    assert result.revision == preview.resulting_revision
    changed = json.loads(path.read_bytes())
    expected = json.loads(before)
    expected["live_pool_detector"].pop("camera_thresholds")
    assert changed["live_pool_detector"].pop("camera_thresholds", {}) == gates
    assert changed == expected and digest(sibling.parent) == other
    assert not store.provider_rollback_status()["available"]  # not mislabeled provider undo
    assert store.camera_threshold_undo_status()["available"]
    store.undo_camera_thresholds(result.revision)
    assert path.read_bytes() == before and digest(sibling.parent) == other
    assert not store.camera_threshold_undo_status()["available"]
    with pytest.raises((OSError, ValueError)):
        store.undo_camera_thresholds(result.revision)


@pytest.mark.parametrize("gates", [None, [], {CAMERAS[0]: 0}, {CAMERAS[0]: -1},
    {CAMERAS[0]: 1.01}, {CAMERAS[0]: True}, {CAMERAS[0]: "0.5"},
    {CAMERAS[0]: float("nan")}, {CAMERAS[0]: float("inf")},
    {"2A1122334499": .5}, {"2a:11:22:33:44:55": .5}, {1: .5}])
def test_invalid_gate_or_unknown_camera_does_not_write(tmp_path, gates):
    store, path = ready(tmp_path)
    before = digest(tmp_path)
    with pytest.raises(AiPortConfigurationError):
        store.apply_camera_thresholds(store.snapshot().revision, gates)
    assert digest(tmp_path) == before


def test_noop_preserves_exact_bytes_and_journals(tmp_path):
    store, _ = ready(tmp_path)
    before = digest(tmp_path)
    rev = store.snapshot().revision
    assert not store.preview_camera_thresholds(rev, {CAMERAS[0]: .72}).restart_required
    assert store.apply_camera_thresholds(rev, {CAMERAS[0]: .72}).revision == rev
    assert digest(tmp_path) == before


@pytest.mark.parametrize("fault", ["stale_save", "later_policy", "later_provider", "tampered_target", "oversize"])
def test_stale_and_cross_policy_undo_are_refused(tmp_path, fault):
    store, path = ready(tmp_path)
    old = store.snapshot().revision
    result = store.apply_camera_thresholds(old, {CAMERAS[0]: .4})
    raw = json.loads(path.read_text())
    if fault == "later_policy":
        raw["live_pool_detector"]["threshold"] = .9
        path.write_text(json.dumps(raw) + "\n")
    elif fault == "later_provider":
        raw["live_pool_detector"]["provider_config"]["model"] = "changed-model"
        path.write_text(json.dumps(raw) + "\n")
    elif fault == "tampered_target":
        record = json.loads(store._threshold_journal.read_text())
        target = store.history_dir / (record["from"] + ".json")
        target.write_text("{}")
    elif fault == "oversize":
        raw["live_pool_detector"]["provider_config"]["model"] = "a" * 4000
        path.write_text(json.dumps(raw) + "\n")
    before = digest(tmp_path)
    with pytest.raises((AiPortConfigurationError, OSError)):
        if fault == "stale_save":
            store.apply_camera_thresholds(old, {})
        else:
            store.undo_camera_thresholds(result.revision)
    assert digest(tmp_path) == before


def test_local_backend_never_switches_to_api(tmp_path):
    from test_aiport_config_store import fixture
    store, path, _ = fixture(tmp_path)
    before = path.read_bytes()
    assert not store.camera_threshold_status()["eligible"]
    with pytest.raises(AiPortConfigurationError):
        store.apply_camera_thresholds(store.snapshot().revision, {CAMERAS[0]: .4})
    assert path.read_bytes() == before and not store.history_dir.exists()
    # A refused first writer may create its private coordination lock, not history.
    assert store.lock_path.stat().st_mode & 0o777 == 0o600


def test_raw_mac_alias_noop_and_full_replacement(tmp_path):
    store, path = ready(tmp_path)
    raw = json.loads(path.read_text())
    raw["live_pool_detector"]["camera_thresholds"] = {"2a:11:22:33:44:55": .72}
    path.write_text(json.dumps(raw) + "\n")
    before = path.read_bytes()
    store.apply_camera_thresholds(store.snapshot().revision, {CAMERAS[0]: .72})
    assert path.read_bytes() == before
    store.apply_camera_thresholds(store.snapshot().revision, {CAMERAS[0]: .6})
    assert json.loads(path.read_text())["live_pool_detector"]["camera_thresholds"] == {CAMERAS[0]: .6}


def test_same_revision_concurrent_saves_have_exactly_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    store, _ = ready(tmp_path)
    rev = store.snapshot().revision
    barrier = threading.Barrier(2)
    def save(value):
        barrier.wait(timeout=5)
        try:
            store.apply_camera_thresholds(rev, {CAMERAS[0]: value})
            return "saved"
        except AiPortConfigurationError:
            return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save, .4)
        second = pool.submit(save, .6)
        assert sorted([first.result(timeout=5), second.result(timeout=5)]) == ["refused", "saved"]
    assert store.camera_threshold_undo_status()["available"]


def test_journal_failure_keeps_commit_and_reports_failure(tmp_path, monkeypatch):
    from aikey import aiport_config_store as module
    store, path = ready(tmp_path)
    original = module.atomic_private
    def fail_journal(destination, content):
        if destination == store._threshold_journal:
            raise OSError("fictional lost journal write")
        return original(destination, content)
    monkeypatch.setattr(module, "atomic_private", fail_journal)
    with pytest.raises(AiPortConfigurationError):
        store.apply_camera_thresholds(store.snapshot().revision, {CAMERAS[0]: .4})
    assert json.loads(path.read_text())["live_pool_detector"]["camera_thresholds"] == {CAMERAS[0]: .4}
    assert not store.camera_threshold_undo_status()["available"]
