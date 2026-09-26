"""Local object observations from decoded AI Port frames.

These observations are internal detector output. They are not Protect smart
events: temporal tracking, camera settings, clock alignment and native event
delivery must be verified separately before enabling smart-detection readiness.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from io import BytesIO
import math
from pathlib import Path
import re


_MAX_FRAME_BYTES = 1024 * 1024
_MAX_CHECKPOINT_BYTES = 1024 * 1024 * 1024
_CHECKPOINT_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
# RF-DETR 1.9.1 returns COCO category IDs, which start at 1 and contain gaps.
# These IDs must match rfdetr.assets.coco_classes.COCO_CLASSES in that release.
_COCO_LABELS = {
    1: ("person", "person"),
    2: ("vehicle", "bicycle"),
    3: ("vehicle", "car"),
    4: ("vehicle", "motorcycle"),
    6: ("vehicle", "bus"),
    8: ("vehicle", "truck"),
    16: ("animal", "bird"),
    17: ("animal", "cat"),
    18: ("animal", "dog"),
    19: ("animal", "horse"),
    20: ("animal", "sheep"),
    21: ("animal", "cow"),
    22: ("animal", "elephant"),
    23: ("animal", "bear"),
    24: ("animal", "zebra"),
    25: ("animal", "giraffe"),
}


class DetectionError(ValueError):
    """Fixed failure code; never contains model output, media or local paths."""


@dataclass(frozen=True)
class ObjectObservation:
    kind: str
    label: str
    score: float
    # Coordinates are fractions of the decoded frame, in xyxy order.
    box: tuple[float, float, float, float]
    # Vehicle plate text, "?" for uncertain characters (aiport_plates).
    plate: str | None = None


def validate_checkpoint(path: str, expected_sha256: str) -> Path:
    """Require operator-supplied weights and verify them before model import."""
    if (not isinstance(path, str) or not Path(path).is_absolute()
            or not isinstance(expected_sha256, str)
            or not _CHECKPOINT_HASH.fullmatch(expected_sha256)):
        raise DetectionError("invalid_checkpoint_policy")
    checkpoint = Path(path)
    try:
        if checkpoint.is_symlink() or not checkpoint.is_file() or checkpoint.suffix != ".pth":
            raise DetectionError("checkpoint_unavailable")
        size = checkpoint.stat().st_size
        if not 0 < size <= _MAX_CHECKPOINT_BYTES:
            raise DetectionError("checkpoint_size_invalid")
        digest = hashlib.sha256()
        with checkpoint.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise DetectionError("checkpoint_unavailable") from exc
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
        raise DetectionError("checkpoint_hash_mismatch")
    return checkpoint


class RFDetrNanoDetector:
    """Opt-in COCO detector using local, pinned RF-DETR Nano weights.

    Model loading is explicit and never downloads a default checkpoint. The
    optional RF-DETR dependency is imported only after the local file passes
    integrity checks.
    """

    def __init__(self, model: object, *, threshold: float = 0.5):
        if (type(threshold) not in (float, int) or not math.isfinite(threshold)
                or not 0 < threshold < 1):
            raise DetectionError("invalid_detection_threshold")
        self.model = model
        self.threshold = float(threshold)

    @classmethod
    def from_checkpoint(cls, path: str, expected_sha256: str, *,
                        threshold: float = 0.5) -> RFDetrNanoDetector:
        checkpoint = validate_checkpoint(path, expected_sha256)
        try:
            from rfdetr import RFDETRNano
        except ImportError as exc:
            raise DetectionError("detector_dependency_unavailable") from exc
        try:
            return cls(RFDETRNano(pretrain_weights=str(checkpoint), trust_checkpoint=False),
                       threshold=threshold)
        except Exception as exc:
            raise DetectionError("checkpoint_load_failed") from exc

    def detect(self, frame: bytes) -> tuple[ObjectObservation, ...]:
        if (not isinstance(frame, bytes) or len(frame) > _MAX_FRAME_BYTES
                or not frame.startswith(b"\xff\xd8\xff")):
            raise DetectionError("invalid_detector_frame")
        try:
            import numpy as np
            from PIL import Image, UnidentifiedImageError
        except ImportError as exc:
            raise DetectionError("detector_dependency_unavailable") from exc
        try:
            with Image.open(BytesIO(frame)) as encoded:
                if (encoded.format != "JPEG" or not (16 <= encoded.width <= 8192
                                                     and 16 <= encoded.height <= 4320)
                        or encoded.width * encoded.height > 4_000_000):
                    raise DetectionError("invalid_detector_frame")
                width, height = encoded.size
                image = np.array(encoded.convert("RGB"), copy=True)
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise DetectionError("invalid_detector_frame") from exc
        try:
            result = self.model.predict(
                image, threshold=self.threshold, include_source_image=False)
        except Exception as exc:
            raise DetectionError("detector_inference_failed") from exc
        try:
            class_ids = result.class_id
            scores = result.confidence
            boxes = result.xyxy
            if not (len(class_ids) == len(scores) == len(boxes) <= 100):
                raise ValueError
            observations = []
            for class_id, score, box in zip(class_ids, scores, boxes, strict=True):
                numeric_id = float(class_id)
                if (not math.isfinite(numeric_id) or numeric_id != int(numeric_id)
                        or isinstance(class_id, bool)):
                    raise ValueError
                score = float(score)
                x1, y1, x2, y2 = (float(point) for point in box)
                if (not math.isfinite(score) or not 0 <= score <= 1
                        or not all(math.isfinite(point) for point in (x1, y1, x2, y2))
                        or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height)):
                    raise ValueError
                category = _COCO_LABELS.get(int(numeric_id))
                if category is None or score < self.threshold:
                    continue
                observations.append(ObjectObservation(
                    category[0], category[1], score,
                    (x1 / width, y1 / height, x2 / width, y2 / height)))
            return tuple(observations)
        except Exception as exc:
            raise DetectionError("invalid_detector_output") from exc
