"""Synthetic camera isolation for bounded AI Port object candidates."""

import pytest

from aikey.aiport_camera_engine import CameraPolicyEngine
from aikey.aiport_detection import ObjectObservation
from aikey.aiport_event_budget import EventBudget, EventBudgetError, _HOUR_NS
from aikey.aiport_ingest import IngressError
from aikey.aiport_smart_settings import parse_smart_settings
from aikey.aiport_tracking import TrackingError


FIRST = "2A1122334455"
SECOND = "2A1122334456"
INSIDE = (0.2, 0.2, 0.5, 0.8)
OUTSIDE = (0.05, 0.2, 0.5, 0.8)


def policy(camera, *, zone=False, reverify=False, kind="person"):
    payload = {"deviceID": camera, "algoVersion": "beta",
               "enableSmartDetect": [kind],
               "eventStartMSec": 1000, "eventStopMSec": 3000}
    if zone:
        payload["zones"] = {"7": {
            "coord": [100, 100, 900, 100, 900, 900, 100, 900],
            "objectTypes": [kind], "sensitivity": 50,
            "triggerLight": True, "triggerAccessTypes": []}}
    if reverify:
        payload["reVerificationPolicy"] = {
            name: ({"enable": True, "mode": "custom",
                    "minPresenceProbability": 40, "maxPresenceProbability": 80}
                   if name == kind else {"enable": False})
            for name in ("person", "vehicle", "animal")}
    return parse_smart_settings(payload, camera_mac=camera)


def person(score=0.9, box=INSIDE):
    return ObjectObservation("person", "person", score, box)


def multiclass_policy(camera, kinds=("person", "vehicle", "animal")):
    return parse_smart_settings({
        "deviceID": camera, "algoVersion": "beta",
        "enableSmartDetect": list(kinds),
        "eventStartMSec": 1000, "eventStopMSec": 3000,
    }, camera_mac=camera)


def test_two_cameras_keep_tracks_zones_and_revoke_independent():
    engine = CameraPolicyEngine([FIRST, SECOND])
    engine.replace_policy(FIRST, policy(FIRST, zone=True))
    engine.replace_policy(SECOND, policy(SECOND))
    assert engine.observe(FIRST, (person(),), now=1) == ()
    assert engine.observe(SECOND, (person(),), now=1) == ()
    first, = engine.observe(FIRST, (person(),), now=2)
    second, = engine.observe(SECOND, (person(),), now=2)
    assert (first.camera_mac, first.change.edge, first.zone_ids) == (
        FIRST, "enter", (7,))
    assert (second.camera_mac, second.change.edge, second.zone_ids) == (
        SECOND, "enter", ())
    closed, = engine.replace_policy(FIRST, None)
    assert (closed.camera_mac, closed.change.edge, closed.zone_ids) == (
        FIRST, "leave", (7,))
    assert not engine.has_policy(FIRST)
    assert engine.has_policy(SECOND)
    assert engine.observe(FIRST, (person(),), now=3) == ()
    remaining, = engine.observe(SECOND, (), now=6)
    assert (remaining.camera_mac, remaining.change.edge) == (SECOND, "leave")


def test_zone_and_reverification_are_applied_before_tracking_per_camera():
    engine = CameraPolicyEngine([FIRST, SECOND])
    engine.replace_policy(FIRST, policy(FIRST, zone=True, reverify=True))
    engine.replace_policy(SECOND, policy(SECOND))
    assert engine.observe(FIRST, (person(0.95, OUTSIDE),), now=1) == ()
    assert engine.observe(FIRST, (person(0.7),), now=2) == ()
    assert engine.observe(FIRST, (person(0.9),), now=3) == ()
    first, = engine.observe(FIRST, (person(0.91),), now=4)
    assert first.zone_ids == (7,)
    assert engine.observe(SECOND, (person(0.7),), now=1) == ()
    second, = engine.observe(SECOND, (person(0.7),), now=2)
    assert second.camera_mac == SECOND
    assert second.zone_ids == ()


