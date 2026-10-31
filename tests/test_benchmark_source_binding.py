"""Comparisons bind supplied exact input digests, not merely matching case names."""

import copy
import json
import random

import pytest

from aikey.benchmark import BenchmarkError, compare, main, score, validate
from test_benchmark import bound_corpus, corpus


def test_source_digests_change_corpus_identity_but_predictions_and_order_do_not():
    original = bound_corpus()
    first = score(original)
    second = copy.deepcopy(original)
    second["cases"].reverse()
    second["run"]["model"] = "other-model"
    second["cases"][0]["predicted"] = "vehicle"
    second["cases"][0]["confidence"] = 0.8
    assert score(second)["corpus_sha256"] == first["corpus_sha256"]
    second["cases"][0]["input_sha256"] = "f" * 64
    assert score(second)["corpus_sha256"] != first["corpus_sha256"]
    with pytest.raises(BenchmarkError, match="different corpora"):
        compare(first, score(second))
    assert '"input_sha256":' not in json.dumps(first)
    assert all(c["input_sha256"] not in json.dumps(first) for c in original["cases"])
    assert "producer_declared_exact_input_sha256" == first["corpus_binding"]


def test_old_metrics_and_digests_are_preserved_without_granting_old_comparisons():
    legacy, bound = score(corpus()), score(bound_corpus())
    for key in ["overall", "calibration", "by_camera_class", "by_condition"]:
        assert legacy[key] == bound[key]
    assert legacy["schema"] == "aikey-benchmark-report/1"
    assert bound["schema"] == "aikey-benchmark-report/2"
    assert legacy["corpus_sha256"] != bound["corpus_sha256"]
    assert "Legacy label-only" in legacy["claims"]
    for left, right in [(legacy, legacy), (legacy, bound), (bound, legacy)]:
        with pytest.raises(BenchmarkError, match="Legacy label-only"):
            compare(left, right)


@pytest.mark.parametrize(
    "value", [None, "", "a" * 63, "A" * 64, "a" * 65, "x" * 64, True, [], "a" * 64 + "\n"]
)
def test_invalid_input_digest_refuses_the_case_file(value):
    cases = bound_corpus()
    cases["cases"][0]["input_sha256"] = value
    with pytest.raises(BenchmarkError):
        validate(cases)


def test_missing_input_digest_and_incorrect_schema_are_not_guessed_or_upgraded():
    cases = bound_corpus()
    del cases["cases"][0]["input_sha256"]
    with pytest.raises(BenchmarkError):
        score(cases)
    cases = corpus()
    cases["cases"][0]["input_sha256"] = "a" * 64
    with pytest.raises(BenchmarkError):
        score(cases)
    cases["schema"] = []
    with pytest.raises(BenchmarkError):
        score(cases)


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.update(corpus_sha256=None),
        lambda v: v.update(corpus_sha256="not-a-sha"),
        lambda v: v.update(corpus_sha256="A" * 64),
        lambda v: v.update(corpus_binding="labels_only"),
        lambda v: v["run"].update(provider="PRIVATE-CANARY\nvalue"),
        lambda v: v["overall"].update(false_alarm_rate=float("nan")),
        lambda v: v["overall"].update(missed=True),
        lambda v: v["overall"].update(positives=-1),
        lambda v: v["overall"].update(negatives=99999),
        lambda v: v["overall"]["labels"]["person"].update(precision=1),
        lambda v: v["overall"]["labels"]["person"].update(fp=0),
        lambda v: v["calibration"].update(answered=0),
        lambda v: v["calibration"].update(ece=float("inf")),
        lambda v: v["calibration"].update(brier=True),
        lambda v: v["calibration"]["bins"][0].update(count=0),
        lambda v: v["calibration"]["bins"][0].update(range=[0, 1]),
        lambda v: v["by_condition"]["night"].update(missed=float("nan")),
        lambda v: v["by_camera_class"]["indoor"].update(cases=100001),
        lambda v: v.update(by_camera_class={}),
        lambda v: v.update(extra="PRIVATE-CANARY"),
    ],
)
def test_malformed_report_is_rejected_on_either_side_even_with_identical_digest(change):
    good = score(bound_corpus())
    bad = copy.deepcopy(good)
    change(bad)
    for left, right in [(bad, good), (good, bad)]:
        with pytest.raises(BenchmarkError):
            compare(left, right)


def test_matching_forged_digest_cannot_hide_different_labelled_membership():
    good = score(bound_corpus())
    cases = bound_corpus()
    cases["cases"][0]["conditions"] = ["different"]
    bad = score(cases)
    bad["corpus_sha256"] = good["corpus_sha256"]
    with pytest.raises(BenchmarkError, match="membership"):
        compare(good, bad)


def test_no_answer_is_unknown_calibration_not_zero_or_a_fabricated_improvement():
    cases = bound_corpus()
    for c in cases["cases"]:
        c.update(predicted="none", confidence=0)
    report = score(cases)
    assert report["calibration"] == {"answered": 0, "ece": None, "brier": None, "bins": []}
    assert compare(report, score(bound_corpus()))["ece"] is None


def test_writer_consumer_contract_across_mixed_predictions_and_rounded_bins():
    rng = random.Random(44)
    for _ in range(30):
        cases = bound_corpus()
        for c in cases["cases"]:
            predicted = rng.choice(["none", "person", "vehicle", "animal", "package", "face"])
            c.update(predicted=predicted, confidence=0 if predicted == "none" else rng.random())
        result = compare(score(cases), score(bound_corpus()))
        assert result["schema"] == "aikey-benchmark-comparison/2"
        assert result["corpus_binding"] == "producer_declared_exact_input_sha256"


def test_cli_legacy_refusal_and_v2_comparison_do_not_print_raw_cases_or_canaries(tmp_path, capsys):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(bound_corpus()))
    assert main(["score", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    path.write_text(json.dumps(result))
    assert main(["compare", str(path), str(path)]) == 0
    output = capsys.readouterr().out
    assert "input_sha256" not in output.replace("producer_declared_exact_input_sha256", "")
    assert "c01" not in output and "c02" not in output
    path.write_text(json.dumps(score(corpus())))
    assert main(["compare", str(path), str(path)]) == 1
    assert "Legacy label-only" in capsys.readouterr().err
    path.write_text('{"schema":"PRIVATE-CANARY","schema":"aikey-benchmark-cases/2"}')
    assert main(["score", str(path)]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "PRIVATE-CANARY" not in captured.err
