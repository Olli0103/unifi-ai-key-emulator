"""Bounded, independent smart-zone geometry for AI Port object candidates.

Coordinates and field names follow the observed controller interface. The
predicate deliberately requires the entire detection box inside a simple
polygon. It favors missed detections over publishing outside a camera zone.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re


_ZONE_ID = re.compile(r"[1-9][0-9]{0,9}\Z")
_OBJECT_TYPES = frozenset({"person", "vehicle", "animal", "package",
                           "face", "licensePlate"})
_SUPPORTED_TYPES = frozenset({"person", "vehicle", "animal"})
_MAX_ZONES = 32
_MAX_VERTICES = 32
_EPSILON = 1e-9


class ZoneError(ValueError):
    """Fixed failure code without polygon or camera details."""


@dataclass(frozen=True)
class SmartZone:
    zone_id: int
    points: tuple[tuple[float, float], ...]
    object_types: frozenset[str]

    def contains_box(self, box: tuple[float, float, float, float]) -> bool:
        if (not isinstance(box, tuple) or len(box) != 4
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in box)):
            return False
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            return False
        corners = ((x1, y1), (x1, y2), (x2, y1), (x2, y2))
        if not all(_strict_inside(point, self.points) for point in corners):
            return False
        rectangle = ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
        return not any(_segments_intersect(a, b, c, d)
                       for a, b in _edges(rectangle)
                       for c, d in _edges(self.points))


def _edges(points: tuple[tuple[float, float], ...]):
    return zip(points, points[1:] + points[:1])


def _cross(a: tuple[float, float], b: tuple[float, float],
           c: tuple[float, float]) -> float:
    return ((b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0]))


def _on_segment(a, b, p) -> bool:
    return (abs(_cross(a, b, p)) <= _EPSILON
            and min(a[0], b[0]) - _EPSILON <= p[0] <= max(a[0], b[0]) + _EPSILON
            and min(a[1], b[1]) - _EPSILON <= p[1] <= max(a[1], b[1]) + _EPSILON)


def _segments_intersect(a, b, c, d) -> bool:
    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    if ((ab_c > _EPSILON and ab_d < -_EPSILON
         or ab_c < -_EPSILON and ab_d > _EPSILON)
            and (cd_a > _EPSILON and cd_b < -_EPSILON
                 or cd_a < -_EPSILON and cd_b > _EPSILON)):
        return True
    return (_on_segment(a, b, c) or _on_segment(a, b, d)
            or _on_segment(c, d, a) or _on_segment(c, d, b))


def _strict_inside(point: tuple[float, float],
                   polygon: tuple[tuple[float, float], ...]) -> bool:
    x, y = point
    inside = False
    for a, b in _edges(polygon):
        if _on_segment(a, b, point):
            return False
        if (a[1] > y) != (b[1] > y):
            crossing_x = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if crossing_x > x:
                inside = not inside
    return inside


def _simple_polygon(points: tuple[tuple[float, float], ...]) -> bool:
    if len(set(points)) != len(points):
        return False
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in _edges(points)) / 2
    if abs(area) <= _EPSILON:
        return False
    edges = tuple(_edges(points))
    for i, (a, b) in enumerate(edges):
        if a == b:
            return False
        for j in range(i + 1, len(edges)):
            if j == i + 1 or i == 0 and j == len(edges) - 1:
                continue
            if _segments_intersect(a, b, *edges[j]):
                return False
    return True


def parse_smart_zones(value: object) -> tuple[SmartZone, ...]:
    """Validate primary-lens zones and retain supported object classes."""
    if not isinstance(value, dict) or len(value) > _MAX_ZONES:
        raise ZoneError("invalid_smart_zone")
    zones = []
    for raw_id, data in value.items():
        if (not isinstance(raw_id, str) or _ZONE_ID.fullmatch(raw_id) is None
                or int(raw_id) > 4_294_967_295 or not isinstance(data, dict)
                or not {"coord", "objectTypes"} <= set(data)
                or not set(data) <= {"coord", "objectTypes", "sensitivity",
                                     "triggerLight", "triggerAccessTypes"}):
            raise ZoneError("invalid_smart_zone")
        object_types = data["objectTypes"]
        if (not isinstance(object_types, list) or len(object_types) > len(_OBJECT_TYPES)
                or any(type(item) is not str or item not in _OBJECT_TYPES
                       for item in object_types)
                or len(set(object_types)) != len(object_types)):
            raise ZoneError("invalid_smart_zone")
        sensitivity = data.get("sensitivity", 50)
        if type(sensitivity) is not int or not 0 <= sensitivity <= 100:
            raise ZoneError("invalid_smart_zone")
        if "triggerLight" in data and type(data["triggerLight"]) is not bool:
            raise ZoneError("invalid_smart_zone")
        if data.get("triggerAccessTypes", []) != []:
            raise ZoneError("unsupported_smart_zone")
        coords = data["coord"]
        if (not isinstance(coords, list) or not 6 <= len(coords) <= 2 * _MAX_VERTICES
                or len(coords) % 2):
            raise ZoneError("invalid_smart_zone")
        if any(type(number) not in (int, float) or not math.isfinite(number)
               or not 0 <= number <= 1000 for number in coords):
            raise ZoneError("invalid_smart_zone")
        points = tuple((coords[index] / 1000, coords[index + 1] / 1000)
                       for index in range(0, len(coords), 2))
        if not _simple_polygon(points):
            raise ZoneError("invalid_smart_zone")
        supported = frozenset(object_types) & _SUPPORTED_TYPES
        if supported:
            zones.append(SmartZone(int(raw_id), points, supported))
    return tuple(sorted(zones, key=lambda zone: zone.zone_id))


def parse_person_zones(value: object) -> tuple[SmartZone, ...]:
    """Keep the person-only view used by older callers and tests."""
    return tuple(zone for zone in parse_smart_zones(value)
                 if "person" in zone.object_types)