def test_moving_updates_are_rate_limited_and_keep_camera_identity():
    engine = CameraPolicyEngine([FIRST, SECOND])
    engine.replace_policy(FIRST, policy(FIRST, zone=True))
    engine.replace_policy(SECOND, policy(SECOND))
    for camera in (FIRST, SECOND):
        assert engine.observe(camera, (person(),), now=1) == ()
        entered, = engine.observe(camera, (person(),), now=2)
        assert entered.change.edge == "enter"
        assert engine.observe(camera, (person(),), now=2.5) == ()
    first, = engine.observe(FIRST, (person(0.92),), now=3)
    assert (first.camera_mac, first.change.edge, first.zone_ids) == (
        FIRST, "moving", (7,))
    assert engine.observe(FIRST, (person(),), now=3.5) == ()
    second, = engine.observe(SECOND, (person(0.93),), now=3.5)
    assert (second.camera_mac, second.change.edge, second.zone_ids) == (
        SECOND, "moving", ())
    closed, = engine.replace_policy(FIRST, None)
    assert (closed.change.edge, closed.zone_ids) == ("leave", (7,))
    assert engine.has_policy(SECOND)


def test_vehicle_and_animal_candidates_are_isolated_by_class_and_zone():
    engine = CameraPolicyEngine([FIRST, SECOND])
    engine.replace_policy(FIRST, policy(FIRST, zone=True, kind="vehicle"))
    engine.replace_policy(SECOND, policy(SECOND, kind="animal"))
    vehicle = ObjectObservation("vehicle", "car", 0.9, INSIDE)
    animal = ObjectObservation("animal", "dog", 0.9, INSIDE)
    assert engine.observe(FIRST, (animal, vehicle), now=1) == ()
    assert engine.observe(SECOND, (vehicle, animal), now=1) == ()
    first, = engine.observe(FIRST, (animal, vehicle), now=2)
    second, = engine.observe(SECOND, (vehicle, animal), now=2)
    assert (first.change.kind, first.zone_ids) == ("vehicle", (7,))
    assert (second.change.kind, second.zone_ids) == ("animal", ())
    closed, = engine.replace_policy(FIRST, None)
    assert closed.change.kind == "vehicle"
    assert engine.has_policy(SECOND)


def test_package_candidate_keeps_its_camera_and_zone():
    engine = CameraPolicyEngine([FIRST, SECOND])
    engine.replace_policy(FIRST, policy(FIRST, zone=True, kind="package"))
    package = ObjectObservation("package", "package", 0.93, INSIDE)
    assert engine.observe(FIRST, (package,), now=1) == ()
    entered, = engine.observe(FIRST, (package,), now=2)
    assert (entered.camera_mac, entered.change.kind, entered.change.edge,
            entered.zone_ids) == (FIRST, "package", "packageDetected", (7,))
    assert engine.observe(SECOND, (package,), now=1) == ()
    # Protect saves a package as a one-shot event: no moving or leave edge.
    assert engine.observe(FIRST, (package,), now=3) == ()
    assert engine.replace_policy(FIRST, None) == ()


def test_package_is_one_shot_per_track_with_camera_cooldown():
    engine = CameraPolicyEngine([FIRST], max_events_per_camera=5,
                                max_track_gap_seconds=20)
    engine.replace_policy(FIRST, policy(FIRST, zone=True, kind="package"))
    package = ObjectObservation("package", "package", 0.93, INSIDE)
    assert engine.observe(FIRST, (package,), now=1) == ()
    assert len(engine.observe(FIRST, (package,), now=2)) == 1
    assert engine.observe(FIRST, (), now=60) == ()      # local track ends
    # A later sparse sample of the same parcel is not a new delivery.
    assert engine.observe(FIRST, (package,), now=100) == ()
    assert engine.observe(FIRST, (package,), now=101) == ()
    assert engine.observe(FIRST, (), now=200) == ()
    assert engine.observe(FIRST, (package,), now=1900) == ()
    again, = engine.observe(FIRST, (package,), now=1901)
    assert again.change.edge == "packageDetected"
    snapshot = engine.camera_snapshot(now=1902)[0]
    assert snapshot["events_entered_by_kind"]["package"] == 2


