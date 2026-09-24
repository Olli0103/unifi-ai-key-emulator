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
    assert (entered.camera_mac, entered.change.kind, entered.zone_ids) == (
        FIRST, "package", (7,))
    assert engine.observe(SECOND, (package,), now=1) == ()
    closed, = engine.replace_policy(FIRST, None)
    assert (closed.change.kind, closed.change.edge) == ("package", "leave")


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
