"""Synthetic AI Port zone contracts; no private camera geometry."""

import pytest

from aikey.aiport_zones import (ZoneError, parse_exclude_zones,
                               parse_person_zones, parse_smart_zones)


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
    # The 2.5-4% inset is Protect's default full-frame rectangle, not a
    # boundary the user drew: a box reaching into it is still in the zone.
    assert zone.contains_box((0.45, 0.7, 0.63, 0.995))


def _animal_zone(**corners):
    zone, = parse_smart_zones({"7": {**square(**corners), "objectTypes": ["animal"]}})
    return zone


def test_cat_along_the_frame_edge_is_inside_a_default_inset_full_frame_zone():
    # Flur, 26 Sep 03:25: a cat box mostly in the <=4% strip between the
    # default zone edge and the frame border overlapped the zone by <50%.
    zone = _animal_zone(x1=25, y1=35, x2=976, y2=960)
    for box in ((0.40, 0.93, 0.50, 1.0),     # under the camera, bottom edge
                (0.0, 0.40, 0.04, 0.55),     # hugging the left border
                (0.96, 0.0, 1.0, 0.06)):     # top-right corner
        assert zone.overlap_ratio(box) < 0.5
        assert zone.contains_box(box)


def test_an_edge_drawn_inside_the_frame_is_still_enforced():
    # 10% in from every border: a deliberate boundary, kept exactly.
    zone = _animal_zone(x1=100, y1=100, x2=900, y2=900)
    assert not zone.contains_box((0.40, 0.93, 0.50, 1.0))
    assert not zone.contains_box((0.0, 0.40, 0.06, 0.55))
    # Mixed: only the edges near the border snap; the inner edge x=0.6 holds.
    half = _animal_zone(x1=0, y1=30, x2=600, y2=970)
    assert half.contains_box((0.20, 0.93, 0.30, 1.0))
    assert not half.contains_box((0.62, 0.93, 0.70, 1.0))


def test_frame_edge_snap_never_widens_an_exclude_zone_or_admits_other_classes():
    exclude, = parse_exclude_zones({"3": {"coord": [0, 960, 1000, 960, 1000, 1000, 0, 1000],
                                          "objectTypes": ["animal"], "patrolSetID": -1}})
    assert not exclude.overlaps_box((0.40, 0.90, 0.50, 0.955))
    assert exclude.overlaps_box((0.40, 0.93, 0.50, 1.0))
    person_only, = parse_person_zones({"7": square(x1=25, y1=35, x2=976, y2=960)})
    assert "animal" not in person_only.object_types


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


def test_exclude_zone_treats_any_box_overlap_as_excluded():
    excluded, = parse_exclude_zones({"4": {
        "coord": [450, 100, 550, 100, 550, 900, 450, 900],
        "objectTypes": ["person"], "patrolSetID": -1,
    }})
    assert excluded.zone_id == 4
    assert excluded.overlaps_box((0.4, 0.2, 0.5, 0.8))
    assert not excluded.overlaps_box((0.2, 0.2, 0.4, 0.8))


@pytest.mark.parametrize("data", [
    {"0": {"coord": [0, 0, 1000, 0, 1000, 1000],
           "objectTypes": ["person"], "patrolSetID": -1}},
    {"1": {"coord": [0, 0, 1000, 0, 1000, 1000],
           "objectTypes": ["person"]}},
    {"1": {"coord": [0, 0, 1000, 0, 1000, 1000],
           "objectTypes": ["person"], "patrolSetID": 0}},
    {"1": {"coord": [0, 0, 1000, 0, 1000, 1000],
           "objectTypes": ["person"], "patrolSetID": -1,
           "unknown": "private"}},
])
def test_invalid_exclude_zone_fails_closed(data):
    with pytest.raises(ZoneError, match="invalid_exclude_zone"):
        parse_exclude_zones(data)


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
