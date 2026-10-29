"""Plate text for AI Port vehicle tracks (#19). Synthetic values only."""

import pytest

from aikey.aiport_api_detection import parse_detections
from aikey.aiport_detection import ObjectObservation
from aikey.aiport_plates import merge_plates, normalize_plate
from aikey.aiport_smart_events import camera_event_payload, smart_event_payload
from aikey.aiport_tracking import TemporalTracker, TrackChange

CAMERA = "AA:BB:CC:00:11:22"


@pytest.mark.parametrize("raw,expected", [
    ("b-ab 1234", "B AB 1234"), (" XY  12 ", "XY 12"), ("AB?1", "AB?1"),
    ("M ?? 12", "M ?? 12"), (None, None), (12, None), ("", None), ("A", None),
    ("AB??", None), ("??1?", None), ("AB 12!", None), ("ÄB 12", None),
    ("ABCDEFGHIJKLM", None),
])
def test_plates_are_normalized_and_mostly_unreadable_ones_dropped(raw, expected):
    assert normalize_plate(raw) == expected


@pytest.mark.parametrize("current,reading,expected", [
    (None, "B AB 12", "B AB 12"), ("B AB 12", None, "B AB 12"),
    ("B A? 12", "B ?B 12", "B AB 12"),          # each reading fills the other's gap
    ("B AB 12", "B AX 12", "B A? 12"),          # disagreement stays uncertain
    ("B AB 1?", "B AB 123", "B AB 123"),        # different length: more legible wins
    ("B AB 12", "BA B12", "B AB 12"),           # same characters, other grouping
])
def test_readings_of_one_track_merge_without_inventing_characters(current, reading, expected):
    assert merge_plates(current, reading) == expected


def test_a_reply_may_carry_a_plate_only_for_vehicles_on_plate_cameras():
    reply = ('{"detections":[{"kind":"vehicle","label":"car","score":0.9,'
             '"box":[0.1,0.1,0.5,0.5],"plate":"b-ab 12?4"},'
             '{"kind":"vehicle","label":"car","score":0.9,"box":[0.5,0.5,0.9,0.9],"plate":null},'
             '{"kind":"person","label":"person","score":0.9,"box":[0.1,0.1,0.2,0.4],'
             '"plate":"X1"}]}')
    rejected = {}
    found = parse_detections(reply, threshold=0.5, rejected=rejected, plates=True)
    assert [item.plate for item in found] == ["B AB 12?4", None]
    assert rejected == {"plate": 1}
    rejected = {}
    assert parse_detections(reply, threshold=0.5, rejected=rejected) == ()
    assert rejected == {"shape": 3}


def _car(x, plate=None):
    return ObjectObservation("vehicle", "car", 0.9, (x, 0.2, x + 0.3, 0.6), plate)


def test_a_vehicle_track_carries_its_merged_plate_into_track_changes():
    tracker = TemporalTracker(min_hits=2)
    tracker.update((_car(0.1, "B A? 12"),), now=0.0)
    [enter] = tracker.update((_car(0.11, "B ?B 12"),), now=0.5)
    assert enter.edge == "enter" and enter.plate == "B AB 12"
    [moving] = tracker.update((_car(0.12, "B AX 12"),), now=1.0)
    assert moving.plate == "B A? 12"
    [moving] = tracker.update((_car(0.13),), now=1.5)          # no reading keeps the plate
    assert moving.plate == "B A? 12"
    people = TemporalTracker(min_hits=2)
    walker = ObjectObservation("person", "person", 0.9, (0.1, 0.1, 0.3, 0.8), "XX 11")
    people.update((walker,), now=0.0)
    assert people.update((walker,), now=0.5)[0].plate is None


def test_protect_receives_the_plate_as_the_vehicle_descriptor_name():
    change = TrackChange("enter", 7, "vehicle", "car", 0.9, (0.1, 0.2, 0.4, 0.6), "B AB 12")
    single = smart_event_payload(CAMERA, change, edge="enter", clock_wall_ms=1000)
    assert single["descriptors"][0]["name"] == "B AB 12"
    event = camera_event_payload(CAMERA, "enter", ((change, ()),), clock_wall_ms=1000)
    assert event["descriptors"][0]["name"] == "B AB 12"
    unread = TrackChange("enter", 8, "vehicle", "car", 0.9, (0.1, 0.2, 0.4, 0.6))
    assert smart_event_payload(CAMERA, unread, edge="enter",
                               clock_wall_ms=1000)["descriptors"][0]["name"] == ""
