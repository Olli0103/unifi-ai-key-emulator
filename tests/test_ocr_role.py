"""Independent OCR assignment: synthetic pixels/keys/replies, no model contact."""

from copy import deepcopy
import json

from aiohttp import web
import pytest

from aikey.aiport_api_detection import ApiDetectionError, ApiObjectDetector
from aikey.aiport_candidate import CandidateError, parse_config
from aikey.aiport_detection import ObjectObservation
from test_aiport_api_detection import FIRST, SECOND, FRAME, _ollama_config, _response
from test_aiport_provider_rollback import saved_twice
from test_control_site_catalog import configured as configured_site
from test_control_site_probe import form as probe_form
from test_provider_probe import ANSWER, fixture_image, reply_for


def selection(tmp_path, name, port):
    key = tmp_path / (name + "-key")
    key.write_text("synthetic-" + name + "-credential")
    key.chmod(0o600)
    return {**_ollama_config(), "model": "synthetic-" + name,
            "base_url": f"http://127.0.0.1:{port}", "api_key_file": str(key)}


def car_reply():
    return _response(json.dumps({"detections": [
        {"kind": "vehicle", "label": "car", "score": 0.95, "box": [0.1, 0.1, 0.8, 0.8]}]}))


def configured(tmp_path, *, cap=None, ocr_reply=None, ocr_key=True):
    calls = []

    def transport(url, headers, payload):
        calls.append((url, dict(headers), deepcopy(payload)))
        if payload["model"] == "synthetic-ocr":
            return _response(ocr_reply if ocr_reply is not None else '{"plate":"SYN 123"}')
        return car_reply()

    ocr = selection(tmp_path, "ocr", 11435)
    if not ocr_key:
        del ocr["api_key_file"]
    detector = ApiObjectDetector(selection(tmp_path, "detection", 11434), tmp_path,
        threshold=0.8, max_requests_per_hour=cap, transport=transport,
        plate_cameras=frozenset({FIRST}), ocr_provider_config=ocr)
    return detector, calls


def test_detection_and_ocr_have_distinct_model_route_and_key(tmp_path):
    detector, calls = configured(tmp_path)
    [item] = detector.detect_for_camera(FIRST, FRAME)
    assert item.kind == "vehicle" and item.plate == "SYN 123"
    assert [p["model"] for _, _, p in calls] == ["synthetic-detection", "synthetic-ocr"]
    assert calls[0][0].startswith("http://127.0.0.1:11434/")
    assert calls[1][0].startswith("http://127.0.0.1:11435/")
    assert "synthetic-detection-credential" in json.dumps(calls[0][1])
    assert "synthetic-ocr-credential" not in json.dumps(calls[0])
    assert "synthetic-ocr-credential" in json.dumps(calls[1][1])
    assert "synthetic-detection-credential" not in json.dumps(calls[1])
    assert "plate" not in json.dumps(calls[0][2]).lower()
    diagnostic = json.dumps(detector.diagnostic_counts(FIRST))
    assert "SYN 123" not in diagnostic and "credential" not in diagnostic


def test_other_cameras_never_call_the_separate_ocr_role(tmp_path):
    detector, calls = configured(tmp_path)
    [item] = detector.detect_for_camera(SECOND, FRAME)
    assert item.plate is None
    assert len(calls) == 1 and calls[0][2]["model"] == "synthetic-detection"


def test_missing_optional_ocr_key_never_inherits_the_detection_key(tmp_path):
    detector, calls = configured(tmp_path, ocr_key=False)
    detector.detect_for_camera(FIRST, FRAME)
    assert len(calls) == 2
    assert "synthetic-detection-credential" in json.dumps(calls[0][1])
    assert "Authorization" not in calls[1][1]
    assert "synthetic-detection-credential" not in json.dumps(calls[1])


