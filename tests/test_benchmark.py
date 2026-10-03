"""Versioned, deterministic benchmark scoring on a synthetic corpus (#7). No media."""

import copy
import json

import pytest

from aikey.benchmark import BenchmarkError, compare, main, score, validate

RUN = {"model": "synthetic-vision", "provider": "synthetic", "profile": "ai-key",
       "code_revision": "0000000"}
ROWS = [  # id, camera class, conditions, expected, predicted, confidence
    ("c01", "outdoor", ["day"], "person", "person", 0.9),
    ("c02", "outdoor", ["night"], "person", "none", 0),
    ("c03", "outdoor", ["rain"], "vehicle", "vehicle", 0.8),
    ("c04", "outdoor", ["night"], "animal", "person", 0.7),
    ("c05", "indoor", ["day"], "none", "none", 0),
    ("c06", "indoor", ["glare"], "none", "person", 0.6),
    ("c07", "outdoor", ["day"], "none", "none", 0),
    ("c08", "indoor", ["day"], "package", "package", 0.95),
    ("c09", "outdoor", ["night"], "vehicle", "vehicle", 0.55),
    ("c10", "outdoor", ["motion_blur"], "none", "none", 0),
]


def corpus(rows=ROWS, run=RUN):
    return {"schema": "aikey-benchmark-cases/1", "run": dict(run),
            "cases": [{"id": i, "camera_class": k, "conditions": list(t), "expected": e,
                       "predicted": p, "confidence": c} for i, k, t, e, p, c in rows]}


def test_metrics_match_the_hand_computed_values():
    report = score(corpus())
    overall = report["overall"]
    assert overall["labels"]["person"] == {"tp": 1, "fp": 2, "fn": 1, "precision": 0.333333, "recall": 0.5}
    assert overall["labels"]["vehicle"] == {"tp": 2, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0}
    # An animal that was never predicted: precision undefined, not faked as 0 or 1.
    assert overall["labels"]["animal"] == {"tp": 0, "fp": 0, "fn": 1, "precision": None, "recall": 0.0}
    assert (overall["negatives"], overall["false_alarms"], overall["false_alarm_rate"]) == (4, 1, 0.25)
    assert (overall["positives"], overall["missed"], overall["misclassified"]) == (6, 1, 1)
    assert "licensePlate" not in overall["labels"]               # absent classes are not reported
    calibration = report["calibration"]
    assert calibration["answered"] == 6 and calibration["brier"] == 0.184167
    assert calibration["ece"] == 0.35
    assert [b["count"] for b in calibration["bins"]] == [1, 1, 1, 1, 2]


def test_slices_by_camera_class_and_condition():
    report = score(corpus())
    assert report["by_camera_class"]["indoor"]["false_alarms"] == 1
    assert report["by_camera_class"]["outdoor"]["missed"] == 1
    night = report["by_condition"]["night"]
    assert night["cases"] == 3 and night["missed"] == 1 and night["misclassified"] == 1
    assert report["by_condition"]["glare"]["false_alarm_rate"] == 1.0


def test_the_report_is_versioned_and_deterministic():
    shuffled = corpus(list(reversed(ROWS)))
    first, second = score(corpus()), score(shuffled)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["schema"] == "aikey-benchmark-report/1" and first["run"] == RUN
    assert len(first["corpus_sha256"]) == 64
    # Changing the corpus (not only the predictions) changes its identity.
    changed = copy.deepcopy(ROWS)
    changed[0] = ("c01", "outdoor", ["day", "rain"], "person", "person", 0.9)
    assert score(corpus(changed))["corpus_sha256"] != first["corpus_sha256"]
    other_model = score(corpus(run=dict(RUN, model="other")))
    assert other_model["corpus_sha256"] == first["corpus_sha256"]


def test_demos_without_negative_cases_cannot_be_scored():
    only_positive = [row for row in ROWS if row[3] != "none"]
    with pytest.raises(BenchmarkError, match="Negative cases"):
        score(corpus(only_positive))
    only_negative = [row for row in ROWS if row[3] == "none"]
    with pytest.raises(BenchmarkError, match="Positive cases"):
        score(corpus(only_negative))


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["cases"][0].__setitem__("confidence", 1.5), "confidence"),
    (lambda d: d["cases"][0].__setitem__("confidence", float("nan")), "confidence"),
    (lambda d: d["cases"][0].__setitem__("confidence", True), "confidence"),
    (lambda d: d["cases"][1].__setitem__("confidence", 0.4), "'none' prediction"),
    (lambda d: d["cases"][0].__setitem__("expected", "dragon"), "Unknown expected"),
    (lambda d: d["cases"][1].__setitem__("id", "c01"), "unique"),
    (lambda d: d["cases"][0].__setitem__("conditions", ["Night"]), "conditions"),
    (lambda d: d["cases"][0].__setitem__("frame", "x.jpg"), "exactly id"),
    (lambda d: d["run"].pop("code_revision"), "run needs"),
    (lambda d: d.__setitem__("schema", "other/1"), "Unsupported case schema"),
])
def test_malformed_case_files_are_refused(mutate, message):
    document = corpus()
    mutate(document)
    with pytest.raises(BenchmarkError, match=message):
        validate(document)


def test_comparison_needs_the_same_corpus():
    ours = score(corpus())
    better = copy.deepcopy(ROWS)
    better[5] = ("c06", "indoor", ["glare"], "none", "none", 0)          # the false alarm fixed
    reference = score(corpus(better, run=dict(RUN, model="reference")))
    result = compare(ours, reference)
    assert result["false_alarm_rate"] == 0.25 and result["missed"] == 0
    assert result["labels"]["person"]["precision"] == round(1 / 3 - 0.5, 6)
    other = copy.deepcopy(ROWS)[:-1]
    with pytest.raises(BenchmarkError, match="different corpora"):
        compare(ours, score(corpus(other)))
    with pytest.raises(BenchmarkError, match="benchmark reports"):
        compare(ours, {"schema": "something-else"})


def test_cli_scores_and_refuses(tmp_path, capsys):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(corpus()))
    assert main(["score", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["overall"]["false_alarms"] == 1
    path.write_text(json.dumps(corpus([row for row in ROWS if row[3] != "none"])))
    assert main(["score", str(path)]) == 1
    assert "Negative cases" in capsys.readouterr().err
