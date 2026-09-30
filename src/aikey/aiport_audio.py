"""Speech presence on a paired camera's relay audio (#15, #28).

Protect 7.3.60 accepts ``EventSmartAudio`` from an AI Port for a paired camera.
It routes the event by ``deviceID`` to the original camera and saves a
``smartAudioDetect`` event. For an ``alrmSpeak`` event it also queues the AI
Key speech-to-text task. The paired camera's own audio events are dropped
while it is paired, so this is the only way speech reaches such a camera.

The detector only decides *whether* someone is speaking. It keeps no audio,
produces no text and reports only fixed edge names and a level in dB. The
transcript comes later from the AI Key, through Protect's own export of the
event, and only for cameras in the Key's speech allowlist.

The payload shape is taken from the controller parser (``audioMessageSchema``):
the clock and level fields are required, and **every** audio type must be
present, because Protect ends the event only once each type reads ``leave``
or ``none``.
"""

from __future__ import annotations

import array
import math
import sys
import time
from dataclasses import dataclass
from typing import Callable

from .aiport_ingest import IngressError, normalize_mac

SAMPLE_RATE = 16000
FRAME_SAMPLES = 480                       # 30 ms of 16 kHz mono s16le
FRAME_BYTES = FRAME_SAMPLES * 2
AUDIO_TYPES = ("alrmSmoke", "alrmCmonx", "alrmSiren", "alrmBabyCry", "alrmSpeak",
               "alrmBark", "alrmBurglar", "alrmCarHorn", "alrmGlassBreak")
SPEECH = "alrmSpeak"
# Protect pads the export of an event; 90 s keeps a capped speech event inside
# the AI Key's 120 s speech bound (and well inside Protect's 300 s sweep).
MAX_EVENT_S = 90.0


class AudioSettingsError(ValueError):
    """A ChangeAudioEventsSettings request outside the accepted shape."""


def parse_audio_settings(payload: object) -> tuple[str, bool]:
    """Return (camera MAC, speech enabled) from Protect's audio-events settings."""
    camera, flags = parse_audio_flags(payload)
    return camera, flags[SPEECH]


def parse_audio_flags(payload: object) -> tuple[str, dict[str, bool]]:
    """Return (camera MAC, {audio type: enabled}) from Protect's audio-events settings.

    Protect sends every ``enableAlrm*`` flag as 0 or 1 plus ``deviceID`` when
    the camera is paired with an AI Port. A type whose flag is absent is off.
    """
    if not isinstance(payload, dict) or len(payload) > 32:
        raise AudioSettingsError("invalid_audio_settings")
    try:
        camera = normalize_mac(payload.get("deviceID"))
    except IngressError as exc:
        raise AudioSettingsError("invalid_audio_settings") from exc
    for key, value in payload.items():
        if key == "deviceID":
            continue
        if not isinstance(key, str) or len(key) > 64 or value not in (0, 1) \
                or type(value) is not int:
            raise AudioSettingsError("invalid_audio_settings")
    if "enableAlrmSpeak" not in payload:
        raise AudioSettingsError("invalid_audio_settings")
    return camera, {kind: payload.get("enable" + kind[0].upper() + kind[1:]) == 1
                    for kind in AUDIO_TYPES}


def speech_event_payload(camera_mac: str, edge: str, *, clock_wall_ms: int,
                         level_db: float) -> dict:
    """The ``EventSmartAudio`` payload for one speech edge."""
    return audio_event_payload(camera_mac, SPEECH, edge, clock_wall_ms=clock_wall_ms,
                               level_db=level_db)


def audio_event_payload(camera_mac: str, kind: str, edge: str, *, clock_wall_ms: int,
                        level_db: float) -> dict:
    """The ``EventSmartAudio`` payload for one edge of one audio type; the rest read none."""
    if edge not in ("enter", "leave"):
        raise AudioSettingsError("invalid_audio_event")
    return audio_state_payload(camera_mac, {kind: edge}, clock_wall_ms=clock_wall_ms,
                               level_db=level_db)


