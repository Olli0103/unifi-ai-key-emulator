"""Read-only continuous-caption preflight (#12). Synthetic state only."""

import hashlib
import json
from pathlib import Path

import pytest

from aikey.caption_budget import HOUR_NS
from aikey.caption_preflight import main, preflight

NOW = 1_800_000_000.0
DAY = 86400
CAMERA = "a" * 24
HEALTH = {"device": {"adopted": True, "connected": True,
                     "control_commands": {"RequestAI": {"count": 0}, "recognizeKeyFrames": {"count": 4}}},
          "worker": {"queued": 0, "active": 0, "pending": 0}}
NATIVE = {"saved_captions": 3, "camera_families_with_captions": 2, "read_after_reload": True}


def jid(name):
    return hashlib.sha256(name.encode()).hexdigest()


def state(tmp_path, *, records=(), permits=(), reservations=None, high_water_ns=None):
    root = tmp_path / "state"
    (root / "worker-jobs").mkdir(parents=True)
    for name, state_name, age, operation in records:
        (root / "worker-jobs" / f"{jid(name)}.json").write_text(json.dumps({
            "jobId": jid(name), "fingerprint": jid(name + "f"), "state": state_name,
            "updatedAt": NOW - age, "operation": operation}))
    if permits:
        (root / "worker-test-scopes").mkdir()
        for index, consumed in enumerate(permits):
            value = {"schema": 1, "camera_id": CAMERA, "permit_id": f"p{index}"}
            if consumed:
                value.update(consumed_at=int(NOW), job_id=jid(f"permit-{index}"))
            (root / "worker-test-scopes" / f"{jid(str(index))}.json").write_text(json.dumps(value))
    if reservations is not None:
        now_ns = int(NOW * 1e9)
        (root / "caption-budget.json").write_text(json.dumps({
            "schema": 1, "high_water_ns": high_water_ns or now_ns,
            "reservations": [{"job_id": jid(f"r{i}"), "fingerprint": jid(f"f{i}"),
                              "camera_id": CAMERA, "at_ns": now_ns - offset}
                             for i, offset in enumerate(reservations)]}))
    return root


CONTINUOUS = {"worker": {"continuous": {"camera_models": ["UVC G6 Instant", "UVC G5 Flex"]}}}
LIVE_LIKE = {"worker": {"test_scopes": [{"kind": "recognizeKeyFrames"}, {"kind": "recognizeKeyFrames"}]}}


def digest(root):
    value = hashlib.sha256()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        value.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return value.hexdigest()


def test_a_ready_configuration_has_no_blockers(tmp_path):
    root = state(tmp_path, records=[("a", "completed", 3600, "describe")], reservations=[60e9])
    report = preflight(root, CONTINUOUS, health=HEALTH, native=NATIVE, now=NOW)
    assert report["ready"] is True and report["blockers"] == []
    assert report["budget"] == {"journal": True, "last_hour": 1, "remaining": 11, "retained_24h": 1,
                                "clock_behind_journal": False}


def test_the_live_shape_lists_every_blocker(tmp_path):
    root = state(tmp_path, permits=[True, True, True, True, True], records=[
        ("done-old", "completed", 2 * DAY, "describe"), ("done-new", "completed", 3600, "speechToText"),
        ("fail-old", "failed", 8 * DAY, None), ("fail-new", "failed", 2 * DAY, "recognizeFaces"),
        ("unsure", "callback_uncertain", 20 * DAY, "indexImages"),
        ("index", "completed", 120, "indexImages")])
    before = digest(root)
    report = preflight(root, LIVE_LIKE, health=HEALTH, now=NOW)
    assert report["scope"] == {"continuous_configured": False, "one_use_scopes": 2,
                               "scope_kinds": ["recognizeKeyFrames"], "camera_models_policy": 0}
    assert report["permits"] == {"total": 5, "consumed": 5, "unconsumed": 0, "unreadable": 0}
    assert report["budget"]["journal"] is False and report["budget"]["remaining"] == 12
    ledger = report["ledger"]
    assert ledger["entries"] == 6 and ledger["uncertain_callbacks"] == 1
    # Rollover would archive the day-old completion, the week-old failure and the index job.
    assert ledger["due_for_rollover"] == 3 and ledger["entries_after_rollover"] == 3
    assert report["key"]["recognize_key_frames_commands"] == 4 and report["key"]["requestai_commands"] == 0
    blockers = " | ".join(report["blockers"])
    for text in ("not configured", "One-use test scopes", "1 uncertain callbacks",
                 "Native Protect readback"):
        assert text in blockers
    assert report["ready"] is False
    encoded = json.dumps(report)
    assert CAMERA not in encoded and jid("unsure") not in encoded and jid("permit-0") not in encoded
    assert digest(root) == before                                   # read-only


