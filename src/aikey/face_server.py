"""Local face detection and embedding server for AI Key face recognition (#20).

``POST /v1/faces`` takes one JPEG (and optional normalized regions to search)
and returns the faces found, each with a normalized box, a detector score
and a 128-value SFace embedding. It runs OpenCV's YuNet detector and SFace
recognizer entirely on this machine: no face image or embedding leaves it,
and nothing is logged or stored. Identity matching happens in the AI Key
against its own private, deletable templates (``aikey.faces``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from typing import Callable

from aiohttp import web


_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_REGIONS = 16
_MAX_FACES = 16

# (jpeg bytes, regions or None) -> [(box xyxy normalized, score, embedding)]
Analyzer = Callable[[bytes, list | None],
                    list[tuple[tuple[float, float, float, float], float, list[float]]]]


class ImageError(ValueError):
    """The upload is not a bounded JPEG or the regions are malformed."""


def parse_regions(raw: str | None) -> list[tuple[float, float, float, float]] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ImageError("regions must be JSON") from exc
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_REGIONS:
        raise ImageError("1 to 16 regions")
    regions = []
    for box in value:
        if (not isinstance(box, list) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)):
            raise ImageError("Each region is [x1, y1, x2, y2]")
        x1, y1, x2, y2 = (float(v) for v in box)
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ImageError("Regions are normalized xyxy boxes")
        regions.append((x1, y1, x2, y2))
    return regions


def opencv_analyzer(detector_path: str, recognizer_path: str, *,
                    score_threshold: float = 0.8) -> Analyzer:
    import cv2
    import numpy
    recognizer = cv2.FaceRecognizerSF.create(recognizer_path, "")

    def analyze(jpeg, regions):
        image = cv2.imdecode(numpy.frombuffer(jpeg, dtype=numpy.uint8), cv2.IMREAD_COLOR)
        if image is None or image.ndim != 3:
            raise ImageError("Unreadable JPEG")
        height, width = image.shape[:2]
        found = []
        for x1, y1, x2, y2 in regions or [(0.0, 0.0, 1.0, 1.0)]:
            left, top = int(x1 * width), int(y1 * height)
            crop = image[top:max(top + 1, int(y2 * height)), left:max(left + 1, int(x2 * width))]
            if crop.shape[0] < 16 or crop.shape[1] < 16:
                continue
            detector = cv2.FaceDetectorYN.create(detector_path, "", (crop.shape[1], crop.shape[0]),
                                                 score_threshold, 0.3, _MAX_FACES)
            _, faces = detector.detect(crop)
            for face in (faces if faces is not None else [])[:_MAX_FACES]:
                aligned = recognizer.alignCrop(crop, face)
                embedding = recognizer.feature(aligned).flatten().tolist()
                fx, fy, fw, fh = (float(v) for v in face[:4])
                box = (max(0.0, (left + fx) / width), max(0.0, (top + fy) / height),
                       min(1.0, (left + fx + fw) / width), min(1.0, (top + fy + fh) / height))
                if box[0] < box[2] and box[1] < box[3]:
                    found.append((box, float(face[-1]), embedding))
        return found[:_MAX_FACES]
    return analyze


def build_app(analyze: Analyzer) -> web.Application:
    lock = asyncio.Lock()
    counters = {"requests": 0, "analyzed": 0, "faces": 0, "rejected": 0, "failed": 0}

    async def faces(request: web.Request) -> web.Response:
        counters["requests"] += 1
        image, regions = None, None
        try:
            if request.content_type != "multipart/form-data":
                raise ImageError("multipart required")
            reader = await request.multipart()
            while (part := await reader.next()) is not None:
                if part.name == "image":
                    image = await part.read(decode=False)
                    if len(image) > _MAX_IMAGE_BYTES:
                        raise ImageError("Image too large")
                elif part.name == "regions":
                    regions = parse_regions((await part.read(decode=False))[:65536].decode())
            if image is None or not image.startswith(b"\xff\xd8\xff"):
                raise ImageError("JPEG required")
        except (ImageError, UnicodeDecodeError):
            counters["rejected"] += 1
            return web.json_response({"error": "invalid_request"}, status=400)
        async with lock:
            try:
                result = await asyncio.to_thread(analyze, image, regions)
            except ImageError:
                counters["rejected"] += 1
                return web.json_response({"error": "invalid_image"}, status=400)
            except Exception:
                counters["failed"] += 1
                return web.json_response({"error": "analysis_failed"}, status=500)
        counters["analyzed"] += 1
        counters["faces"] += len(result)
        return web.json_response({"faces": [
            {"box": [round(v, 5) for v in box], "score": round(score, 4), "embedding": embedding}
            for box, score, embedding in result]})

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", **counters})

    app = web.Application(client_max_size=_MAX_IMAGE_BYTES + 131072)
    app.router.add_post("/v1/faces", faces)
    app.router.add_get("/healthz", health)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-face-server")
    parser.add_argument("--detector", required=True)
    parser.add_argument("--recognizer", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8179)
    parser.add_argument("--score-threshold", type=float, default=0.8)
    args = parser.parse_args(argv)
    app = build_app(opencv_analyzer(args.detector, args.recognizer,
                                    score_threshold=args.score_threshold))
    web.run_app(app, host=args.host, port=args.port, access_log=None, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
