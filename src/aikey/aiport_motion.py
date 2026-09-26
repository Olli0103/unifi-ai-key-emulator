"""Zone-scoped local motion events for AI Port paired cameras.

Protect drops a paired camera's own motion events and expects the AI Port to
send ``EventSmartMotion`` instead (static Protect 7.2.105). Protect configures
it with ``ChangeSmartMotionSettings``: an enable flag, linger timings and the
camera's motion zones. This detector compares small grayscale thumbnails
inside those zones only. It never calls a paid model, keeps no frames beyond
one thumbnail background per camera, and exposes only counters.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
import re

from PIL import Image, UnidentifiedImageError

from .aiport_ingest import IngressError, normalize_mac


_GRID_W, _GRID_H = 64, 36
_PIXEL_DELTA = 18
# A zone at level 0 (sensitivity 100) reacts to 0.2% of its cells; level
# 100 (sensitivity 0) needs 3.2%, so sensitivity 50 needs 1.7%: roughly a
# distant person in a full-frame zone. The mapping is local, not Protect's.
_MIN_FRACTION, _FRACTION_RANGE = 0.002, 0.03
# At 2 fps a moving person can leave one sample unchanged. Such a short gap
# neither restarts the start linger nor counts as quiet time.
_START_GAP_SECONDS = 1.0
# Protect's own cameras merge intermittent movement into one event; a short
# stop delay alone split a seated person into an event every few seconds.
_MIN_STOP_HOLD_MS = 8000
_BANDS = (("under_0_2", 0.002), ("0_2_to_1", 0.01), ("1_to_3", 0.03),
          ("3_to_10", 0.1), ("over_10", math.inf))
# Most of the frame changing at once is an exposure, IR or codec jump.
_SCENE_CHANGE_FRACTION = 0.6
_BACKGROUND_WEIGHT = 0.15
_ZONE_ID = re.compile(r"[0-9]{1,10}")
_MAX_ZONES, _MAX_VERTICES = 16, 32


class MotionSettingsError(ValueError):
    """Fixed failure code without camera identity or zone geometry."""


@dataclass(frozen=True)
class MotionZone:
    zone_id: int
    level: int
    cells: frozenset[int]


@dataclass(frozen=True)
class MotionPolicy:
    camera_mac: str
    enabled: bool
    linger_start_ms: int
    linger_stop_ms: int
    max_duration_ms: int
    zones: tuple[MotionZone, ...]


def _inside(x: float, y: float, points: list[tuple[float, float]]) -> bool:
    result = False
    j = len(points) - 1
    for i, (xi, yi) in enumerate(points):
        xj, yj = points[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            result = not result
        j = i
    return result


def _zone_cells(points: list[tuple[float, float]]) -> frozenset[int]:
    return frozenset(
        row * _GRID_W + column
        for row in range(_GRID_H) for column in range(_GRID_W)
        if _inside((column + 0.5) / _GRID_W, (row + 0.5) / _GRID_H, points))


def parse_motion_settings(payload: object, *, camera_mac: str) -> MotionPolicy:
    """Validate the exact enhanced-motion envelope Protect sends an AI Port."""
    if (not isinstance(payload, dict) or set(payload) != {
            "algoVersion", "deviceID", "enable", "eventMaxDurationMSec",
            "bgmodel", "lingerEventStartMSec", "lingerEventStopMSec", "zones"}
            or payload["algoVersion"] != "beta" or payload["bgmodel"] != "default"
            or type(payload["enable"]) is not bool):
        raise MotionSettingsError("invalid_motion_settings")
    try:
        expected = normalize_mac(camera_mac)
        if normalize_mac(payload["deviceID"]) != expected:
            raise MotionSettingsError("wrong_camera")
    except IngressError as exc:
        raise MotionSettingsError("invalid_motion_settings") from exc
    duration = payload["eventMaxDurationMSec"]
    start, stop = payload["lingerEventStartMSec"], payload["lingerEventStopMSec"]
    if (type(duration) is not int or not 1000 <= duration <= 86_400_000
            or any(type(value) is not int or not 0 <= value <= 120_000
                   for value in (start, stop))):
        raise MotionSettingsError("invalid_motion_settings")
    raw_zones = payload["zones"]
    if not isinstance(raw_zones, dict) or len(raw_zones) > _MAX_ZONES:
        raise MotionSettingsError("invalid_motion_zone")
    zones = []
    for raw_id, data in raw_zones.items():
        if (not isinstance(raw_id, str) or _ZONE_ID.fullmatch(raw_id) is None
                or int(raw_id) > 4_294_967_295 or not isinstance(data, dict)
                or not {"coord", "level"} <= set(data)
                or not set(data) <= {"coord", "level", "triggerLight"}):
            raise MotionSettingsError("invalid_motion_zone")
        level, coords = data["level"], data["coord"]
        if (type(level) not in (int, float) or not math.isfinite(level)
                or not 0 <= level <= 100
                or "triggerLight" in data and type(data["triggerLight"]) is not bool
                or not isinstance(coords, list)
                or not 6 <= len(coords) <= 2 * _MAX_VERTICES or len(coords) % 2
                or any(type(number) not in (int, float) or not math.isfinite(number)
                       or not 0 <= number <= 1000 for number in coords)):
            raise MotionSettingsError("invalid_motion_zone")
        points = [(coords[i] / 1000, coords[i + 1] / 1000)
                  for i in range(0, len(coords), 2)]
        cells = _zone_cells(points)
        if cells:
            zones.append(MotionZone(int(raw_id), round(level), cells))
    return MotionPolicy(expected, payload["enable"], start, stop, duration,
                        tuple(sorted(zones, key=lambda zone: zone.zone_id)))


@dataclass(frozen=True)
class MotionEdge:
    edge: str
    levels: dict[str, int]


class MotionDetector:
    """One camera's zone-scoped frame-difference motion state machine."""

    def __init__(self, policy: MotionPolicy):
        if not isinstance(policy, MotionPolicy):
            raise MotionSettingsError("invalid_motion_settings")
        self.policy = policy
        self._background: list[float] | None = None
        self._moving_since: float | None = None
        self._last_motion: float | None = None
        self._started_at: float | None = None
        self._peak: dict[str, int] = {}
        self.frames = 0
        self.scene_changes = 0
        # Count-only histogram of the largest per-zone change fraction.
        self.change_bands = dict.fromkeys((name for name, _ in _BANDS), 0)
        self.starts = 0
        self.stops = 0

    @property
    def active(self) -> bool:
        return self._started_at is not None

    def _thumbnail(self, frame: bytes) -> bytes:
        try:
            with Image.open(BytesIO(frame)) as image:
                if (image.format != "JPEG" or not 16 <= image.width <= 8192
                        or not 16 <= image.height <= 4320):
                    raise ValueError
                image.draft("L", (_GRID_W * 2, _GRID_H * 2))
                return image.convert("L").resize((_GRID_W, _GRID_H)).tobytes()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise MotionSettingsError("invalid_motion_frame") from exc

    def observe(self, frame: bytes, *, now: float) -> tuple[MotionEdge, ...]:
        """Update with one frame at a monotonic time in seconds."""
        if type(now) not in (int, float) or not math.isfinite(now):
            raise MotionSettingsError("invalid_motion_time")
        policy = self.policy
        if not policy.enabled or not policy.zones:
            return self.stop(now=now) if self.active else ()
        thumbnail = self._thumbnail(frame)
        self.frames += 1
        background = self._background
        if background is None:
            self._background = [float(value) for value in thumbnail]
            return ()
        changed = {index for index, value in enumerate(thumbnail)
                   if abs(value - background[index]) >= _PIXEL_DELTA}
        for index, value in enumerate(thumbnail):
            background[index] += _BACKGROUND_WEIGHT * (value - background[index])
        moving_levels: dict[str, int] = {}
        if len(changed) >= _SCENE_CHANGE_FRACTION * len(thumbnail):
            self.scene_changes += 1
            self._background = [float(value) for value in thumbnail]
        else:
            fractions = [len(changed & zone.cells) / len(zone.cells)
                         for zone in policy.zones]
            peak = max(fractions)
            self.change_bands[next(name for name, limit in _BANDS
                                   if peak < limit)] += 1
            for zone, fraction in zip(policy.zones, fractions):
                if fraction >= _MIN_FRACTION + _FRACTION_RANGE * zone.level / 100:
                    moving_levels[str(zone.zone_id)] = min(
                        100, round(100 * fraction / max(_MIN_FRACTION, 0.04)))
        result = []
        if moving_levels:
            self._last_motion = now
            if self._moving_since is None:
                self._moving_since = now
            for zone_id, level in moving_levels.items():
                self._peak[zone_id] = max(self._peak.get(zone_id, 0), level)
            if (not self.active
                    and (now - self._moving_since) * 1000 >= policy.linger_start_ms):
                self._started_at = now
                self.starts += 1
                result.append(MotionEdge("start", dict(self._peak)))
        elif (not self.active and self._last_motion is not None
              and now - self._last_motion > _START_GAP_SECONDS):
            self._moving_since = None
            self._peak = {}
        if self.active and (
                (now - self._last_motion) * 1000
                >= max(policy.linger_stop_ms, _MIN_STOP_HOLD_MS)
                or (now - self._started_at) * 1000 >= policy.max_duration_ms):
            result.extend(self.stop(now=now))
        return tuple(result)

    def stop(self, *, now: float) -> tuple[MotionEdge, ...]:
        """Close an active motion event, e.g. on policy or stream change."""
        if not self.active:
            return ()
        levels = dict(self._peak)
        self._started_at = None
        self._moving_since = None
        self._peak = {}
        self.stops += 1
        return (MotionEdge("stop", levels),)

    def snapshot(self) -> dict[str, object]:
        return {"enabled": self.policy.enabled, "zones": len(self.policy.zones),
                "active": self.active, "frames": self.frames,
                "scene_changes": self.scene_changes,
                "change_bands": dict(self.change_bands),
                "starts": self.starts, "stops": self.stops}


def motion_event_payload(camera_mac: str, edge: MotionEdge, *,
                         clock_wall_ms: int) -> dict:
    """The motion message shape Protect's parser requires."""
    if (not isinstance(edge, MotionEdge) or edge.edge not in {"start", "stop"}
            or type(clock_wall_ms) is not int or clock_wall_ms <= 0):
        raise MotionSettingsError("invalid_motion_event")
    return {"deviceID": normalize_mac(camera_mac),
            "clockBestMonotonic": 0, "clockBestWall": clock_wall_ms,
            "clockMonotonic": 0, "clockStream": 0, "clockStreamRate": 0,
            "clockWall": clock_wall_ms, "edgeType": edge.edge, "eventId": 0,
            "eventType": "motion", "levels": dict(edge.levels),
            "motionHeatmap": "", "motionSnapshot": ""}
