"""Pinned, local CPU Silero speech-presence gate for bounded 16-kHz PCM.

The ONNX state/context protocol follows snakers4/silero-vad (MIT), revision
066409f3d9db45a30d21dfcbf9147826ffdd7052. See docs/speech-voice-activity.md.
No downloading, resampling, transcript heuristics, cropping or shared audio state.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat


MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
MODEL_BYTES = 2_327_524
SPEECH_THRESHOLD = 0.5


class VoiceActivityError(ValueError):
    """Fixed, content-free failure; never fall back to unfiltered transcription."""


class SileroVoiceActivity:
    """A positive frame retains the whole original clip, including its timestamps.

    Each call owns its recurrent state. The server serializes the gate with its
    speech model and retains that lock until cancelled inference actually ends.
    """

    def __init__(self, model_path: str | Path):
        import onnxruntime

        try:
            fd = os.open(Path(model_path), os.O_RDONLY | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != MODEL_BYTES:
                    raise VoiceActivityError("vad_model_identity_mismatch")
                model = stream.read(MODEL_BYTES + 1)
        except OSError:
            raise VoiceActivityError("vad_model_unavailable") from None
        if len(model) != MODEL_BYTES or hashlib.sha256(model).hexdigest() != MODEL_SHA256:
            raise VoiceActivityError("vad_model_identity_mismatch")
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        # Consume precisely the verified bytes, not a path that can change later.
        try:
            self._session = onnxruntime.InferenceSession(
                model, sess_options=options, providers=["CPUExecutionProvider"]
            )
        except Exception:
            raise VoiceActivityError("vad_model_load_failed") from None

    def __call__(self, samples) -> bool:
        import numpy as np

        audio = np.asarray(samples)
        if (audio.dtype != np.float32 or audio.ndim != 1 or not 0 < audio.size <= 120 * 16000
                or not np.isfinite(audio).all() or np.max(np.abs(audio)) > 1):
            raise VoiceActivityError("vad_invalid_audio")
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, 64), dtype=np.float32)
        try:
            for offset in range(0, audio.size, 512):
                frame = audio[offset:offset + 512]
                frame = np.pad(frame, (0, 512 - frame.size)).reshape(1, 512)
                current = np.concatenate((context, frame), axis=1)
                probability, state = self._session.run(None, {
                    "input": current, "state": state, "sr": np.array(16000, dtype=np.int64)
                })
                if (probability.shape != (1, 1) or state.shape != (2, 1, 128)
                        or not np.isfinite(probability).all() or not np.isfinite(state).all()
                        or not 0 <= float(probability.item()) <= 1):
                    raise VoiceActivityError("vad_invalid_result")
                if float(probability.item()) >= SPEECH_THRESHOLD:
                    return True
                context = current[:, -64:]
        except VoiceActivityError:
            raise
        except Exception:
            raise VoiceActivityError("vad_inference_failed") from None
        return False
