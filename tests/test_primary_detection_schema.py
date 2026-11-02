"""Opt-in local primary schema: unchanged semantics, no retry or validation bypass."""

import json

import pytest
from multidict import MultiDict

from aikey.aiport_api_detection import (
    ApiDetectionError,
    ApiObjectDetector,
    build_detection_request,
    parse_detections,
    _PROMPT,
)
from aikey.aiport_candidate import parse_config, CandidateError
from aikey.aiport_config_store import AiPortConfigurationError
from aikey.providers import validate_inference_config
from test_aiport_api_detection import _ollama_config, _response, FIRST, SECOND, STILL
from test_aiport_config_store import fixture, settings
from test_control_site_secrets import signed_in


def config(provider):
    return {
        **_ollama_config(),
        "provider": provider,
        "base_url": "http://127.0.0.1:8178/v1"
        if provider == "openai-compatible"
        else "http://127.0.0.1:11434",
    }


@pytest.mark.parametrize("provider", ["ollama", "openai-compatible"])
def test_explicit_schema_preserves_destination_prompt_image_sampling_and_coordinate_contract(
    provider,
):
    selected = validate_inference_config(config(provider), require_api_key=False)
    original = selected.build_request([STILL], _PROMPT)
    assert build_detection_request(selected, STILL) == original
    url, headers, payload = build_detection_request(selected, STILL, structured_output=True)
    assert (url, headers) == original[:2]
    key = "format" if provider == "ollama" else "response_format"
    schema = payload.pop(key)
    if provider == "openai-compatible":
        assert schema["type"] == "json_schema"
        schema = schema["json_schema"]["schema"]
    assert (
        payload == original[2]
    )  # no semantic prompt, token, sampling, image-order or route tuning
    items = schema["properties"]["detections"]["items"]
    assert schema["properties"]["detections"]["maxItems"] == 20
    assert items["additionalProperties"] is False
    assert set(items["required"]) == {"kind", "label", "score", "box"}
    assert "plate" not in items["properties"]
    assert items["properties"]["box"]["items"] == {"type": "number", "minimum": 0, "maximum": 1}
    assert items["properties"]["box"]["minItems"] == items["properties"]["box"]["maxItems"] == 4
    assert items["properties"]["score"] == {"type": "number", "minimum": 0, "maximum": 1}
    assert (
        "animal" not in items["properties"]["label"]["enum"]
    )  # kind is not a valid animal species label
    assert set(items["properties"]["kind"]["enum"]) == {"person", "animal", "vehicle", "package"}
    # Callers cannot mutate a shared global schema for the next camera.
    items["properties"]["box"]["items"]["maximum"] = 1000
    again = build_detection_request(selected, STILL, structured_output=True)[2][key]
    if provider == "openai-compatible":
        again = again["json_schema"]["schema"]
    assert again["properties"]["detections"]["items"]["properties"]["box"]["items"]["maximum"] == 1


@pytest.mark.parametrize("provider", ["ollama", "openai-compatible"])
@pytest.mark.parametrize("separate_ocr", [False, True])
def test_real_detector_scopes_plate_schema_and_never_changes_fallback(
    provider, separate_ocr, tmp_path
):
    calls = []

    def transport(url, headers, payload):
        calls.append(payload)
        return (
            _response('{"detections":[]}')
            if provider == "ollama"
            else {
                "choices": [{"message": {"content": '{"detections":[]}'}, "finish_reason": "stop"}]
            }
        )

    detector = ApiObjectDetector(
        config(provider),
        tmp_path,
        threshold=0.8,
        transport=transport,
        plate_cameras=frozenset({FIRST}),
        structured_output=True,
        ocr_provider_config=config(provider) if separate_ocr else None,
    )
    detector.detect_for_camera(FIRST, STILL)
    detector.detect_for_camera(SECOND, STILL)
    for i, payload in enumerate(calls):
        schema = (
            payload["format"]
            if provider == "ollama"
            else payload["response_format"]["json_schema"]["schema"]
        )
        assert ("plate" in schema["properties"]["detections"]["items"]["properties"]) == (
            i == 0 and not separate_ocr
        )
    assert detector.fallback_counts["requests"] == 0 and len(calls) == 2


