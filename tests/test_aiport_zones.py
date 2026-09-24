"""Synthetic AI Port zone contracts; no private camera geometry."""

import pytest

from aikey.aiport_zones import ZoneError, parse_person_zones, parse_smart_zones


def square(*, x1=100, y1=100, x2=900, y2=900):
    return {"coord": [x1, y1, x2, y1, x2, y2, x1, y2],
            "sensitivity": 50, "objectTypes": ["person"],
            "triggerLight": True, "triggerAccessTypes": []}


def test_person_box_needs_ninety_percent_zone_overlap():
    zone, = parse_person_zones({"7": square()})
    assert zone.zone_id == 7
    assert zone.contains_box((0.2, 0.3, 0.5, 0.8))
    assert not zone.contains_box((0.05, 0.3, 0.5, 0.8))
    assert zone.contains_box((0.1, 0.3, 0.5, 0.8))
    assert zone.contains_box((0.09, 0.3, 0.2, 0.8))
    assert not zone.contains_box((0.08, 0.3, 0.2, 0.8))
    assert not zone.contains_box((0.95, 0.3, 1.0, 0.8))


def test_person_at_frame_bottom_still_matches_mostly_full_frame_zone():
    zone, = parse_person_zones({"7": square(x1=25, y1=35, x2=976, y2=960)})
    assert zone.contains_box((0.45, 0.36, 0.63, 0.995))
    assert not zone.contains_box((0.45, 0.7, 0.63, 0.995))


def test_concave_zone_rejects_box_crossing_cutout():
    concave = square()
    concave["coord"] = [100, 100, 900, 100, 900, 900, 700, 900,
                        700, 400, 300, 400, 300, 900, 100, 900]
    zone, = parse_person_zones({"2": concave})
    assert zone.contains_box((0.15, 0.15, 0.25, 0.3))
    assert zone.contains_box((0.2, 0.5, 0.31, 0.8))
    assert not zone.contains_box((0.2, 0.5, 0.32, 0.8))
    assert not zone.contains_box((0.2, 0.5, 0.8, 0.8))


def test_non_person_zone_is_valid_but_cannot_admit_person():
    other = square()
    other["objectTypes"] = ["vehicle"]
    assert parse_person_zones({"1": other}) == ()
    zone, = parse_smart_zones({"1": other})
    assert zone.object_types == frozenset({"vehicle"})
    assert zone.contains_box((0.2, 0.2, 0.5, 0.8))


@pytest.mark.parametrize("data", [
    {"0": square()},
    {"01": square()},
    {"4294967296": square()},
    {"1": {**square(), "coord": [100, 100, 900, 100]}},
    {"1": {**square(), "coord": [100, 100, 900, 900, 900, 100, 100, 900]}},
    {"1": {**square(), "coord": [100, 100, 900, 100, 900, 900, 100, 100]}},
    {"1": {**square(), "coord": [100, 100, 900, 100, 900, 1000.1]}},
    {"1": {**square(), "objectTypes": ["person", "person"]}},
    {"1": {**square(), "sensitivity": True}},
    {"1": {**square(), "triggerAccessTypes": ["person"]}},
])
def test_invalid_or_unsupported_zones_fail_closed(data):
    with pytest.raises(ZoneError):
        parse_person_zones(data)
