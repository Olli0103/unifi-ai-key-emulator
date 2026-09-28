"""OpenAI-first detection with a bounded local fallback. Synthetic frames and replies only."""

import json
import time

import pytest

from aikey.aiport_api_detection import (ApiDetectionError, ApiObjectDetector,
                                        fallback_to_fractions)
from test_aiport_api_detection import FRAME, STILL, _ollama_config, _response

CAMERA = "2A1122334455"
FALLBACK = {"provider_config": {"provider": "ollama", "model": "qwen3-vl:4b-instruct",
                                "base_url": "http://192.168.0.110:11434",
                                "allow_remote": True, "allow_insecure_http": True},
            "timeout_s": 60, "max_per_hour": 3}
LOCAL_REPLY = _response(json.dumps({"detections": [
    {"kind": "person", "label": "person", "score": 0.9, "box": [100, 200, 400, 800]}]}))
PRIMARY_REPLY = _response(json.dumps({"detections": [
    {"kind": "person", "label": "person", "score": 0.95, "box": [0.1, 0.2, 0.4, 0.8]}]}))


def http_error(code):
    def fail(url, headers, payload):
        raise ApiDetectionError(code)
    return fail


def detector(tmp_path, primary, fallback_calls, *, fallback=FALLBACK, local=LOCAL_REPLY):
    def local_transport(url, headers, payload):
        fallback_calls.append(payload)
        if isinstance(local, Exception):
            raise local
        return local
    return ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.7, transport=primary,
                             fallback=fallback, fallback_transport=local_transport)


@pytest.mark.parametrize("code,reason", [("api_detection_http_429", "http_429"),
                                         ("api_detection_http_5xx", "http_5xx"),
                                         ("api_detection_request_failed", "request_failed")])
def test_a_failing_primary_is_answered_by_the_local_model(tmp_path, code, reason):
    calls = []
    found = detector(tmp_path, http_error(code), calls).detect_for_camera(CAMERA, STILL)
    assert [(o.kind, o.box) for o in found] == [("person", (0.1, 0.2, 0.4, 0.8))]
    assert calls and calls[0]["format"]["required"] == ["detections"]
    assert calls[0]["model"] == "qwen3-vl:4b-instruct"


def test_a_healthy_primary_never_calls_the_fallback(tmp_path):
    calls = []
    engine = detector(tmp_path, lambda url, headers, payload: PRIMARY_REPLY, calls)
    assert engine.detect_for_camera(CAMERA, STILL)[0].kind == "person"
    assert calls == [] and engine.fallback_counts["requests"] == 0


def test_a_rejected_request_is_not_retried_locally(tmp_path):
    calls = []
    with pytest.raises(ApiDetectionError, match="http_4xx"):
        detector(tmp_path, http_error("api_detection_http_4xx"), calls).detect_for_camera(
            CAMERA, STILL)
    assert calls == []


def test_during_the_provider_backoff_frames_go_to_the_local_model(tmp_path):
    calls = []
    engine = detector(tmp_path, http_error("api_detection_http_429"), calls)
    engine.detect_for_camera(CAMERA, STILL)                      # primary fails -> local
    assert time.monotonic() < engine._provider_retry_at          # primary now paused
    engine.detect_for_camera(CAMERA, FRAME)                      # paused -> straight to local
    status = engine.provider_status()["api_fallback"]
    assert status["reasons"]["http_429"] == 1 and status["reasons"]["backoff"] == 1
    assert status["requests"] == 2 and status["objects"] == 2


def test_the_fallback_is_capped_per_camera_and_hour(tmp_path, monkeypatch):
    calls = []
    engine = detector(tmp_path, http_error("api_detection_http_5xx"), calls)
    monkeypatch.setattr(engine.motion, "should_request", lambda *a, **k: True)
    for _ in range(3):
        engine.detect_for_camera(CAMERA, STILL)
    with pytest.raises(ApiDetectionError, match="fallback_rate_limited"):
        engine.detect_for_camera(CAMERA, STILL)
    assert len(calls) == 3 and engine.fallback_counts["rate_limited"] == 1


def test_a_failing_fallback_raises_a_fixed_code(tmp_path):
    calls = []
    engine = detector(tmp_path, http_error("api_detection_http_429"), calls,
                      local=ApiDetectionError("api_detection_request_failed"))
    with pytest.raises(ApiDetectionError, match="fallback_failed"):
        engine.detect_for_camera(CAMERA, STILL)
    assert engine.fallback_counts["failed"] == 1
    assert engine.provider_status()["api_consecutive_provider_failures"] == 1


@pytest.mark.parametrize("bad", [
    dict(FALLBACK, provider_config=dict(FALLBACK["provider_config"],
                                        base_url="http://203.0.113.9:11434")),   # public host
    dict(FALLBACK, provider_config=dict(FALLBACK["provider_config"], provider="openai-compatible",
                                        base_url="http://192.168.0.110:11434/v1")),
    dict(FALLBACK, timeout_s=1), dict(FALLBACK, max_per_hour=0)])
def test_the_fallback_must_be_a_bounded_private_ollama_server(tmp_path, bad):
    with pytest.raises(ApiDetectionError, match="invalid_api_detection_fallback"):
        detector(tmp_path, http_error("api_detection_http_429"), [], fallback=bad)


def test_local_grid_boxes_become_fractions():
    text = fallback_to_fractions(json.dumps({"detections": [
        {"kind": "vehicle", "label": "car", "score": 0.8, "box": [0, 500, 1000, 1200]}]}))
    assert json.loads(text)["detections"][0]["box"] == [0.0, 0.5, 1.0, 1.0]
    with pytest.raises(ApiDetectionError):
        fallback_to_fractions("not json")


def test_the_candidate_config_accepts_only_an_ollama_fallback(tmp_path):
    from aikey.aiport_candidate import CandidateError, load_config
    from test_aiport_candidate import private_file
    from test_aiport_speech import pool_config
    config = pool_config(tmp_path, speech=None)
    config["live_pool_detector"]["fallback"] = FALLBACK
    path = tmp_path / "ok.json"
    private_file(path, json.dumps(config).encode())
    assert load_config(path, check_decoder_executable=False)["live_pool_detector"]["fallback"]
    for bad in (dict(FALLBACK, provider_config=dict(FALLBACK["provider_config"], provider="openai")),
                dict(FALLBACK, extra=1),
                dict(FALLBACK, provider_config=dict(FALLBACK["provider_config"], api_key_file="/k"))):
        config["live_pool_detector"]["fallback"] = bad
        path = tmp_path / "bad.json"
        private_file(path, json.dumps(config).encode())
        with pytest.raises(CandidateError):
            load_config(path, check_decoder_executable=False)
