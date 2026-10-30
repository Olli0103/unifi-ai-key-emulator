"""Local role policy must be checked before a saved config reaches a worker."""

import json
import socket

import pytest

from aikey.config import ConfigError, defaults, validate_config
from aikey.config_store import ConfigurationStore, UnsafeConfigurationChange


@pytest.fixture(autouse=True)
def no_model_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("role admission must not load a face store or contact a service")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr("aikey.faces.FaceStore.__init__", forbidden)


@pytest.mark.parametrize("role,selection", [
    ("face_recognition", {}),
    ("face_recognition", {"server": "http://127.0.0.1:8182", "camera_ids": []}),
    ("face_recognition", {"server": "http://127.0.0.1:8182", "camera_ids": ["synthetic"],
                          "model": "unimplemented-model-selection"}),
    ("face_recognition", {"server": "http://127.0.0.1:8182", "camera_ids": ["synthetic"],
                          "max_faces": True}),
    ("face_recognition", {"server": "http://127.0.0.1:0", "camera_ids": ["synthetic"]}),
    ("face_enhancement", {"server": "https://unapproved.invalid"}),
    ("face_enhancement", {"server": "http://127.0.0.1:8183", "model": "text-model"}),
    ("face_enhancement", {"server": "http://synthetic:secret@127.0.0.1:8183"}),
    ("deep_understanding", {"reid_server": "http://127.0.0.1:8184", "model": "clip"}),
    ("deep_understanding", {"rerank_server": "http://127.0.0.1:8185"}),
])
def test_bad_local_role_refused_without_config_history_or_activation(tmp_path, role, selection):
    raw = defaults(tmp_path / "state", "020000000001")
    raw["controller"]["host"] = "127.0.0.1"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw) + "\n")
    path.chmod(0o600)
    store = ConfigurationStore(path)
    before = path.read_bytes()
    revision = store.snapshot().revision
    candidate = json.loads(before)
    candidate[role] = selection
    with pytest.raises(ConfigError):
        validate_config(candidate, base=tmp_path)
    with pytest.raises(UnsafeConfigurationChange):
        store.apply(revision, {role: selection})
    assert path.read_bytes() == before and store.snapshot().revision == revision
    assert not store.history_dir.exists() and not (tmp_path / "state" / "faces").exists()


def test_valid_local_roles_are_checked_without_rewriting_their_saved_policy(tmp_path):
    raw = defaults(tmp_path / "state", "020000000001")
    raw["controller"]["host"] = "127.0.0.1"
    raw.update(face_recognition={"server": "http://127.0.0.1:8182/", "camera_ids": ["synthetic"]},
        face_enhancement={"server": "http://127.0.0.1:8183/"},
        deep_understanding={"reid_server": "http://127.0.0.1:8184/",
                            "rerank_server": "http://127.0.0.1:8185/"})
    checked = validate_config(raw, base=tmp_path)
    for key in ("face_recognition", "face_enhancement", "deep_understanding"):
        assert checked[key] == raw[key]
    assert "max_faces" not in checked["face_recognition"]
    assert not (tmp_path / "state").exists()
