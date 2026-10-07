"""The slot diff never pairs cameras and converges after safe local edits."""

import json

import pytest

from aikey.aiport_slot_diff import (
    SlotDiffError, apply_allowlist, diff_slots, main, public,
)

A, B, C, D = (f"{n}" * 24 for n in "abcd")
MAC = {A: "2A1100000001", B: "2A1100000002", C: "2A1100000003", D: "2A1100000004"}


def cameras():
    return [{"id": cid, "mac": MAC[cid], "name": name, "type": "UVC G4 Instant",
             "state": "CONNECTED", "host": f"192.168.10.{i}",
             "aiPortCapacityPoints": 0.25}
            for i, (cid, name) in enumerate(((A, "Flur"), (B, "Esszimmer"),
                                             (C, "Garage"), (D, "Keller")), 1)]


def aiports():
    return [{"mac": "2A11000000F1", "pairedCameras": [A]},
            {"mac": "2A11000000F2", "pairedCameras": [B, C]}]


def stream(cid):
    return {"camera_mac": MAC[cid], "source_ip": "192.168.10.9", "ffmpeg_path": "/usr/bin/ffmpeg"}


def slots():
    # Mac still allowlists Esszimmer, which Protect paired to the NAS slot.
    return {"mac": {"mac": "2A11000000F1", "paired_streams": [stream(A), stream(B)]},
            "nas-2": {"mac": "2A11000000F2", "paired_streams": [stream(B)]}}


def test_diff_classifies_without_any_pairing_action():
    diff = diff_slots(slots(), aiports(), cameras())
    kinds = [(a["action"], a.get("slot"), a.get("camera")) for a in diff["actions"]]
    assert ("remove_from_allowlist", "mac", "Esszimmer") in kinds
    assert ("add_to_allowlist", "nas-2", "Garage") in kinds
    assert ("manual_pairing", None, "Keller") in kinds
    assert not any(a["action"] in {"pair", "unpair"} for a in diff["actions"])
    assert diff["slots"]["nas-2"]["capacity_points"] == 0.5
    text = json.dumps(public(diff))
    assert "2A11" not in text and "192.168" not in text


def test_safe_apply_is_idempotent_and_leaves_review_items():
    first = diff_slots(slots(), aiports(), cameras())
    applied = apply_allowlist(slots(), first)
    assert [s["camera_mac"] for s in applied["mac"]["paired_streams"]] == [MAC[A]]
    assert applied["nas-2"] == slots()["nas-2"]          # review item not applied
    second = diff_slots(applied, aiports(), cameras())
    assert [a["action"] for a in second["actions"]] == ["add_to_allowlist", "manual_pairing"]
    assert apply_allowlist(applied, second) == applied    # nothing more to do
    reviewed = apply_allowlist(applied, second, include_review=True)
    added = reviewed["nas-2"]["paired_streams"][-1]
    assert added == {"camera_mac": MAC[C], "source_ip": "192.168.10.3",
                     "ffmpeg_path": "/usr/bin/ffmpeg"}
    assert [a["action"] for a in diff_slots(reviewed, aiports(), cameras())["actions"]] == [
        "manual_pairing"]


def test_capacity_missing_and_unmanaged_ports_are_reported_only():
    heavy = cameras()
    for row in heavy:
        row["aiPortCapacityPoints"] = 0.6
    diff = diff_slots({"nas-2": slots()["nas-2"],
                       "gone": {"mac": "2A11000000F9", "paired_streams": []}},
                      aiports(), heavy)
    actions = {a["action"] for a in diff["actions"]}
    assert {"over_capacity", "missing_ai_port", "unmanaged_ai_port"} <= actions
    assert apply_allowlist({"nas-2": slots()["nas-2"],
                            "gone": {"mac": "2A11000000F9", "paired_streams": []}},
                           {"actions": [a for a in diff["actions"]
                                        if a["action"] != "remove_from_allowlist"]}) is not None


def test_invalid_exports_fail_closed():
    with pytest.raises(SlotDiffError):
        diff_slots(slots(), {"not": "a list"}, cameras())
    broken = aiports()
    broken[0]["pairedCameras"] = ["f" * 24]
    with pytest.raises(SlotDiffError):
        diff_slots(slots(), broken, cameras())


def test_cli_dry_run_writes_nothing_and_apply_converges(tmp_path, capsys):
    paths = {}
    for label, config in slots().items():
        paths[label] = tmp_path / f"{label}.json"
        paths[label].write_text(json.dumps(config, indent=2) + "\n")
        paths[label].chmod(0o600)
    (tmp_path / "aiports.json").write_text(json.dumps(aiports()))
    (tmp_path / "cameras.json").write_text(json.dumps(cameras()))
    args = [f"--slot={label}={path}" for label, path in paths.items()] + [
        "--protect-aiports", str(tmp_path / "aiports.json"),
        "--protect-cameras", str(tmp_path / "cameras.json")]
    before = {label: path.read_text() for label, path in paths.items()}
    main(args)
    assert {label: path.read_text() for label, path in paths.items()} == before
    main(args + ["--apply-local"])
    assert paths["mac"].read_text().startswith("{\n")          # formatting kept
    assert (tmp_path / "mac.json.before-slot-diff").read_text() == before["mac"]
    capsys.readouterr()
    main(args)
    remaining = [a["action"] for a in json.loads(capsys.readouterr().out)["actions"]]
    assert "remove_from_allowlist" not in remaining
