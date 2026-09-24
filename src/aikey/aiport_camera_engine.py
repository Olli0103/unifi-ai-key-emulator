"""Isolated per-camera smart-policy and track decisions for AI Port.

This engine consumes model observations, not media or raw controller payloads.
It emits bounded event candidates. A separate transport must validate and send
them before any native Protect result can be claimed.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .aiport_detection import ObjectObservation
from .aiport_ingest import IngressError, normalize_mac
from .aiport_smart_settings import SmartPolicy
from .aiport_tracking import (
    TemporalTracker, TrackChange, TrackingError, validate_observation,
)


@dataclass(frozen=True)
class CameraEventCandidate:
    camera_mac: str
    change: TrackChange
    zone_ids: tuple[int, ...]


class CameraPolicyEngine:
    """One independent policy and tracker per explicitly allowed camera."""

    def __init__(self, camera_macs: list[str], *, max_events_per_camera: int = 1):
        if (not isinstance(camera_macs, list) or not 1 <= len(camera_macs) <= 5
                or type(max_events_per_camera) is not int
                or not 1 <= max_events_per_camera <= 100):
            raise IngressError("invalid_camera_engine")
        try:
            cameras = [normalize_mac(value) for value in camera_macs]
        except IngressError as exc:
            raise IngressError("invalid_camera_engine") from exc
        if len(set(cameras)) != len(cameras):
            raise IngressError("duplicate_camera")
        self._cameras = frozenset(cameras)
        self._policies: dict[str, SmartPolicy | None] = {
            camera: None for camera in cameras}
        self._trackers = {camera: TemporalTracker() for camera in cameras}
        self._active: dict[str, dict[int, tuple[TrackChange, tuple[int, ...]]]] = {
            camera: {} for camera in cameras}
        self._event_counts = {camera: 0 for camera in cameras}
        self._last_moving: dict[str, dict[int, float]] = {
            camera: {} for camera in cameras}
        self._generations = dict.fromkeys(cameras, 0)
        self._max_events = max_events_per_camera

    def _camera(self, camera_mac: str) -> str:
        camera = normalize_mac(camera_mac)
        if camera not in self._cameras:
            raise IngressError("camera_not_authorized")
        return camera

    def replace_policy(self, camera_mac: str,
                       policy: SmartPolicy | None) -> tuple[CameraEventCandidate, ...]:
        """Close this camera's active event, then replace only its policy."""
        camera = self._camera(camera_mac)
        if (policy is not None and (not isinstance(policy, SmartPolicy)
                or policy.camera_mac != camera
                or not 1 <= len(policy.enabled_types) <= 3
                or not policy.enabled_types <= {"person", "vehicle", "animal"})):
            raise IngressError("invalid_camera_policy")
        result = tuple(
            CameraEventCandidate(camera, TrackChange(
                "leave", previous.track_id, previous.kind, previous.label,
                previous.score, previous.box), zones)
            for _, (previous, zones) in sorted(self._active[camera].items()))
        self._active[camera] = {}
        self._last_moving[camera] = {}
        self._trackers[camera] = TemporalTracker()
        self._policies[camera] = policy
        self._generations[camera] += 1
        return result

    def observe(self, camera_mac: str, observations: tuple[ObjectObservation, ...],
                *, now: float) -> tuple[CameraEventCandidate, ...]:
        camera = self._camera(camera_mac)
        if (not isinstance(observations, tuple) or len(observations) > 100
                or any(not isinstance(value, ObjectObservation)
                       for value in observations)
                or type(now) not in (int, float) or not math.isfinite(now)):
            raise TrackingError("invalid_tracking_observation")
        for observation in observations:
            validate_observation(observation)
        policy = self._policies[camera]
        if policy is None:
            return ()
        selected = tuple(value for value in observations
                         if policy.allows_score(value.kind, value.score)
                         and policy.zone_ids(value.kind, value.box) is not None)
        changes = self._trackers[camera].update(selected, now=now)
        result = []
        for change in changes:
            if change.kind not in policy.enabled_types:
                continue
            active = self._active[camera].get(change.track_id)
            if (change.edge == "enter" and active is None
                    and self._event_counts[camera] < self._max_events):
                zones = policy.zone_ids(change.kind, change.box)
                if zones is not None:
                    self._active[camera][change.track_id] = (change, zones)
                    self._last_moving[camera][change.track_id] = now
                    self._event_counts[camera] += 1
                    result.append(CameraEventCandidate(camera, change, zones))
            elif (change.edge == "moving" and active is not None
                  and active[0].kind == change.kind
                  and now - self._last_moving[camera][change.track_id] >= 1):
                zones = policy.zone_ids(change.kind, change.box)
                if zones == active[1]:
                    self._active[camera][change.track_id] = (change, zones)
                    self._last_moving[camera][change.track_id] = now
                    result.append(CameraEventCandidate(camera, change, zones))
            elif (change.edge == "leave" and active is not None
                  and active[0].kind == change.kind):
                del self._active[camera][change.track_id]
                del self._last_moving[camera][change.track_id]
                result.append(CameraEventCandidate(camera, change, active[1]))
        return tuple(result)

    def has_policy(self, camera_mac: str) -> bool:
        return self._policies[self._camera(camera_mac)] is not None

    def policy_generation(self, camera_mac: str) -> int:
        """Tag frames so a policy change cannot consume an older model result."""
        return self._generations[self._camera(camera_mac)]
