"""Hand-calculated multimodal metrics; synthetic labels, no model or media."""
from copy import deepcopy
import json

import pytest

from aikey.benchmark import BenchmarkError, main
from aikey.benchmark_multi import CASES_SCHEMA, REPORT_SCHEMA, compare, score


def case(identity, kind, **values):
    return {"id": identity, "camera_class": "outdoor", "conditions": ["night", "glare"],
            "input_sha256": "a" * 64, "kind": kind, **values}


def document():
    return {"schema": CASES_SCHEMA,
            "run": {"model": "synthetic", "provider": "local", "profile": "ai-port", "code_revision": "fixture"},
            "cases": [
                case("p1", "plate", expected="AB12", predicted="AB?2"),
                case("p2", "plate", expected="", predicted="ZZ"),
                case("s1", "speech", expected="hello small world", predicted="hello world"),
                case("s2", "speech", expected="", predicted="noise"),
                case("r1", "retrieval", relevant=["good", "best"], ranked=["wrong", "best", "good"], k=2),
                case("r2", "retrieval", relevant=[], ranked=["wrong"], k=2),
                case("o1", "operation", camera_days=1, false_alarms=1, critical_events=4,
                     missed_critical=1, latency_ms=10, provider_charge_usd=0.02),
                case("o2", "operation", camera_days=1, false_alarms=2, critical_events=0,
                     missed_critical=0, latency_ms=100, provider_charge_usd=None),
                case("o3", "operation", camera_days=0.5, false_alarms=0, critical_events=1,
                     missed_critical=1, latency_ms=None, provider_charge_usd=0),
            ]}


def test_plate_uncertainty_and_negative_false_read_are_errors():
    p = score(document())["metrics"]["plate"]["overall"]
    assert p["character_errors"] == 3 and p["reference_characters"] == 4
    assert p["cer"] == 0.75 and p["positive_exact_accuracy"] == 0
    assert p["negative_false_positive_rate"] == 1


def test_speech_silence_hallucination_is_not_hidden_by_empty_ground_truth():
    s = score(document())["metrics"]["speech"]["overall"]
    assert s["reference_words"] == 3 and s["word_errors"] == 2
    assert s["wer"] == 0.666667 and s["false_positive_negatives"] == 1


def test_retrieval_top_k_ranking_and_negative_queries():
    r = score(document())["metrics"]["retrieval"]["overall"]
    assert r["recall_at_k"] == 0.5 and r["mrr_at_k"] == 0.5
    assert r["ndcg_at_k"] == 0.386853 and r["negative_result_rate"] == 1


def test_exposure_latency_and_unknown_cost_are_reported_separately():
    o = score(document())["metrics"]["operation"]["overall"]
    assert o["camera_days"] == 2.5 and o["false_alarms_per_camera_day"] == 1.2
    assert o["critical_events"] == 5 and o["missed_critical"] == 2
    assert o["latency_ms"] == {"p50": 10, "p95": 100, "p99": 100}
    assert o["latency_samples"] == 2 and o["latency_unknown"] == 1
    assert o["known_provider_charge_usd"] == 0.02 and o["cost_unknown_samples"] == 1
    assert o["total_provider_charge_usd"] is None


def test_determinism_slices_and_report_redaction():
    d = document()
    before = deepcopy(d)
    a = score(d)
    assert d == before and a["schema"] == REPORT_SCHEMA
    d["cases"].reverse()
    assert score(d) == a
    for kind in a["metrics"].values():
        assert kind["overall"] == kind["by_camera_class"]["outdoor"]
        assert kind["overall"] == kind["by_condition"]["night"]
    encoded = json.dumps(a)
    for private in ("AB12", "AB?2", "hello small world", "noise", "best", "wrong"):
        assert private not in encoded


def test_like_for_like_comparison_binds_source_labels_and_cutoff_not_predictions():
    d = document()
    reference = score(d)
    d["cases"][0]["predicted"] = "AB12"
    ours = score(d)
    assert ours["corpus_sha256"] == reference["corpus_sha256"]
    result = compare(ours, reference)
    assert result["deltas"]["plate"]["overall"]["positive_exact_accuracy"] == 1
    assert result["deltas"]["operation"]["overall"]["total_provider_charge_usd"] is None
    for mutate in (lambda x: x["cases"][0].update(input_sha256="b" * 64),
                   lambda x: x["cases"][0].update(expected="CD12"),
                   lambda x: x["cases"][4].update(k=3)):
        other = document()
        mutate(other)
        with pytest.raises(BenchmarkError, match="different corpora"):
            compare(ours, score(other))


@pytest.mark.parametrize("change", [
    lambda d: d["cases"][6].update(provider_charge_usd=float("nan")),
    lambda d: d["cases"][6].update(latency_ms=True),
    lambda d: d["cases"][6].update(camera_days=0),
    lambda d: d["cases"][6].update(missed_critical=5),
    lambda d: d["cases"][6].update(false_alarms=-1),
    lambda d: d["cases"][6].update(critical_events=1.5),
    lambda d: d["cases"][0].update(input_sha256="unknown"),
    lambda d: d["cases"][0].update(expected=["not-text"]),
    lambda d: d["cases"][0].update(extra="private"),
    lambda d: d["cases"][0].update(kind=[]),
    lambda d: d["cases"][0].update(id="p2"),
    lambda d: d["cases"][0].update(conditions=["night", "night"]),
    lambda d: d["cases"][4].update(ranked=["good", "good"]),
    lambda d: d["cases"][4].update(k=True),
    lambda d: d["cases"][5].update(relevant=["good"]),
    lambda d: d["cases"][1].update(expected="positive-only"),
    lambda d: d["cases"][2].update(expected=""),
])
def test_bad_or_positive_only_records_are_refused(change):
    d = document()
    change(d)
    with pytest.raises(BenchmarkError):
        score(d)


def test_empty_prediction_and_no_latency_do_not_become_successful_measurements():
    d = document()
    for c in d["cases"]:
        if c["kind"] in ("plate", "speech"):
            c["predicted"] = ""
        if c["kind"] == "operation":
            c["latency_ms"] = None
    r = score(d)
    assert r["metrics"]["speech"]["overall"]["wer"] == 1
    assert r["metrics"]["plate"]["overall"]["positive_exact_accuracy"] == 0
    assert r["metrics"]["operation"]["overall"]["latency_ms"]["p95"] is None


def test_cli_and_corrupt_comparison_reports(tmp_path, capsys):
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(document()))
    assert main(["multi-score", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    path.write_text(json.dumps(report))
    assert main(["multi-compare", str(path), str(path)]) == 0
    capsys.readouterr()
    bad = deepcopy(report)
    bad["metrics"]["plate"]["overall"] = None
    with pytest.raises(BenchmarkError, match="structures"):
        compare(bad, report)
    bad = deepcopy(report)
    bad["metrics"]["plate"]["overall"]["cer"] = float("inf")
    with pytest.raises(BenchmarkError, match="Nonfinite"):
        compare(bad, report)


def test_unknown_does_not_hide_nonfinite_values_in_a_reference():
    report = score(document())
    bad = deepcopy(report)
    bad["metrics"]["operation"]["overall"]["total_provider_charge_usd"] = float("inf")
    with pytest.raises(BenchmarkError, match="Nonfinite"):
        compare(report, bad)
    del bad["run"]
    with pytest.raises(BenchmarkError, match="reports"):
        compare(report, bad)