def test_ocr_uses_the_existing_budget_and_cannot_fall_back_to_detection(tmp_path):
    detector, calls = configured(tmp_path, cap=2)
    # Exercise the crop boundary directly: startup/motion sampling is separate.
    assert detector.budget.claim(FIRST) and detector.budget.claim(FIRST)
    [item] = detector._read_plates(FIRST, FRAME, (
        ObjectObservation("vehicle", "car", 0.95, (0.1, 0.1, 0.8, 0.8)),))
    assert item.plate is None and calls == []
    assert detector.diagnostic_counts(FIRST)["plate_crops"]["budget_denied"] == 1


@pytest.mark.parametrize("reply", ['{"detections":[]}', '{"plate":"SYN 123","plate":"OTHER"}',
                                  '{"plate":NaN}', '{"plate":["SYN 123"]}'])
def test_wrong_or_ambiguous_ocr_output_never_publishes_a_plate_or_retries(tmp_path, reply):
    detector, calls = configured(tmp_path, ocr_reply=reply)
    [item] = detector.detect_for_camera(FIRST, FRAME)
    assert item.plate is None and len(calls) == 2
    assert detector.diagnostic_counts(FIRST)["plate_crops"]["failed"] == 1


def test_explicit_ocr_requires_an_explicit_plate_camera_scope(tmp_path):
    with pytest.raises(ApiDetectionError):
        ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.8,
                          ocr_provider_config=selection(tmp_path, "ocr", 11435))


def test_parser_role_edit_and_provider_undo_keep_assignments_separate(tmp_path):
    store, path, _, _ = saved_twice(tmp_path)
    raw = json.loads(path.read_text())
    raw["live_pool_detector"]["plate_cameras"] = [raw["paired_streams"][0]["camera_mac"]]
    raw["live_pool_detector"]["ocr_provider_config"] = selection(tmp_path, "ocr", 11435)
    path.write_text(json.dumps(raw) + "\n")
    before = path.read_bytes()
    parse_config(before, state_dir=tmp_path)
    original = deepcopy(raw["live_pool_detector"]["provider_config"])
    revision = store.snapshot().revision
    p = store.preview_role_model(revision, "ocr", "synthetic-ocr-next")
    assert path.read_bytes() == before and p.role == "ocr"
    assert store.role_models()["ocr"]["model"] == "synthetic-ocr"
    selected, _, _ = store.catalog_configuration(revision, "ocr")
    assert selected["api_key_file"].endswith("ocr-key")
    saved = store.apply_role_model(revision, "ocr", "synthetic-ocr-next")
    assert saved.revision == p.resulting_revision
    assert json.loads(path.read_text())["live_pool_detector"]["provider_config"] == original
    store.rollback_provider(saved.revision)
    assert path.read_bytes() == before


def test_primary_provider_form_preserves_ocr_and_missing_ocr_key_blocks_undo(tmp_path):
    store, path, _, _ = saved_twice(tmp_path)
    raw = json.loads(path.read_text())
    d = raw["live_pool_detector"]
    d["plate_cameras"] = [raw["paired_streams"][0]["camera_mac"]]
    d["ocr_provider_config"] = selection(tmp_path, "ocr", 11435)
    path.write_text(json.dumps(raw) + "\n")
    snapshot = store.snapshot()
    settings = {"provider": snapshot.provider, "model": "synthetic-detection-next",
                "base_url": snapshot.base_url, "allow_remote": snapshot.allow_remote,
                "allow_insecure_http": snapshot.allow_insecure_http,
                "max_output_tokens": snapshot.max_output_tokens, "threshold": snapshot.threshold,
                "smart_types": list(snapshot.smart_types),
                "max_requests_per_hour": snapshot.max_requests_per_hour,
                "api_key_file": d["provider_config"]["api_key_file"]}
    store.apply(snapshot.revision, settings)
    assert json.loads(path.read_text())["live_pool_detector"]["ocr_provider_config"] == d["ocr_provider_config"]
    # An OCR-only model edit also needs its role's retained key for safe undo.
    store.apply_role_model(store.snapshot().revision, "ocr", "synthetic-ocr-next")
    (tmp_path / "ocr-key").unlink()
    assert store.provider_rollback_status() == {"available": False, "reason": "prior_key_missing"}


