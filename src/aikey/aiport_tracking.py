"""Bounded in-memory tracks for local AI Port object observations.

Track changes are internal evidence, not Protect smart events. A native event
needs a validated camera policy, wall-clock alignment and controller acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .aiport_detection import ObjectObservation


_KINDS = frozenset({"person", "vehicle", "animal"})
_MAX_OBSERVATIONS_PER_FRAME = 100


class TrackingError(ValueError):
    """Fixed failure code without media, object coordinates or camera data."""


@dataclass(frozen=True)
class TrackChange:
    edge: str
    track_id: int
    kind: str
    label: str
    score: float
    box: tuple[float, float, float, float]


@dataclass
class _Track:
    track_id: int
    observation: ObjectObservation
    last_seen: float
    hits: int = 1
    active: bool = False

    def change(self, edge: str) -> TrackChange:
        return TrackChange(edge, self.track_id, self.observation.kind,
                           self.observation.label, self.observation.score,
                           self.observation.box)


def _check_observation(value: object) -> None:
    if not isinstance(value, ObjectObservation) or value.kind not in _KINDS:
        raise TrackingError("invalid_tracking_observation")
    if (not isinstance(value.label, str) or not 1 <= len(value.label) <= 32
            or type(value.score) not in (int, float)
            or not math.isfinite(value.score) or not 0 <= value.score <= 1):
        raise TrackingError("invalid_tracking_observation")
    box = value.box
    if (not isinstance(box, tuple) or len(box) != 4
            or any(type(point) not in (int, float) or not math.isfinite(point)
                   for point in box)):
        raise TrackingError("invalid_tracking_observation")
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise TrackingError("invalid_tracking_observation")


def _iou(first: tuple[float, float, float, float],
         second: tuple[float, float, float, float]) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    if intersection == 0:
        return 0.0
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / (first_area + second_area - intersection)


class TemporalTracker:
    """Match observations by class and overlap, then require repeated evidence.

    ``update`` accepts one frame's observations at a nondecreasing monotonic
    timestamp. A track enters after ``min_hits`` consecutive matching frames.
    An active track leaves once its last sighting exceeds ``max_gap_seconds``.
    Tentative tracks disappear on a missed frame. Nothing is persisted.
    """

    def __init__(self, *, min_hits: int = 2, max_gap_seconds: float = 3.0,
                 iou_threshold: float = 0.25, max_tracks: int = 32):
        if (type(min_hits) is not int or not 2 <= min_hits <= 10
                or type(max_gap_seconds) not in (int, float)
                or not math.isfinite(max_gap_seconds)
                or not 0.5 <= max_gap_seconds <= 30
                or type(iou_threshold) not in (int, float)
                or not math.isfinite(iou_threshold)
                or not 0 < iou_threshold < 1
                or type(max_tracks) is not int or not 1 <= max_tracks <= 100):
            raise TrackingError("invalid_tracking_policy")
        self.min_hits = min_hits
        self.max_gap_seconds = float(max_gap_seconds)
        self.iou_threshold = float(iou_threshold)
        self.max_tracks = max_tracks
        self._tracks: dict[int, _Track] = {}
        self._next_id = 1
        self._last_update: float | None = None

    def update(self, observations: tuple[ObjectObservation, ...], *,
               now: float) -> tuple[TrackChange, ...]:
        if (type(now) not in (int, float) or not math.isfinite(now)
                or (self._last_update is not None and now < self._last_update)):
            raise TrackingError("invalid_tracking_time")
        if not isinstance(observations, tuple):
            raise TrackingError("invalid_tracking_observation")
        if len(observations) > _MAX_OBSERVATIONS_PER_FRAME:
            raise TrackingError("tracking_capacity_exceeded")
        for observation in observations:
            _check_observation(observation)

        changes = []
        for track_id, track in tuple(self._tracks.items()):
            if now - track.last_seen > self.max_gap_seconds:
                if track.active:
                    changes.append(track.change("leave"))
                del self._tracks[track_id]

        candidates = []
        for index, observation in enumerate(observations):
            for track_id, track in self._tracks.items():
                if observation.kind != track.observation.kind:
                    continue
                overlap = _iou(observation.box, track.observation.box)
                if overlap >= self.iou_threshold:
                    candidates.append((-overlap, track_id, index))
        matched_tracks: set[int] = set()
        matched_observations: set[int] = set()
        for _, track_id, index in sorted(candidates):
            if track_id in matched_tracks or index in matched_observations:
                continue
            matched_tracks.add(track_id)
            matched_observations.add(index)
            track = self._tracks[track_id]
            track.observation = observations[index]
            track.last_seen = now
            track.hits += 1
            if track.active:
                changes.append(track.change("moving"))
            elif track.hits >= self.min_hits:
                track.active = True
                changes.append(track.change("enter"))

        for track_id, track in tuple(self._tracks.items()):
            if track_id not in matched_tracks and not track.active:
                del self._tracks[track_id]
        for index, observation in enumerate(observations):
            if index in matched_observations:
                continue
            if len(self._tracks) >= self.max_tracks:
                # Existing confirmed tracks take priority over new candidates.
                # A crowded frame must not disable the camera's observer.
                continue
            track_id = self._next_id
            self._next_id += 1
            self._tracks[track_id] = _Track(track_id, observation, now)
        self._last_update = now
        return tuple(changes)
