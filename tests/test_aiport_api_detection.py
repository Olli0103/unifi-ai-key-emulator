"""The API detector uses synthetic responses and never sends private footage."""

import json
from io import BytesIO
import socket
from urllib.error import HTTPError, URLError

import pytest
from PIL import Image, ImageDraw

from aikey.aiport_api_detection import (
    ApiDetectionError, ApiObjectDetector, _post, parse_detections,
)
from aikey.aiport_event_budget import _HOUR_NS
from aikey.aiport_tracking import TemporalTracker


FIRST = "2A1122334455"
SECOND = "2A1122334456"

def _frame(color):
    data = BytesIO()
    Image.new("RGB", (64, 64), color).save(data, format="JPEG")
    return data.getvalue()


STILL = _frame("black")
FRAME = _frame("white")


def _ollama_config():
    return {"provider": "ollama", "model": "synthetic-vision",
            "base_url": "http://127.0.0.1:11434", "max_output_tokens": 128}


def _response(text):
    return {"done": True, "message": {"content": text}}


def test_detects_only_valid_bounded_observations_and_caps_paid_calls(tmp_path):
    calls = []

    def transport(url, headers, payload):
        calls.append((url, headers, payload))
        return _response(json.dumps({"detections": [
            {"kind": "person", "label": "person", "score": 0.92,
             "box": [0.1, 0.2, 0.4, 0.8]},
            {"kind": "animal", "label": "cat", "score": 0.2,
             "box": [0.5, 0.5, 0.8, 0.9]},
        ]}))

    detector = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.7,
                                 max_requests_per_hour=2, transport=transport)
    assert [result.kind for result in detector.detect_for_camera(FIRST, STILL)] == ["person"]
    assert [result.kind for result in detector.detect_for_camera(FIRST, FRAME)] == ["person"]
    assert detector.detect_for_camera(FIRST, FRAME) == ()
    assert len(calls) == 2
    assert calls[0][0] == "http://127.0.0.1:11434/api/chat"
    assert calls[0][1] == {}
    assert detector.detect_for_camera(SECOND, STILL)[0].label == "person"
    assert detector.detect_for_camera(SECOND, FRAME)[0].label == "person"
    restarted = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.7,
                                  max_requests_per_hour=2, transport=transport)
    assert restarted.detect_for_camera(FIRST, FRAME) == ()
    assert len(calls) == 4


def test_motion_gate_detects_person_sized_change(tmp_path):
    requests = []

    def scene(with_figure):
        image = Image.new("RGB", (640, 360), (35, 35, 35))
        if with_figure:
            ImageDraw.Draw(image).rectangle((300, 100, 330, 200),
                                            fill=(210, 210, 210))
        data = BytesIO()
        image.save(data, format="JPEG", quality=80)
        return data.getvalue()

    def transport(*_args):
        requests.append(1)
        return _response('{"detections":[]}')

    detector = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.8,
                                 max_requests_per_hour=4, transport=transport)
    assert detector.detect_for_camera(FIRST, scene(False)) == ()
    assert detector.detect_for_camera(FIRST, scene(False)) == ()
    for _ in range(2):
        assert detector.detect_for_camera(FIRST, scene(False)) == ()
    assert detector.detect_for_camera(FIRST, scene(True)) == ()
    assert detector.detect_for_camera(FIRST, scene(True)) == ()
    assert len(requests) == 3


def test_stationary_person_is_confirmed_by_startup_probe(tmp_path):
    requests = []

    def transport(*_args):
        requests.append(1)
        return _response(json.dumps({"detections": [
            {"kind": "person", "label": "person", "score": 0.92,
             "box": [0.1, 0.2, 0.4, 0.8]},
        ]}))

    detector = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.8,
                                 max_requests_per_hour=2, transport=transport)
    tracker = TemporalTracker(max_gap_seconds=20)
    first = detector.detect_for_camera(FIRST, STILL)
    second = detector.detect_for_camera(FIRST, STILL)
    assert tracker.update(first, now=1) == ()
    assert [change.edge for change in tracker.update(second, now=2)] == ["enter"]
    assert detector.detect_for_camera(FIRST, STILL) == ()
    assert len(requests) == 2
    assert detector.budget.remaining(FIRST) == 0


