"""Existing paired-camera plate consent only; no network, inference or new streams."""

from copy import deepcopy
import json
import socket

import pytest

from aikey.aiport_config_store import AiPortConfigurationError
from test_aiport_provider_rollback import digest
from test_aiport_config_store import CAMERAS
from test_ocr_provider_control import ready as ocr_ready


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Plate permission settings must not contact models or resolve endpoints")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.mark.parametrize("initial,explicit,selected", [
    (False, False, [CAMERAS[0]]), (True, False, list(CAMERAS)),
    (True, False, [CAMERAS[1]]), (True, False, []), (True, True, []),
    (True, True, [CAMERAS[1]])])
def test_preview_apply_only_plate_scope_and_disclosed_last_ocr_route(tmp_path, initial, explicit, selected):
    store, path = ocr_ready(tmp_path / "selected", explicit=explicit)
    _, other_path = ocr_ready(tmp_path / "sibling", explicit=True)
    raw = json.loads(path.read_text())
    if not initial:
        raw["live_pool_detector"].pop("plate_cameras")
    raw["live_pool_detector"]["camera_thresholds"] = {CAMERAS[0]: .62}
    path.write_text(json.dumps(raw, indent=1) + "\n")
    before, sibling = digest(path.parent), digest(other_path.parent)
    rev = store.snapshot().revision
    preview = store.preview_plate_cameras(rev, selected)
    assert digest(path.parent) == before
    saved = store.apply_plate_cameras(rev, selected)
    assert saved.revision == preview.resulting_revision
    expected = deepcopy(raw)
    if selected:
        expected["live_pool_detector"]["plate_cameras"] = [c for c in CAMERAS if c in selected]
    else:
        expected["live_pool_detector"].pop("plate_cameras", None)
        expected["live_pool_detector"].pop("ocr_provider_config", None)
    assert json.loads(path.read_text()) == expected and digest(other_path.parent) == sibling
    if explicit:
        assert (path.parent / "ocr-old-key").read_text() == "synthetic-old-ocr-key"
    # Status has only public route information, never a stored credential reference.
    status = json.dumps(store.plate_camera_status())
    assert "api_key" not in status and "ocr-old-key" not in status and "openai-key" not in status


@pytest.mark.parametrize("selected", [None, {}, "all", [True], [1], ["unpaired"],
    [CAMERAS[0], CAMERAS[0]], ["2a:11:22:33:44:55"]])
def test_invalid_selection_does_not_mutate(tmp_path, selected):
    store, path = ocr_ready(tmp_path)
    before = digest(tmp_path)
    with pytest.raises(AiPortConfigurationError):
        store.apply_plate_cameras(store.snapshot().revision, selected)
    assert digest(tmp_path) == before


def test_semantic_noop_keeps_raw_spelling_order_bytes_and_journal(tmp_path):
    store, path = ocr_ready(tmp_path, explicit=True)
    raw = json.loads(path.read_text())
    raw["live_pool_detector"]["plate_cameras"] = ["2a:11:22:33:44:55"]
    path.write_text(json.dumps(raw, indent=1) + "\n")
    before = digest(tmp_path)
    rev = store.snapshot().revision
    assert not store.preview_plate_cameras(rev, [CAMERAS[0]]).restart_required
    assert store.apply_plate_cameras(rev, [CAMERAS[0]]).revision == rev
    assert digest(tmp_path) == before


def test_stale_camera_permission_write_is_refused(tmp_path):
    store, path = ocr_ready(tmp_path)
    old = store.snapshot().revision
    store.apply_plate_cameras(old, list(CAMERAS))
    before = digest(tmp_path)
    with pytest.raises(AiPortConfigurationError):
        store.apply_plate_cameras(old, [])
    assert digest(tmp_path) == before


def test_local_detector_cannot_enable_plate_permission(tmp_path):
    from test_aiport_config_store import fixture
    store, path, _ = fixture(tmp_path)
    before = digest(tmp_path)
    assert not store.plate_camera_status()["eligible"]
    with pytest.raises(AiPortConfigurationError):
        store.apply_plate_cameras(store.snapshot().revision, [CAMERAS[0]])
    assert digest(tmp_path) == before