def test_budget_exhaustion_and_a_clock_behind_the_journal_are_reported(tmp_path):
    root = state(tmp_path, reservations=[i * 1e9 for i in range(12)] + [2 * HOUR_NS])
    full = preflight(root, CONTINUOUS, health=HEALTH, native=NATIVE, now=NOW)["budget"]
    assert (full["last_hour"], full["remaining"], full["retained_24h"]) == (12, 0, 13)
    ahead = state(tmp_path / "b", reservations=[1e9], high_water_ns=int((NOW + 600) * 1e9))
    report = preflight(ahead, CONTINUOUS, health=HEALTH, native=NATIVE, now=NOW)
    assert report["budget"]["clock_behind_journal"] is True
    assert any("budget journal needs review" in b for b in report["blockers"])


def test_a_ledger_near_its_cap_and_its_growth_are_projected(tmp_path):
    records = ([(f"n{i}", "completed", 600, "describe") for i in range(6)]
               + [(f"u{i}", "callback_uncertain", 600, "indexImages") for i in range(3)])
    root = state(tmp_path, records=records)
    config = {"worker": {**CONTINUOUS["worker"], "max_ledger_entries": 10}}
    report = preflight(root, config, health=HEALTH, native=NATIVE, now=NOW)
    ledger = report["ledger"]
    assert (ledger["percent"], ledger["uncertain_added_last_24h"]) == (90.0, 3)
    assert ledger["days_to_full_at_uncertain_rate"] == round(1 / 3, 1)       # (10 - 9) / 3
    assert any("above 80%" in b for b in report["blockers"])
    # Completed work alone rolls over, so it projects no fill date.
    calm = preflight(state(tmp_path / "calm", records=records[:6]), config, health=HEALTH,
                     native=NATIVE, now=NOW)["ledger"]
    assert calm["days_to_full_at_uncertain_rate"] is None


@pytest.mark.parametrize("native", [None, {"camera_families_with_captions": 1, "read_after_reload": True},
                                    {"camera_families_with_captions": 2, "read_after_reload": False}])
def test_native_readback_on_two_families_after_reload_is_required(tmp_path, native):
    report = preflight(state(tmp_path), CONTINUOUS, health=HEALTH, native=native, now=NOW)
    assert report["ready"] is False and any("Native Protect readback" in b for b in report["blockers"])


def test_missing_or_disconnected_key_health_blocks(tmp_path):
    root = state(tmp_path)
    assert "No adopted" in " ".join(preflight(root, CONTINUOUS, native=NATIVE, now=NOW)["blockers"])
    offline = {**HEALTH, "device": {**HEALTH["device"], "connected": False}}
    assert preflight(root, CONTINUOUS, health=offline, native=NATIVE, now=NOW)["ready"] is False


def test_cli_exit_codes(tmp_path, capsys):
    root = state(tmp_path)
    (root / "config.json").write_text(json.dumps(LIVE_LIKE))
    assert main(["--state", str(root)]) == 2
    assert json.loads(capsys.readouterr().out)["ready"] is False
    (root / "config.json").write_text(json.dumps(CONTINUOUS))
    (tmp_path / "health.json").write_text(json.dumps(HEALTH))
    (tmp_path / "native.json").write_text(json.dumps(NATIVE))
    assert main(["--state", str(root), "--health", str(tmp_path / "health.json"),
                 "--native", str(tmp_path / "native.json")]) == 0
    capsys.readouterr()
    assert main(["--state", str(tmp_path / "missing")]) == 1
