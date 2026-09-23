"""Local object observations must remain bounded and separate from native events."""

from io import BytesIO
import hashlib
from types import SimpleNamespace

import pytest
from PIL import Image

from aikey.aiport_detection import DetectionError, RFDetrNanoDetector, validate_checkpoint


def jpeg_frame() -> bytes:
    image = Image.new("RGB", (64, 48), "white")
    output = BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def test_checkpoint_must_be_local_and_pinned(tmp_path):
    checkpoint = tmp_path / "nano.pth"
    checkpoint.write_bytes(b"synthetic-checkpoint")
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert validate_checkpoint(str(checkpoint), digest) == checkpoint
    with pytest.raises(DetectionError, match="checkpoint_hash_mismatch"):
        validate_checkpoint(str(checkpoint), "0" * 64)
    with pytest.raises(DetectionError, match="invalid_checkpoint_policy"):
        validate_checkpoint("nano.pth", digest)
    linked = tmp_path / "linked.pth"
    linked.symlink_to(checkpoint)
    with pytest.raises(DetectionError, match="checkpoint_unavailable"):
        validate_checkpoint(str(linked), digest)


def test_local_detector_returns_normalized_supported_objects():
    class Model:
        def predict(self, image, *, threshold, include_source_image):
            assert image.shape == (48, 64, 3)
            assert threshold == 0.5
            assert include_source_image is False
            return SimpleNamespace(class_id=[1, 3, 18, 60],
                                   confidence=[0.9, 0.8, 0.7, 0.9],
                                   xyxy=[(8, 6, 40, 42), (0, 0, 32, 24),
                                         (32, 24, 64, 48), (0, 0, 10, 10)])

    detections = RFDetrNanoDetector(Model()).detect(jpeg_frame())
    assert [d.kind for d in detections] == ["person", "vehicle", "animal"]
    assert [d.label for d in detections] == ["person", "car", "dog"]
    assert detections[0].box == (0.125, 0.125, 0.625, 0.875)
    assert detections[1].box == (0, 0, 0.5, 0.5)


@pytest.mark.parametrize("box,score,class_id", [
    ((-1, 0, 10, 10), 0.9, 1),
    ((0, 0, 65, 10), 0.9, 1),
    ((10, 10, 10, 20), 0.9, 1),
    ((0, 0, 10, 10), float("nan"), 1),
    ((0, 0, 10, 10), 0.9, 0.5),
])
def test_local_detector_rejects_invalid_model_output(box, score, class_id):
    model = SimpleNamespace(predict=lambda image, threshold, include_source_image: SimpleNamespace(
        class_id=[class_id], confidence=[score], xyxy=[box]))
    with pytest.raises(DetectionError, match="invalid_detector_output"):
        RFDetrNanoDetector(model).detect(jpeg_frame())


def test_local_detector_rejects_non_jpeg_and_corrupt_jpeg():
    detector = RFDetrNanoDetector(SimpleNamespace(predict=lambda *a, **k: None))
    for frame in (b"", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xffbroken"):
        with pytest.raises(DetectionError, match="invalid_detector_frame"):
            detector.detect(frame)
