"""Native object candidates require validated local tracks and no false plate."""

import pytest

from aikey.aiport_smart_events import (SmartEventError, camera_event_payload,
                                      smart_event_payload)
from aikey.aiport_tracking import TrackChange


PERSON = TrackChange("enter", 4, "person", "person", 0.87,
                     (0.1, 0.2, 0.4, 0.7))


def test_enter_payload_has_camera_timestamp_and_controller_descriptor_shape():
    payload = smart_event_payload("2A:11:22:33:44:55", PERSON,
                                  edge="enter", clock_wall_ms=1_700_000_000_000)
    assert payload["deviceID"] == "2A1122334455"
    assert payload["clockWall"] == 1_700_000_000_000
    assert payload["edgeType"] == "enter"
    assert payload["zonesStatus"] == {}
    assert payload["descriptors"] == [{
        "trackerID": 4, "name": "person", "confidenceLevel": 87,
        "coord": [100, 200, 300, 500], "objectType": "person",
        "zones": [], "lines": [], "stationary": False,
        "attributes": {}, "coord3d": [],
    }]


def test_leave_payload_keeps_the_tracked_object_for_class_association():
    payload = smart_event_payload("2A1122334455", PERSON,
                                  edge="leave", clock_wall_ms=1_700_000_001_000)
    assert payload["deviceID"] == "2A1122334455"
    assert payload["edgeType"] == "leave"
    assert payload["clockWall"] == 1_700_000_001_000
    assert payload["displayTimeoutMSec"] == 1000
    assert payload["zonesStatus"] == {}
    assert payload["objectTypes"] == ["person"]
    assert payload["trackerIDAttrMap"] == {
        "4": {"objectType": "person", "zone": []}}
    assert payload["descriptors"][0] == {
        "trackerID": 4, "name": "person", "confidenceLevel": 87,
        "coord": [100, 200, 300, 500], "objectType": "person",
        "zones": [], "lines": [], "stationary": False,
        "attributes": {}, "coord3d": [],
    }


def test_moving_payload_updates_track_without_new_zone_transition():
    moving = TrackChange("moving", 4, "person", "person", 0.91,
                         (0.12, 0.2, 0.42, 0.7))
    payload = smart_event_payload("2A1122334455", moving,
                                  edge="moving", clock_wall_ms=1_700_000_001_000,
                                  zone_ids=(7,))
    assert payload["edgeType"] == "moving"
    assert payload["zonesStatus"] == {}
    assert payload["descriptors"][0]["trackerID"] == 4
    assert payload["descriptors"][0]["objectType"] == "person"
    assert payload["descriptors"][0]["zones"] == [7]
    assert payload["descriptors"][0]["confidenceLevel"] == 91


def test_recorded_track_can_carry_one_validated_first_seen_time_across_edges():
    first = 1_700_000_000_000
    for edge, clock in (("enter", first), ("moving", first + 2000),
                        ("leave", first + 4000)):
        payload = smart_event_payload(
            "2A1122334455", PERSON, edge=edge, clock_wall_ms=clock,
            first_shown_ms=first)
        assert payload["descriptors"][0]["firstShownTimeMs"] == first


@pytest.mark.parametrize("first_seen", [True, 0, 1_700_000_000_001])
def test_first_seen_time_must_be_an_earlier_positive_integer(first_seen):
    with pytest.raises(SmartEventError):
        smart_event_payload("2A1122334455", PERSON, edge="enter",
                            clock_wall_ms=1_700_000_000_000,
                            first_shown_ms=first_seen)


def test_zone_enter_and_leave_keep_same_numeric_zone_id():
    enter = smart_event_payload("2A1122334455", PERSON, edge="enter",
                                clock_wall_ms=1_700_000_000_000, zone_ids=(7,))
    assert enter["zonesStatus"] == {"7": {"status": "enter", "level": 87}}
    assert enter["descriptors"][0]["zones"] == [7]
    leave = smart_event_payload("2A1122334455", PERSON, edge="leave",
                                clock_wall_ms=1_700_000_001_000, zone_ids=(7,))
    assert leave["zonesStatus"] == {"7": {"status": "leave", "level": 87}}
    assert leave["displayTimeoutMSec"] == 1000
    assert leave["descriptors"][0]["zones"] == [7]
    assert leave["descriptors"][0]["trackerID"] == enter["descriptors"][0]["trackerID"]
    assert leave["trackerIDAttrMap"] == {
        "4": {"objectType": "person", "zone": [7]}}
    assert enter["trackerIDAttrMap"] == {}


