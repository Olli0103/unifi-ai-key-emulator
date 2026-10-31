"""Versioned detection-quality metrics from labelled prediction records (#7).

A case file lists, per case, what should have been detected (``expected``)
and what a model/provider/profile answered (``predicted``, ``confidence``),
with the camera class and capture conditions. No media is read here: the
owner produces predictions locally from consented or synthetic material, and
this runner only scores them, deterministically.

Rules the report enforces:

* negative cases (``expected: "none"``) are required, so a corpus of
  successful demos cannot produce a score;
* calibration (expected calibration error, Brier score) is always reported;
* v2 reports bind producer-declared exact input hashes and labelled membership;
  comparison refuses unbound legacy reports, malformed metrics and differing
  corpora. Supplied hashes/labels are not independently verified media or
  native/hardware-quality acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

CASES_SCHEMA = "aikey-benchmark-cases/1"
REPORT_SCHEMA = "aikey-benchmark-report/1"
BOUND_CASES_SCHEMA = "aikey-benchmark-cases/2"
BOUND_REPORT_SCHEMA = "aikey-benchmark-report/2"
INPUT_BINDING = "producer_declared_exact_input_sha256"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
LABELS = ("person", "vehicle", "animal", "package", "licensePlate", "face")
NONE = "none"
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
_TAG = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_RUN_KEYS = {"model", "provider", "profile", "code_revision"}
_CASE_KEYS = {"id", "camera_class", "conditions", "expected", "predicted", "confidence"}
_BINS = 10
_MAX_CASES = 100_000


class BenchmarkError(ValueError):
    """The case file or comparison cannot be scored honestly."""


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise BenchmarkError(f"Invalid {field}")
    return value


def validate(document: object) -> dict:
    """Strictly check a case file; returns a normalized copy."""
    if not isinstance(document, dict) or set(document) != {"schema", "run", "cases"}:
        raise BenchmarkError("A case file has exactly schema, run and cases")
    schema = document["schema"]
    if not isinstance(schema, str) or schema not in {CASES_SCHEMA, BOUND_CASES_SCHEMA}:
        raise BenchmarkError("Unsupported case schema")
    input_bound = schema == BOUND_CASES_SCHEMA
    run = document["run"]
    if not isinstance(run, dict) or set(run) != _RUN_KEYS:
        raise BenchmarkError("run needs exactly model, provider, profile and code_revision")
    run = {key: _text(run[key], f"run.{key}") for key in sorted(_RUN_KEYS)}
    cases = document["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= _MAX_CASES:
        raise BenchmarkError("cases must be a nonempty list")
    seen, normalized = set(), []
    for case in cases:
        if not isinstance(case, dict) or set(case) != _CASE_KEYS | (
            {"input_sha256"} if input_bound else set()
        ):
            raise BenchmarkError(
                "Each case has exactly id, camera_class, conditions, expected, "
                "predicted and confidence; v2 also requires input_sha256"
            )
        if input_bound and (
            not isinstance(case["input_sha256"], str) or not _SHA.fullmatch(case["input_sha256"])
        ):
            raise BenchmarkError("Each v2 case requires an exact lowercase input SHA-256")
        case_id = _text(case["id"], "case id")
        if case_id in seen:
            raise BenchmarkError("Case ids must be unique")
        seen.add(case_id)
        conditions = case["conditions"]
        if (
            not isinstance(conditions, list)
            or len(conditions) > 16
            or any(not isinstance(t, str) or not _TAG.fullmatch(t) for t in conditions)
            or len(set(conditions)) != len(conditions)
        ):
            raise BenchmarkError("conditions must be unique lowercase tags")
        for field in ("expected", "predicted"):
            if case[field] not in (*LABELS, NONE):
                raise BenchmarkError(f"Unknown {field} label")
        confidence = case["confidence"]
        if (
            type(confidence) not in (int, float)
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise BenchmarkError("confidence must be a finite number from 0 to 1")
        if case["predicted"] == NONE and confidence != 0:
            raise BenchmarkError("A 'none' prediction carries confidence 0")
        normalized.append(
            {
                "id": case_id,
                "camera_class": _text(case["camera_class"], "camera_class"),
                "conditions": sorted(conditions),
                "expected": case["expected"],
                "predicted": case["predicted"],
                "confidence": float(confidence),
                **({"input_sha256": case["input_sha256"]} if input_bound else {}),
            }
        )
    if not any(case["expected"] == NONE for case in normalized):
        raise BenchmarkError("Negative cases (expected 'none') are required")
    if not any(case["expected"] != NONE for case in normalized):
        raise BenchmarkError("Positive cases are required")
    normalized.sort(key=lambda case: case["id"])
    return {"schema": schema, "run": run, "cases": normalized}


def _ratio(numerator: int, denominator: int):
    return None if denominator == 0 else round(numerator / denominator, 6)


def _counts(cases: list[dict]) -> dict:
    labels = {}
    for label in LABELS:
        tp = sum(c["expected"] == label and c["predicted"] == label for c in cases)
        fp = sum(c["predicted"] == label and c["expected"] != label for c in cases)
        fn = sum(c["expected"] == label and c["predicted"] != label for c in cases)
        if tp or fp or fn:
            labels[label] = {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "precision": _ratio(tp, tp + fp),
                "recall": _ratio(tp, tp + fn),
            }
    negatives = [c for c in cases if c["expected"] == NONE]
    positives = [c for c in cases if c["expected"] != NONE]
    return {
        "cases": len(cases),
        "labels": labels,
        "false_alarms": sum(c["predicted"] != NONE for c in negatives),
        "negatives": len(negatives),
        "false_alarm_rate": _ratio(sum(c["predicted"] != NONE for c in negatives), len(negatives)),
        "missed": sum(c["predicted"] == NONE for c in positives),
        "misclassified": sum(c["predicted"] not in (NONE, c["expected"]) for c in positives),
        "positives": len(positives),
    }


def _calibration(cases: list[dict]) -> dict:
    """ECE and Brier over answered cases: is the confidence right as often as it says?"""
    answered = [c for c in cases if c["predicted"] != NONE]
    if not answered:
        return {"answered": 0, "ece": None, "brier": None, "bins": []}
    bins, ece, brier = [], 0.0, 0.0
    for c in answered:
        brier += (c["confidence"] - (c["predicted"] == c["expected"])) ** 2
    for index in range(_BINS):
        low, high = index / _BINS, (index + 1) / _BINS
        members = [
            c
            for c in answered
            if low <= c["confidence"] < high or (index == _BINS - 1 and c["confidence"] == 1)
        ]
        if not members:
            continue
        accuracy = sum(c["predicted"] == c["expected"] for c in members) / len(members)
        confidence = sum(c["confidence"] for c in members) / len(members)
        ece += len(members) / len(answered) * abs(accuracy - confidence)
        bins.append(
            {
                "range": [low, high],
                "count": len(members),
                "accuracy": round(accuracy, 6),
                "mean_confidence": round(confidence, 6),
            }
        )
    return {
        "answered": len(answered),
        "ece": round(ece, 6),
        "brier": round(brier / len(answered), 6),
        "bins": bins,
    }


def _digest(cases: list[dict], *, exact_inputs=False) -> str:
    """Legacy label identity or separately namespaced, input-bound v2 identity."""
    corpus = [[c["id"], c["camera_class"], c["conditions"], c["expected"]] for c in cases]
    if exact_inputs:
        corpus = [
            INPUT_BINDING,
            [row + [case["input_sha256"]] for row, case in zip(corpus, cases, strict=True)],
        ]
    return hashlib.sha256(json.dumps(corpus, separators=(",", ":")).encode()).hexdigest()


def score(document: object) -> dict:
    checked = validate(document)
    cases = checked["cases"]
    by_class = {
        name: _counts([c for c in cases if c["camera_class"] == name])
        for name in sorted({c["camera_class"] for c in cases})
    }
    by_condition = {
        tag: _counts([c for c in cases if tag in c["conditions"]])
        for tag in sorted({t for c in cases for t in c["conditions"]})
    }
    input_bound = checked["schema"] == BOUND_CASES_SCHEMA
    return {
        "schema": BOUND_REPORT_SCHEMA if input_bound else REPORT_SCHEMA,
        "run": checked["run"],
        **({"corpus_binding": INPUT_BINDING} if input_bound else {}),
        "corpus_sha256": _digest(cases, exact_inputs=input_bound),
        "overall": _counts(cases),
        "calibration": _calibration(cases),
        "by_camera_class": by_class,
        "by_condition": by_condition,
        "claims": (
            "Producer-declared input hashes and labels, not independently verified media, "
            "native acceptance or hardware-quality evidence."
            if input_bound
            else "Legacy label-only scores: no exact input binding. Not eligible for comparison; "
            "rescore v2 cases with producer-declared exact input hashes."
        ),
    }


def _comparison_refused():
    raise BenchmarkError("Malformed input-bound benchmark report")


def _bounded_count(value, maximum=_MAX_CASES):
    if type(value) is not int or not 0 <= value <= maximum:
        _comparison_refused()
    return value


def _fraction(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        _comparison_refused()
    return value


def _validate_counts(value):
    keys = {
        "cases",
        "labels",
        "false_alarms",
        "negatives",
        "false_alarm_rate",
        "missed",
        "misclassified",
        "positives",
    }
    if not isinstance(value, dict) or set(value) != keys:
        _comparison_refused()
    n = _bounded_count(value["cases"])
    if n == 0:
        _comparison_refused()
    for key in keys - {"labels", "false_alarm_rate"}:
        _bounded_count(value[key], n)
    if (
        value["positives"] + value["negatives"] != n
        or value["false_alarms"] > value["negatives"]
        or value["missed"] + value["misclassified"] > value["positives"]
        or value["false_alarm_rate"] != _ratio(value["false_alarms"], value["negatives"])
    ):
        _comparison_refused()
    if value["false_alarm_rate"] is not None:
        _fraction(value["false_alarm_rate"])
    labels = value["labels"]
    if not isinstance(labels, dict) or not set(labels) <= set(LABELS):
        _comparison_refused()
    totals = {"tp": 0, "fp": 0, "fn": 0}
    for row in labels.values():
        if not isinstance(row, dict) or set(row) != {"tp", "fp", "fn", "precision", "recall"}:
            _comparison_refused()
        for key in totals:
            totals[key] += _bounded_count(row[key], n)
        if not 0 < row["tp"] + row["fp"] + row["fn"] <= n:
            _comparison_refused()
        for key, denominator in (
            ("precision", row["tp"] + row["fp"]),
            ("recall", row["tp"] + row["fn"]),
        ):
            if row[key] != _ratio(row["tp"], denominator):
                _comparison_refused()
            if row[key] is not None:
                _fraction(row[key])
    if (
        totals["tp"] + value["missed"] + value["misclassified"] != value["positives"]
        or totals["fn"] != value["missed"] + value["misclassified"]
        or totals["fp"] != value["false_alarms"] + value["misclassified"]
    ):
        _comparison_refused()


def _validate_comparison_report(value):
    if isinstance(value, dict) and value.get("schema") == REPORT_SCHEMA:
        raise BenchmarkError(
            "Legacy label-only reports cannot be compared; rescore v2 cases with exact input hashes"
        )
    keys = {
        "schema",
        "run",
        "corpus_sha256",
        "corpus_binding",
        "overall",
        "calibration",
        "by_camera_class",
        "by_condition",
        "claims",
    }
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or value.get("schema") != BOUND_REPORT_SCHEMA
        or value.get("corpus_binding") != INPUT_BINDING
        or not isinstance(value["corpus_sha256"], str)
        or not _SHA.fullmatch(value["corpus_sha256"])
        or not isinstance(value["run"], dict)
        or set(value["run"]) != _RUN_KEYS
        or not isinstance(value["claims"], str)
        or len(value["claims"]) > 512
    ):
        raise BenchmarkError("Both inputs must be valid input-bound benchmark reports")
    for key, item in value["run"].items():
        _text(item, "run." + key)
    _validate_counts(value["overall"])
    overall = value["overall"]
    if not overall["positives"] or not overall["negatives"]:
        _comparison_refused()
    for group in ("by_camera_class", "by_condition"):
        rows = value[group]
        if (
            not isinstance(rows, dict)
            or len(rows) > _MAX_CASES
            or group == "by_camera_class"
            and not rows
        ):
            _comparison_refused()
        for name, row in rows.items():
            _text(name, "metric slice")
            if group == "by_condition" and not _TAG.fullmatch(name):
                _comparison_refused()
            _validate_counts(row)
            if any(
                row[key] > overall[key]
                for key in (
                    "cases",
                    "positives",
                    "negatives",
                    "false_alarms",
                    "missed",
                    "misclassified",
                )
            ):
                _comparison_refused()
            for label, counts in row["labels"].items():
                if any(
                    counts[key] > overall["labels"].get(label, {}).get(key, 0)
                    for key in ("tp", "fp", "fn")
                ):
                    _comparison_refused()
    for key in ("cases", "positives", "negatives", "false_alarms", "missed", "misclassified"):
        if sum(v[key] for v in value["by_camera_class"].values()) != overall[key]:
            _comparison_refused()
    for label in LABELS:
        for key in ("tp", "fp", "fn"):
            if sum(
                row["labels"].get(label, {}).get(key, 0)
                for row in value["by_camera_class"].values()
            ) != overall["labels"].get(label, {}).get(key, 0):
                _comparison_refused()
    calibration = value["calibration"]
    if not isinstance(calibration, dict) or set(calibration) != {
        "answered",
        "ece",
        "brier",
        "bins",
    }:
        _comparison_refused()
    answered = _bounded_count(calibration["answered"], overall["cases"])
    if (
        answered
        != overall["cases"] - overall["negatives"] + overall["false_alarms"] - overall["missed"]
    ):
        _comparison_refused()
    bins = calibration["bins"]
    if not isinstance(bins, list) or len(bins) > _BINS:
        _comparison_refused()
    if not answered:
        if calibration["ece"] is not None or calibration["brier"] is not None or bins:
            _comparison_refused()
        return
    _fraction(calibration["ece"])
    _fraction(calibration["brier"])
    counted, last, reconstructed = 0, -1, 0.0
    for row in bins:
        if not isinstance(row, dict) or set(row) != {
            "range",
            "count",
            "accuracy",
            "mean_confidence",
        }:
            _comparison_refused()
        bounds = row["range"]
        if not isinstance(bounds, list) or len(bounds) != 2:
            _comparison_refused()
        low, high = (_fraction(v) for v in bounds)
        if [low, high] not in [[i / _BINS, (i + 1) / _BINS] for i in range(_BINS)] or low <= last:
            _comparison_refused()
        last = low
        count = _bounded_count(row["count"], answered)
        accuracy, confidence = _fraction(row["accuracy"]), _fraction(row["mean_confidence"])
        if not count or not low <= confidence <= high:
            _comparison_refused()
        counted += count
        reconstructed += count / answered * abs(accuracy - confidence)
    if counted != answered or abs(reconstructed - calibration["ece"]) > 0.000002:
        _comparison_refused()


def _corpus_membership(report):
    def membership(counts):
        return [
            counts["cases"],
            counts["positives"],
            counts["negatives"],
            [
                sum(counts["labels"].get(label, {}).get(k, 0) for k in ("tp", "fn"))
                for label in LABELS
            ],
        ]

    return {
        "overall": membership(report["overall"]),
        **{
            group: {key: membership(row) for key, row in report[group].items()}
            for group in ("by_camera_class", "by_condition")
        },
    }


def compare(report: dict, reference: dict) -> dict:
    """Like-for-like differences, or refusal when the corpora differ."""
    for value in (report, reference):
        _validate_comparison_report(value)
    if report["corpus_sha256"] != reference["corpus_sha256"]:
        raise BenchmarkError("Reports scored different corpora; no comparison is possible")
    if _corpus_membership(report) != _corpus_membership(reference):
        raise BenchmarkError("Reports disagree about their claimed corpus membership")

    def delta(a, b):
        return None if a is None or b is None else round(a - b, 6)

    ours, theirs = report["overall"], reference["overall"]
    labels = sorted(set(ours["labels"]) | set(theirs["labels"]))
    return {
        "schema": "aikey-benchmark-comparison/2",
        "corpus_binding": INPUT_BINDING,
        "claims": "Differences on producer-declared matching input hashes/labels; not media or native-quality verification.",
        "corpus_sha256": report["corpus_sha256"],
        "run": report["run"],
        "reference_run": reference["run"],
        "false_alarm_rate": delta(ours["false_alarm_rate"], theirs["false_alarm_rate"]),
        "missed": ours["missed"] - theirs["missed"],
        "ece": delta(report["calibration"]["ece"], reference["calibration"]["ece"]),
        "labels": {
            label: {
                metric: delta(
                    ours["labels"].get(label, {}).get(metric),
                    theirs["labels"].get(label, {}).get(metric),
                )
                for metric in ("precision", "recall")
            }
            for label in labels
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aikey-benchmark", description="Score labelled predictions; never reads media."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("score")
    run.add_argument("cases", type=Path)
    versus = sub.add_parser("compare")
    versus.add_argument("report", type=Path)
    versus.add_argument("reference", type=Path)
    multi = sub.add_parser(
        "multi-score", help="Score plates, speech, retrieval and operation records"
    )
    multi.add_argument("cases", type=Path)
    multi_compare = sub.add_parser("multi-compare")
    multi_compare.add_argument("report", type=Path)
    multi_compare.add_argument("reference", type=Path)
    args = parser.parse_args(argv)
    from .protocol import _json_object

    def read(path):
        return _json_object(path.read_bytes())

    try:
        if args.command == "score":
            output = score(read(args.cases))
        elif args.command == "compare":
            output = compare(read(args.report), read(args.reference))
        else:
            from . import benchmark_multi

            if args.command == "multi-score":
                output = benchmark_multi.score(read(args.cases))
            else:
                output = benchmark_multi.compare(read(args.report), read(args.reference))
    except (BenchmarkError, OSError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