@pytest.mark.parametrize("change", ["missing_scope", "inline_key", "speech_provider", "relative_key"])
def test_invalid_ocr_configuration_is_refused_before_persistence(tmp_path, change):
    store, path, _, _ = saved_twice(tmp_path)
    raw = json.loads(path.read_text())
    d = raw["live_pool_detector"]
    d["plate_cameras"] = [raw["paired_streams"][0]["camera_mac"]]
    d["ocr_provider_config"] = selection(tmp_path, "ocr", 11435)
    if change == "missing_scope":
        del d["plate_cameras"]
    elif change == "inline_key":
        d["ocr_provider_config"]["api_key"] = "synthetic-must-not-be-inline"
    elif change == "speech_provider":
        d["ocr_provider_config"]["provider"] = "transcription-only"
    else:
        d["ocr_provider_config"]["api_key_file"] = "relative-key"
    before = path.read_bytes()
    with pytest.raises(CandidateError):
        parse_config(json.dumps(raw).encode(), state_dir=tmp_path)
    assert path.read_bytes() == before


async def test_http_ocr_catalog_and_one_use_probe_use_only_the_ocr_credential(tmp_path, monkeypatch):
    seen = []

    async def endpoint(request):
        seen.append((request.method, request.path, request.headers.get("Authorization")))
        if request.method == "GET":
            return web.json_response({"data": [{"id": "synthetic-ocr"}], "has_more": False})
        return web.json_response(reply_for("openai-compatible"))

    monkeypatch.setattr("aikey.provider_probe._challenge", lambda: (fixture_image(), ANSWER))
    upstream, site, server, client, cookie, key_path = await configured_site(tmp_path, endpoint)
    try:
        store = site.aiports["aiport"]
        raw = json.loads(store.path.read_text())
        raw["live_pool_detector"]["plate_cameras"] = [raw["paired_streams"][0]["camera_mac"]]
        raw["live_pool_detector"]["ocr_provider_config"] = {
            "provider": "openai-compatible", "model": "synthetic-ocr",
            "base_url": str(upstream.make_url("/ocr/v1")), "api_key_file": "/state/ocr-key"}
        store.path.write_text(json.dumps(raw) + "\n")
        key = store.path.parent / "ocr-key"
        key.write_text("synthetic-ocr-credential")
        key.chmod(0o600)
        fields, page = await probe_form(client, profile="aiport", role="ocr")
        assert "AI Port plate text (OCR)" in page
        assert "name='acknowledge_cost' value='yes' required checked" not in page
        before = key_path.read_bytes(), store.path.read_bytes()
        catalog = await client.post("/model-catalog", data={k: fields[k] for k in (
            "csrf", "profile", "role", "revision")}, headers={"Origin": site.origin})
        assert catalog.status == 200 and "synthetic-ocr" in await catalog.text()
        result = await client.post("/provider-probe", data=fields, headers={"Origin": site.origin})
        body = await result.text()
        assert result.status == 200 and "generated image answer matched" in body
        assert "role: ocr" in body and "not general modality" in body
        assert "synthetic-ocr-credential" not in body and ANSWER not in body
        assert seen == [("GET", "/ocr/v1/models", "Bearer synthetic-ocr-credential"),
                        ("POST", "/ocr/v1/chat/completions", "Bearer synthetic-ocr-credential")]
        assert (key_path.read_bytes(), store.path.read_bytes()) == before
        site._probe_grants.next_at = 0
        replay = await client.post("/provider-probe", data=fields, headers={"Origin": site.origin})
        assert replay.status == 409 and len(seen) == 2
    finally:
        await client.close()
        await server.close()
        await upstream.close()
