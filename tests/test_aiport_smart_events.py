"""One native event candidate comes only from a validated local person track."""

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


def test_leave_payload_closes_same_camera_without_new_object():
    payload = smart_event_payload("2A1122334455", PERSON,
                                  edge="leave", clock_wall_ms=1_700_000_001_000)
    assert payload == {"deviceID": "2A1122334455", "edgeType": "leave",
                       "clockWall": 1_700_000_001_000,
                       "displayTimeoutMSec": 1000, "zonesStatus": {},
                       "trackerIDAttrMap": {}}


def test_zone_enter_and_leave_keep_same_numeric_zone_id():
    enter = smart_event_payload("2A1122334455", PERSON, edge="enter",
                                clock_wall_ms=1_700_000_000_000, zone_ids=(7,))
    assert enter["zonesStatus"] == {"7": {"status": "enter", "level": 87}}
    assert enter["descriptors"][0]["zones"] == [7]
    leave = smart_event_payload("2A1122334455", PERSON, edge="leave",
                                clock_wall_ms=1_700_000_001_000, zone_ids=(7,))
    assert leave["zonesStatus"] == {"7": {"status": "leave"}}
    assert leave["displayTimeoutMSec"] == 1000
    assert "descriptors" not in leave


@pytest.mark.parametrize("track,edge,clock", [
    (TrackChange("enter", 1, "vehicle", "car", 0.9, (0.1, 0.2, 0.4, 0.5)),
     "enter", 1_700_000_000_000),
    (PERSON, "moving", 1_700_000_000_000),
    (PERSON, "enter", True),
    (TrackChange("enter", 4, "person", "person", 0.87,
                 (-0.1, 0.2, 0.4, 0.7)), "enter", 1_700_000_000_000),
])
def test_event_rejects_unsupported_or_invalid_track(track, edge, clock):
    with pytest.raises(SmartEventError):
        smart_event_payload("2A1122334455", track, edge=edge, clock_wall_ms=clock)