def _package_policy(zones):
    return parse_smart_settings({
        "deviceID": FIRST, "algoVersion": "beta",
        "enableSmartDetect": ["person", "package"],
        "eventStartMSec": 1000, "eventStopMSec": 3000, "zones": zones,
        "excludeZones": {"9": {"coord": [0, 0, 100, 0, 100, 100, 0, 100],
                                "objectTypes": ["person", "package"], "patrolSetID": -1}}},
        camera_mac=FIRST)


def test_package_stays_inside_detection_area_when_protect_cannot_zone_it():
    # Paired first-party cameras never get Package in a primary zone.
    area = {"7": {"coord": [100, 100, 900, 100, 900, 900, 100, 900],
                  "objectTypes": ["person"], "sensitivity": 50}}
    policy = _package_policy(area)
    assert policy.package_scope == "detection_area"
    assert policy.zone_ids("package", INSIDE) == (7,)
    assert policy.zone_ids("package", OUTSIDE) is None     # zones still apply
    assert policy.zone_ids("package", (0.01, 0.01, 0.08, 0.08)) is None  # excluded
    engine = CameraPolicyEngine([FIRST], max_events_per_camera=4)
    engine.replace_policy(FIRST, policy)
    package = ObjectObservation("package", "package", 0.93, INSIDE)
    engine.observe(FIRST, (package,), now=1)
    entered, = engine.observe(FIRST, (package,), now=2)
    assert (entered.change.edge, entered.zone_ids) == ("packageDetected", (7,))
    assert engine.camera_snapshot(now=3)[0]["package_scope"] == "detection_area"


def test_explicit_package_zone_keeps_exact_zone_semantics():
    policy = _package_policy({
        "7": {"coord": [100, 100, 900, 100, 900, 900, 100, 900],
              "objectTypes": ["person"]},
        "8": {"coord": [500, 500, 1000, 500, 1000, 1000, 500, 1000],
              "objectTypes": ["package"]}})
    assert policy.package_scope == "package_zone"
    assert policy.zone_ids("package", INSIDE) is None
    assert policy.zone_ids("package", (0.6, 0.6, 0.8, 0.8)) == (8,)
    assert _package_policy({}).package_scope == "full_frame"


def test_package_rejected_outside_any_zone():
    engine = CameraPolicyEngine([FIRST], max_events_per_camera=4)
    engine.replace_policy(FIRST, parse_smart_settings({
        "deviceID": FIRST, "algoVersion": "beta",
        "enableSmartDetect": ["person", "package"],
        "eventStartMSec": 1000, "eventStopMSec": 3000,
        "zones": {"7": {"coord": [100, 100, 900, 100, 900, 900, 100, 900],
                        "objectTypes": ["person"], "sensitivity": 50}}},
        camera_mac=FIRST))
    package = ObjectObservation("package", "package", 0.93, OUTSIDE)
    assert engine.observe(FIRST, (package,), now=1) == ()
    assert engine.observe(FIRST, (package,), now=2) == ()
    snapshot = engine.camera_snapshot(now=3)[0]
    assert snapshot["zone_rejections"]["below_overlap"] == 2
    assert snapshot["zone_rejections_by_kind"]["package"] == 2
    assert snapshot["events_entered_by_kind"]["package"] == 0


def test_unknown_camera_or_cross_camera_policy_is_rejected():
    engine = CameraPolicyEngine([FIRST, SECOND])
    with pytest.raises(IngressError, match="camera_not_authorized"):
        engine.observe("2A1122334457", (person(),), now=1)
    with pytest.raises(IngressError, match="invalid_camera_policy"):
        engine.replace_policy(FIRST, policy(SECOND))
    assert not engine.has_policy(FIRST)