@pytest.mark.parametrize(
    "reply",
    [
        '{"detections":[',
        '{"detections":[],"detections":[]}',
        '{"detections":[{"kind":"person","label":"person","score":NaN,"box":[0,0,1,1]}]}',
    ],
)
def test_backend_ignoring_schema_still_fails_existing_strict_parser(tmp_path, reply):
    calls = []
    detector = ApiObjectDetector(
        _ollama_config(),
        tmp_path,
        threshold=0.8,
        structured_output=True,
        transport=lambda *args: calls.append(args) or _response(reply),
    )
    with pytest.raises(ApiDetectionError):
        detector.detect_for_camera(FIRST, STILL)
    assert len(calls) == 1  # no free-text recovery request


def test_schema_cannot_make_grid_coordinates_valid():
    rejected = {}
    assert (
        parse_detections(
            json.dumps(
                {
                    "detections": [
                        {
                            "kind": "person",
                            "label": "person",
                            "score": 0.99,
                            "box": [100, 100, 800, 900],
                        }
                    ]
                }
            ),
            threshold=0.5,
            rejected=rejected,
        )
        == ()
    )
    assert rejected


def test_schema_refusal_has_no_second_primary_or_fallback_request(tmp_path):
    calls = []

    def refused(*args):
        calls.append(args)
        raise ApiDetectionError("api_detection_http_4xx")

    def forbidden(*args):
        pytest.fail("A schema refusal must not issue an unconstrained recovery request")

    detector = ApiObjectDetector(
        _ollama_config(),
        tmp_path,
        threshold=0.8,
        structured_output=True,
        transport=refused,
        fallback={"provider_config": _ollama_config()},
        fallback_transport=forbidden,
    )
    with pytest.raises(ApiDetectionError, match="http_4xx"):
        detector.detect_for_camera(FIRST, STILL)
    assert len(calls) == 1 and detector.fallback_counts["requests"] == 0