def test_response_counts_distinguish_empty_from_score_rejection(tmp_path):
    replies = iter((
        '{"detections":[{"kind":"person","label":"person","score":0.79,"box":[0.1,0.2,0.4,0.8]}]}',
        '{"detections":[{"kind":"person","label":"person","score":0.92,"box":[0.1,0.2,0.4,0.8]}]}',
        '{"detections":[]}',
    ))
    detector = ApiObjectDetector(
        _ollama_config(), tmp_path, threshold=0.8, max_requests_per_hour=3,
        transport=lambda *_args: _response(next(replies)))
    assert detector.detect_for_camera(FIRST, STILL) == ()
    assert detector.detect_for_camera(FIRST, STILL) == ()
    assert len(detector.detect_for_camera(FIRST, FRAME)) == 1
    assert detector.detect_for_camera(FIRST, FRAME) == ()
    assert detector.diagnostic_counts(FIRST) == {
        "responses": 3, "empty_responses": 1,
        "below_threshold": 1, "accepted_objects": 1,
    }
    assert detector.diagnostic_counts(SECOND) == {
        "responses": 0, "empty_responses": 0,
        "below_threshold": 0, "accepted_objects": 0,
    }


def test_recovered_hourly_budget_waits_for_fresh_motion(tmp_path, monkeypatch):
    now_ns = [10 * _HOUR_NS]
    now_mono = [100.0]
    monkeypatch.setattr("aikey.aiport_api_detection.time.monotonic",
                        lambda: now_mono[0])
    requests = []
    detector = ApiObjectDetector(
        _ollama_config(), tmp_path, threshold=0.8,
        max_requests_per_hour=2,
        transport=lambda *_args: (requests.append(1) or _response('{"detections":[]}')))
    detector.budget.clock_ns = lambda: now_ns[0]
    assert detector.budget.claim(FIRST)
    assert detector.budget.claim(FIRST)
    assert detector.detect_for_camera(FIRST, STILL) == ()
    assert detector.detect_for_camera(FIRST, STILL) == ()
    assert requests == []
    now_ns[0] += _HOUR_NS
    now_mono[0] += 3600
    for _ in range(3):
        assert detector.detect_for_camera(FIRST, STILL) == ()
    assert requests == []
    assert detector.budget.remaining(FIRST) == 2
    assert detector.detect_for_camera(FIRST, FRAME) == ()
    assert requests == [1]
    assert detector.budget.remaining(FIRST) == 1


def test_recovered_budget_accepts_continuous_motion_after_denied_probe(tmp_path,
                                                                        monkeypatch):
    now_ns = [10 * _HOUR_NS]
    now_mono = [100.0]
    monkeypatch.setattr("aikey.aiport_api_detection.time.monotonic",
                        lambda: now_mono[0])
    requests = []
    detector = ApiObjectDetector(
        _ollama_config(), tmp_path, threshold=0.8,
        max_requests_per_hour=2,
        transport=lambda *_args: (requests.append(1) or _response('{"detections":[]}')))
    detector.budget.clock_ns = lambda: now_ns[0]
    detector.detect_for_camera(FIRST, STILL)
    detector.detect_for_camera(FIRST, FRAME)
    detector.detect_for_camera(FIRST, STILL)  # Confirmation denied by budget.
    assert len(requests) == 2
    now_ns[0] += _HOUR_NS
    now_mono[0] += 3600
    detector.detect_for_camera(FIRST, FRAME)
    assert len(requests) == 3


def test_failed_first_startup_probe_does_not_spend_confirmation_request(tmp_path):
    calls = []

    def transport(*_args):
        calls.append(1)
        if len(calls) == 1:
            raise ApiDetectionError("api_detection_request_failed")
        return _response('{"detections":[]}')

    detector = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.8,
                                 max_requests_per_hour=3, transport=transport)
    with pytest.raises(ApiDetectionError, match="api_detection_request_failed"):
        detector.detect_for_camera(FIRST, STILL)
    detector.detect_for_camera(FIRST, STILL)
    assert len(calls) == 1
    detector.detect_for_camera(FIRST, FRAME)
    assert len(calls) == 2


