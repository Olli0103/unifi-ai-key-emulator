from io import BytesIO
import hashlib
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from aikey.aiport_detection import DetectionError
from aikey.aiport_onnx_detection import OnnxRFDetrNanoDetector, validate_onnx_model


class Session:
    def __init__(self, outputs):
        self.outputs = outputs

    def get_inputs(self):
        return [SimpleNamespace(name="image", shape=[1, 3, 384, 384])]

    def get_outputs(self):
        return [SimpleNamespace(name="dets", shape=[1, 300, 4]),
                SimpleNamespace(name="labels", shape=[1, 300, 91])]

    def run(self, names, feed):
        assert names == ["dets", "labels"]
        assert feed["image"].shape == (1, 3, 384, 384)
        return self.outputs


def frame():
    encoded = BytesIO()
    Image.new("RGB", (64, 48), "white").save(encoded, format="JPEG")
    return encoded.getvalue()


def test_pinned_model_rejects_tampering_and_symlink(tmp_path):
    model = tmp_path / "nano.onnx"
    model.write_bytes(b"test model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    assert validate_onnx_model(str(model), digest) == model
    with pytest.raises(DetectionError, match="onnx_model_hash_mismatch"):
        validate_onnx_model(str(model), "0" * 64)
    link = tmp_path / "link.onnx"
    link.symlink_to(model)
    with pytest.raises(DetectionError, match="onnx_model_unavailable"):
        validate_onnx_model(str(link), digest)


def test_onnx_decodes_sparse_coco_id_and_rejects_nonfinite_output(monkeypatch):
    helper = ModuleType("rfdetr.export._onnx.inference")
    helper._preprocess_pil_to_nchw = lambda image, h, w, c: np.zeros((1, c, h, w),
                                                                       dtype=np.float32)
    monkeypatch.setitem(sys.modules, "rfdetr", ModuleType("rfdetr"))
    monkeypatch.setitem(sys.modules, "rfdetr.export", ModuleType("rfdetr.export"))
    monkeypatch.setitem(sys.modules, "rfdetr.export._onnx", ModuleType("rfdetr.export._onnx"))
    monkeypatch.setitem(sys.modules, "rfdetr.export._onnx.inference", helper)
    boxes = np.full((1, 300, 4), [0.5, 0.5, 0.3, 0.4], dtype=np.float32)
    logits = np.full((1, 300, 91), -20.0, dtype=np.float32)
    logits[0, 0, 1] = 4.0  # COCO category 1: person
    logits[0, 1, 18] = 4.0  # COCO category 18: dog
    detector = OnnxRFDetrNanoDetector(Session([boxes, logits]), threshold=0.5)
    observations = detector.detect(frame())
    assert [(item.kind, item.label) for item in observations] == [
        ("person", "person"), ("animal", "dog")]
    assert observations[0].box == pytest.approx((0.35, 0.3, 0.65, 0.7), abs=1e-6)
    boxes[0, 0, 0] = np.nan
    with pytest.raises(DetectionError, match="invalid_detector_output"):
        detector.detect(frame())


def test_openvino_request_fails_when_only_cpu_provider_is_available(tmp_path,
                                                                    monkeypatch):
    model = tmp_path / "nano.onnx"
    model.write_bytes(b"test model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    runtime = ModuleType("onnxruntime")
    runtime.get_available_providers = lambda: ["CPUExecutionProvider"]
    monkeypatch.setitem(sys.modules, "onnxruntime", runtime)
    with pytest.raises(DetectionError, match="onnx_provider_unavailable"):
        OnnxRFDetrNanoDetector.from_model(str(model), digest,
                                         backend="onnx_openvino_gpu")


def test_openvino_request_rejects_runtime_cpu_fallback(tmp_path, monkeypatch):
    model = tmp_path / "nano.onnx"
    model.write_bytes(b"test model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    runtime = ModuleType("onnxruntime")
    runtime.get_available_providers = lambda: ["OpenVINOExecutionProvider",
                                               "CPUExecutionProvider"]
    runtime.InferenceSession = lambda *args, **kwargs: SimpleNamespace(
        get_providers=lambda: ["CPUExecutionProvider"])
    monkeypatch.setitem(sys.modules, "onnxruntime", runtime)
    with pytest.raises(DetectionError, match="onnx_provider_unavailable"):
        OnnxRFDetrNanoDetector.from_model(str(model), digest,
                                         backend="onnx_openvino_gpu")
