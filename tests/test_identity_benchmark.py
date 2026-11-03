"""Hand-calculated identity decisions; fictional opaque labels, no face data."""

from copy import deepcopy
import json

import pytest

from aikey.benchmark import BenchmarkError
from aikey.benchmark_multi import CASES_SCHEMA, compare, score


def document():
    decisions = [
        ("Fictional-A", "Fictional-A"),
        ("Fictional-A", "Fictional-B"),
        ("Fictional-B", None),
        ("Fictional-B", "Fictional-B"),
        (None, "Fictional-A"),
        (None, None),
    ]
    return {
        "schema": CASES_SCHEMA,
        "run": {
            "model": "synthetic-sface128",
            "provider": "local",
            "profile": "aikey",
            "code_revision": "fictional",
        },
        "cases": [
            {
                "id": f"case-{n}",
                "kind": "identity",
                "camera_class": "fictional-camera",
                "conditions": ["night" if expected else "day"],
                "input_sha256": str(n) * 64,
                "expected": expected,
                "predicted": predicted,
            }
            for n, (expected, predicted) in enumerate(decisions, 1)
        ],
    }


def test_known_mistakes_rejections_and_unknown_false_accepts_are_separate():
    result = score(document())["metrics"]["identity"]["overall"]
    assert result == {
        "cases": 6,
        "known_trials": 4,
        "unknown_trials": 2,
        "correctly_identified": 2,
        "misidentified_known": 1,
        "rejected_known": 1,
        "falsely_identified_unknown": 1,
        "correctly_rejected_unknown": 1,
        "known_identification_accuracy": 0.5,
        "known_misidentification_rate": 0.25,
        "known_rejection_rate": 0.25,
        "unknown_false_accept_rate": 0.5,
        "unknown_rejection_rate": 0.5,
        "identified_precision": 0.5,
        "overall_accuracy": 0.5,
    }


def test_single_class_slices_have_unknown_denominators_not_perfect_scores():
    report = score(document())["metrics"]["identity"]
    assert report["by_condition"]["night"]["unknown_false_accept_rate"] is None
    assert report["by_condition"]["day"]["known_identification_accuracy"] is None
    assert report["by_camera_class"]["fictional-camera"] == report["overall"]


def test_no_identified_prediction_has_no_precision_denominator():
    d = document()
    for row in d["cases"]:
        row["predicted"] = None
    result = score(d)["metrics"]["identity"]["overall"]
    assert result["identified_precision"] is None
    assert result["known_rejection_rate"] == 1 and result["unknown_false_accept_rate"] == 0


def test_case_order_and_caller_records_are_preserved_without_identity_output():
    d = document()
    before = deepcopy(d)
    a = score(d)
    assert d == before
    d["cases"].reverse()
    assert score(d) == a
    output = json.dumps(a)
    for private in ("Fictional-A", "Fictional-B", "case-1"):
        assert private not in output


def test_comparison_binds_expected_identity_and_input_not_prediction():
    d = document()
    reference = score(d)
    d["cases"][1]["predicted"] = "Fictional-A"
    actual = score(d)
    assert actual["corpus_sha256"] == reference["corpus_sha256"]
    result = compare(actual, reference)["deltas"]["identity"]["overall"]
    assert result["known_identification_accuracy"] == 0.25
    assert result["known_misidentification_rate"] == -0.25
    for key, value in (("expected", "Fictional-C"), ("input_sha256", "f" * 64)):
        other = document()
        other["cases"][0][key] = value
        with pytest.raises(BenchmarkError, match="different corpora"):
            compare(actual, score(other))


@pytest.mark.parametrize("known", [True, False])
def test_both_known_and_unknown_trials_are_required(known):
    d = document()
    d["cases"] = [r for r in d["cases"] if (r["expected"] is not None) == known]
    with pytest.raises(BenchmarkError):
        score(d)


