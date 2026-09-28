"""Local face detection and 512-value face embeddings for paired cameras (#20, #28).

Protect's detection service groups a face by the ``faceEmbed`` float array of
its tracker (``trackerIDAttrMap``) through the console's face search, the same
way it does for a camera with onboard face detection. A paired legacy camera
has none, so the AI Port supplies it:

* YuNet (OpenCV Zoo, MIT) finds the face and five landmarks inside the upper
  part of a detected person;
* the face is aligned to the standard 112x112 ArcFace template;
* ArcFace ResNet100 (ONNX Model Zoo, Apache-2.0) returns a 512-value
  embedding, L2-normalised.

Both models run locally on the AI Port host; nothing leaves the network and no
provider is called. The embedding exists only inside the event message that
Protect asked for; it is never logged or kept. Attribute names follow the
face records Protect stores for an onboard-face camera.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import math
from pathlib import Path

from PIL import Image, ImageFilter, UnidentifiedImageError

from .aiport_snapshots import SmartSnapshot, SnapshotError

# ArcFace's canonical five-point template for a 112x112 crop, in image order:
# eye on the left, eye on the right, nose, mouth corner left, mouth corner right.
_TEMPLATE = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366),
             (41.5493, 92.3655), (70.7299, 92.2041))
_YUNET_SIZE = 640
_STRIDES = (8, 16, 32)
EMBEDDING_SIZE = 512


class FaceError(ValueError):
    """Fixed-code face failure; never carries image content."""


@dataclass(frozen=True)
class FaceResult:
    box: tuple[float, float, float, float]            # normalised x1, y1, x2, y2
    landmarks: tuple[tuple[float, float], ...]        # five normalised points
    score: float
    embedding: tuple[float, ...]
    quality: float                                    # 0..1
    blurness: float                                   # 0 (sharp) .. 1 (blurred)
    pose: dict

    def attributes(self, track_id: int, zone_ids: tuple[int, ...]) -> dict:
        """The face tracker's attributes, shaped like Protect's own face records."""
        points = [round(value * 1000) for point in self.landmarks for value in point]
        # Protect's own face records (G6, 7.3.68): faceMask.val is "face" or
        # "face_mask" and qualityScore is 0..100. A value outside that
        # vocabulary made Protect drop the whole event message (28 Sep).
        return {"objectType": "face", "trackerId": track_id, "zone": list(zone_ids),
                "faceEmbed": [round(value, 6) for value in self.embedding],
                "faceLandmarks": points, "qualityScore": round(self.quality * 100),
                "blurness": round(self.blurness * 100, 1), "facePose": dict(self.pose),
                # Protect's detection service parses both as u8 (0..100); a
                # float here dropped the whole event message (ds.log, 28 Sep).
                "faceMask": {"val": "face", "confidence": round(self.score * 100)},
                # The same six checks Protect stores for an onboard face; lower
                # is better, as in its records.
                "faceVerifyStatus": [
                    {"verifyType": "is_invalid_cropped", "confidence": 0.05},
                    {"verifyType": "occluded", "confidence": 0.05},
                    {"verifyType": "blur_motion", "confidence": 0.0},
                    {"verifyType": "blur_focus", "confidence": round(self.blurness * 0.01, 6)},
                    {"verifyType": "bad_pose", "confidence": round(
                        min(1.0, abs(self.pose.get("yaw", 0.0)) / 90), 6)},
                    {"verifyType": "non_face", "confidence": round(1 - self.score, 6)}],
                "namesTopK": [], "topKCandidate": [], "matchedName": ""}

    def descriptor(self, track_id: int, zone_ids: tuple[int, ...]) -> dict:
        x1, y1, x2, y2 = self.box
        return {"trackerID": track_id, "name": "face",
                "confidenceLevel": round(self.score * 100),
                "coord": [round(x1 * 1000), round(y1 * 1000),
                          round((x2 - x1) * 1000), round((y2 - y1) * 1000)],
                "objectType": "face", "zones": list(zone_ids), "lines": [],
                "stationary": False, "coord3d": [],
                "attributes": {"faceMask": {"val": "face", "confidence": round(self.score * 100)}}}


def verify_model(path: str, sha256: str) -> str:
    """The model path, if the file exists and matches its pinned digest."""
    file = Path(path)
    if not file.is_absolute() or not file.is_file():
        raise FaceError("face_model_missing")
    digest = hashlib.sha256()
    with file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise FaceError("face_model_digest")
    return str(file)


def similarity_transform(source, target) -> tuple[float, float, float, float, float, float]:
    """Least-squares similarity (Umeyama) mapping source points onto target.

    Returns (a, b, tx, c, d, ty) with x' = a*x + b*y + tx and y' = c*x + d*y + ty.
    """
    n = len(source)
    if n < 2 or n != len(target):
        raise FaceError("invalid_landmarks")
    mx = sum(p[0] for p in source) / n
    my = sum(p[1] for p in source) / n
    ux = sum(p[0] for p in target) / n
    uy = sum(p[1] for p in target) / n
    sxx = sxy = norm = 0.0
    for (x, y), (u, v) in zip(source, target):
        x, y, u, v = x - mx, y - my, u - ux, v - uy
        sxx += x * u + y * v
        sxy += x * v - y * u
        norm += x * x + y * y
    if norm <= 1e-9:
        raise FaceError("invalid_landmarks")
    a, b = sxx / norm, sxy / norm            # scale*cos, scale*sin
    return (a, -b, ux - a * mx + b * my, b, a, uy - b * mx - a * my)


def estimate_pose(landmarks) -> dict:
    """Coarse yaw, pitch and roll in degrees from five landmarks."""
    (lx, ly), (rx, ry), (nx, ny), (mlx, mly), (mrx, mry) = landmarks
    eye_dx, eye_dy = rx - lx, ry - ly
    width = math.hypot(eye_dx, eye_dy) or 1e-6
    roll = math.degrees(math.atan2(eye_dy, eye_dx))
    eye_mid = ((lx + rx) / 2, (ly + ry) / 2)
    mouth_mid = ((mlx + mrx) / 2, (mly + mry) / 2)
    yaw = max(-90.0, min(90.0, (nx - eye_mid[0]) / width * 120))
    height = math.hypot(mouth_mid[0] - eye_mid[0], mouth_mid[1] - eye_mid[1]) or 1e-6
    pitch = max(-90.0, min(90.0, ((ny - eye_mid[1]) / height - 0.55) * 120))
    return {"yaw": round(yaw, 1), "pitch": round(pitch, 1), "roll": round(roll, 1)}


def decode_yunet(outputs: dict, *, threshold: float) -> list[tuple[float, tuple, tuple]]:
    """(score, box xywh, five landmarks) in 640x640 input pixels, after NMS."""
    found = []
    for stride in _STRIDES:
        cols = _YUNET_SIZE // stride
        cls, obj = outputs[f"cls_{stride}"][0], outputs[f"obj_{stride}"][0]
        bbox, kps = outputs[f"bbox_{stride}"][0], outputs[f"kps_{stride}"][0]
        for index in range(len(cls)):
            score = math.sqrt(max(0.0, min(1.0, float(cls[index][0])))
                              * max(0.0, min(1.0, float(obj[index][0]))))
            if score < threshold:
                continue
            row, col = divmod(index, cols)
            box = bbox[index]
            cx, cy = (col + float(box[0])) * stride, (row + float(box[1])) * stride
            w, h = math.exp(float(box[2])) * stride, math.exp(float(box[3])) * stride
            points = tuple(((float(kps[index][2 * k]) + col) * stride,
                            (float(kps[index][2 * k + 1]) + row) * stride) for k in range(5))
            found.append((score, (cx - w / 2, cy - h / 2, w, h), points))
    found.sort(key=lambda item: item[0], reverse=True)
    kept = []
    for item in found:
        if all(_iou(item[1], other[1]) < 0.3 for other in kept):
            kept.append(item)
    return kept


def _iou(a, b) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _blurness(face: Image.Image) -> float:
    """0 for a sharp face, 1 for a flat one, from edge energy."""
    edges = face.convert("L").resize((64, 64)).filter(ImageFilter.FIND_EDGES)
    pixels = list(edges.getdata())
    energy = sum(value * value for value in pixels) / len(pixels)
    return max(0.0, min(1.0, 1.0 - math.sqrt(energy) / 64.0))


class FaceEngine:
    """Face detection and embedding with two pinned local ONNX models."""

    def __init__(self, detector_path: str, embedder_path: str, *,
                 min_score: float = 0.75, min_face_px: int = 32, session_factory=None):
        if not 0.3 <= min_score <= 0.99 or not 16 <= min_face_px <= 256:
            raise FaceError("invalid_face_settings")
        if session_factory is None:
            import onnxruntime   # optional dependency of the AI Port image

            def session_factory(path):
                options = onnxruntime.SessionOptions()
                options.intra_op_num_threads = 2
                return onnxruntime.InferenceSession(path, options,
                                                    providers=["CPUExecutionProvider"])
        self._detector = session_factory(detector_path)
        self._embedder = session_factory(embedder_path)
        self.min_score, self.min_face_px = min_score, min_face_px
        self.analysed = 0
        self.faces = 0

    def analyse(self, frame: bytes, person_box: tuple[float, float, float, float]
                ) -> FaceResult | None:
        """The best face inside a person's box, or None."""
        import numpy as np
        try:
            with Image.open(BytesIO(frame)) as image:
                if image.format != "JPEG":
                    raise FaceError("invalid_face_frame")
                image = image.convert("RGB")
        except (OSError, UnidentifiedImageError) as exc:
            raise FaceError("invalid_face_frame") from exc
        width, height = image.size
        x1, y1, x2, y2 = person_box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise FaceError("invalid_person_box")
        # A face sits in the upper part of a person; a small margin keeps an
        # upward-tilted head.
        pad = 0.08 * (x2 - x1)
        left, top = max(0.0, x1 - pad) * width, max(0.0, y1 - 0.05 * (y2 - y1)) * height
        right = min(1.0, x2 + pad) * width
        bottom = (y1 + 0.6 * (y2 - y1)) * height
        if right - left < self.min_face_px or bottom - top < self.min_face_px:
            return None
        region = image.crop((round(left), round(top), round(right), round(bottom)))
        scale = _YUNET_SIZE / max(region.size)
        canvas = Image.new("RGB", (_YUNET_SIZE, _YUNET_SIZE))
        canvas.paste(region.resize((max(1, round(region.width * scale)),
                                    max(1, round(region.height * scale)))))
        pixels = np.asarray(canvas, dtype=np.float32)[:, :, ::-1]      # YuNet wants BGR
        tensor = np.ascontiguousarray(pixels.transpose(2, 0, 1)[None])
        names = [output.name for output in self._detector.get_outputs()]
        values = self._detector.run(names, {self._detector.get_inputs()[0].name: tensor})
        self.analysed += 1
        faces = decode_yunet(dict(zip(names, values)), threshold=self.min_score)
        if not faces:
            return None
        score, (fx, fy, fw, fh), points = faces[0]
        if min(fw, fh) / scale < self.min_face_px:
            return None
        to_frame = lambda px, py: (left + px / scale, top + py / scale)    # noqa: E731
        box_px = (*to_frame(fx, fy), *to_frame(fx + fw, fy + fh))
        landmarks_px = tuple(to_frame(px, py) for px, py in points)
        a, b, tx, c, d, ty = similarity_transform(landmarks_px, _TEMPLATE)
        det = a * d - b * c
        if abs(det) < 1e-9:
            return None
        # PIL maps output to input: invert the similarity transform.
        ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
        aligned = image.transform((112, 112), Image.Transform.AFFINE,
                                  (ia, ib, -(ia * tx + ib * ty), ic, id_, -(ic * tx + id_ * ty)),
                                  resample=Image.Resampling.BILINEAR)
        face_tensor = np.ascontiguousarray(
            np.asarray(aligned, dtype=np.float32).transpose(2, 0, 1)[None])     # RGB, 0..255
        vector = self._embedder.run(None, {self._embedder.get_inputs()[0].name: face_tensor})[0][0]
        norm = float(np.linalg.norm(vector))
        if vector.shape != (EMBEDDING_SIZE,) or not math.isfinite(norm) or norm <= 0:
            raise FaceError("invalid_face_embedding")
        embedding = tuple(float(value) / norm for value in vector)
        face_px = min(box_px[2] - box_px[0], box_px[3] - box_px[1])
        blur = _blurness(aligned)
        quality = max(0.0, min(1.0, score * min(1.0, face_px / 96.0) * (1.0 - 0.5 * blur)))
        self.faces += 1
        norm_box = (max(0.0, box_px[0] / width), max(0.0, box_px[1] / height),
                    min(1.0, box_px[2] / width), min(1.0, box_px[3] / height))
        return FaceResult(norm_box, tuple((px / width, py / height) for px, py in landmarks_px),
                          score, embedding, quality, blur, estimate_pose(landmarks_px))


