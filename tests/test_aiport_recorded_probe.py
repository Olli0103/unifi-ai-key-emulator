"""A historical probe requires two verified, private, matching frames."""

import hashlib
from pathlib import Path

import pytest

from aikey.aiport_detection import ObjectObservation
from aikey.aiport_recorded_probe import (
    RecordedProbeError, infer_recorded_person, parse_recorded_probe,
)


NOW_MS = 1_800_000_000_000


def fixture_probe(tmp_path: Path):
    state = tmp_path / "recorded-probe"
    state.mkdir(mode=0o700)
    frames = []
    for index, when in enumerate((NOW_MS - 3_602_000, NOW_MS - 3_600_000)):
        path = state / f"frame-{index}.jpg"
        content = f"private-frame-{index}".encode()
        path.write_bytes(content)
        path.chmod(0o600)
        frames.append({"path": str(path), "sha256": hashlib.sha256(content).hexdigest(),
                       "captured_ms": when})
    raw = {"camera_mac": "2A1122334455", "nonce": "a" * 32,
           "frames": frames, "checkpoint_path": "/models/nano.pth",
           "checkpoint_sha256": "b" * 64, "threshold": 0.5}
    return raw


def parse(raw, tmp_path):
    return parse_recorded_probe(raw, state_dir=tmp_path,
                                camera_mac="2A1122334455", now_ms=NOW_MS)


def test_two_matching_private_frames_confirm_one_person(tmp_path, monkeypatch):
    raw = fixture_probe(tmp_path)
    probe = parse(raw, tmp_path)

    class StubDetector:
        def detect(self, frame):
            assert frame.startswith(b"private-frame-")
            box = ((0.2, 0.2, 0.5, 0.8) if frame.endswith(b"0")
                   else (0.21, 0.2, 0.51, 0.8))
            return (ObjectObservation("person", "person", 0.95, box),)

    monkeypatch.setattr(
        "aikey.aiport_recorded_probe.RFDetrNanoDetector.from_checkpoint",
        lambda *_args, **_kwargs: StubDetector())
    track = infer_recorded_person(probe)
    assert (track.enter.edge, track.enter.kind, track.enter.score) == (
        "enter", "person", 0.95)
    assert track.moving.edge == "moving"
    assert track.enter.track_id == track.moving.track_id
    assert track.enter.box != track.moving.box
    assert probe.frames[1].captured_ms - probe.frames[0].captured_ms == 2000


@pytest.mark.parametrize("change", [
    "other_camera", "bad_nonce", "three_frames", "old", "future", "gap",
    "world_readable_dir", "outside_state", "symlink_dir", "bad_threshold",
])
def test_recorded_probe_rejects_unbounded_or_untrusted_input(tmp_path, change):
    raw = fixture_probe(tmp_path)
    if change == "other_camera":
        raw["camera_mac"] = "2A1122334456"
    elif change == "bad_nonce":
        raw["nonce"] = "short"
    elif change == "three_frames":
        raw["frames"].append(raw["frames"][1])
    elif change == "old":
        raw["frames"][0]["captured_ms"] -= 25 * 60 * 60 * 1000
    elif change == "future":
        raw["frames"][1]["captured_ms"] = NOW_MS + 1000
    elif change == "gap":
        raw["frames"][1]["captured_ms"] += 3001
    elif change == "world_readable_dir":
        (tmp_path / "recorded-probe").chmod(0o755)
    elif change == "outside_state":
        raw["frames"][0]["path"] = str(tmp_path / "outside.jpg")
    elif change == "symlink_dir":
        state = tmp_path / "recorded-probe"
        state.rename(tmp_path / "real-frames")
        state.symlink_to(tmp_path / "real-frames", target_is_directory=True)
    else:
        raw["threshold"] = 1.0
    with pytest.raises(RecordedProbeError):
        parse(raw, tmp_path)


@pytest.mark.parametrize("change", ["hash", "mode", "symlink"])
def test_recorded_probe_rechecks_frame_at_use(tmp_path, monkeypatch, change):
    raw = fixture_probe(tmp_path)
    probe = parse(raw, tmp_path)
    frame = probe.frames[0].path
    if change == "hash":
        frame.write_bytes(b"changed")
    elif change == "mode":
        frame.chmod(0o644)
    else:
        frame.rename(frame.with_suffix(".bak"))
        frame.symlink_to(frame.with_suffix(".bak"))
    monkeypatch.setattr(
        "aikey.aiport_recorded_probe.RFDetrNanoDetector.from_checkpoint",
        lambda *_args, **_kwargs: pytest.fail("model must not load for bad media"))
    with pytest.raises(RecordedProbeError):
        infer_recorded_person(probe)


def test_recorded_probe_requires_same_person_across_frames(tmp_path, monkeypatch):
    raw = fixture_probe(tmp_path)
    probe = parse(raw, tmp_path)

    class StubDetector:
        def detect(self, frame):
            if frame.endswith(b"0"):
                return (ObjectObservation("person", "person", 0.95,
                                          (0.2, 0.2, 0.4, 0.7)),)
            return (ObjectObservation("person", "person", 0.95,
                                      (0.6, 0.2, 0.8, 0.7)),)

    monkeypatch.setattr(
        "aikey.aiport_recorded_probe.RFDetrNanoDetector.from_checkpoint",
        lambda *_args, **_kwargs: StubDetector())
    with pytest.raises(RecordedProbeError, match="recorded_person_unconfirmed"):
        infer_recorded_person(probe)


def test_recorded_probe_rejects_ambiguous_person_frame(tmp_path, monkeypatch):
    probe = parse(fixture_probe(tmp_path), tmp_path)

    class StubDetector:
        def detect(self, frame):
            first = ObjectObservation("person", "person", 0.95,
                                      (0.2, 0.2, 0.4, 0.7))
            if frame.endswith(b"0"):
                return (first, ObjectObservation("person", "person", 0.9,
                                                 (0.6, 0.2, 0.8, 0.7)))
            return (first,)

    monkeypatch.setattr(
        "aikey.aiport_recorded_probe.RFDetrNanoDetector.from_checkpoint",
        lambda *_args, **_kwargs: StubDetector())
    with pytest.raises(RecordedProbeError, match="recorded_person_unconfirmed"):
        infer_recorded_person(probe)