def test_bad_model_observation_is_rejected_even_when_policy_would_filter_it():
    engine = CameraPolicyEngine([FIRST])
    engine.replace_policy(FIRST, policy(FIRST, zone=True))
    malformed = ObjectObservation("vehicle", "vehicle", 0.9, (0.9, 0.2, 0.1, 0.8))
    with pytest.raises(TrackingError, match="invalid_tracking_observation"):
        engine.observe(FIRST, (malformed,), now=1)


def test_one_camera_tracks_person_vehicle_and_animal_independently():
    engine = CameraPolicyEngine([FIRST], max_events_per_camera=3)
    engine.replace_policy(FIRST, multiclass_policy(FIRST))
    observations = (person(), ObjectObservation("vehicle", "car", 0.91, INSIDE),
                    ObjectObservation("animal", "dog", 0.92, INSIDE))
    assert engine.observe(FIRST, observations, now=1) == ()
    entered = engine.observe(FIRST, observations, now=2)
    assert {item.change.kind for item in entered} == {"person", "vehicle", "animal"}
    assert len({item.change.track_id for item in entered}) == 3
    assert all(item.change.edge == "enter" for item in entered)
    assert engine.observe(FIRST, observations, now=2.5) == ()
    moving = engine.observe(FIRST, observations, now=3.5)
    assert {(item.change.kind, item.change.track_id) for item in moving} == {
        (item.change.kind, item.change.track_id) for item in entered}
    assert all(item.change.edge == "moving" for item in moving)
    left = engine.observe(FIRST, (), now=7)
    assert {(item.change.kind, item.change.track_id) for item in left} == {
        (item.change.kind, item.change.track_id) for item in entered}
    assert all(item.change.edge == "leave" for item in left)


def test_four_class_policy_accepts_package_with_other_object_classes():
    engine = CameraPolicyEngine([FIRST], max_events_per_camera=4)
    kinds = ("person", "vehicle", "animal", "package")
    engine.replace_policy(FIRST, multiclass_policy(FIRST, kinds))
    observations = (person(), ObjectObservation("vehicle", "car", 0.91, INSIDE),
                    ObjectObservation("animal", "dog", 0.92, INSIDE),
                    ObjectObservation("package", "package", 0.93, INSIDE))
    assert engine.observe(FIRST, observations, now=1) == ()
    entered = engine.observe(FIRST, observations, now=2)
    assert {item.change.kind for item in entered} == set(kinds)
    assert engine.camera_snapshot(now=2)[0]["policy_enabled_types"] == list(kinds)


def test_multiclass_revoke_closes_only_its_camera_tracks():
    engine = CameraPolicyEngine([FIRST, SECOND], max_events_per_camera=3)
    engine.replace_policy(FIRST, multiclass_policy(FIRST))
    engine.replace_policy(SECOND, policy(SECOND))
    first_observations = (person(), ObjectObservation("vehicle", "car", 0.9, INSIDE))
    assert engine.observe(FIRST, first_observations, now=1) == ()
    assert engine.observe(SECOND, (person(),), now=1) == ()
    entered = engine.observe(FIRST, first_observations, now=2)
    assert len(entered) == 2
    second, = engine.observe(SECOND, (person(),), now=2)
    closed = engine.replace_policy(FIRST, None)
    assert {(item.change.kind, item.change.track_id) for item in closed} == {
        (item.change.kind, item.change.track_id) for item in entered}
    assert all(item.change.edge == "leave" and item.camera_mac == FIRST
               for item in closed)
    still_active, = engine.observe(SECOND, (person(),), now=3.5)
    assert (still_active.camera_mac, still_active.change.track_id,
            still_active.change.edge) == (SECOND, second.change.track_id, "moving")