def make_face_snapshot(frame: bytes, face: FaceResult, track_id: int, wall_ms: int, *,
                       filename_id: int) -> SmartSnapshot:
    """A face crop that Protect can request like any smart-detection snapshot."""
    if type(track_id) is not int or track_id <= 0 or type(wall_ms) is not int or wall_ms <= 0:
        raise SnapshotError("invalid_snapshot_track")
    try:
        with Image.open(BytesIO(frame)) as image:
            image.load()
            x1, y1, x2, y2 = face.box
            cx, cy = (x1 + x2) / 2 * image.width, (y1 + y2) / 2 * image.height
            side = max(32, min(max((x2 - x1) * image.width, (y2 - y1) * image.height) * 1.6,
                               image.width, image.height))
            left = max(0, min(image.width - side, cx - side / 2))
            top = max(0, min(image.height - side, cy - side / 2))
            crop = image.crop((round(left), round(top), round(left + side), round(top + side)))
            crop.thumbnail((256, 256))
            output = BytesIO()
            crop.convert("RGB").save(output, format="JPEG", quality=85)
            full = BytesIO()
            image.convert("RGB").save(full, format="JPEG", quality=85)
            full_size = image.size
    except (OSError, UnidentifiedImageError) as exc:
        raise SnapshotError("invalid_snapshot_frame") from exc
    filename = f"smartdetectsnap_face_{filename_id}{wall_ms}.jpg"
    x1, y1, x2, y2 = face.box
    metadata = {"clockBestWall": wall_ms, "smartDetectSnapshot": filename,
                "smartDetectSnapshotType": "face", "smartDetectSnapshotName": "",
                "smartDetectSnapshotWidth": crop.width, "smartDetectSnapshotHeight": crop.height,
                "trackerID": track_id, "confidenceLevel": round(face.score * 100),
                "coord": [round(x1 * 1000), round(y1 * 1000),
                          round((x2 - x1) * 1000), round((y2 - y1) * 1000)],
                "reVerifyEligible": False}
    return SmartSnapshot(filename, output.getvalue(), metadata,
                         f"smartdetectsnap_face_{filename_id}{wall_ms}_fullfov.jpg",
                         full.getvalue(), full_size[0], full_size[1])
