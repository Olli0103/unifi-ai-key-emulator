"""Isolated per-camera smart-policy and track decisions for AI Port.

This engine consumes model observations, not media or raw controller payloads.
It emits bounded event candidates. A separate transport must validate and send
them before any native Protect result can be claimed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import math

from .aiport_detection import ObjectObservation
from .aiport_event_budget import EventBudget, EventBudgetError
from .aiport_ingest import IngressError, normalize_mac
from .aiport_smart_settings import SmartPolicy
from .aiport_tracking import (
    TemporalTracker, TrackChange, TrackingError, _iou, validate_observation,
)


_OBJECT_KINDS = frozenset({"person", "vehicle", "animal", "package"})
_KIND_ORDER = ("person", "vehicle", "animal", "package")
# A package stays put, so a later sparse sample would re-detect it as a new
# track. Protect saves each package as its own one-shot event.
_PACKAGE_COOLDOWN_SECONDS = 1800.0
# A package first confirmed in night IR is held: the Flur cat was read as a
# package in IR, while a parcel stays put. It is announced once it stays in
# place this long (or is seen in colour); an animal sighting makes it Animal.
_IR_PACKAGE_DWELL_SECONDS = 20.0
_IR_PACKAGE_STATIONARY_IOU = 0.5


@dataclass(frozen=True)
class CameraEventCandidate:
    camera_mac: str
    change: TrackChange
    zone_ids: tuple[int, ...]


class CameraPolicyEngine:
    """One independent policy and tracker per explicitly allowed camera."""

    def __init__(self, camera_macs: list[str], *, max_events_per_camera: int = 1,
                 event_window_seconds: float | None = None,
                 event_budget: EventBudget | None = None,
                 max_track_gap_seconds: float = 3.0,
                 max_center_distance: float | None = None,
                 package_cooldown: EventBudget | None = None):
        if (not isinstance(camera_macs, list) or not 1 <= len(camera_macs) <= 5
                or type(max_events_per_camera) is not int
                or not 1 <= max_events_per_camera <= 3600
                or event_window_seconds is not None
                and (type(event_window_seconds) not in (int, float)
                     or not math.isfinite(event_window_seconds)
                     or not 60 <= event_window_seconds <= 86_400)
                or event_budget is not None and not isinstance(event_budget, EventBudget)
                or package_cooldown is not None
                and not isinstance(package_cooldown, EventBudget)
                or type(max_track_gap_seconds) not in (int, float)
                or not math.isfinite(max_track_gap_seconds)
                or not 0.5 <= max_track_gap_seconds <= 30
                or max_center_distance is not None
                and (type(max_center_distance) not in (int, float)
                     or not math.isfinite(max_center_distance)
                     or not 0 < max_center_distance <= 3)):
            raise IngressError("invalid_camera_engine")
        try:
            cameras = [normalize_mac(value) for value in camera_macs]
        except IngressError as exc:
            raise IngressError("invalid_camera_engine") from exc
        if len(set(cameras)) != len(cameras):
            raise IngressError("duplicate_camera")
        self._cameras = frozenset(cameras)
        self._camera_order = tuple(cameras)
        self._policies: dict[str, SmartPolicy | None] = {
            camera: None for camera in cameras}
        self._track_gap = float(max_track_gap_seconds)
        self._center_distance = max_center_distance
        self._trackers = {camera: self._new_tracker() for camera in cameras}
        self._association_totals = {camera: {
            "iou_matches": 0, "proximity_matches": 0, "tentative_unmatched": 0,
            "class_resolved": 0,
        } for camera in cameras}
        self._tentative_totals = {camera: dict.fromkeys(_KIND_ORDER, 0)
                                  for camera in cameras}
        self._active: dict[str, dict[int, tuple[TrackChange, tuple[int, ...]]]] = {
            camera: {} for camera in cameras}
        self._event_counts = {camera: 0 for camera in cameras}
        self._event_times: dict[str, deque[float]] = {
            camera: deque() for camera in cameras}
        self._eligible_observations = dict.fromkeys(cameras, 0)
        self._score_eligible_observations = dict.fromkeys(cameras, 0)
        self._eligible_frames = dict.fromkeys(cameras, 0)
        self._entered_by_kind = {camera: dict.fromkeys(_KIND_ORDER, 0)
                                 for camera in cameras}
        self._rejected_by_kind = {camera: dict.fromkeys(_KIND_ORDER, 0)
                                  for camera in cameras}
        self._last_package_at: dict[str, float] = {}
        self._zone_rejections = {camera: {
            "excluded": 0, "no_class_zone": 0, "outside_zone": 0,
            "below_overlap": 0,
        } for camera in cameras}
        self._zone_overlap_bands = {camera: {
            "trace_under_50": 0, "partial_50_to_80": 0,
            "rejected_at_least_80": 0,
        } for camera in cameras}
        self._last_moving: dict[str, dict[int, float]] = {
            camera: {} for camera in cameras}
        # track_id -> (since, box) of night-IR packages not yet announced.
        self._ir_held: dict[str, dict[int, tuple[float, tuple[float, ...]]]] = {
            camera: {} for camera in cameras}
        self._generations = dict.fromkeys(cameras, 0)
        self.policy_repeats = 0
        self.package_cooldown_skips = 0
        self.package_ir_held = 0
        self.package_ir_confirmed = 0
        self.package_ir_as_animal = 0
        self.package_ir_dropped = 0
        self._max_events = max_events_per_camera
        self._event_window_seconds = event_window_seconds
        self._event_budget = event_budget
        self._package_cooldown = package_cooldown

    def _new_tracker(self) -> TemporalTracker:
        return TemporalTracker(max_gap_seconds=self._track_gap,
                               max_center_distance=self._center_distance)

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
                or not policy.enabled_types
                or not policy.enabled_types <= _OBJECT_KINDS)):
            raise IngressError("invalid_camera_policy")
        if policy is not None and policy == self._policies[camera]:
            # Protect re-sends identical settings several times after an AI
            # Port connects, while the startup pair samples the scene. That
            # pair is the only sample a stationary object (a package, a
            # sleeping cat) gets; resetting here lost its confirmation.
            self.policy_repeats += 1
            return ()
        result = tuple(
            CameraEventCandidate(camera, TrackChange(
                "leave", previous.track_id, previous.kind, previous.label,
                previous.score, previous.box), zones)
            for _, (previous, zones) in sorted(self._active[camera].items()))
        self._active[camera] = {}
        self._last_moving[camera] = {}
        self._ir_held[camera] = {}
        for name, count in self._trackers[camera].stats.items():
            self._association_totals[camera][name] += count
        for kind, count in self._trackers[camera].tentative_by_kind.items():
            self._tentative_totals[camera][kind] += count
        self._trackers[camera] = self._new_tracker()
        self._policies[camera] = policy
        self._generations[camera] += 1
        return result

    def observe(self, camera_mac: str, observations: tuple[ObjectObservation, ...],
                *, now: float, infrared: bool = False) -> tuple[CameraEventCandidate, ...]:
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
        score_selected = tuple(value for value in observations
                               if policy.allows_score(value.kind, value.score))
        self._score_eligible_observations[camera] += len(score_selected)
        selected_values = []
        for value in score_selected:
            if policy.zone_ids(value.kind, value.box) is not None:
                selected_values.append(value)
                continue
            if any(value.kind in zone.object_types
                   and zone.overlaps_box(value.box)
                   for zone in policy.exclude_zones):
                reason = "excluded"
            else:
                matching = policy.zones_for(value.kind)
                if not matching:
                    reason = "no_class_zone"
                else:
                    overlap = max(zone.overlap_ratio(value.box)
                                  for zone in matching)
                    reason = "below_overlap" if overlap > 0 else "outside_zone"
                    if overlap > 0:
                        band = ("rejected_at_least_80" if overlap >= 0.8 else
                                "partial_50_to_80" if overlap >= 0.5 else
                                "trace_under_50")
                        self._zone_overlap_bands[camera][band] += 1
            self._zone_rejections[camera][reason] += 1
            self._rejected_by_kind[camera][value.kind] += 1
        selected = tuple(selected_values)
        self._eligible_observations[camera] += len(selected)
        self._eligible_frames[camera] += bool(selected)
        changes = self._trackers[camera].update(selected, now=now)
        if self._event_window_seconds is not None:
            cutoff = now - self._event_window_seconds
            event_times = self._event_times[camera]
            while event_times and event_times[0] <= cutoff:
                event_times.popleft()
        result = []
        for change in changes:
            if change.kind not in policy.enabled_types:
                continue
            active = self._active[camera].get(change.track_id)
            budget_used = (len(self._event_times[camera])
                           if self._event_window_seconds is not None
                           else self._event_counts[camera])
            held = self._ir_held[camera]
            if change.track_id in held and active is None:
                since, box = held[change.track_id]
                if change.edge == "leave":
                    del held[change.track_id]
                    self.package_ir_dropped += 1
                    continue
                if change.kind == "animal":
                    # The tracker resolved the held package track as a cat.
                    del held[change.track_id]
                    self.package_ir_as_animal += 1
                elif infrared and (
                        _iou(box, change.box) < _IR_PACKAGE_STATIONARY_IOU
                        or now - since < _IR_PACKAGE_DWELL_SECONDS):
                    if _iou(box, change.box) < _IR_PACKAGE_STATIONARY_IOU:
                        held[change.track_id] = (now, change.box)  # it moved
                    continue
                else:
                    # Stationary through the dwell, or seen in colour.
                    del held[change.track_id]
                    self._trackers[camera].hold(change.track_id, False)
                    self.package_ir_confirmed += 1
                    change = replace(change, edge="enter")
            elif (change.kind == "package" and change.edge == "enter"
                    and active is None and infrared):
                # Every indoor Package in night IR so far was the user's cat
                # (Flur, Esszimmer); 0 of 24 real parcels in 30 days were IR.
                held[change.track_id] = (now, change.box)
                self._trackers[camera].hold(change.track_id)
                self.package_ir_held += 1
                continue
            if (change.kind == "package" and change.edge == "enter"
                    and active is None):
                # Protect 7.3.68 resolves an AI Port's packageDetected edge by
                # the AI Port's own MAC ("Camera not found"); only the
                # enter/moving/leave lifecycle is routed by deviceID. A parcel
                # re-sampled later is not a new delivery.
                last = self._last_package_at.get(camera)
                if last is not None and now - last < _PACKAGE_COOLDOWN_SECONDS:
                    continue
                if self._package_cooldown is not None:
                    try:
                        cooling = self._package_cooldown.remaining(camera) == 0
                    except EventBudgetError:
                        cooling = True  # fail closed: no duplicate parcel event
                    if cooling:
                        self.package_cooldown_skips += 1
                        continue
            if (change.edge == "enter" and active is None
                    and budget_used < self._max_events):
                zones = policy.zone_ids(change.kind, change.box)
                if (zones is not None and (self._event_budget is None
                                           or self._event_budget.claim(camera))):
                    self._active[camera][change.track_id] = (change, zones)
                    self._last_moving[camera][change.track_id] = now
                    self._event_counts[camera] += 1
                    self._entered_by_kind[camera][change.kind] += 1
                    if change.kind == "package":
                        self._last_package_at[camera] = now
                        if self._package_cooldown is not None:
                            try:
                                self._package_cooldown.claim(camera)
                            except EventBudgetError:
                                pass  # the in-memory cooldown still holds
                    if self._event_window_seconds is not None:
                        self._event_times[camera].append(now)
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

    def needs_confirmation(self, camera_mac: str) -> bool:
        """Whether this camera's last sample left an unconfirmed object."""
        camera = self._camera(camera_mac)
        return self._policies[camera] is not None and self._trackers[camera].has_tentative

    def has_policy(self, camera_mac: str) -> bool:
        return self._policies[self._camera(camera_mac)] is not None

    def current_policy(self, camera_mac: str) -> SmartPolicy | None:
        return self._policies[self._camera(camera_mac)]

    def policy_generation(self, camera_mac: str) -> int:
        """Tag frames so a policy change cannot consume an older model result."""
        return self._generations[self._camera(camera_mac)]

    def camera_snapshot(self, *, now: float) -> tuple[dict[str, object], ...]:
        """Policy counters in config order, without camera identifiers."""
        if type(now) not in (int, float) or not math.isfinite(now):
            raise IngressError("invalid_camera_engine")
        result = []
        for camera in self._camera_order:
            if self._event_window_seconds is None:
                used = self._event_counts[camera]
            else:
                cutoff = now - self._event_window_seconds
                used = sum(at > cutoff for at in self._event_times[camera])
            budget_healthy = True
            if self._event_budget is not None:
                try:
                    used = max(used, self._max_events
                               - self._event_budget.remaining(camera))
                except EventBudgetError:
                    used = self._max_events
                    budget_healthy = False
            policy = self._policies[camera]
            result.append({
                "policy_enabled": policy is not None,
                "policy_enabled_types": ([kind for kind in _KIND_ORDER
                                          if kind in policy.enabled_types]
                                         if policy is not None else []),
                "score_eligible_observations": self._score_eligible_observations[camera],
                "eligible_observations": self._eligible_observations[camera],
                "eligible_frames": self._eligible_frames[camera],
                "zone_rejections": dict(self._zone_rejections[camera]),
                "zone_overlap_bands": dict(self._zone_overlap_bands[camera]),
                "events_entered_by_kind": dict(self._entered_by_kind[camera]),
                "unconfirmed_by_kind": {
                    kind: count + self._trackers[camera].tentative_by_kind[kind]
                    for kind, count in self._tentative_totals[camera].items()},
                "zone_rejections_by_kind": dict(self._rejected_by_kind[camera]),
                "package_scope": policy.package_scope if policy is not None else None,
                "secondary_lens": ({
                    "zones": len(policy.secondary_lens_zones),
                    "classes": sorted(set().union(*(
                        zone.object_types for zone in policy.secondary_lens_zones))),
                    "processed_by": "camera",
                } if policy is not None and policy.secondary_lens_zones else None),
                "track_associations": {
                    name: count + self._trackers[camera].stats[name]
                    for name, count in self._association_totals[camera].items()},
                "events_entered": self._event_counts[camera],
                "active_tracks": len(self._active[camera]),
                "event_budget_remaining": max(0, self._max_events - used),
                "event_budget_healthy": budget_healthy,
            })
        return tuple(result)
