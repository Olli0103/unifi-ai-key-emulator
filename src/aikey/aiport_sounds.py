"""Alarm and household sound events on a paired camera's relay audio (#28).

A paired camera loses its own audio events to the AI Port, exactly like
speech (see ``aiport_audio``). Protect's audio types besides ``alrmSpeak``
are smoke alarm, CO alarm, siren, baby cry, dog bark, burglar alarm, car horn
and glass break. A pinned local AudioSet classifier (an ONNX model with its
class map, e.g. YAMNet) scores the audio; nothing is kept, uploaded or
transcribed, and only fixed type and edge names leave this module.

Protect ends an audio event only once each type reads ``leave`` or ``none``,
and how it treats two types open at once is not established. So a camera has
at most one open audio type: a higher-priority sound first closes the open
one, and a lower-priority sound waits.

Smoke and CO alarms share AudioSet classes; the standard temporal patterns
tell them apart. Smoke alarms sound T3 (three ~0.5 s beeps), CO alarms T4
(four ~0.1 s beeps).
"""

from __future__ import annotations

import array
import csv
import hashlib
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .aiport_audio import MAX_EVENT_S, SAMPLE_RATE, SPEECH

SOUND_TYPES = ("alrmSmoke", "alrmCmonx", "alrmSiren", "alrmBabyCry", "alrmBark",
               "alrmBurglar", "alrmCarHorn", "alrmGlassBreak")
# Lower number wins. Speech is the lowest, so any sound preempts it.
PRIORITY = {"alrmSmoke": 0, "alrmCmonx": 0, "alrmBurglar": 1, "alrmSiren": 1,
            "alrmGlassBreak": 1, "alrmBabyCry": 2, "alrmBark": 3, "alrmCarHorn": 4,
            SPEECH: 5}
# AudioSet display names per Protect type. "alarm" covers both smoke and CO;
# the beep pattern decides which.
CLASSES = {
    "alarm": ("Smoke detector, smoke alarm", "Fire alarm", "Beep, bleep"),
    "alrmSiren": ("Siren", "Civil defense siren", "Police car (siren)", "Ambulance (siren)",
                  "Fire engine, fire truck (siren)", "Emergency vehicle"),
    "alrmBabyCry": ("Baby cry, infant cry",),
    "alrmBark": ("Bark", "Bow-wow", "Yip"),
    # YAMNet's 521 classes have no "Burglar alarm". The generic "Alarm"
    # also scored reversing beeps and appliance tones (30 Sep: 9 false
    # burglar events on the road-facing cameras), so only "Car alarm" counts.
    "alrmBurglar": ("Car alarm",),
    "alrmCarHorn": ("Vehicle horn, car horn, honking", "Air horn, truck horn", "Toot"),
    # "Glass" is mostly clinking dishes and mugs (30 Sep: 16 false indoor
    # events); only "Shatter" is breaking glass.
    "alrmGlassBreak": ("Shatter",),
}
WINDOW_SAMPLES = 15600                     # 0.975 s, the YAMNet patch
HOP_SAMPLES = 7680                         # 0.48 s
HISTORY_SAMPLES = 6 * SAMPLE_RATE          # beep-pattern analysis only
# Score to enter, consecutive 0.48 s hops above it, seconds below it to
# leave. Tightened after the first live day (30 Sep): sirens and car alarms
# must hold for about 2 s (road noise crossed a 1 s bar), glass must shatter
# clearly, and the rest need a higher score.
POLICY = {"alarm": (0.4, 3, 6.0), "alrmSiren": (0.6, 4, 4.0),
          "alrmBabyCry": (0.5, 3, 4.0), "alrmBark": (0.5, 2, 3.0),
          "alrmBurglar": (0.5, 4, 4.0), "alrmCarHorn": (0.5, 2, 2.0),
          "alrmGlassBreak": (0.6, 1, 2.0)}


# YAMNet's published frontend: 25 ms periodic Hann window, 10 ms hop, a
# 512-point FFT magnitude, 64 HTK-mel bands from 125 to 7500 Hz, log(mel +
# 0.001), and 96-frame (0.96 s) patches. Exports that take patches instead of
# a waveform are fed through it; 0.975 s of audio is exactly one patch.
MEL_FRAMES, MEL_BANDS = 96, 64
_STFT_WINDOW, _STFT_HOP, _FFT = 400, 160, 512


def _hz_to_mel(hz):
    import numpy as np
    return 1127.0 * np.log1p(np.asarray(hz, dtype=np.float64) / 700.0)


