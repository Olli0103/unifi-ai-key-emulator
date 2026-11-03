"""Hand-calculated metrics keep failed and unrun slots visible."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def reporter():
    path = Path(__file__).resolve().parents[1] / "tools/benchmark_person_report.py"
    spec = importlib.util.spec_from_file_location("person_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def data():
    cases, records = [], {}
    # Six TP, one FN, one positive error; thirteen TN, two FP, one negative error.
    scores = [0.8] * 6 + [0.2, None] + [0.1] * 13 + [0.9, 0.9, None]
    for index, p in enumerate(scores):
        identity = str(index)
        cases.append({"case_id": identity, "expected": "person" if index < 8 else "none",
                      "input_sha256": "a" * 64})
        records[identity] = {
            "case_id": identity, "input_sha256": "a" * 64,
            "client_attempts": 1, "automatic_retries": 0,
            "api_receipt_and_parse_ms": 100 + index,
            "status": "error" if p is None else "valid",
            **({"maximum_person_score": p, "predicted": "person" if p >= 0.5 else "none"}
               if p is not None else {}),
        }
    return {"cases": cases}, records


def test_independent_count_and_calibration_arithmetic(reporter):
    gold, records = data()
    r = reporter.summarize(gold, records)
    assert r["complete"] and r["terminal"] == 24 and r["valid"] == 22 and r["errors"] == 2
    assert r["confusion"]["person"] == {"person": 6, "none": 1, "__error__": 1, "__unrun__": 0}
    assert r["confusion"]["none"] == {"person": 2, "none": 13, "__error__": 1, "__unrun__": 0}
    assert r["all_slot_accuracy"] == 19 / 24
    assert r["all_slot_person_recall"] == 6 / 8 and r["person_precision"] == 6 / 8
    assert r["negative_false_positive_fraction"] == 2 / 16
    assert r["always_none_accuracy"] == 16 / 24
    assert r["calibration"]["brier_all_slots_invalid_loss_one"] == pytest.approx(4.63 / 24)
    assert r["calibration"]["brier_valid_only"] == pytest.approx(2.63 / 22)
    assert r["calibration"]["ece_valid_only"] == pytest.approx(5.1 / 22)
    assert r["api_receipt_and_parse_ms"] == {"terminal_cases": 24, "p50": 111,
                                            "p95": 122, "p99": 123}
    assert r["provider_charge_usd"] is None and r["event_to_alert_ms"] is None
    target = reporter.targets(r)
    assert target["minimum_person_recall"] == 7 / 8
    assert target["maximum_negative_false_positive_fraction"] == 1 / 16
    assert target["maximum_api_receipt_parse_p95_ms"] == 135


def test_unrun_is_not_a_negative_or_a_success(reporter):
    gold, records = data()
    del records["0"]
    r = reporter.summarize(gold, records)
    assert not r["complete"] and r["unrun"] == 1 and r["terminal"] == 23
    assert r["confusion"]["person"]["__unrun__"] == 1
    assert r["all_slot_person_recall"] == 5 / 8
    with pytest.raises(AssertionError):
        reporter.targets(r)


@pytest.mark.parametrize("change", ["hash", "retry", "nan", "wrong_label", "foreign_case"])
def test_mismatched_or_invalid_records_refuse(reporter, change):
    gold, records = data()
    if change == "hash":
        records["0"]["input_sha256"] = "b" * 64
    elif change == "retry":
        records["0"]["client_attempts"] = 2
    elif change == "nan":
        records["0"]["maximum_person_score"] = float("nan")
    elif change == "wrong_label":
        records["0"]["predicted"] = "none"
    else:
        records["foreign"] = records["0"]
    with pytest.raises(AssertionError):
        reporter.summarize(gold, records)