def test_restart_with_recent_requests_waits_for_motion(tmp_path):
    calls = []

    def transport(*_args):
        calls.append(1)
        return _response('{"detections":[]}')

    first = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.8,
                              max_requests_per_hour=3, transport=transport)
    first.detect_for_camera(FIRST, STILL)
    assert first.budget.remaining(FIRST) == 2
    restarted = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.8,
                                  max_requests_per_hour=3, transport=transport)
    restarted.detect_for_camera(FIRST, STILL)
    assert len(calls) == 1
    assert restarted.budget.remaining(FIRST) == 2
    restarted.detect_for_camera(FIRST, FRAME)
    assert len(calls) == 2


def test_package_observation_requires_exact_class_label_and_bounded_box():
    result = parse_detections(json.dumps({"detections": [{
        "kind": "package", "label": "package", "score": 0.91,
        "box": [0.2, 0.3, 0.5, 0.7],
    }]}), threshold=0.8)
    assert [(item.kind, item.label) for item in result] == [("package", "package")]
    with pytest.raises(ApiDetectionError, match="invalid_api_detection_response"):
        parse_detections(json.dumps({"detections": [{
            "kind": "package", "label": "person", "score": 0.91,
            "box": [0.2, 0.3, 0.5, 0.7],
        }]}), threshold=0.8)


def test_continuous_motion_is_one_burst_until_three_quiet_frames(tmp_path):
    calls = []

    def transport(*_args):
        calls.append(1)
        return _response('{"detections":[]}')

    detector = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.8,
                                 max_requests_per_hour=4, transport=transport)
    detector.detect_for_camera(FIRST, STILL)
    detector.detect_for_camera(FIRST, FRAME)
    detector.detect_for_camera(FIRST, STILL)
    for image in (FRAME, STILL) * 5:
        detector.detect_for_camera(FIRST, image)
    assert len(calls) == 3
    for _ in range(3):
        detector.detect_for_camera(FIRST, STILL)
    detector.detect_for_camera(FIRST, FRAME)
    assert len(calls) == 4


def test_http_error_exposes_only_status_class(monkeypatch):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise HTTPError("https://api.openai.com/v1/responses", 429,
                            "private provider response", {},
                            BytesIO(b"private provider response"))

    monkeypatch.setattr("aikey.aiport_api_detection.build_opener",
                        lambda *_args: Opener())
    with pytest.raises(ApiDetectionError, match="api_detection_http_429") as failure:
        _post("https://api.openai.com/v1/responses", {}, {})
    assert "private" not in str(failure.value)


def test_dns_failure_exposes_safe_code_without_provider_details(monkeypatch):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise URLError(socket.gaierror("private resolver detail"))

    monkeypatch.setattr("aikey.aiport_api_detection.build_opener",
                        lambda *_args: Opener())
    with pytest.raises(ApiDetectionError, match="api_detection_dns_unavailable") as failure:
        _post("https://api.openai.com/v1/responses", {}, {})
    assert "private" not in str(failure.value)


def test_dns_outage_preserves_budget_and_recovers_without_restart(tmp_path, monkeypatch):
    key = tmp_path / "openai-key"
    key.write_text("synthetic-test-key\n")
    key.chmod(0o600)
    config = {"provider": "openai", "model": "gpt-6-luna",
              "base_url": "https://api.openai.com/v1", "allow_remote": True,
              "api_key_file": str(key), "max_output_tokens": 256}
    detector = ApiObjectDetector(config, tmp_path, threshold=0.8,
                                 max_requests_per_hour=2)
    calls = []
    detector.transport = lambda *_args: (calls.append(1) or {
        "status": "completed", "output": [{
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": '{"detections":[]}'}],
        }]})
    clock = [100.0]
    monkeypatch.setattr("aikey.aiport_api_detection.time.monotonic",
                        lambda: clock[0])
    resolutions = []

    def unavailable(*_args, **_kwargs):
        resolutions.append(1)
        raise socket.gaierror("private resolver detail")

    monkeypatch.setattr("aikey.aiport_api_detection.socket.getaddrinfo", unavailable)
    with pytest.raises(ApiDetectionError, match="api_detection_dns_unavailable"):
        detector.detect_for_camera(FIRST, STILL)
    with pytest.raises(ApiDetectionError, match="api_detection_dns_unavailable"):
        detector.detect_for_camera(FIRST, FRAME)
    assert len(resolutions) == 1
    assert calls == []
    assert detector.budget.remaining(FIRST) == 2

    clock[0] += 61
    monkeypatch.setattr("aikey.aiport_api_detection.socket.getaddrinfo",
                        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM)])
    for _ in range(4):
        detector.detect_for_camera(FIRST, STILL)
    assert detector.detect_for_camera(FIRST, FRAME) == ()
    assert calls == [1, 1]
    assert detector.budget.remaining(FIRST) == 0


