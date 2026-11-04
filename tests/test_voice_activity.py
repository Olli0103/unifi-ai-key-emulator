"""Synthetic model-boundary tests; no packaged weights or inference network."""

import hashlib
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from aikey import voice_activity as module


@pytest.fixture
def model(tmp_path, monkeypatch):
    payload = b"fictional onnx bytes, not a real model"
    path = tmp_path / "model.onnx"
    path.write_bytes(payload)
    monkeypatch.setattr(module, "MODEL_BYTES", len(payload))
    monkeypatch.setattr(module, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    calls, scores = [], [0.1]

    class Session:
        def __init__(self, data, *, sess_options, providers):
            assert data == payload
            assert providers == ["CPUExecutionProvider"]
            assert sess_options.intra_op_num_threads == sess_options.inter_op_num_threads == 1

        def run(self, outputs, inputs):
            calls.append({k: v.copy() for k, v in inputs.items()})
            score = scores.pop(0) if len(scores) > 1 else scores[0]
            if isinstance(score, BaseException):
                raise score
            return np.array([[score]], np.float32), inputs["state"] + 1

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        SessionOptions=SimpleNamespace, InferenceSession=Session))
    return path, calls, scores


def test_negative_chunks_use_context_padding_and_reset_between_audio(model):
    path, calls, _ = model
    gate = module.SileroVoiceActivity(path)
    audio = np.linspace(-0.5, 0.5, 600, dtype=np.float32)
    original = audio.copy()
    assert gate(audio) is False
    assert len(calls) == 2
    np.testing.assert_array_equal(calls[0]["input"][0, :64], np.zeros(64))
    np.testing.assert_array_equal(calls[1]["input"][0, :64], audio[448:512])
    np.testing.assert_array_equal(calls[1]["input"][0, 64:152], audio[512:])
    assert not calls[1]["input"][0, 152:].any()
    assert calls[1]["state"].min() == 1
    assert gate(audio) is False
    assert not calls[2]["state"].any() and not calls[2]["input"][0, :64].any()
    np.testing.assert_array_equal(audio, original)


def test_one_positive_frame_retains_short_or_late_speech(model):
    path, calls, scores = model
    scores[:] = [0.1, 0.5]
    assert module.SileroVoiceActivity(path)(np.zeros(513, np.float32)) is True
    assert len(calls) == 2


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.01, 1.01,
                                  RuntimeError("private model input must not escape")])
def test_invalid_output_is_failure_not_a_correct_negative(model, score):
    path, _, scores = model
    scores[:] = [score]
    with pytest.raises(module.VoiceActivityError, match="^vad_(invalid_result|inference_failed)$"):
        module.SileroVoiceActivity(path)(np.zeros(512, np.float32))


@pytest.mark.parametrize("audio", [np.array([], np.float32), np.zeros(512, np.float64),
                                   np.zeros((1, 512), np.float32),
                                   np.array([np.nan], np.float32), np.array([1.1], np.float32),
                                   np.zeros(120 * 16000 + 1, np.float32)])
def test_invalid_audio_never_reaches_model(model, audio):
    path, calls, _ = model
    with pytest.raises(module.VoiceActivityError, match="vad_invalid_audio"):
        module.SileroVoiceActivity(path)(audio)
    assert calls == []


@pytest.mark.parametrize("fault", ["missing", "size", "hash", "directory", "fifo"])
def test_only_exact_regular_pinned_model_is_loaded(model, fault):
    import os
    path, calls, _ = model
    data = path.read_bytes()
    path.unlink()
    if fault == "size":
        path.write_bytes(data + b"x")
    elif fault == "hash":
        path.write_bytes(b"x" * len(data))
    elif fault == "directory":
        path.mkdir()
    elif fault == "fifo":
        os.mkfifo(path)
    with pytest.raises(module.VoiceActivityError, match="^vad_model_(unavailable|identity_mismatch)$"):
        module.SileroVoiceActivity(path)
    assert calls == []