def test_event_budget_applies_across_classes_on_one_camera():
    engine = CameraPolicyEngine([FIRST], max_events_per_camera=2)
    engine.replace_policy(FIRST, multiclass_policy(FIRST))
    observations = (person(), ObjectObservation("vehicle", "car", 0.9, INSIDE),
                    ObjectObservation("animal", "dog", 0.9, INSIDE))
    assert engine.observe(FIRST, observations, now=1) == ()
    entered = engine.observe(FIRST, observations, now=2)
    assert {item.change.kind for item in entered} == {"person", "vehicle"}
    engine.replace_policy(FIRST, None)
    engine.replace_policy(FIRST, multiclass_policy(FIRST))
    assert engine.observe(FIRST, observations, now=3) == ()
    assert engine.observe(FIRST, observations, now=4) == ()


def test_live_event_budget_rolls_forward_without_affecting_other_camera():
    engine = CameraPolicyEngine([FIRST, SECOND], max_events_per_camera=1,
                                event_window_seconds=60)
    engine.replace_policy(FIRST, policy(FIRST))
    engine.replace_policy(SECOND, policy(SECOND))
    assert engine.observe(FIRST, (person(),), now=1) == ()
    first, = engine.observe(FIRST, (person(),), now=2)
    assert first.change.edge == "enter"
    assert engine.observe(FIRST, (), now=6)[0].change.edge == "leave"
    assert engine.observe(FIRST, (person(),), now=7) == ()
    assert engine.observe(FIRST, (person(),), now=8) == ()
    assert engine.observe(SECOND, (person(),), now=7) == ()
    second, = engine.observe(SECOND, (person(),), now=8)
    assert second.camera_mac == SECOND
    engine.replace_policy(FIRST, None)
    engine.replace_policy(FIRST, policy(FIRST))
    assert engine.observe(FIRST, (person(),), now=63) == ()
    renewed, = engine.observe(FIRST, (person(),), now=64)
    assert renewed.change.edge == "enter"
    status = engine.camera_snapshot(now=64)
    assert status[0]["events_entered"] == 2
    assert status[0]["event_budget_remaining"] == 0
    assert status[0]["eligible_observations"] >= 2
    assert FIRST not in str(status)


def test_camera_counters_separate_score_zone_and_tracking_gates():
    engine = CameraPolicyEngine([FIRST], max_events_per_camera=2,
                                event_window_seconds=3600)
    engine.replace_policy(FIRST, policy(FIRST, zone=True, reverify=True))
    # Below Protect's 80% reverification ceiling.
    assert engine.observe(FIRST, (person(score=0.7),), now=1) == ()
    # Above the score gate, outside the validated zone.
    assert engine.observe(FIRST, (person(box=OUTSIDE),), now=2) == ()
    assert engine.observe(FIRST, (person(),), now=3) == ()
    status, = engine.camera_snapshot(now=3)
    assert status["score_eligible_observations"] == 2
    assert status["eligible_observations"] == 1
    assert status["eligible_frames"] == 1
    assert status["events_entered"] == 0