@pytest.mark.parametrize("value", [None, 0, 1, "yes", [], {}])
def test_library_rejects_non_boolean_before_budget_creation(tmp_path, value):
    with pytest.raises(ApiDetectionError, match="schema_policy"):
        ApiObjectDetector(
            _ollama_config(),
            tmp_path,
            threshold=0.8,
            max_requests_per_hour=12,
            structured_output=value,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_unsupported_provider_is_not_retried_or_loaded(tmp_path, provider):
    with pytest.raises(ApiDetectionError, match="schema_policy"):
        ApiObjectDetector(
            {"provider": provider, "api_key_file": str(tmp_path / "absent")},
            tmp_path,
            threshold=0.8,
            structured_output=True,
        )
    assert list(tmp_path.iterdir()) == []


def local_settings(tmp_path, **changes):
    selected = settings(tmp_path)
    selected.pop("api_key_file")
    selected.update(
        provider="ollama",
        model="fictional-local",
        base_url="http://127.0.0.1:11434",
        allow_remote=False,
        **changes,
    )
    return selected


@pytest.mark.parametrize("enabled", [True, False])
async def test_candidate_runtime_factory_passes_saved_choice(tmp_path, monkeypatch, enabled):
    import aikey.aiport_candidate as candidate

    store, path, _ = fixture(tmp_path)
    store.apply(store.snapshot().revision, local_settings(tmp_path, structured_output=enabled))
    loaded = candidate.load_config(path)
    service = candidate.CandidateService(loaded, tmp_path)
    captured = []
    sentinel = object()

    def factory(*args, **kwargs):
        captured.append(kwargs)
        return sentinel

    monkeypatch.setattr(candidate, "ApiObjectDetector", factory)
    try:
        assert service._inference._load_detector() is sentinel
        assert captured[0]["structured_output"] is enabled
    finally:
        await service._inference.close()
        service._model_work_fence.close()


def test_saved_remote_schema_choice_refused_before_history(tmp_path):
    store, path, _ = fixture(tmp_path)
    before = path.read_bytes()
    selected = settings(tmp_path)
    selected["structured_output"] = True
    with pytest.raises(AiPortConfigurationError):
        store.apply(store.snapshot().revision, selected)
    assert path.read_bytes() == before and not store.history_dir.exists()


def test_saved_flag_optional_and_preserved_by_old_provider_edits_with_exact_undo(tmp_path):
    store, path, _ = fixture(tmp_path)
    original = path.read_bytes()
    result = store.apply(
        store.snapshot().revision, local_settings(tmp_path, structured_output=True)
    )
    assert result.structured_output is True
    enabled = path.read_bytes()
    fields = local_settings(tmp_path)
    fields["model"] = "fictional-replacement"
    next_result = store.apply(result.revision, fields)
    assert next_result.structured_output is True
    assert json.loads(path.read_text())["live_pool_detector"]["structured_output"] is True
    store.rollback_provider(next_result.revision)
    assert path.read_bytes() == enabled
    # A provider-side refusal does not rewrite the flag or original profile.
    assert json.loads(original)["mac"] == json.loads(enabled)["mac"]


@pytest.mark.parametrize("value", [None, 0, 1, "true", []])
def test_candidate_and_store_reject_invalid_saved_flag(tmp_path, value):
    store, path, _ = fixture(tmp_path)
    result = store.apply(store.snapshot().revision, local_settings(tmp_path))
    raw = json.loads(path.read_bytes())
    raw["live_pool_detector"]["structured_output"] = value
    before = path.read_bytes()
    with pytest.raises(CandidateError):
        parse_config(json.dumps(raw).encode(), state_dir=tmp_path)
    with pytest.raises(AiPortConfigurationError):
        store.apply(result.revision, local_settings(tmp_path, structured_output=value))
    assert path.read_bytes() == before


async def test_browser_form_explicit_toggle_and_legacy_omission_preserves_enabled_flag(tmp_path):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        form = {
            "csrf": site.security.csrf_token(cookie),
            "profile": "aiport",
            "revision": site.aiports["aiport"].snapshot().revision,
            "provider": "ollama",
            "model": "fictional-local",
            "base_url": "http://127.0.0.1:11434",
            "api_key": "",
            "max_output_tokens": "256",
            "threshold": ".8",
            "smart_types": "person",
            "max_requests_per_hour": "",
            "structured_output_present": "yes",
            "structured_output": "yes",
        }
        page = await (await client.get("/")).text()
        assert "Require a JSON schema for primary local detections" in page
        for mode in ["enable", "legacy", "disable"]:
            form["revision"] = site.aiports["aiport"].snapshot().revision
            payload = dict(form)
            if mode == "legacy":
                payload.pop("structured_output_present")
                payload.pop("structured_output")
            if mode == "disable":
                payload.pop("structured_output")
            response = await client.post(
                "/provider", data=payload, headers={"Origin": site.origin}, allow_redirects=False
            )
            assert response.status == 303
            assert site.aiports["aiport"].snapshot().structured_output is (mode != "disable")
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("fault", ["marker_missing", "repeated", "bad_value", "unsupported"])
async def test_malformed_new_checkbox_never_saves(tmp_path, fault):
    site, server, client, cookie, path = await signed_in(tmp_path)
    try:
        port_path = site.aiports["aiport"].path
        before = port_path.read_bytes()
        data = MultiDict(
            {
                "csrf": site.security.csrf_token(cookie),
                "profile": "aiport",
                "revision": site.aiports["aiport"].snapshot().revision,
                "provider": "ollama",
                "model": "fictional",
                "base_url": "http://127.0.0.1:11434",
                "api_key": "",
                "max_output_tokens": "256",
                "threshold": ".8",
                "smart_types": "person",
                "max_requests_per_hour": "",
                "structured_output_present": "yes",
                "structured_output": "yes",
            }
        )
        if fault == "marker_missing":
            del data["structured_output_present"]
        if fault == "repeated":
            data.add("structured_output", "yes")
        if fault == "bad_value":
            data["structured_output"] = "on"
        if fault == "unsupported":
            data["provider"] = "anthropic"
        response = await client.post(
            "/provider", data=data, headers={"Origin": site.origin}, allow_redirects=False
        )
        assert response.status == 200 and "Settings not saved" in await response.text()
        assert port_path.read_bytes() == before
    finally:
        await client.close()
        await server.close()
