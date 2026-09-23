"""One-use, model-backed AI Port probe from two private recorded frames.

This validates local model and tracking evidence. The caller still needs a
paired stream, an accepted camera policy, and a native timeline check.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import math
import os
from pathlib import Path
import re
import stat
import time

from .aiport_detection import DetectionError, RFDetrNanoDetector
from .aiport_ingest import IngressError, normalize_mac
from .aiport_tracking import TemporalTracker, TrackChange


_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
_NONCE = re.compile(r"[0-9a-f]{32}\Z")
_MAX_FRAME_BYTES = 1024 * 1024
_MAX_AGE_MS = 24 * 60 * 60 * 1000


class RecordedProbeError(ValueError):
    """Fixed failure code without paths, media, or model output."""


@dataclass(frozen=True)
class RecordedFrame:
    path: Path
    sha256: str
    captured_ms: int


@dataclass(frozen=True)
class RecordedProbe:
    camera_mac: str
    nonce: str
    frames: tuple[RecordedFrame, RecordedFrame]
    checkpoint_path: str
    checkpoint_sha256: str
    threshold: float


def parse_recorded_probe(raw: object, *, state_dir: Path,
                         camera_mac: str, now_ms: int | None = None) -> RecordedProbe:
    """Allow exactly two nearby frames from a private state subdirectory."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if (not isinstance(raw, dict) or set(raw) != {
            "camera_mac", "nonce", "frames", "checkpoint_path",
            "checkpoint_sha256", "threshold"}
            or not isinstance(raw["nonce"], str)
            or not _NONCE.fullmatch(raw["nonce"])
            or not isinstance(raw["frames"], list)
            or len(raw["frames"]) != 2
            or not isinstance(raw["checkpoint_path"], str)
            or not Path(raw["checkpoint_path"]).is_absolute()
            or not isinstance(raw["checkpoint_sha256"], str)
            or not _HASH.fullmatch(raw["checkpoint_sha256"])):
        raise RecordedProbeError("invalid_recorded_probe")
    try:
        normalized = normalize_mac(raw["camera_mac"])
        selected = normalize_mac(camera_mac)
        RFDetrNanoDetector(object(), threshold=raw["threshold"])
    except (IngressError, DetectionError, KeyError, TypeError):
        raise RecordedProbeError("invalid_recorded_probe") from None
    if normalized != selected:
        raise RecordedProbeError("camera_mismatch")
    parent = state_dir / "recorded-probe"
    try:
        info = parent.lstat()
    except OSError as exc:
        raise RecordedProbeError("recorded_probe_state_unavailable") from exc
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise RecordedProbeError("recorded_probe_state_unavailable")
    frames = []
    for item in raw["frames"]:
        if (not isinstance(item, dict)
                or set(item) != {"path", "sha256", "captured_ms"}
                or not isinstance(item["path"], str)
                or not isinstance(item["sha256"], str)
                or not _HASH.fullmatch(item["sha256"])
                or type(item["captured_ms"]) is not int):
            raise RecordedProbeError("invalid_recorded_probe")
        path = Path(item["path"])
        if (not path.is_absolute() or path.parent != parent
                or path.suffix.lower() not in {".jpg", ".jpeg"}
                or path.name in {".", ".."}):
            raise RecordedProbeError("invalid_recorded_probe_path")
        frames.append(RecordedFrame(path, item["sha256"].lower(),
                                    item["captured_ms"]))
    first, second = frames
    if (first.path == second.path or not first.captured_ms < second.captured_ms
            or second.captured_ms - first.captured_ms > 3000
            or not now_ms - _MAX_AGE_MS <= first.captured_ms
            or second.captured_ms > now_ms - 60_000):
        raise RecordedProbeError("invalid_recorded_probe_time")
    return RecordedProbe(normalized, raw["nonce"], (first, second),
                         raw["checkpoint_path"],
                         raw["checkpoint_sha256"].lower(),
                         float(raw["threshold"]))


def _read_frame(frame: RecordedFrame) -> bytes:
    try:
        fd = os.open(frame.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                    or not 0 < info.st_size <= _MAX_FRAME_BYTES):
                raise RecordedProbeError("recorded_probe_frame_unavailable")
            data = source.read(_MAX_FRAME_BYTES + 1)
    except OSError as exc:
        raise RecordedProbeError("recorded_probe_frame_unavailable") from exc
    if (len(data) > _MAX_FRAME_BYTES
            or not hmac.compare_digest(hashlib.sha256(data).hexdigest(),
                                       frame.sha256)):
        raise RecordedProbeError("recorded_probe_frame_mismatch")
    return data


def infer_recorded_person(probe: RecordedProbe) -> TrackChange:
    """Return one confirmed person enter or fail closed; never retain frames."""
    first, second = (_read_frame(frame) for frame in probe.frames)
    detector = RFDetrNanoDetector.from_checkpoint(
        probe.checkpoint_path, probe.checkpoint_sha256,
        threshold=probe.threshold)
    tracker = TemporalTracker()
    changes = []
    for frame, meta in zip((first, second), probe.frames, strict=True):
        observations = detector.detect(frame)
        people = tuple(item for item in observations if item.kind == "person")
        changes.extend(tracker.update(people, now=meta.captured_ms / 1000))
    enter = [change for change in changes if change.edge == "enter"
             and change.kind == "person"]
    if len(enter) != 1 or not math.isfinite(enter[0].score):
        raise RecordedProbeError("recorded_person_unconfirmed")
    return enter[0]
