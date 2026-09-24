"""Native object candidates require validated local tracks and no false plate."""

import pytest

from aikey.aiport_smart_events import SmartEventError, smart_event_payload
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
    assert payload["descriptors"][0] == {
        "trackerID": 4, "name": "person", "confidenceLevel": 87,
        "coord": [100, 200, 300, 500], "objectType": "person",
        "zones": [], "lines": [], "stationary": False,
        "attributes": {}, "coord3d": [],
    }


def test_moving_payload_updates_track_and_preserves_zone_status():
    moving = TrackChange("moving", 4, "person", "person", 0.91,
                         (0.12, 0.2, 0.42, 0.7))
    payload = smart_event_payload("2A1122334455", moving,
                                  edge="moving", clock_wall_ms=1_700_000_001_000,
                                  zone_ids=(7,))
    assert payload["edgeType"] == "moving"
    assert payload["zonesStatus"] == {"7": {"status": "moving", "level": 91}}
    assert payload["descriptors"][0]["trackerID"] == 4
    assert payload["descriptors"][0]["objectType"] == "person"
    assert payload["descriptors"][0]["zones"] == [7]
    assert payload["descriptors"][0]["confidenceLevel"] == 91


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


@pytest.mark.parametrize("track,edge,clock", [
    (TrackChange("enter", 1, "package", "package", 0.9, (0.1, 0.2, 0.4, 0.5)),
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
