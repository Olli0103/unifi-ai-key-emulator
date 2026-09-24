"""The API detector uses synthetic responses and never sends private footage."""

import json
from io import BytesIO

import pytest
from PIL import Image

from aikey.aiport_api_detection import (
    ApiDetectionError, ApiObjectDetector, parse_detections,
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