@pytest.mark.parametrize("text", [
    "not json",
    '{"detections":[{"kind":"person","label":"person","score":1,"box":[0,0,2,1]}]}',
    '{"detections":[{"kind":"person","label":"car","score":1,"box":[0,0,1,1]}]}',
    '{"detections":[{"kind":"person","label":"person","score":true,"box":[0,0,1,1]}]}',
    '{"detections":[],"private":"extra"}',
])
def test_malformed_or_untrusted_model_output_fails_closed(text):
    with pytest.raises(ApiDetectionError, match="invalid_api_detection_response"):
        parse_detections(text, threshold=0.7)


def test_key_and_endpoint_boundaries_are_checked_before_requests(tmp_path):
    with pytest.raises(ApiDetectionError, match="inline_api_key_forbidden"):
        ApiObjectDetector({**_ollama_config(), "api_key": "do-not-log"}, tmp_path,
                          threshold=0.7, max_requests_per_hour=2)
    with pytest.raises(ApiDetectionError, match="endpoint_not_approved"):
        ApiObjectDetector({**_ollama_config(), "base_url": "https://example.net",
                           "allow_remote": True}, tmp_path,
                          threshold=0.7, max_requests_per_hour=2)


def test_bad_response_spends_one_request_but_does_not_publish(tmp_path):
    detector = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.7,
                                 max_requests_per_hour=2,
                                 transport=lambda *_: _response("private malformed text"))
    with pytest.raises(ApiDetectionError, match="invalid_api_detection_response") as failure:
        detector.detect_for_camera(FIRST, STILL)
    assert "private malformed text" not in str(failure.value)
    assert detector.budget.remaining(FIRST) == 1


def test_openai_luna_uses_private_key_and_no_response_storage(tmp_path):
    key = tmp_path / "openai-key"
    key.write_text("synthetic-test-key\n")
    key.chmod(0o600)
    requests = []

    def transport(url, headers, payload):
        requests.append((url, headers, payload))
        return {"status": "completed", "output": [{
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": json.dumps({"detections": []})}],
        }]}

    detector = ApiObjectDetector(
        {"provider": "openai", "model": "gpt-6-luna",
         "base_url": "https://api.openai.com/v1", "allow_remote": True,
         "api_key_file": str(key), "max_output_tokens": 256},
        tmp_path, threshold=0.8, max_requests_per_hour=2, transport=transport)
    assert detector.detect_for_camera(FIRST, STILL) == ()
    assert detector.detect_for_camera(FIRST, FRAME) == ()
    assert len(requests) == 2
    url, headers, payload = requests[0]
    assert url == "https://api.openai.com/v1/responses"
    assert headers == {"Authorization": "Bearer synthetic-test-key"}
    assert payload["model"] == "gpt-6-luna"
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["store"] is False and payload["stream"] is False
    assert "synthetic-test-key" not in json.dumps(payload)


def test_claude_adapter_uses_private_key_and_official_endpoint(tmp_path):
    key = tmp_path / "anthropic-key"
    key.write_text("synthetic-claude-key\n")
    key.chmod(0o600)
    calls = []

    def transport(url, headers, payload):
        calls.append((url, headers, payload))
        return {"type": "message", "role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": '{"detections":[]}'}]}

    detector = ApiObjectDetector(
        {"provider": "anthropic", "model": "claude-opus-5-5",
         "base_url": "https://api.anthropic.com/v1", "allow_remote": True,
         "api_key_file": str(key), "max_output_tokens": 256},
        tmp_path, threshold=0.8, max_requests_per_hour=2, transport=transport)
    assert detector.detect_for_camera(FIRST, STILL) == ()
    assert detector.detect_for_camera(FIRST, FRAME) == ()
    assert len(calls) == 2
    url, headers, payload = calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers == {"x-api-key": "synthetic-claude-key",
                       "anthropic-version": "2023-06-01"}
    assert payload["model"] == "claude-opus-5-5"
    assert "synthetic-claude-key" not in json.dumps(payload)