def test_zone_rejection_health_distinguishes_overlap_and_exclusion_without_geometry():
    engine = CameraPolicyEngine([FIRST])
    engine.replace_policy(FIRST, policy(FIRST, zone=True))
    # Three score-eligible boxes in one frame: near threshold, grazing, and
    # entirely outside. Aggregated bands retain all three distinctions.
    engine.observe(FIRST, (person(box=OUTSIDE),
                           person(box=(0.05, 0.2, 0.11, 0.8)),
                           person(box=(0.95, 0.2, 1.0, 0.8))), now=1)
    status, = engine.camera_snapshot(now=1)
    assert status["zone_rejections"] == {
        "excluded": 0, "no_class_zone": 0,
        "outside_zone": 1, "below_overlap": 2,
    }
    assert status["zone_overlap_bands"] == {
        "trace_under_50": 1, "partial_50_to_80": 0,
        "rejected_at_least_80": 1,
    }
    assert FIRST not in str(status)
    assert "coord" not in str(status)

    excluded = {"deviceID": FIRST, "enableSmartDetect": ["person"],
                "eventStartMSec": 1000, "eventStopMSec": 3000,
                "zones": {"7": {"coord": [100, 100, 900, 100,
                                         900, 900, 100, 900],
                                "objectTypes": ["person"]}},
                "excludeZones": {"4": {
                    "coord": [0, 100, 100, 100, 100, 900, 0, 900],
                    "objectTypes": ["person"], "patrolSetID": -1}}}
    engine.replace_policy(FIRST, parse_smart_settings(excluded,
                                                     camera_mac=FIRST))
    # This box is both excluded and below 90% zone overlap; exclusion wins.
    prior_bands = dict(status["zone_overlap_bands"])
    engine.observe(FIRST, (person(box=OUTSIDE),), now=2)
    status, = engine.camera_snapshot(now=2)
    assert status["zone_rejections"]["excluded"] == 1
    assert status["zone_overlap_bands"] == prior_bands

    no_class = {"deviceID": FIRST, "enableSmartDetect": ["person"],
                "eventStartMSec": 1000, "eventStopMSec": 3000,
                "zones": {"7": {"coord": [100, 100, 900, 100,
                                         900, 900, 100, 900],
                                "objectTypes": ["vehicle"]}}}
    engine.replace_policy(FIRST, parse_smart_settings(no_class,
                                                     camera_mac=FIRST))
    engine.observe(FIRST, (person(),), now=3)
    status, = engine.camera_snapshot(now=3)
    assert status["zone_rejections"]["no_class_zone"] == 1
    assert status["eligible_observations"] == 0


def test_live_pool_event_budget_survives_engine_restart(tmp_path):
    wall = [5 * _HOUR_NS]
    budget = EventBudget(tmp_path, limit=1, clock_ns=lambda: wall[0])
    first = CameraPolicyEngine([FIRST], max_events_per_camera=1,
                               event_window_seconds=3600, event_budget=budget)
    first.replace_policy(FIRST, policy(FIRST))
    assert first.observe(FIRST, (person(),), now=1) == ()
    assert first.observe(FIRST, (person(),), now=2)[0].change.edge == "enter"

    restarted = CameraPolicyEngine([FIRST], max_events_per_camera=1,
                                   event_window_seconds=3600,
                                   event_budget=EventBudget(
                                       tmp_path, limit=1, clock_ns=lambda: wall[0]))
    restarted.replace_policy(FIRST, policy(FIRST))
    assert restarted.observe(FIRST, (person(),), now=10) == ()
    assert restarted.observe(FIRST, (person(),), now=11) == ()
    status, = restarted.camera_snapshot(now=11)
    assert status["event_budget_remaining"] == 0
    assert status["event_budget_healthy"] is True
    wall[0] += _HOUR_NS
    restarted.replace_policy(FIRST, None)
    restarted.replace_policy(FIRST, policy(FIRST))
    assert restarted.observe(FIRST, (person(),), now=12) == ()
    assert restarted.observe(FIRST, (person(),), now=13)[0].change.edge == "enter"


def test_live_pool_budget_corruption_denies_new_enters(tmp_path):
    budget = EventBudget(tmp_path, limit=1)
    engine = CameraPolicyEngine([FIRST], max_events_per_camera=1,
                                event_window_seconds=3600, event_budget=budget)
    engine.replace_policy(FIRST, policy(FIRST))
    budget.path.write_text("{}")
    assert engine.observe(FIRST, (person(),), now=1) == ()
    with pytest.raises(EventBudgetError, match="corrupt"):
        engine.observe(FIRST, (person(),), now=2)
    status, = engine.camera_snapshot(now=2)
    assert status["event_budget_remaining"] == 0
    assert status["event_budget_healthy"] is False


WALK_START = (0.10, 0.30, 0.25, 0.90)
WALK_NEXT = (0.32, 0.28, 0.47, 0.92)


