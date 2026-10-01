"""Face geometry shared by the AI Port face engine and the vision server.

YuNet (OpenCV Zoo, MIT) output decoding and the least-squares similarity
transform that maps five landmarks onto an alignment template. No images or
models here, only arithmetic.
"""

from __future__ import annotations

import math

# ArcFace's canonical five-point template for a 112x112 crop, in image order:
# eye on the left, eye on the right, nose, mouth corner left, mouth corner right.
ARCFACE_TEMPLATE = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366),
                    (41.5493, 92.3655), (70.7299, 92.2041))
# GFPGAN's FFHQ template for a 512x512 face, same point order.
FFHQ_TEMPLATE_512 = ((192.98138, 239.94708), (318.90277, 240.1936), (256.63416, 314.01935),
                     (201.26117, 371.41043), (313.08905, 371.15118))
YUNET_SIZE = 640
_STRIDES = (8, 16, 32)
_YUNET_SIZE = YUNET_SIZE


class FaceError(ValueError):
    """Fixed-code face failure; never carries image content."""


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


