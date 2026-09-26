"""Local follow-up for a night-IR package the provider saw only at startup.

A parcel already in view when an AI Port restarts gets only the startup
sample pair. In night IR the engine holds such a Package (the user's cat was
read as a package there), and without motion no further sample arrives. This
follow-up uses the frames the ingest already decodes (no provider request)
to watch the held box for the dwell time:

* the box changed much more than its surroundings -> it moved or left;
* the box keeps showing small changes its surroundings do not -> animal-like
  (breathing, ears or tail of a resting cat), so no Package;
* otherwise the object stayed in place, inert -> the engine may announce it.

Only a few 24x24 grayscale summaries per held box are kept in memory, for
at most ``max_seconds``. Nothing is written or logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError


_SIZE = 24               # summary resolution of the held box
_RING = 0.5              # ring width around the box, as a fraction of its size
_MOVED_FLOOR = 10.0      # mean grey-level change that can mean "moved"
_MOVED_RATIO = 3.0       # ... when this many times the ring's change
_ACTIVE_FLOOR = 1.0      # frame-to-frame box change that can mean "alive"
_ACTIVE_RATIO = 2.5      # ... when this many times the ring's change
_ANIMATED_SHARE = 0.2    # share of active frames that makes a box animal-like
_ANIMATED_MIN = 3


@dataclass
class _Held:
    box: tuple[float, float, float, float]
    started: float
    reference: tuple[Image.Image, Image.Image, float]
    previous: tuple[Image.Image, Image.Image, float]
    frames: int = 0
    active_frames: int = 0


@dataclass
class HeldPackageFollowup:
    dwell_seconds: float = 20.0
    max_seconds: float = 30.0
    min_frames: int = 30
    max_per_camera: int = 2
    _held: dict[str, dict[int, _Held]] = field(default_factory=dict)
    decisions: dict[str, int] = field(default_factory=lambda: {
        "confirmed": 0, "moved": 0, "animated": 0, "expired": 0, "unreadable": 0})

    def tracking(self, camera: str) -> frozenset[int]:
        return frozenset(self._held.get(camera, {}))

    def start(self, camera: str, track_id: int,
              box: tuple[float, float, float, float], frame: bytes, now: float) -> bool:
        held = self._held.setdefault(camera, {})
        if track_id in held or len(held) >= self.max_per_camera:
            return False
        regions = _regions(frame, box)
        if regions is None:
            self.decisions["unreadable"] += 1
            return False
        held[track_id] = _Held(box, now, regions, regions)
        return True

    def discard(self, camera: str, track_id: int | None = None) -> None:
        if track_id is None:
            self._held.pop(camera, None)
        else:
            self._held.get(camera, {}).pop(track_id, None)

    def observe(self, camera: str, frame: bytes, now: float) -> dict[int, str]:
        """Return a decision per held track: keep, confirmed, moved, animated, expired."""
        result: dict[int, str] = {}
        for track_id, held in tuple(self._held.get(camera, {}).items()):
            decision = self._step(held, frame, now)
            result[track_id] = decision
            if decision != "keep":
                self.decisions[decision] += 1
                del self._held[camera][track_id]
        return result

    def _step(self, held: _Held, frame: bytes, now: float) -> str:
        if now - held.started > self.max_seconds:
            return "expired"
        regions = _regions(frame, held.box)
        if regions is None:
            return "keep"
        ring_scale = regions[2]
        box_moved, ring_moved = (_change(regions[0], held.reference[0]),
                                 ring_scale * _change(regions[1], held.reference[1]))
        if box_moved > max(_MOVED_FLOOR, _MOVED_RATIO * ring_moved):
            return "moved"
        box_active, ring_active = (_change(regions[0], held.previous[0]),
                                   ring_scale * _change(regions[1], held.previous[1]))
        held.previous = regions
        held.frames += 1
        held.active_frames += box_active > max(_ACTIVE_FLOOR, _ACTIVE_RATIO * ring_active)
        if now - held.started < self.dwell_seconds or held.frames < self.min_frames:
            return "keep"
        if held.active_frames >= max(_ANIMATED_MIN, _ANIMATED_SHARE * held.frames):
            return "animated"
        return "confirmed"


def _regions(frame: bytes, box: tuple[float, float, float, float]
             ) -> tuple[Image.Image, Image.Image, float] | None:
    """24x24 greyscale summaries of the box and the ring around it.

    The third value rescales a ring mean to the ring's own area (the box
    part of the ring crop is blanked and never changes).
    """
    try:
        with Image.open(BytesIO(frame)) as image:
            image.draft("L", (image.width // 4, image.height // 4))
            grey = image.convert("L")
    except (OSError, ValueError, UnidentifiedImageError):
        return None
    width, height = grey.size
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    inner = (int(x1 * width), int(y1 * height),
             max(int(x1 * width) + 1, int(x2 * width)),
             max(int(y1 * height) + 1, int(y2 * height)))
    outer = (int(max(0.0, x1 - _RING * bw) * width), int(max(0.0, y1 - _RING * bh) * height),
             int(min(1.0, x2 + _RING * bw) * width), int(min(1.0, y2 + _RING * bh) * height))
    if outer[2] - outer[0] < 2 or outer[3] - outer[1] < 2:
        return None
    ring = grey.crop(outer).copy()
    # Blank the box inside the ring crop so the ring measures surroundings only.
    ring.paste(0, (inner[0] - outer[0], inner[1] - outer[1],
                   inner[2] - outer[0], inner[3] - outer[1]))
    outer_area = (outer[2] - outer[0]) * (outer[3] - outer[1])
    inner_area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    if outer_area <= inner_area:
        return None
    return (grey.crop(inner).resize((_SIZE, _SIZE)), ring.resize((_SIZE, _SIZE)),
            outer_area / (outer_area - inner_area))


def _change(current: Image.Image, before: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(current, before)).mean[0]
