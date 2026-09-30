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


def test_the_fallback_has_no_count_ceiling_and_ignores_a_legacy_hourly_cap(tmp_path, monkeypatch):
    # Local detection is compute, not a paid call: a legacy max_per_hour
    # (formerly 120 per camera and hour) no longer limits it.
    calls = []
    engine = detector(tmp_path, http_error("api_detection_http_5xx"), calls,
                      fallback=dict(FALLBACK, max_per_hour=3))
    monkeypatch.setattr(engine.motion, "should_request", lambda *a, **k: True)
    for _ in range(200):
        assert engine.detect_for_camera(CAMERA, STILL)[0].kind == "person"
    assert len(calls) == 200 and engine.fallback_counts["requests"] == 200
    assert "rate_limited" not in engine.fallback_counts


def test_a_failing_local_model_pauses_the_fallback_then_recovers(tmp_path, monkeypatch):
    import aikey.aiport_api_detection as module
    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    calls, local = [], {"fail": True}

    def local_transport(url, headers, payload):
        calls.append(1)
        if local["fail"]:
            raise ApiDetectionError("api_detection_request_failed")
        return LOCAL_REPLY
    engine = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.7,
                               transport=http_error("api_detection_http_5xx"),
                               fallback=FALLBACK, fallback_transport=local_transport)
    monkeypatch.setattr(engine.motion, "should_request", lambda *a, **k: True)
    with pytest.raises(ApiDetectionError, match="fallback_failed"):
        engine.detect_for_camera(CAMERA, STILL)
    now[0] += 29                                   # inside the 30 s pause
    with pytest.raises(ApiDetectionError, match="fallback_backoff"):
        engine.detect_for_camera(CAMERA, STILL)
    assert len(calls) == 1 and engine.fallback_counts["backoff_skipped"] == 1
    now[0] += 2
    with pytest.raises(ApiDetectionError, match="fallback_failed"):
        engine.detect_for_camera(CAMERA, STILL)    # second failure: 60 s pause
    now[0] += 59
    with pytest.raises(ApiDetectionError, match="fallback_backoff"):
        engine.detect_for_camera(CAMERA, STILL)
    local["fail"] = False
    now[0] += 2
    assert engine.detect_for_camera(CAMERA, STILL)[0].kind == "person"
    assert engine.detect_for_camera(CAMERA, STILL)[0].kind == "person"   # no pause after success
    assert len(calls) == 4


def test_a_spent_paid_cost_cap_sends_frames_to_the_local_model_not_away(tmp_path, monkeypatch):
    # max_requests_per_hour is a cost guard for the paid provider only; once
    # it is spent the frame is still detected, locally, so the cap never
    # limits object events.
    paid, calls = [], []

    def primary(url, headers, payload):
        paid.append(1)
        return PRIMARY_REPLY

    def local_transport(url, headers, payload):
        calls.append(1)
        return LOCAL_REPLY
    engine = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.7, transport=primary,
                               max_requests_per_hour=4, fallback=FALLBACK,
                               fallback_transport=local_transport)
    monkeypatch.setattr(engine.motion, "should_request", lambda *a, **k: True)
    monkeypatch.setattr(engine.motion, "is_refresh", lambda *a, **k: False)
    found = [engine.detect_for_camera(CAMERA, STILL) for _ in range(10)]
    assert all(result and result[0].kind == "person" for result in found)
    assert len(paid) == 4 and len(calls) == 6
    assert engine.fallback_counts["reasons"]["paid_budget"] == 6


def test_without_a_fallback_a_spent_paid_cap_still_only_defers(tmp_path, monkeypatch):
    paid = []

    def primary(url, headers, payload):
        paid.append(1)
        return PRIMARY_REPLY
    engine = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.7, transport=primary,
                               max_requests_per_hour=2)
    monkeypatch.setattr(engine.motion, "should_request", lambda *a, **k: True)
    monkeypatch.setattr(engine.motion, "is_refresh", lambda *a, **k: False)
    results = [engine.detect_for_camera(CAMERA, STILL) for _ in range(4)]
    assert len(paid) == 2 and results[2:] == [(), ()]


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
                                        base_url="http://203.0.113.9:8000/v3")),        # public host
    dict(FALLBACK, timeout_s=1), dict(FALLBACK, max_per_hour=0)])   # legacy key still type-checked
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


def test_a_local_openai_compatible_fallback_asks_for_the_schema_as_a_response_format(tmp_path):
    # OpenVINO Model Server on the NAS backend replaces Ollama as the fallback.
    calls = []
    ovms = dict(FALLBACK, provider_config={"provider": "openai-compatible", "model": "qwen3-vl-8b",
                                           "base_url": "http://172.30.50.13:8000/v3",
                                           "allow_remote": True, "allow_insecure_http": True})
    local = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"detections": [
        {"kind": "person", "label": "person", "score": 0.9, "box": [100, 200, 400, 800]}]})}}]}
    engine = detector(tmp_path, http_error("api_detection_http_429"), calls, fallback=ovms, local=local)
    found = engine.detect_for_camera(CAMERA, STILL)
    assert [(o.kind, o.box) for o in found] == [("person", (0.1, 0.2, 0.4, 0.8))]
    assert calls[0]["response_format"]["json_schema"]["schema"]["required"] == ["detections"]
    assert "format" not in calls[0]
