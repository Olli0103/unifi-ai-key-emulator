"""Score labelled plate, speech, retrieval and operational records without media.

Raw text and identities remain in the owner's input file, never in reports.
JiWER supplies the maintained edit-distance scorer; no inference is performed.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from .benchmark import BenchmarkError, _text

CASES_SCHEMA = "aikey-multimodal-cases/1"
REPORT_SCHEMA = "aikey-multimodal-report/1"
COMMON = {"id", "camera_class", "conditions", "input_sha256", "kind"}
FIELDS = {"plate": {"expected", "predicted"}, "speech": {"expected", "predicted"},
          "retrieval": {"relevant", "ranked", "k"},
          "operation": {"camera_days", "false_alarms", "critical_events", "missed_critical",
                        "latency_ms", "provider_charge_usd"}}
SHA = re.compile(r"[0-9a-f]{64}\Z")
TAG = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")


def number(value, *, nullable=False, positive=False):
    if value is None and nullable:
        return None
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < 0 or (positive and value == 0) or value > 1e12):
        raise BenchmarkError("Expected a bounded finite nonnegative metric")
    return value


def validate(document):
    if (not isinstance(document, dict) or set(document) != {"schema", "run", "cases"}
            or document["schema"] != CASES_SCHEMA):
        raise BenchmarkError("Invalid multimodal case schema")
    run = document["run"]
    if not isinstance(run, dict) or set(run) != {"model", "provider", "profile", "code_revision"}:
        raise BenchmarkError("Invalid run identity")
    run = {k: _text(v, "run identity") for k, v in sorted(run.items())}
    cases = document["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 10000:
        raise BenchmarkError("Expected 1 to 10000 cases")
    checked, seen = [], set()
    for row in cases:
        if not isinstance(row, dict) or not isinstance(row.get("kind"), str):
            raise BenchmarkError("Invalid case")
        kind = row["kind"]
        if kind not in FIELDS or set(row) != COMMON | FIELDS[kind]:
            raise BenchmarkError("Invalid case fields")
        identity = _text(row["id"], "case id")
        if identity in seen:
            raise BenchmarkError("Duplicate case id")
        seen.add(identity)
        _text(row["camera_class"], "camera class")
        if not isinstance(row["input_sha256"], str) or not SHA.fullmatch(row["input_sha256"]):
            raise BenchmarkError("Each case needs its exact input digest")
        tags = row["conditions"]
        if (not isinstance(tags, list) or len(tags) > 16
                or any(not isinstance(t, str) or not TAG.fullmatch(t) for t in tags)
                or len(tags) != len(set(tags))):
            raise BenchmarkError("Invalid condition tags")
        copy = {**row, "conditions": sorted(tags)}
        if kind in ("plate", "speech"):
            for key in ("expected", "predicted"):
                text = row[key]
                if (not isinstance(text, str) or len(text) > (64 if kind == "plate" else 4096)
                        or any(ord(c) < 32 and c not in "\n\t" for c in text)):
                    raise BenchmarkError("Invalid labelled text")
                if kind == "plate" and ("\n" in text or "\t" in text):
                    raise BenchmarkError("Plate labels must be single-line")
                # Explicit, identical whitespace policy; never infer punctuation,
                # case or uncertain plate characters from a neighbouring reading.
                copy[key] = " ".join(text.split())
        elif kind == "retrieval":
            for key in ("relevant", "ranked"):
                values = row[key]
                if not isinstance(values, list) or len(values) > 1000:
                    raise BenchmarkError("Invalid retrieval list")
                for v in values:
                    _text(v, "retrieval item")
                if len(set(values)) != len(values):
                    raise BenchmarkError("Duplicate retrieval item")
            if type(row["k"]) is not int or not 1 <= row["k"] <= 100:
                raise BenchmarkError("Retrieval k must be 1 to 100")
            copy["relevant"] = sorted(row["relevant"])
        else:
            number(row["camera_days"], positive=True)
            for key in ("false_alarms", "critical_events", "missed_critical"):
                if type(row[key]) is not int:
                    raise BenchmarkError("Event counts must be integers")
                number(row[key])
            if row["missed_critical"] > row["critical_events"]:
                raise BenchmarkError("Misses exceed labelled critical events")
            number(row["latency_ms"], nullable=True)
            number(row["provider_charge_usd"], nullable=True)
        checked.append(copy)
    for kind in ("plate", "speech", "retrieval"):
        group = [c for c in checked if c["kind"] == kind]
        if group:
            truth = "relevant" if kind == "retrieval" else "expected"
            if not any(c[truth] for c in group) or not any(not c[truth] for c in group):
                raise BenchmarkError("Every scored recognition/retrieval kind needs positives and negatives")
    return run, sorted(checked, key=lambda c: c["id"])


def ratio(a, b):
    return round(a / b, 6) if b else None


def _text_metrics(cases, kind):
    try:
        import jiwer
    except ImportError as exc:
        raise BenchmarkError("Text scoring requires the benchmark extra (JiWER)") from exc
    expected = [c["expected"] for c in cases]
    predicted = [c["predicted"] for c in cases]
    chars = jiwer.process_characters(expected, predicted)
    words = jiwer.process_words(expected, predicted)
    empty = [c for c in cases if not c["expected"]]
    positives = [c for c in cases if c["expected"]]
    errors = chars.substitutions + chars.deletions + chars.insertions
    base = {"cases": len(cases), "positives": len(positives), "negatives": len(empty),
            "exact_matches": sum(c["expected"] == c["predicted"] for c in cases),
            "positive_exact_accuracy": ratio(sum(c["expected"] == c["predicted"] for c in positives),
                                             len(positives)),
            "false_positive_negatives": sum(bool(c["predicted"]) for c in empty),
            "negative_false_positive_rate": ratio(sum(bool(c["predicted"]) for c in empty), len(empty)),
            "character_errors": errors,
            "reference_characters": chars.hits + chars.substitutions + chars.deletions,
            "cer": ratio(errors, chars.hits + chars.substitutions + chars.deletions)}
    if kind == "speech":
        base.update(word_errors=words.substitutions + words.deletions + words.insertions,
                    reference_words=words.hits + words.substitutions + words.deletions,
                    wer=ratio(words.substitutions + words.deletions + words.insertions,
                              words.hits + words.substitutions + words.deletions))
    return base


def _retrieval(cases):
    positive, negative = [], []
    for c in cases:
        relevant, ranked, k = set(c["relevant"]), c["ranked"][:c["k"]], c["k"]
        if not relevant:
            negative.append(bool(ranked))
            continue
        hits = [n for n, item in enumerate(ranked, 1) if item in relevant]
        dcg = sum(1 / math.log2(n + 1) for n in hits)
        ideal = sum(1 / math.log2(n + 1) for n in range(1, min(k, len(relevant)) + 1))
        positive.append((len(hits) / len(relevant), 1 / hits[0] if hits else 0, dcg / ideal))
    return {"cases": len(cases), "positive_queries": len(positive), "negative_queries": len(negative),
            "recall_at_k": ratio(sum(x[0] for x in positive), len(positive)),
            "mrr_at_k": ratio(sum(x[1] for x in positive), len(positive)),
            "ndcg_at_k": ratio(sum(x[2] for x in positive), len(positive)),
            "negative_queries_with_results": sum(negative),
            "negative_result_rate": ratio(sum(negative), len(negative))}


def _operation(cases):
    days = sum(c["camera_days"] for c in cases)
    latencies = sorted(c["latency_ms"] for c in cases if c["latency_ms"] is not None)
    costs = [c["provider_charge_usd"] for c in cases if c["provider_charge_usd"] is not None]
    return {"cases": len(cases), "camera_days": days,
            "false_alarms": sum(c["false_alarms"] for c in cases),
            "false_alarms_per_camera_day": ratio(sum(c["false_alarms"] for c in cases), days),
            "critical_events": sum(c["critical_events"] for c in cases),
            "missed_critical": sum(c["missed_critical"] for c in cases),
            "latency_samples": len(latencies), "latency_unknown": len(cases) - len(latencies),
            "latency_ms": {f"p{p}": latencies[math.ceil(p / 100 * len(latencies)) - 1]
                           if latencies else None for p in (50, 95, 99)},
            "cost_known_samples": len(costs), "cost_unknown_samples": len(cases) - len(costs),
            "known_provider_charge_usd": round(sum(costs), 8),
            "total_provider_charge_usd": round(sum(costs), 8) if len(costs) == len(cases) else None}


def metrics(cases, kind):
    if kind in ("plate", "speech"):
        return _text_metrics(cases, kind)
    return _retrieval(cases) if kind == "retrieval" else _operation(cases)


def score(document):
    run, cases = validate(document)
    truth = []
    for c in cases:
        row = {k: c[k] for k in sorted(COMMON)}
        for key in ({"expected"} if c["kind"] in ("plate", "speech") else
                    {"relevant", "k"} if c["kind"] == "retrieval" else
                    {"camera_days", "critical_events"}):
            row[key] = c[key]
        truth.append(row)
    digest = hashlib.sha256(json.dumps(truth, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
    results = {}
    for kind in sorted({c["kind"] for c in cases}):
        rows = [c for c in cases if c["kind"] == kind]
        results[kind] = {"overall": metrics(rows, kind),
                         "by_camera_class": {name: metrics([c for c in rows if c["camera_class"] == name], kind)
                                             for name in sorted({c["camera_class"] for c in rows})},
                         "by_condition": {tag: metrics([c for c in rows if tag in c["conditions"]], kind)
                                          for tag in sorted({t for c in rows for t in c["conditions"]})}}
    return {"schema": REPORT_SCHEMA, "run": run, "corpus_sha256": digest, "metrics": results,
            "normalization": "whitespace-only; case and punctuation preserved",
            "claims": "Labelled records only, not native acceptance or an AI Key quality claim. "
                      "Provider-reported charges only; unknown is not zero. No theoretical API estimate is inferred."}


def compare(report, reference):
    def finite_tree(value):
        if isinstance(value, dict):
            for item in value.values():
                finite_tree(item)
        elif value is not None:
            if type(value) not in (int, float):
                raise BenchmarkError("Incompatible metric structures")
            if not math.isfinite(value) or value < 0:
                raise BenchmarkError("Nonfinite or negative comparison metric")

    for value in (report, reference):
        if (not isinstance(value, dict) or value.get("schema") != REPORT_SCHEMA
                or not isinstance(value.get("corpus_sha256"), str)
                or not SHA.fullmatch(value["corpus_sha256"])
                or not isinstance(value.get("metrics"), dict) or not value["metrics"]
                or not set(value["metrics"]) <= set(FIELDS)
                or not isinstance(value.get("run"), dict)
                or set(value["run"]) != {"model", "provider", "profile", "code_revision"}):
            raise BenchmarkError("Expected multimodal reports")
        for item in value["run"].values():
            _text(item, "run identity")
        finite_tree(value["metrics"])
    if report["corpus_sha256"] != reference["corpus_sha256"]:
        raise BenchmarkError("Reports scored different corpora")

    def difference(a, b):
        if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
            return {k: difference(a[k], b[k]) for k in a}
        if ((a is None and (b is None or type(b) in (int, float)))
                or (b is None and type(a) in (int, float))):
            return None
        if type(a) in (int, float) and type(b) in (int, float):
            if not math.isfinite(a) or not math.isfinite(b):
                raise BenchmarkError("Nonfinite comparison metric")
            return round(a - b, 8)
        raise BenchmarkError("Incompatible metric structures")
    return {"corpus_sha256": report["corpus_sha256"], "run": report.get("run"),
            "reference_run": reference.get("run"),
            "deltas": difference(report["metrics"], reference["metrics"])}