@pytest.mark.parametrize("kind,label,expected_name", [
    ("vehicle", "car", ""),
    ("animal", "dog", "animal"),
])
def test_other_object_events_never_claim_a_recognized_plate(kind, label, expected_name):
    track = TrackChange("enter", 2, kind, label, 0.9, (0.1, 0.2, 0.4, 0.5))
    payload = smart_event_payload("2A1122334455", track, edge="enter",
                                  clock_wall_ms=1_700_000_000_000, zone_ids=(3,))
    assert payload["objectTypes"] == [kind]
    assert payload["descriptors"][0]["objectType"] == kind
    assert payload["descriptors"][0]["name"] == expected_name
    assert payload["descriptors"][0]["zones"] == [3]
    leave = smart_event_payload("2A1122334455", track, edge="leave",
                                clock_wall_ms=1_700_000_001_000, zone_ids=(3,))
    assert leave["trackerIDAttrMap"] == {
        "2": {"objectType": kind, "zone": [3]}}
    assert "matchedName" not in leave["trackerIDAttrMap"]["2"]


@pytest.mark.parametrize("track,edge,clock", [
    (TrackChange("enter", 1, "face", "face", 0.9, (0.1, 0.2, 0.4, 0.5)),
     "enter", 1_700_000_000_000),
    (PERSON, "unknown", 1_700_000_000_000),
    (PERSON, "enter", True),
    (TrackChange("enter", 4, "person", "person", 0.87,
                 (-0.1, 0.2, 0.4, 0.7)), "enter", 1_700_000_000_000),
    (TrackChange("leave", 4, "person", "person", 0.87,
                 (-0.1, 0.2, 0.4, 0.7)), "leave", 1_700_000_000_000),
])
def test_event_rejects_unsupported_or_invalid_track(track, edge, clock):
    with pytest.raises(SmartEventError):
        smart_event_payload("2A1122334455", track, edge=edge, clock_wall_ms=clock)


def test_package_uses_protects_one_shot_package_edge():
    track = TrackChange("enter", 5, "package", "package", 0.91, (0.1, 0.2, 0.4, 0.5))
    payload = smart_event_payload("2A1122334455", track, edge="packageDetected",
                                  clock_wall_ms=1_700_000_000_000, zone_ids=(3,))
    assert payload["edgeType"] == "packageDetected"
    assert payload["objectTypes"] == ["package"]
    assert payload["zonesStatus"] == {"3": {"status": "enter", "level": 91}}
    with pytest.raises(SmartEventError):
        smart_event_payload("2A1122334455", track, edge="enter",
                            clock_wall_ms=1_700_000_000_000, zone_ids=(3,))
    person = TrackChange("enter", 6, "person", "person", 0.9, (0.1, 0.2, 0.4, 0.5))
    with pytest.raises(SmartEventError):
        smart_event_payload("2A1122334455", person, edge="packageDetected",
                            clock_wall_ms=1_700_000_000_000)


def test_camera_event_carries_every_object_and_closes_once():
    person = TrackChange("moving", 1, "person", "person", 0.9, (0.1, 0.2, 0.3, 0.8))
    car = TrackChange("enter", 2, "vehicle", "car", 0.8, (0.5, 0.5, 0.9, 0.9))
    moving = camera_event_payload("2A1122334455", "moving",
                                  ((person, (3,)), (car, (3, 4))),
                                  clock_wall_ms=1_700_000_000_000)
    assert moving["objectTypes"] == ["person", "vehicle"]
    assert [d["trackerID"] for d in moving["descriptors"]] == [1, 2]
    assert moving["descriptors"][1]["name"] == ""   # no plate claim
    assert moving["zonesStatus"] == {} and moving["trackerIDAttrMap"] == {}
    leave = camera_event_payload("2A1122334455", "leave",
                                 ((person, (3,)), (car, (3, 4))),
                                 clock_wall_ms=1_700_000_001_000)
    assert leave["zonesStatus"] == {"3": {"status": "leave", "level": 90},
                                    "4": {"status": "leave", "level": 80}}
    assert leave["trackerIDAttrMap"] == {
        "1": {"objectType": "person", "zone": [3]},
        "2": {"objectType": "vehicle", "zone": [3, 4]}}
    # Protect 7.3.68 routes only this lifecycle by deviceID for an AI Port,
    # so a package joins the camera's event like any other class.
    package = TrackChange("enter", 3, "package", "package", 0.9, (0.1, 0.2, 0.3, 0.4))
    parcel = camera_event_payload("2A1122334455", "enter", ((package, (5,)),),
                                  clock_wall_ms=1_700_000_000_000)
    assert (parcel["edgeType"], parcel["objectTypes"],
            parcel["descriptors"][0]["objectType"]) == ("enter", ["package"], "package")
    with pytest.raises(SmartEventError):
        camera_event_payload("2A1122334455", "enter", (),
                             clock_wall_ms=1_700_000_000_000)