@pytest.mark.parametrize("key", ["expected", "predicted"])
@pytest.mark.parametrize(
    "label", ["", False, 1, [], {}, "not an opaque id", "a" * 65, "private\nlabel"]
)
def test_identity_labels_are_bounded_exact_opaque_ids_or_none(key, label):
    d = document()
    d["cases"][0][key] = label
    with pytest.raises(BenchmarkError):
        score(d)


def test_literal_unknown_is_an_identity_id_only_none_means_unknown():
    d = document()
    d["cases"][0].update(expected="unknown", predicted="UNKNOWN")
    result = score(d)["metrics"]["identity"]["overall"]
    assert result["known_trials"] == 4 and result["misidentified_known"] == 2


def test_identity_only_scoring_does_not_import_edit_distance_or_inference(monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"jiwer", "cv2", "numpy", "onnxruntime", "torch"}:
            pytest.fail("Identity decision counts must not load scorers or models")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert score(document())["metrics"]["identity"]["overall"]["cases"] == 6


@pytest.mark.parametrize("extra", ["image", "embedding", "confidence", "threshold"])
def test_identity_records_cannot_smuggle_an_image_vector_or_unspecified_score(extra):
    d = document()
    d["cases"][0][extra] = "fictional-not-a-supported-field"
    with pytest.raises(BenchmarkError):
        score(d)


def test_optional_identity_kind_can_coexist_with_existing_operational_metrics():
    d = document()
    d["cases"].append(
        {
            "id": "operation",
            "kind": "operation",
            "camera_class": "fictional-camera",
            "conditions": [],
            "input_sha256": "f" * 64,
            "camera_days": 1,
            "false_alarms": 0,
            "critical_events": 0,
            "missed_critical": 0,
            "latency_ms": None,
            "provider_charge_usd": None,
        }
    )
    result = score(d)
    assert set(result["metrics"]) == {"identity", "operation"}
    assert result["metrics"]["identity"]["overall"]["cases"] == 6
    assert result["metrics"]["operation"]["overall"]["total_provider_charge_usd"] is None


def test_changed_unknown_truth_cannot_compare_even_when_case_counts_are_equal():
    d = document()
    reference = score(d)
    d["cases"][4]["expected"] = "Fictional-A"
    with pytest.raises(BenchmarkError, match="different corpora"):
        compare(score(d), reference)


def test_existing_cli_scores_and_compares_only_supplied_fictional_decisions(tmp_path, capsys):
    from aikey.benchmark import main

    d = document()
    source = tmp_path / "cases.json"
    source.write_text(json.dumps(d))
    before = source.read_bytes()
    assert main(["multi-score", str(source)]) == 0
    output = capsys.readouterr()
    assert not output.err
    a = json.loads(output.out)
    assert source.read_bytes() == before
    assert "Fictional-A" not in output.out and "Fictional-B" not in output.out
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps(a))
    d["cases"][1]["predicted"] = "Fictional-A"
    ours = tmp_path / "ours.json"
    ours.write_text(json.dumps(score(d)))
    assert main(["multi-compare", str(ours), str(reference)]) == 0
    output = capsys.readouterr()
    assert not output.err
    assert (
        json.loads(output.out)["deltas"]["identity"]["overall"]["known_identification_accuracy"]
        == 0.25
    )


def test_unavailable_precision_delta_stays_unknown_not_zero():
    d = document()
    reference = score(d)
    for row in d["cases"]:
        row["predicted"] = None
    delta = compare(score(d), reference)["deltas"]["identity"]["overall"]
    assert delta["identified_precision"] is None
    assert delta["unknown_false_accept_rate"] == -0.5


def test_invalid_identity_cli_never_echoes_the_label(tmp_path, capsys):
    from aikey.benchmark import main

    d = document()
    d["cases"][0]["expected"] = "FICTIONAL PRIVATE LABEL"
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps(d))
    before = source.read_bytes()
    assert main(["multi-score", str(source)]) != 0
    result = capsys.readouterr()
    assert not result.out and "FICTIONAL PRIVATE LABEL" not in result.err
    assert source.read_bytes() == before
