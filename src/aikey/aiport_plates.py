"""License-plate text for AI Port vehicle tracks (issue #19).

Protect stores the ``name`` of a ``vehicle`` descriptor in an AI Port smart
event as that track's ``licensePlate`` (``parseSmartDetectTrackPayload``,
7.3.60 bundle; the field is present on 7.3.68 track entries). This module
keeps that text honest: characters the reader was not certain of stay ``?``,
a mostly unreadable plate is dropped, and readings of one track are merged
character by character, where disagreement also becomes ``?``.
"""

from __future__ import annotations

import re


_ALLOWED = re.compile(r"[A-Z0-9?]+(?: [A-Z0-9?]+){0,3}\Z")
MAX_CHARACTERS = 12


def normalize_plate(value: object) -> str | None:
    """Uppercase characters, ``?`` for uncertain ones, single-space groups.

    ``None`` when there is no usable reading: not text, other symbols, too
    short or long, or fewer legible characters than uncertain ones.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.upper().replace("-", " ").split())
    if not text or not _ALLOWED.fullmatch(text):
        return None
    characters = text.replace(" ", "")
    legible = sum(ch != "?" for ch in characters)
    if not 2 <= len(characters) <= MAX_CHARACTERS or legible < 2 or legible * 2 <= len(characters):
        return None
    return text


def merge_plates(current: str | None, reading: str | None) -> str | None:
    """Combine two readings of the same vehicle track without inventing text."""
    if current is None or reading is None:
        return current if reading is None else reading
    if current.replace(" ", "") == reading.replace(" ", "") or len(current) != len(reading):
        # Same plate, or a different length: keep the more legible reading.
        return reading if reading.count("?") < current.count("?") else current
    merged = []
    for old, new in zip(current, reading):
        if old == new or new == "?":
            merged.append(old)
        elif old == "?":
            merged.append(new)
        elif " " in (old, new):
            return current if current.count("?") <= reading.count("?") else reading
        else:
            merged.append("?")
    return normalize_plate("".join(merged))
