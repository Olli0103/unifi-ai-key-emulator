"""The trial refuses ambiguous outputs, records failures and never retries."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def adapter():
    path = Path(__file__).resolve().parents[1] / "tools/benchmark_person_adapter.py"
    spec = importlib.util.spec_from_file_location("person_adapter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def envelope(text, model="qwen3-vl-8b"):
    return {"model": model, "choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


def manifest(adapter):
    return {"schema": "synthetic-person-execution/1", "model": "qwen3-vl-8b",
            "base_url": "http://127.0.0.1:8000/v3", "person_threshold": 0.5,
            "driver_sha256": hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest(),
            "request_timeout_s": 30, "retries": 0, "structured_output": True,
            "output_token_limit": 1024, "temperature": 0, "case_count": 1,
            "source_commit": "fixture-source"}


@pytest.mark.parametrize("text", [
    "not JSON", '{"detections":[],"detections":[]}',
    '{"detections":[{"kind":"person","score":NaN,"box":[0,0,1,1]}]}',
    '{"detections":[{"kind":"person","score":2,"box":[0,0,1,1]}]}',
    '{"detections":[{"kind":"person","score":0.9,"box":[0,0,1,0]}]}',
    '{"detections":[{"kind":"person","score":true,"box":[0,0,1,1]}]}',
])
def test_malformed_case_is_not_rescued(adapter, text):
    with pytest.raises(ValueError):
        adapter.normalize(envelope(text), manifest(adapter))


def test_missing_or_changed_returned_model_refuses(adapter):
    for model in (None, "another-model"):
        with pytest.raises(ValueError, match="model_identity_mismatch"):
            adapter.normalize(envelope('{"detections":[]}', model), manifest(adapter))


def test_boundary_and_negative_person_score_preserved(adapter):
    for score, label in ((0.49, "none"), (0.5, "person")):
        text = json.dumps({"detections": [{"kind": "person", "label": "person",
                                            "score": score, "box": [0.1, 0.1, 0.8, 0.9]}]})
        result = adapter.normalize(envelope(text), manifest(adapter))
        assert result["predicted"] == label
        assert result["maximum_person_score"] == score
    assert adapter.normalize(envelope('{"detections":[]}'), manifest(adapter))["predicted"] == "none"


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("budget", [30, 120])
def test_exact_one_attempt_and_retained_terminal_receipts(adapter, monkeypatch, tmp_path, failure,
                                                         budget):
    root, out = tmp_path / "bundle", tmp_path / "results"
    (root / "images").mkdir(parents=True)
    out.mkdir()
    from PIL import Image
    image = root / "images" / "case.png"
    Image.new("RGB", (8, 8), "red").save(image)
    (root / "inputs.json").write_text(json.dumps({"cases": [{
        "case_id": "case", "file_name": "case.png",
        "input_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
    }]}))
    frozen = manifest(adapter)
    frozen["request_timeout_s"] = budget
    (root / "execution-manifest.json").write_text(json.dumps(frozen))
    (root / "execution-environment.json").write_text('{"synthetic":true}')
    calls = []

    def post(url, headers, payload, timeout):
        calls.append(url)
        assert timeout == budget and payload["model"] == "qwen3-vl-8b"
        assert payload["response_format"]["type"] == "json_schema"
        assert not any(k.lower() in {"cookie", "authorization"} for k in headers)
        if failure:
            raise adapter.detection.ApiDetectionError("api_detection_request_failed")
        raw = json.dumps(envelope('{"detections":[]}')).encode()
        return adapter.detection._reply_object(raw, "invalid_api_detection_response")

    monkeypatch.setattr(adapter.detection, "_post", post)
    result = adapter.run_case(root, out, 0)
    assert len(calls) == 1
    assert result["status"] == ("error" if failure else "valid")
    record = json.loads((out / "case-result.json").read_text())
    assert record["automatic_retries"] == 0 and record["provider_charge_usd"] is None
    assert record["http_body_received"] is not failure
    if failure:
        assert record["transport_outcome_uncertain"]
        assert not (out / "case-raw.json").exists()
    else:
        assert json.loads((out / "case-http-body.bin").read_bytes()) == json.loads(
            (out / "case-raw.json").read_bytes())
    with pytest.raises(FileExistsError):
        adapter.run_case(root, out, 0)
    assert len(calls) == 1
