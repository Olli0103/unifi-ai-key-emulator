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
* the report carries the run identity and a digest of the scored cases, and
  ``compare`` refuses two reports whose corpus digests differ, so no quality
  claim is made without like-for-like evidence.
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
    if document["schema"] != CASES_SCHEMA:
        raise BenchmarkError(f"Unsupported case schema; expected {CASES_SCHEMA}")
    run = document["run"]
    if not isinstance(run, dict) or set(run) != _RUN_KEYS:
        raise BenchmarkError("run needs exactly model, provider, profile and code_revision")
    run = {key: _text(run[key], f"run.{key}") for key in sorted(_RUN_KEYS)}
    cases = document["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= _MAX_CASES:
        raise BenchmarkError("cases must be a nonempty list")
    seen, normalized = set(), []
    for case in cases:
        if not isinstance(case, dict) or set(case) != _CASE_KEYS:
            raise BenchmarkError("Each case has exactly id, camera_class, conditions, expected, "
                                 "predicted and confidence")
        case_id = _text(case["id"], "case id")
        if case_id in seen:
            raise BenchmarkError("Case ids must be unique")
        seen.add(case_id)
        conditions = case["conditions"]
        if (not isinstance(conditions, list) or len(conditions) > 16
                or any(not isinstance(t, str) or not _TAG.fullmatch(t) for t in conditions)
                or len(set(conditions)) != len(conditions)):
            raise BenchmarkError("conditions must be unique lowercase tags")
        for field in ("expected", "predicted"):
            if case[field] not in (*LABELS, NONE):
                raise BenchmarkError(f"Unknown {field} label")
        confidence = case["confidence"]
        if (type(confidence) not in (int, float) or not math.isfinite(confidence)
                or not 0 <= confidence <= 1):
            raise BenchmarkError("confidence must be a finite number from 0 to 1")
        if case["predicted"] == NONE and confidence != 0:
            raise BenchmarkError("A 'none' prediction carries confidence 0")
        normalized.append({"id": case_id, "camera_class": _text(case["camera_class"], "camera_class"),
                           "conditions": sorted(conditions), "expected": case["expected"],
                           "predicted": case["predicted"], "confidence": float(confidence)})
    if not any(case["expected"] == NONE for case in normalized):
        raise BenchmarkError("Negative cases (expected 'none') are required")
    if not any(case["expected"] != NONE for case in normalized):
        raise BenchmarkError("Positive cases are required")
    normalized.sort(key=lambda case: case["id"])
    return {"schema": CASES_SCHEMA, "run": run, "cases": normalized}


def _ratio(numerator: int, denominator: int):
    return None if denominator == 0 else round(numerator / denominator, 6)


def _counts(cases: list[dict]) -> dict:
    labels = {}
    for label in LABELS:
        tp = sum(c["expected"] == label and c["predicted"] == label for c in cases)
        fp = sum(c["predicted"] == label and c["expected"] != label for c in cases)
        fn = sum(c["expected"] == label and c["predicted"] != label for c in cases)
        if tp or fp or fn:
            labels[label] = {"tp": tp, "fp": fp, "fn": fn, "precision": _ratio(tp, tp + fp),
                             "recall": _ratio(tp, tp + fn)}
    negatives = [c for c in cases if c["expected"] == NONE]
    positives = [c for c in cases if c["expected"] != NONE]
    return {"cases": len(cases), "labels": labels,
            "false_alarms": sum(c["predicted"] != NONE for c in negatives),
            "negatives": len(negatives),
            "false_alarm_rate": _ratio(sum(c["predicted"] != NONE for c in negatives), len(negatives)),
            "missed": sum(c["predicted"] == NONE for c in positives),
            "misclassified": sum(c["predicted"] not in (NONE, c["expected"]) for c in positives),
            "positives": len(positives)}


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
        members = [c for c in answered
                   if low <= c["confidence"] < high or (index == _BINS - 1 and c["confidence"] == 1)]
        if not members:
            continue
        accuracy = sum(c["predicted"] == c["expected"] for c in members) / len(members)
        confidence = sum(c["confidence"] for c in members) / len(members)
        ece += len(members) / len(answered) * abs(accuracy - confidence)
        bins.append({"range": [low, high], "count": len(members),
                     "accuracy": round(accuracy, 6), "mean_confidence": round(confidence, 6)})
    return {"answered": len(answered), "ece": round(ece, 6), "brier": round(brier / len(answered), 6),
            "bins": bins}


def _digest(cases: list[dict]) -> str:
    """Identity of the scored corpus: ids, classes, conditions and expected labels only."""
    corpus = [[c["id"], c["camera_class"], c["conditions"], c["expected"]] for c in cases]
    return hashlib.sha256(json.dumps(corpus, separators=(",", ":")).encode()).hexdigest()


def score(document: object) -> dict:
    checked = validate(document)
    cases = checked["cases"]
    by_class = {name: _counts([c for c in cases if c["camera_class"] == name])
                for name in sorted({c["camera_class"] for c in cases})}
    by_condition = {tag: _counts([c for c in cases if tag in c["conditions"]])
                    for tag in sorted({t for c in cases for t in c["conditions"]})}
    return {"schema": REPORT_SCHEMA, "run": checked["run"], "corpus_sha256": _digest(cases),
            "overall": _counts(cases), "calibration": _calibration(cases),
            "by_camera_class": by_class, "by_condition": by_condition,
            "claims": "Scores describe this corpus and run only; no comparative claim without "
                      "a reference report on the same corpus."}


def compare(report: dict, reference: dict) -> dict:
    """Like-for-like differences, or refusal when the corpora differ."""
    for value in (report, reference):
        if not isinstance(value, dict) or value.get("schema") != REPORT_SCHEMA:
            raise BenchmarkError("Both inputs must be benchmark reports")
    if report["corpus_sha256"] != reference["corpus_sha256"]:
        raise BenchmarkError("Reports scored different corpora; no comparison is possible")

    def delta(a, b):
        return None if a is None or b is None else round(a - b, 6)
    ours, theirs = report["overall"], reference["overall"]
    labels = sorted(set(ours["labels"]) | set(theirs["labels"]))
    return {"corpus_sha256": report["corpus_sha256"], "run": report["run"],
            "reference_run": reference["run"],
            "false_alarm_rate": delta(ours["false_alarm_rate"], theirs["false_alarm_rate"]),
            "missed": ours["missed"] - theirs["missed"],
            "ece": delta(report["calibration"]["ece"], reference["calibration"]["ece"]),
            "labels": {label: {metric: delta(ours["labels"].get(label, {}).get(metric),
                                             theirs["labels"].get(label, {}).get(metric))
                               for metric in ("precision", "recall")} for label in labels}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aikey-benchmark",
                                     description="Score labelled predictions; never reads media.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("score")
    run.add_argument("cases", type=Path)
    versus = sub.add_parser("compare")
    versus.add_argument("report", type=Path)
    versus.add_argument("reference", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "score":
            output = score(json.loads(args.cases.read_text()))
        else:
            output = compare(json.loads(args.report.read_text()), json.loads(args.reference.read_text()))
    except (BenchmarkError, OSError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
