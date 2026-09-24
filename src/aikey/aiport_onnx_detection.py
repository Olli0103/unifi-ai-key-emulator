"""Optional RF-DETR ONNX inference for an explicitly selected AI Port pool.

The ONNX artifact is operator supplied and pinned. In particular, selecting an
OpenVINO GPU provider must never silently select a CPU-only session instead.
"""

from __future__ import annotations

from io import BytesIO
import hashlib
import hmac
import math
from pathlib import Path
import re

from .aiport_detection import DetectionError, ObjectObservation, _COCO_LABELS


_PIN = re.compile(r"[0-9a-fA-F]{64}\Z")
_MAX_MODEL_BYTES = 1024 * 1024 * 1024
_MAX_FRAME_BYTES = 1024 * 1024
_PROVIDERS = {
    "onnx_cpu": "CPUExecutionProvider",
    "onnx_openvino_gpu": "OpenVINOExecutionProvider",
}


def validate_onnx_model(path: str, expected_sha256: str) -> Path:
    if (not isinstance(path, str) or not Path(path).is_absolute()
            or not isinstance(expected_sha256, str)
            or not _PIN.fullmatch(expected_sha256)):
        raise DetectionError("invalid_onnx_policy")
    artifact = Path(path)
    try:
        if artifact.is_symlink() or not artifact.is_file() or artifact.suffix != ".onnx":
            raise DetectionError("onnx_model_unavailable")
        size = artifact.stat().st_size
        if not 0 < size <= _MAX_MODEL_BYTES:
            raise DetectionError("onnx_model_size_invalid")
        digest = hashlib.sha256()
        with artifact.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise DetectionError("onnx_model_unavailable") from exc
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
        raise DetectionError("onnx_model_hash_mismatch")
    return artifact


class OnnxRFDetrNanoDetector:
    """Decode a pinned RF-DETR Nano ONNX export with a requested provider."""

    def __init__(self, session: object, *, threshold: float = 0.5):
        if (type(threshold) not in (float, int) or not math.isfinite(threshold)
                or not 0 < threshold < 1):
            raise DetectionError("invalid_detection_threshold")
        try:
            inputs = session.get_inputs()
            outputs = session.get_outputs()
            if (len(inputs) != 1 or inputs[0].shape != [1, 3, 384, 384]
                    or {item.name for item in outputs} != {"dets", "labels"}
                    or {item.name: item.shape for item in outputs} != {
                        "dets": [1, 300, 4], "labels": [1, 300, 91]}):
                raise ValueError
        except Exception as exc:
            raise DetectionError("invalid_onnx_model_contract") from exc
        self.session = session
        self.threshold = float(threshold)
        self.input_name = inputs[0].name

    @classmethod
    def from_model(cls, path: str, expected_sha256: str, *,
                   backend: str, threshold: float = 0.5) -> OnnxRFDetrNanoDetector:
        artifact = validate_onnx_model(path, expected_sha256)
        if type(backend) is not str or backend not in _PROVIDERS:
            raise DetectionError("invalid_onnx_provider")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise DetectionError("onnx_dependency_unavailable") from exc
        provider = _PROVIDERS[backend]
        if provider not in ort.get_available_providers():
            raise DetectionError("onnx_provider_unavailable")
        providers = ([provider] if backend == "onnx_cpu" else
                     [(provider, {"device_type": "GPU"})])
        try:
            session = ort.InferenceSession(str(artifact), providers=providers)
            if not session.get_providers() or session.get_providers()[0] != provider:
                raise DetectionError("onnx_provider_unavailable")
            session.disable_fallback()
            return cls(session, threshold=threshold)
        except DetectionError:
            raise
        except Exception as exc:
            raise DetectionError("onnx_model_load_failed") from exc

    def detect(self, frame: bytes) -> tuple[ObjectObservation, ...]:
        if (not isinstance(frame, bytes) or len(frame) > _MAX_FRAME_BYTES
                or not frame.startswith(b"\xff\xd8\xff")):
            raise DetectionError("invalid_detector_frame")
        try:
            import numpy as np
            from PIL import Image, UnidentifiedImageError
            from rfdetr.export._onnx.inference import _preprocess_pil_to_nchw
        except ImportError as exc:
            raise DetectionError("onnx_dependency_unavailable") from exc
        try:
            with Image.open(BytesIO(frame)) as image:
                if (image.format != "JPEG" or not (16 <= image.width <= 8192
                                                   and 16 <= image.height <= 4320)
                        or image.width * image.height > 4_000_000):
                    raise DetectionError("invalid_detector_frame")
                tensor = _preprocess_pil_to_nchw(image, 384, 384, 3)
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise DetectionError("invalid_detector_frame") from exc
        try:
            boxes, logits = self.session.run(
                ["dets", "labels"], {self.input_name: tensor})
        except Exception as exc:
            raise DetectionError("detector_inference_failed") from exc
        try:
            if (boxes.shape != (1, 300, 4) or logits.shape != (1, 300, 91)
                    or not np.isfinite(boxes).all() or not np.isfinite(logits).all()):
                raise ValueError
            scores_all = 1.0 / (1.0 + np.exp(-np.clip(logits[0, :, :-1], -88, 88)))
            classes = scores_all.argmax(axis=1)
            scores = scores_all.max(axis=1)
            observations = []
            for box, category_id, score in zip(boxes[0], classes, scores, strict=True):
                category = _COCO_LABELS.get(int(category_id))
                if category is None or score < self.threshold:
                    continue
                cx, cy, width, height = (float(point) for point in box)
                x1, y1 = cx - width / 2, cy - height / 2
                x2, y2 = cx + width / 2, cy + height / 2
                if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                    raise ValueError
                observations.append(ObjectObservation(
                    category[0], category[1], float(score), (x1, y1, x2, y2)))
            if len(observations) > 100:
                raise ValueError
            return tuple(observations)
        except Exception as exc:
            raise DetectionError("invalid_detector_output") from exc
