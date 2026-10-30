"""Saved model identities are role-specific, non-secret, and not capability proof."""

from copy import deepcopy
import json
import socket

import aiohttp
import pytest

from aikey import clip, deep_mode, model_roles
from aikey.config import defaults
from test_aiport_provider_rollback import saved_twice
from test_config_store import store as key_store
from test_control_site_secrets import signed_in, SECRET


def by_role(report):
    return {row["role"]: row for row in report["roles"]}


def test_all_requested_roles_have_separate_explicit_contracts_without_io(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("registry must not contact a model or resolve a host")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    raw = defaults(tmp_path / "state", "020000000001")
    key = by_role(model_roles.key_roles(raw, "a" * 64))
    port = by_role(model_roles.port_roles({}, "b" * 64))
    assert set(key) | set(port) == {"caption", "text_embedding", "search_image_text", "detection",
        "reverification", "ocr", "speech", "reidentification", "recognition", "reranking", "enhancement"}
    assert key["text_embedding"]["dimensions"] == 384
    assert key["search_image_text"]["dimensions"] == clip.DIMENSIONS == 768
    assert key["reidentification"]["source_dimensions"] == deep_mode.REID_SOURCE_DIMENSIONS == 256
    assert key["reidentification"]["dimensions"] == deep_mode.REID_DIMENSIONS == 512
    assert key["recognition"]["dimensions"] == 128
    assert port["recognition"]["dimensions"] == 512
    assert all(r["weights_verified"] is False and r["capability_status"] == "not_checked_by_this_read"
               for r in [*key.values(), *port.values()])


def test_identity_is_versioned_but_does_not_include_keys_paths_or_camera_scopes(tmp_path):
    raw = defaults(tmp_path / "state", "020000000001")
    raw["inference"].update(model="synthetic-caption", base_url="http://127.0.0.1:9191/private")
    raw["inference"]["api_key_file"] = "/private/secret-marker/key"
    raw["speech_to_text"] = {"provider": "openai-compatible", "model": "synthetic-speech",
        "base_url": "http://127.0.0.1:9192/private", "api_key_file": "/private/speech-key",
        "camera_ids": ["private-camera-marker"]}
    original = model_roles.key_roles(raw, "a" * 64)
    changed = deepcopy(raw)
    changed["inference"]["api_key_file"] = "/different/private-key"
    changed["speech_to_text"].update(api_key_file="/rotated/key", camera_ids=["other-camera"])
    after = model_roles.key_roles(changed, "b" * 64)
    assert original["schema"] == after["schema"] == "aikey-model-registry/1"
    assert [r["selection_id"] for r in original["roles"]] == [r["selection_id"] for r in after["roles"]]
    public = json.dumps(original)
    for value in ("secret-marker", "/private/", "private-camera-marker", "127.0.0.1"):
        assert value not in public
    changed["inference"]["model"] = "synthetic-caption-new"
    newest = by_role(model_roles.key_roles(changed, "c" * 64))
    assert newest["caption"]["selection_id"] != by_role(after)["caption"]["selection_id"]
    assert newest["speech"]["selection_id"] == by_role(after)["speech"]["selection_id"]


def test_unreported_external_weights_are_not_invented(tmp_path):
    raw = defaults(tmp_path / "state", "020000000001")
    raw.update(face_recognition={"server": "http://127.0.0.1:8182", "camera_ids": ["private"]},
               face_enhancement={"server": "http://127.0.0.1:8183"},
               deep_understanding={"reid_server": "http://127.0.0.1:8184",
                                   "rerank_server": "http://127.0.0.1:8185"})
    roles = by_role(model_roles.key_roles(raw, "a" * 64))
    for role in ("recognition", "enhancement", "reranking", "reidentification"):
        assert roles[role]["configured"] and roles[role]["model"] is None
        assert roles[role]["declared_revision"] is None and not roles[role]["weights_verified"]
        assert len(roles[role]["selection_id"]) == 64
    assert roles["reidentification"]["expected_model"] == deep_mode.REID_MODEL


def test_port_registry_distinguishes_legacy_shared_ocr_and_a_separate_assignment(tmp_path):
    store, path, _, _ = saved_twice(tmp_path)
    raw = json.loads(path.read_text())
    raw["live_pool_detector"]["plate_cameras"] = [raw["paired_streams"][0]["camera_mac"]]
    path.write_text(json.dumps(raw) + "\n")
    before = path.read_bytes()
    legacy = by_role(store.model_registry())
    assert legacy["ocr"]["assignment"] == "legacy_shared_detection"
    assert path.read_bytes() == before
    raw["live_pool_detector"]["ocr_provider_config"] = {
        "provider": "ollama", "model": "synthetic-ocr", "base_url": "http://127.0.0.1:11435"}
    path.write_text(json.dumps(raw) + "\n")
    separate = by_role(store.model_registry())
    assert separate["ocr"]["assignment"] == "explicit" and separate["ocr"]["model"] == "synthetic-ocr"
    assert separate["detection"]["selection_id"] == legacy["detection"]["selection_id"]
    assert separate["ocr"]["selection_id"] != legacy["ocr"]["selection_id"]


def test_store_registry_keeps_configuration_bytes_and_reads_no_secret_file(tmp_path):
    store, path = key_store(tmp_path)
    before = path.read_bytes()
    # Fixture references do not even exist; a registry read needs none of them.
    report = store.model_registry()
    assert report["configuration_revision"] == store.snapshot().revision
    assert path.read_bytes() == before and not store.history_dir.exists()
    assert "provider-key" not in json.dumps(report) and "embedding-key" not in json.dumps(report)


async def test_private_registry_page_is_read_only_and_never_calls_a_provider(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    anonymous = aiohttp.ClientSession()
    before = path.read_bytes(), site.aiport.path.read_bytes()
    try:
        denied = await anonymous.get(site.origin + "/model-registry", allow_redirects=False)
        assert denied.status == 303
        await denied.read()
        result = await client.get("/model-registry")
        body = await result.text()
        assert result.status == 200 and "Saved model role registry" in body
        assert "not active-runtime status" in body and "Capability and weights are not checked" in body
        assert "256 source → 512 stored" in body and "128" in body
        assert "127.0.0.1" not in body and "api_key_file" not in body and SECRET not in body
        assert "<script" not in body.lower() and "localStorage" not in body
        assert result.headers["Cache-Control"] == "no-store"
        assert (path.read_bytes(), site.aiport.path.read_bytes()) == before
    finally:
        await anonymous.close()
        await client.close()
        await server.close()