def mel_weights():
    """[257, 64] triangle weights, as TensorFlow's linear_to_mel_weight_matrix builds them."""
    import numpy as np
    bins = _FFT // 2 + 1
    linear = _hz_to_mel(np.linspace(0.0, SAMPLE_RATE / 2, bins)[1:])[:, None]
    edges = np.linspace(_hz_to_mel(125.0), _hz_to_mel(7500.0), MEL_BANDS + 2)
    lower, center, upper = edges[:-2], edges[1:-1], edges[2:]
    weights = np.maximum(0.0, np.minimum((linear - lower) / (center - lower),
                                         (upper - linear) / (upper - center)))
    return np.vstack([np.zeros((1, MEL_BANDS)), weights]).astype(np.float32)


def log_mel_patch(wave):
    """One [96, 64] log-mel patch from 0.975 s of float audio in [-1, 1]."""
    import numpy as np
    wave = np.asarray(wave, dtype=np.float32)
    if wave.shape != (WINDOW_SAMPLES,):
        raise SoundError("sound_window_size")
    window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(_STFT_WINDOW) / _STFT_WINDOW)).astype(np.float32)
    starts = np.arange(MEL_FRAMES) * _STFT_HOP
    frames = np.stack([wave[i:i + _STFT_WINDOW] for i in starts]) * window
    magnitude = np.abs(np.fft.rfft(frames, n=_FFT, axis=1)).astype(np.float32)
    return np.log(magnitude @ mel_weights() + 0.001).astype(np.float32)


class SoundError(ValueError):
    """An invalid sound model, class map or detector input."""


@dataclass(frozen=True)
class SoundEdge:
    kind: str           # a Protect audio type
    edge: str           # "enter" or "leave"
    level_db: float


