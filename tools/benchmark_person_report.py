"""All-slot binary person metrics, including errors; not field-quality evidence.

Inputs are locally reviewed gold and immutable one-attempt terminal records.
No model calls, raw media reading, retries, tuning or deployment actions.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path


def summarize(gold, records):
    cases = gold["cases"]
    assert cases and len({c["case_id"] for c in cases}) == len(cases)
    assert {c["expected"] for c in cases} == {"person", "none"}
    expected_ids = {c["case_id"] for c in cases}
    assert set(records) <= expected_ids
    confusion = {k: {v: 0 for v in ("person", "none", "__error__", "__unrun__")}
                 for k in ("person", "none")}
    valid, errors, unrun, latencies, samples = 0, 0, 0, [], []
    total_loss = 0.0
    for case in cases:
        record = records.get(case["case_id"])
        if record is None:
            unrun += 1
            total_loss += 1
            confusion[case["expected"]]["__unrun__"] += 1
            continue
        assert record["input_sha256"] == case["input_sha256"]
        assert record["client_attempts"] == 1 and record["automatic_retries"] == 0
        ms = record["api_receipt_and_parse_ms"]
        assert type(ms) is int and ms >= 0
        latencies.append(ms)
        if record["status"] == "error":
            errors += 1
            total_loss += 1
            confusion[case["expected"]]["__error__"] += 1
            continue
        assert record["status"] == "valid"
        probability = record["maximum_person_score"]
        assert type(probability) in (int, float) and math.isfinite(probability)
        assert 0 <= probability <= 1
        predicted = "person" if probability >= 0.5 else "none"
        assert record["predicted"] == predicted
        valid += 1
        confusion[case["expected"]][predicted] += 1
        truth = int(case["expected"] == "person")
        samples.append((probability, truth))
        total_loss += (probability - truth) ** 2
    count = len(cases)
    tp = confusion["person"]["person"]
    fp = confusion["none"]["person"]
    tn = confusion["none"]["none"]
    positives = sum(confusion["person"].values())
    negatives = sum(confusion["none"].values())

    def ratio(a, b):
        return a / b if b else None

    def nearest(q):
        return sorted(latencies)[math.ceil(q * len(latencies)) - 1] if latencies else None

    bins, ece = [], 0.0
    for index in range(10):
        group = [(p, y) for p, y in samples if min(int(p * 10), 9) == index]
        if not group:
            continue
        mean = sum(p for p, _ in group) / len(group)
        observed = sum(y for _, y in group) / len(group)
        ece += len(group) / len(samples) * abs(mean - observed)
        bins.append({"range": [index / 10, (index + 1) / 10], "count": len(group),
                     "mean_person_score": mean, "observed_person_fraction": observed})
    return {
        "schema": "synthetic-person-report/1", "planned": count, "terminal": valid + errors,
        "valid": valid, "errors": errors, "unrun": unrun, "complete": unrun == 0,
        "confusion": confusion, "all_slot_accuracy": (tp + tn) / count,
        "always_none_accuracy": negatives / count, "valid_coverage": valid / count,
        "person_precision": ratio(tp, tp + fp), "all_slot_person_recall": tp / positives,
        "all_slot_none_recall": tn / negatives, "negative_false_positive_fraction": fp / negatives,
        "error_fraction": errors / count,
        "calibration": {"scope": "person score as binary probability, not a calibrated posterior",
                        "valid_cases": len(samples), "ece_valid_only": ece if samples else None,
                        "brier_valid_only": ratio(sum((p-y)**2 for p, y in samples), len(samples)),
                        "brier_all_slots_invalid_loss_one": total_loss / count, "bins": bins},
        "api_receipt_and_parse_ms": {"terminal_cases": len(latencies),
                                     "p50": nearest(0.5), "p95": nearest(0.95),
                                     "p99": nearest(0.99)},
        "provider_charge_usd": None, "camera_day_exposure": None,
        "event_to_alert_ms": None, "hardware_reference": None,
        "scope": "related synthetic cartoons, not field quality, native event latency or hardware parity",
    }


def targets(baseline):
    """Prospective policy must be frozen before generating the initial baseline."""
    assert baseline["complete"] and baseline["planned"] == 24
    calibration = baseline["calibration"]
    return {
        "scope": "future identical-corpus adapter regression gate only; no production promotion",
        "minimum_valid_coverage": max(23 / 24, baseline["valid_coverage"]),
        "minimum_person_recall": max(7 / 8, baseline["all_slot_person_recall"]),
        "minimum_person_precision": max(0.9, baseline["person_precision"] or 0),
        "maximum_negative_false_positive_fraction": min(1 / 16, baseline["negative_false_positive_fraction"]),
        "maximum_error_fraction": min(1 / 24, baseline["error_fraction"]),
        "maximum_ece_valid_only": min(0.1, calibration["ece_valid_only"] + 0.02)
        if calibration["ece_valid_only"] is not None else None,
        "maximum_brier_all_slots": min(0.1, calibration["brier_all_slots_invalid_loss_one"] + 0.02),
        "maximum_api_receipt_parse_p95_ms": min(30000, math.ceil(
            baseline["api_receipt_and_parse_ms"]["p95"] * 1.1)),
        "require_all_planned_slots_terminal": True,
        "unmeasured_modalities_and_field_metrics": "needs_evidence; cannot pass by null or fixture-only scores",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gold", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    gold = json.loads(args.gold.read_bytes())
    records = {}
    for path in args.results.glob("*-result.json"):
        row = json.loads(path.read_bytes())
        assert row["case_id"] not in records
        records[row["case_id"]] = row
    report = summarize(gold, records)
    manifest = json.loads(args.manifest.read_bytes())
    assert manifest["schema"] == "synthetic-person-execution/1"
    assert manifest["gold_sha256"] == hashlib.sha256(args.gold.read_bytes()).hexdigest()
    assert set(manifest["schedule"]) == {c["case_id"] for c in gold["cases"]}
    assert len(manifest["schedule"]) == len(gold["cases"]) == manifest["case_count"]
    assert all(r["source_commit"] == manifest["source_commit"] for r in records.values())
    report["run"] = {"model": manifest["model"], "provider": "openai-compatible-local-ovms",
                     "profile": "ai-port-primary-fraction-adapter",
                     "code_revision": manifest["source_commit"],
                     "camera_classes": sorted({c["camera_class"] for c in gold["cases"]}),
                     "model_revision": manifest["model_revision"]}
    report["execution_manifest_sha256"] = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    report["gold_sha256"] = hashlib.sha256(args.gold.read_bytes()).hexdigest()
    report["prospective_targets"] = targets(report) if report["complete"] else None
    print(json.dumps(report, indent=2, allow_nan=False))