def test_sparse_api_samples_confirm_a_walking_person_without_box_overlap():
    # Flur regression: two paid samples ~2 s apart both report one in-zone
    # person, but the walking person's boxes do not overlap at all.
    strict = CameraPolicyEngine([FIRST], max_track_gap_seconds=20)
    strict.replace_policy(FIRST, policy(FIRST))
    assert strict.observe(FIRST, (person(box=WALK_START),), now=1) == ()
    assert strict.observe(FIRST, (person(box=WALK_NEXT),), now=3.2) == ()
    assert strict.camera_snapshot(now=4)[0]["track_associations"] == {
        "iou_matches": 0, "proximity_matches": 0, "tentative_unmatched": 1}

    sparse = CameraPolicyEngine([FIRST], max_track_gap_seconds=20,
                                max_center_distance=1.5)
    sparse.replace_policy(FIRST, policy(FIRST))
    assert sparse.observe(FIRST, (person(box=WALK_START),), now=1) == ()
    entered, = sparse.observe(FIRST, (person(box=WALK_NEXT),), now=3.2)
    assert entered.change.edge == "enter"
    assert sparse.camera_snapshot(now=4)[0]["track_associations"] == {
        "iou_matches": 0, "proximity_matches": 1, "tentative_unmatched": 0}


def test_sparse_association_still_rejects_distant_or_other_class_objects():
    engine = CameraPolicyEngine([FIRST], max_track_gap_seconds=20,
                                max_center_distance=1.5)
    engine.replace_policy(FIRST, multiclass_policy(FIRST))
    small_left = (0.02, 0.02, 0.10, 0.20)
    small_right = (0.85, 0.75, 0.95, 0.95)
    assert engine.observe(FIRST, (person(box=small_left),), now=1) == ()
    assert engine.observe(FIRST, (person(box=small_right),), now=3) == ()
    assert engine.observe(FIRST, (ObjectObservation(
        "animal", "dog", 0.9, small_right),), now=5) == ()
    # Overlap wins over proximity when both people are near the track.
    assert engine.observe(FIRST, (person(box=INSIDE),), now=7) == ()
    entered, = engine.observe(FIRST, (person(box=(0.21, 0.2, 0.51, 0.8)),
                                      person(box=(0.55, 0.2, 0.85, 0.8))), now=9)
    assert entered.change.box == (0.21, 0.2, 0.51, 0.8)


def test_single_animal_sighting_is_counted_as_unconfirmed():
    engine = CameraPolicyEngine([FIRST], max_events_per_camera=4,
                                max_track_gap_seconds=20, max_center_distance=1.5)
    engine.replace_policy(FIRST, multiclass_policy(FIRST))
    cat = ObjectObservation("animal", "cat", 0.9, (0.1, 0.6, 0.2, 0.7))
    assert engine.observe(FIRST, (cat,), now=1) == ()
    assert engine.observe(FIRST, (person(),), now=3) == ()
    snapshot = engine.camera_snapshot(now=4)[0]
    assert snapshot["unconfirmed_by_kind"]["animal"] == 1
    assert snapshot["events_entered_by_kind"]["animal"] == 0


def test_confirmation_is_needed_only_for_new_unconfirmed_objects():
    engine = CameraPolicyEngine([FIRST], max_events_per_camera=4,
                                max_track_gap_seconds=20, max_center_distance=1.5)
    engine.replace_policy(FIRST, multiclass_policy(FIRST))
    engine.observe(FIRST, (person(),), now=1)
    assert engine.needs_confirmation(FIRST)            # first sighting
    engine.observe(FIRST, (person(),), now=3)          # person confirmed
    assert not engine.needs_confirmation(FIRST)
    engine.observe(FIRST, (person(),), now=10)         # same active person
    assert not engine.needs_confirmation(FIRST)
    cat = ObjectObservation("animal", "cat", 0.9, (0.6, 0.6, 0.7, 0.7))
    engine.observe(FIRST, (person(), cat), now=12)      # a cat arrives
    assert engine.needs_confirmation(FIRST)