def audio_state_payload(camera_mac: str, states: dict[str, str], *, clock_wall_ms: int,
                        level_db: float) -> dict:
    """The ``EventSmartAudio`` payload for the camera's whole audio event.

    ``states`` names each type that enters, stays open (``moving``) or leaves;
    every other type reads ``none``. Protect adds each entering type to the
    camera's one open audio event and ends it once all types read ``leave`` or
    ``none`` (``onAudioAlarm``).
    """
    if (not states or any(kind not in AUDIO_TYPES or edge not in ("enter", "moving", "leave")
                          for kind, edge in states.items())
            or type(clock_wall_ms) is not int or clock_wall_ms <= 0
            or not isinstance(level_db, (int, float)) or not math.isfinite(level_db)):
        raise AudioSettingsError("invalid_audio_event")
    payload: dict[str, object] = {
        "deviceID": normalize_mac(camera_mac),
        "clockMonotonic": 0, "clockStream": 0, "clockStreamRate": 0,
        "clockWall": clock_wall_ms, "eventId": 0,
        "leveldB": round(max(-120.0, min(0.0, float(level_db))), 1), "levels": 0,
        "loudNoise": "none", "soundLoss": "none"}
    payload.update(dict.fromkeys(AUDIO_TYPES, "none"))
    payload.update(states)
    return payload


@dataclass(frozen=True)
class SpeechEdge:
    edge: str            # "enter" or "leave"
    level_db: float


class SpeechActivity:
    """Energy and zero-crossing speech-presence detector with hysteresis.

    A 30 ms frame is *voiced* when its level is ``margin_db`` above a slowly
    rising noise floor and its zero-crossing rate lies in the band typical of
    voiced speech. That rejects steady hum (few crossings) and hiss or wind
    (many crossings). ``enter`` needs ``enter_ms`` of voiced frames within
    ``window_ms``; ``leave`` follows ``leave_ms`` without a voiced frame, or the
    ``MAX_EVENT_S`` cap.
    """

    def __init__(self, *, margin_db: float = 12.0, enter_ms: int = 600,
                 window_ms: int = 1500, leave_ms: int = 2500,
                 min_level_db: float = -50.0, clock: Callable[[], float] = time.monotonic):
        if not (3 <= margin_db <= 40 and 90 <= enter_ms <= window_ms <= 5000
                and 300 <= leave_ms <= 10000 and -90 <= min_level_db <= -10):
            raise AudioSettingsError("invalid_speech_detector")
        self.margin_db, self.min_level_db = margin_db, min_level_db
        self._window = max(1, window_ms // 30)
        self._enter = max(1, enter_ms // 30)
        self._leave_s = leave_ms / 1000
        self._clock = clock
        self._recent: list[bool] = []
        self._floor_db: float | None = None
        self._pending = b""
        self.active = False
        self._started_at = 0.0
        self._last_voiced_at = 0.0
        self.peak_db = -120.0
        self.frames = 0
        self.voiced_frames = 0

    def reset(self) -> None:
        self._recent.clear()
        self._pending = b""
        self.active = False

    def feed(self, pcm: bytes) -> list[SpeechEdge]:
        data = self._pending + pcm
        whole = len(data) - len(data) % FRAME_BYTES
        self._pending = data[whole:][:FRAME_BYTES]
        edges: list[SpeechEdge] = []
        for offset in range(0, whole, FRAME_BYTES):
            edge = self._frame(data[offset:offset + FRAME_BYTES])
            if edge is not None:
                edges.append(edge)
        return edges

    def _frame(self, raw: bytes) -> SpeechEdge | None:
        samples = array.array("h")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        energy = sum(value * value for value in samples) / len(samples)
        level = 10 * math.log10(energy / (32768.0 ** 2)) if energy > 0 else -120.0
        crossings = sum(1 for a, b in zip(samples, samples[1:]) if (a < 0) != (b < 0))
        zcr = crossings / (len(samples) - 1)
        if self._floor_db is None:
            self._floor_db = level
        # The floor follows quiet quickly and loud slowly, so a long speech
        # passage does not become the new floor.
        self._floor_db += (level - self._floor_db) * (0.2 if level < self._floor_db else 0.005)
        voiced = (level >= self.min_level_db and level >= self._floor_db + self.margin_db
                  and 0.02 <= zcr <= 0.35)
        self.frames += 1
        now = self._clock()
        self._recent.append(voiced)
        del self._recent[:-self._window]
        if voiced:
            self.voiced_frames += 1
            self._last_voiced_at = now
            self.peak_db = max(self.peak_db, level)
        if not self.active:
            if sum(self._recent) >= self._enter:
                self.active, self._started_at = True, now
                return SpeechEdge("enter", level)
            return None
        if now - self._last_voiced_at >= self._leave_s or now - self._started_at >= MAX_EVENT_S:
            self.active = False
            self._recent.clear()
            return SpeechEdge("leave", level)
        return None
