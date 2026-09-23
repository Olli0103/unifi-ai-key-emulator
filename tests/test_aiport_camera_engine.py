"""Synthetic camera isolation for bounded AI Port object candidates."""

import pytest

from aikey.aiport_camera_engine import CameraPolicyEngine
from aikey.aiport_detection import ObjectObservation
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
