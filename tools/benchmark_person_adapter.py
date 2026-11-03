"""One explicit local synthetic-image trial; no retries, profile reads or service actions.

Use a separately frozen manifest and unlabelled inputs. The controller owns
shared-model admission, corpus review and process/device preservation checks.
This tool does not attest those external gates or infer deployment quality.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time

from aikey import aiport_api_detection as detection
from aikey.providers import ProviderError, validate_inference_config


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_object_key")
        result[key] = value
    return result


def strict(raw):
    def refuse(_):
        raise ValueError("nonfinite")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=refuse)


def save(path, raw):
    with Path(path).open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def save_json(path, value):
    save(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode())


def provider(manifest):
    assert manifest["base_url"] == "http://127.0.0.1:8000/v3"
    return validate_inference_config({
        "provider": "openai-compatible", "base_url": manifest["base_url"],
        "model": manifest["model"], "max_output_tokens": 1024,
        "temperature": 0,
    }, require_api_key=False)


def normalize(reply, manifest):
    if not isinstance(reply, dict) or reply.get("model") != manifest["model"]:
        raise ValueError("model_identity_mismatch")
    text = provider(manifest).parse_response(reply)
    strict(text)
    rejected = {}
    items = detection.parse_detections(text, threshold=0, rejected=rejected)
    if rejected:
        raise ValueError("invalid_detection_item")
    score = max((item.score for item in items if item.kind == "person"), default=0.0)
    if not math.isfinite(score):
        raise ValueError("nonfinite_score")
    predicted = "person" if score >= manifest["person_threshold"] else "none"
    return {"status": "valid", "predicted": predicted,
            "confidence": score if predicted == "person" else 0.0,
            "maximum_person_score": score, "returned_model": reply["model"],
            "valid_detection_count": len(items)}


def run_case(root, output, index):
    root, output = Path(root), Path(output)
    manifest = strict((root / "execution-manifest.json").read_bytes())
    assert manifest["schema"] == "synthetic-person-execution/1"
    assert manifest["driver_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    timeout = manifest["request_timeout_s"]
    assert type(timeout) is int and timeout in (30, 120) and manifest["retries"] == 0
    assert manifest["structured_output"] is True and manifest["person_threshold"] == 0.5
    assert manifest["output_token_limit"] == 1024 and manifest["temperature"] == 0
    rows = strict((root / "inputs.json").read_bytes())["cases"]
    assert len(rows) == manifest["case_count"] and len({r["case_id"] for r in rows}) == len(rows)
    assert type(index) is int and 0 <= index < len(rows)
    case = rows[index]
    assert Path(case["file_name"]).name == case["file_name"]
    frame = (root / "images" / case["file_name"]).read_bytes()
    assert len(frame) <= 1024 * 1024
    assert hashlib.sha256(frame).hexdigest() == case["input_sha256"]
    url, headers, payload = detection.build_detection_request(provider(manifest), frame,
                                                             structured_output=True)
    assert url == manifest["base_url"] + "/chat/completions"
    assert not any(k.lower() in {"authorization", "cookie"} for k in headers)
    environment = strict((root / "execution-environment.json").read_bytes())
    save_json(output / (case["case_id"] + "-started.json"), {
        "case_id": case["case_id"], "one_attempt": True,
        "driver_sha256": manifest["driver_sha256"],
    })
    result = {"case_id": case["case_id"], "input_sha256": case["input_sha256"],
              "client_attempts": 1, "automatic_retries": 0, "provider_charge_usd": None,
              "environment": environment, "source_commit": manifest["source_commit"],
              "prompt_sha256": hashlib.sha256(detection._PROMPT.encode()).hexdigest(),
              "request_sha256": hashlib.sha256(json.dumps(
                  payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    original = detection._reply_object
    received = False

    def capture(raw, failure):
        nonlocal received
        save(output / (case["case_id"] + "-http-body.bin"), raw)
        received = True
        return original(raw, failure)

    detection._reply_object = capture
    started = time.monotonic()
    reply = None
    try:
        reply = detection._post(url, headers, payload, timeout=timeout)
        save_json(output / (case["case_id"] + "-raw.json"), reply)
        result.update(normalize(reply, manifest))
    except (detection.ApiDetectionError, ProviderError, OSError, ValueError,
            TypeError, KeyError) as exc:
        code = exc.args[0] if isinstance(exc, detection.ApiDetectionError) and exc.args else None
        if not isinstance(code, str) or not code.startswith("api_detection_"):
            code = "normalization_invalid" if reply is not None else "request_failed"
        result.update(status="error", failure_code=code,
                      transport_outcome_uncertain=reply is None and not received
                      and not code.startswith("api_detection_http_"),
                      server_rejected_protocol=code == "api_detection_http_4xx")
    finally:
        detection._reply_object = original
    result["http_body_received"] = received
    result["api_receipt_and_parse_ms"] = round((time.monotonic() - started) * 1000)
    usage = reply.get("usage", {}) if isinstance(reply, dict) else {}
    result["usage"] = usage if isinstance(usage, dict) else {}
    save_json(output / (case["case_id"] + "-result.json"), result)
    return {k: result[k] for k in ("case_id", "status", "api_receipt_and_parse_ms")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("index", type=int)
    args = parser.parse_args()
    print(json.dumps(run_case(args.root, args.output, args.index)))
