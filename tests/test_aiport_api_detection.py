"""The API detector uses synthetic responses and never sends private footage."""

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest
from PIL import Image, ImageDraw

from aikey.aiport_api_detection import (
    ApiDetectionError, ApiObjectDetector, _post, parse_detections,
)


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
    assert detector.detect_for_camera(FIRST, STILL) == ()
    assert [result.kind for result in detector.detect_for_camera(FIRST, FRAME)] == ["person"]
    assert len(detector.detect_for_camera(FIRST, FRAME)) == 1
    assert detector.detect_for_camera(FIRST, FRAME) == ()
    assert len(calls) == 2
    assert calls[0][0] == "http://127.0.0.1:11434/api/chat"
    assert calls[0][1] == {}
    assert detector.detect_for_camera(SECOND, STILL) == ()
    assert detector.detect_for_camera(SECOND, FRAME)[0].label == "person"
    restarted = ApiObjectDetector(_ollama_config(), tmp_path, threshold=0.7,
                                  max_requests_per_hour=2, transport=transport)
    assert restarted.detect_for_camera(FIRST, FRAME) == ()
    assert len(calls) == 3


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
                                 max_requests_per_hour=2, transport=transport)
    assert detector.detect_for_camera(FIRST, scene(False)) == ()
    assert detector.detect_for_camera(FIRST, scene(True)) == ()
    assert len(requests) == 1


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
    assert len(calls) == 2
    for _ in range(3):
        detector.detect_for_camera(FIRST, STILL)
    detector.detect_for_camera(FIRST, FRAME)
    assert len(calls) == 3


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
    assert detector.detect_for_camera(FIRST, STILL) == ()
    with pytest.raises(ApiDetectionError, match="invalid_api_detection_response") as failure:
        detector.detect_for_camera(FIRST, FRAME)
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
    assert len(requests) == 1
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
    assert len(calls) == 1
    url, headers, payload = calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers == {"x-api-key": "synthetic-claude-key",
                       "anthropic-version": "2023-06-01"}
    assert payload["model"] == "claude-opus-5-5"
    assert "synthetic-claude-key" not in json.dumps(payload)