def beep_kind(samples: array.array) -> str | None:
    """"alrmCmonx" for short T4 beeps, "alrmSmoke" for long T3 beeps, else None.

    The 10 ms envelope is thresholded halfway between its quiet and loud
    levels; the median length of the loud runs separates the patterns.
    """
    step = SAMPLE_RATE // 100
    levels = []
    for start in range(0, len(samples) - step + 1, step):
        chunk = samples[start:start + step]
        levels.append(math.sqrt(sum(v * v for v in chunk) / step))
    if len(levels) < 50:
        return None
    ordered = sorted(levels)
    # T4 beeps fill only ~7 % of a cycle, so "loud" is the 98th percentile.
    quiet, loud = ordered[len(ordered) // 10], ordered[-max(1, len(ordered) // 50)]
    if loud < 4 * max(quiet, 1.0):
        return None
    threshold = (quiet + loud) / 2
    runs, length = [], 0
    for level in levels + [0.0]:
        if level >= threshold:
            length += 1
        elif length:
            runs.append(length)
            length = 0
    runs = [run for run in runs if run >= 3]          # at least 30 ms
    if len(runs) < 3:
        return None
    median = sorted(runs)[len(runs) // 2] * 10        # ms
    return "alrmCmonx" if median <= 250 else "alrmSmoke"


class SoundClassifier:
    """A pinned AudioSet ONNX classifier: 0.975 s of 16 kHz audio -> scores."""

    def __init__(self, model_path: Path, model_sha256: str, class_map_path: Path,
                 class_map_sha256: str, *, threads: int = 1):
        for path, digest in ((model_path, model_sha256), (class_map_path, class_map_sha256)):
            try:
                data = Path(path).read_bytes()
            except OSError as exc:
                raise SoundError("sound_model_unavailable") from exc
            if hashlib.sha256(data).hexdigest() != digest:
                raise SoundError("sound_model_digest_mismatch")
        with open(class_map_path, newline="") as handle:
            names = [row[-1].strip() for row in csv.reader(handle)][1:]
        index = {name: i for i, name in enumerate(names)}
        self.groups = {}
        for group, labels in CLASSES.items():
            missing = [label for label in labels if label not in index]
            if missing:
                raise SoundError("sound_class_map_incomplete")
            self.groups[group] = [index[label] for label in labels]
        import onnxruntime
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = threads
        self._session = onnxruntime.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"])
        model_input = self._session.get_inputs()[0]
        self._input = model_input.name
        shape = [d if isinstance(d, int) else None for d in (getattr(model_input, "shape", None) or [])]
        # A waveform model takes [samples] (or [1, samples]); a patch model's
        # input ends in [96, 64], optionally after batch and channel axes.
        self._patch_shape = (tuple(1 if d is None else d for d in shape)
                             if shape[-2:] == [MEL_FRAMES, MEL_BANDS] else None)
        self._classes = len(names)

    def __call__(self, window: array.array) -> dict[str, float]:
        import numpy as np
        wave = np.frombuffer(window.tobytes(), dtype=np.int16).astype(np.float32) / 32768.0
        feed = (log_mel_patch(wave).reshape(self._patch_shape)
                if self._patch_shape is not None else wave)
        outputs = self._session.run(None, {self._input: feed})
        scores = next((o for o in outputs if getattr(o, "shape", ())[-1:] == (self._classes,)),
                      None)
        if scores is None:
            raise SoundError("sound_model_output")
        top = scores.reshape(-1, self._classes).max(axis=0)
        return {group: float(max(top[i] for i in ids)) for group, ids in self.groups.items()}


class SoundEvents:
    """Hysteresis per sound group over classifier scores, one open type at most."""

    def __init__(self, classify: Callable[[array.array], dict[str, float]], *,
                 enabled: Callable[[str], bool], min_level_db: float = -55.0,
                 clock: Callable[[], float] = time.monotonic):
        self._classify, self._enabled = classify, enabled
        self.min_level_db = min_level_db
        self._clock = clock
        self._history = array.array("h")
        self._since_hop = 0
        self._pending = b""
        self._hits = dict.fromkeys(POLICY, 0)
        self._last_seen = dict.fromkeys(POLICY, 0.0)
        self.open: str | None = None
        self._open_group: str | None = None
        self._opened_at = 0.0
        self.classifications = 0
        self.skipped_quiet = 0

    def reset(self) -> None:
        self._history = array.array("h")
        self._pending, self._since_hop = b"", 0
        self._hits = dict.fromkeys(POLICY, 0)
        self.open = self._open_group = None

    def close(self) -> None:
        """Forget an open type that was closed from outside (preemption, stop)."""
        self.open = self._open_group = None

    def feed(self, pcm: bytes) -> list[SoundEdge]:
        data = self._pending + pcm
        whole = len(data) - len(data) % 2
        self._pending = data[whole:]
        samples = array.array("h")
        samples.frombytes(data[:whole])
        if sys.byteorder != "little":
            samples.byteswap()
        self._history.extend(samples)
        del self._history[:-HISTORY_SAMPLES]
        self._since_hop += len(samples)
        edges: list[SoundEdge] = []
        while self._since_hop >= HOP_SAMPLES and len(self._history) >= WINDOW_SAMPLES:
            self._since_hop -= HOP_SAMPLES
            edges += self._hop()
        return edges

    def _hop(self) -> list[SoundEdge]:
        window = self._history[-WINDOW_SAMPLES:]
        energy = sum(v * v for v in window[::4]) / max(1, len(window[::4]))
        level = 10 * math.log10(energy / 32768.0 ** 2) if energy > 0 else -120.0
        now = self._clock()
        if level < self.min_level_db:
            self.skipped_quiet += 1
            scores = dict.fromkeys(POLICY, 0.0)
        else:
            self.classifications += 1
            scores = self._classify(window)
        edges: list[SoundEdge] = []
        for group, (threshold, needed, _) in POLICY.items():
            if scores.get(group, 0.0) >= threshold:
                self._hits[group] += 1
                self._last_seen[group] = now
            else:
                self._hits[group] = 0
        if self._open_group is not None:
            _, _, leave_s = POLICY[self._open_group]
            if (now - self._last_seen[self._open_group] >= leave_s
                    or now - self._opened_at >= MAX_EVENT_S):
                edges.append(SoundEdge(self.open, "leave", level))
                self.close()
        candidates = []
        for group, (_, needed, _) in POLICY.items():
            if self._hits[group] < needed:
                continue
            # A smoke or CO alarm needs its T3/T4 beep pattern; alarm-like
            # scores without one (microwave or oven beeps) do not count.
            kind = beep_kind(self._history) if group == "alarm" else group
            if kind is None:
                continue
            if self._enabled(kind):
                candidates.append((PRIORITY[kind], kind, group))
        if candidates:
            _, kind, group = min(candidates)
            if self.open is None:
                self.open, self._open_group, self._opened_at = kind, group, now
                edges.append(SoundEdge(kind, "enter", level))
            elif PRIORITY[kind] < PRIORITY[self.open]:
                edges.append(SoundEdge(self.open, "leave", level))
                self.open, self._open_group, self._opened_at = kind, group, now
                edges.append(SoundEdge(kind, "enter", level))
        return edges
