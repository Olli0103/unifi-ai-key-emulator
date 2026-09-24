"""Bounded smart-detection snapshots for a paired AI Port camera."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import ipaddress
import re
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

from .aiport_tracking import TrackChange


class SnapshotError(ValueError):
    """A snapshot or controller upload request failed validation."""


_UPLOAD_PATH = re.compile(
    r"/internal/camera-upload/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")


@dataclass(frozen=True)
class SmartSnapshot:
    filename: str
    jpeg: bytes
    metadata: dict
    full_fov_filename: str
    full_fov_jpeg: bytes
    full_fov_width: int
    full_fov_height: int

    def add_to_event(self, payload: dict) -> None:
        payload["smartDetectSnapshots"] = [self.metadata]
        payload["smartDetectSnapshotFullFoV"] = self.full_fov_filename
        payload["smartDetectSnapshotFullFoVWidth"] = self.full_fov_width
        payload["smartDetectSnapshotFullFoVHeight"] = self.full_fov_height


def make_smart_snapshot(frame: bytes, change: TrackChange, wall_ms: int) -> SmartSnapshot:
    """Keep one cropped JPEG in memory until Protect requests it."""
    if not isinstance(frame, bytes) or len(frame) > 2_000_000 or len(frame) < 16:
        raise SnapshotError("invalid_snapshot_frame")
    if not isinstance(change, TrackChange) or change.kind not in {"person", "vehicle", "animal"}:
        raise SnapshotError("invalid_snapshot_track")
    if type(wall_ms) is not int or wall_ms <= 0:
        raise SnapshotError("invalid_snapshot_time")
    try:
        with Image.open(BytesIO(frame)) as image:
            if image.format != "JPEG" or not 1 <= image.width <= 4096 or not 1 <= image.height <= 4096:
                raise SnapshotError("invalid_snapshot_frame")
            image.load()
            x1, y1, x2, y2 = change.box
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                raise SnapshotError("invalid_snapshot_track")
            # Preserve some context around the detection, as stock cameras do.
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            side = max((x2 - x1) * image.width, (y2 - y1) * image.height) * 1.25
            side = max(32, min(side, image.width, image.height))
            left = max(0, min(image.width - side, cx * image.width - side / 2))
            top = max(0, min(image.height - side, cy * image.height - side / 2))
            crop = image.crop((round(left), round(top), round(left + side), round(top + side)))
            crop.thumbnail((360, 360))
            output = BytesIO()
            crop.convert("RGB").save(output, format="JPEG", quality=85)
            full_fov = BytesIO()
            image.convert("RGB").save(full_fov, format="JPEG", quality=85)
            full_fov_width, full_fov_height = image.size
    except (OSError, UnidentifiedImageError) as exc:
        raise SnapshotError("invalid_snapshot_frame") from exc
    jpeg = output.getvalue()
    if len(jpeg) > 2_000_000:
        raise SnapshotError("snapshot_too_large")
    full_fov_jpeg = full_fov.getvalue()
    if len(full_fov_jpeg) > 2_000_000:
        raise SnapshotError("snapshot_too_large")
    filename = f"smartdetectsnap_zone_{change.track_id}{wall_ms}.jpg"
    full_fov_filename = f"smartdetectsnap_zone_{change.track_id}{wall_ms}_fullfov.jpg"
    metadata = {
        "clockBestWall": wall_ms,
        "smartDetectSnapshot": filename,
        "smartDetectSnapshotType": change.kind,
        "smartDetectSnapshotName": "",
        "smartDetectSnapshotWidth": crop.width,
        "smartDetectSnapshotHeight": crop.height,
        "trackerID": change.track_id,
        "confidenceLevel": round(change.score * 100),
        "coord": [round(x1 * 1000), round(y1 * 1000),
                  round((x2 - x1) * 1000), round((y2 - y1) * 1000)],
        "reVerifyEligible": False,
    }
    return SmartSnapshot(filename, jpeg, metadata, full_fov_filename,
                         full_fov_jpeg, full_fov_width, full_fov_height)


def validated_upload_url(payload: object, *, controller_ip: str, filename: str,
                         what: str = "smartDetectZoneSnapshot") -> str:
    """Accept only Protect's one-use snapshot endpoint on the pinned controller."""
    if (what not in {"smartDetectZoneSnapshot", "smartDetectZoneSnapshotFullFoV"}
            or not isinstance(payload, dict) or payload.get("what") != what):
        raise SnapshotError("unsupported_snapshot_request")
    if payload.get("filename") != filename or payload.get("quality") not in (None, "medium"):
        raise SnapshotError("unexpected_snapshot_request")
    timeout_ms = payload.get("timeoutMs", 60_000)
    if type(timeout_ms) is not int or not 0 < timeout_ms <= 60_000:
        raise SnapshotError("invalid_snapshot_timeout")
    uri = payload.get("uri")
    if not isinstance(uri, str) or len(uri) > 256:
        raise SnapshotError("invalid_snapshot_url")
    try:
        parsed = urlsplit(uri)
        expected = ipaddress.IPv4Address(controller_ip)
        actual = ipaddress.IPv4Address(parsed.hostname or "")
        port = parsed.port
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise SnapshotError("invalid_snapshot_url") from exc
    if (parsed.scheme != "https" or actual != expected or port != 6666
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not _UPLOAD_PATH.fullmatch(parsed.path)):
        raise SnapshotError("invalid_snapshot_url")
    return uri
