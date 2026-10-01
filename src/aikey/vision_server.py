"""Local OpenVINO vision models for the AI Key: open-vocabulary tags, faces, text.

One small server holds three optional models, each compiled for the first
device in ``--device`` that accepts it (the NAS NPU, then the iGPU, then the
CPU):

- ``POST /v1/tags``: one JPEG, answered with RAM++ tags above their per-class
  thresholds, as ``{"tags": [{"tag", "confScore"}], "model"}``.
- ``POST /v1/enhance``: one JPEG face crop, answered with a GFPGAN-restored
  JPEG at least as large as the crop, or 204 to decline. With ``--face-detector``
  (YuNet) the face is aligned to GFPGAN's FFHQ template first and pasted back;
  crops too small to show a face are declined.
- ``POST /v1/embeddings``: OpenAI-compatible multilingual-e5-small vectors
  (384 values, mean-pooled and L2-normalized). Callers add E5's ``query:`` or
  ``passage:`` prefix themselves.
- ``POST /v1/reid``: one JPEG person crop, answered with Intel's
  person-reidentification-retail-0288 vector (256 values, L2-normalized) for
  deep-mode session grouping.
- ``POST /v1/rerank``: ``{"query", "documents"}``, answered with one
  cross-encoder relevance logit per document as ``{"scores", "model"}``; the
  Key relays Protect's hybrid session-search rerank sidecar here.

Images and texts are never logged or stored; only counters are kept. The
server listens on a container network address.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from pathlib import Path
from typing import Any, Callable

from aiohttp import web

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_TEXTS = 32
_MAX_TEXT_CHARS = 8192
_MAX_TAGS = 32
_TAG_SIZE = 384
_TAG_MEAN = (0.485, 0.456, 0.406)
_TAG_STD = (0.229, 0.224, 0.225)
_FACE_SIZE = 512
_MIN_FACE_SIDE = 24
# A crop is the face plus 1.6x context; under 64 px the face is below about
# 40 px and any restorer invents it (1 Oct). Applies with the aligned path.
_MIN_ALIGNED_CROP_SIDE = 64
_YUNET_THRESHOLD = 0.6
_TEXT_BUCKETS = (128, 512)
_RERANK_BUCKETS = (128, 256)
_MAX_DOCUMENTS = 32                       # Protect reranks at most 30 sessions
TAG_MODEL = "ram-plus-swin-large-14m"
FACE_MODEL = "gfpgan-1.4"
TEXT_MODEL = "intfloat/multilingual-e5-small"
REID_MODEL = "person-reidentification-retail-0288"
RERANK_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
_REID_SIZE = (128, 256)                   # width, height


class InputError(ValueError):
    """The request is not a bounded JPEG or a bounded list of texts."""


def _compile(core, model, devices: list[str]):
    """Compile for the first device that accepts the model; return (compiled, device)."""
    failures = []
    for device in devices:
        if device not in core.available_devices and device != "CPU":
            failures.append(f"{device}: unavailable")
            continue
        try:
            return core.compile_model(model, device), device
        except RuntimeError as exc:
            failures.append(f"{device}: {type(exc).__name__}")
    raise RuntimeError("No device compiled the model (" + "; ".join(failures) + ")")


def named_outputs(compiled) -> Callable[[Any], dict]:
    """One inference per call, its results keyed by output name (YuNet has 12 outputs)."""
    ports = list(compiled.outputs)
    names = [port.any_name for port in ports]

    def run(pixels):
        result = compiled(pixels)
        return {name: result[port] for name, port in zip(names, ports)}
    return run


def _picture(jpeg: bytes):
    from PIL import Image
    try:
        picture = Image.open(io.BytesIO(jpeg))
        if picture.format != "JPEG" or max(picture.size) > 8192:
            raise InputError("JPEG required")
        return picture.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InputError("Unreadable image") from exc


def tag_pixels(picture):
    """RAM++'s own preprocessing: 384 x 384 bilinear, ImageNet normalization."""
    import numpy
    from PIL import Image
    pixels = numpy.asarray(picture.resize((_TAG_SIZE, _TAG_SIZE), Image.Resampling.BILINEAR),
                           dtype=numpy.float32) / 255.0
    pixels = (pixels - numpy.array(_TAG_MEAN, dtype=numpy.float32)) / numpy.array(_TAG_STD, dtype=numpy.float32)
    return pixels.transpose(2, 0, 1)[None]


def select_tags(probabilities, names: list[str], thresholds: list[float]) -> list[dict[str, Any]]:
    """Tags whose probability exceeds their RAM++ class threshold, strongest first."""
    chosen = [(float(p), name) for p, name, bar in zip(probabilities, names, thresholds) if p > bar]
    chosen.sort(key=lambda item: (-item[0], item[1]))
    return [{"tag": name, "confScore": round(p, 4)} for p, name in chosen[:_MAX_TAGS]]


def face_square(picture):
    """The crop padded to a square by edge replication, plus the crop's box in it."""
    import numpy
    from PIL import Image
    width, height = picture.size
    side = max(width, height)
    left, top = (side - width) // 2, (side - height) // 2
    pixels = numpy.asarray(picture)
    padded = numpy.pad(pixels, ((top, side - height - top), (left, side - width - left), (0, 0)),
                       mode="edge")
    return Image.fromarray(padded), (left, top, left + width, top + height), side


def detect_landmarks(run: Callable[[Any], Any], picture):
    """YuNet's five landmarks of the strongest face in the crop, or None."""
    import numpy
    from PIL import Image
    from .face_geometry import YUNET_SIZE, decode_yunet
    scale = YUNET_SIZE / max(picture.size)
    canvas = Image.new("RGB", (YUNET_SIZE, YUNET_SIZE))
    canvas.paste(picture.resize((max(1, round(picture.width * scale)),
                                 max(1, round(picture.height * scale))), Image.Resampling.BILINEAR))
    pixels = numpy.asarray(canvas, dtype=numpy.float32)[:, :, ::-1]          # YuNet wants BGR
    faces = decode_yunet(run(numpy.ascontiguousarray(pixels.transpose(2, 0, 1)[None])),
                         threshold=_YUNET_THRESHOLD)
    if not faces:
        return None
    return tuple((x / scale, y / scale) for x, y in faces[0][2])


def restore_aligned_face(run: Callable[[Any], Any], detect: Callable[[Any], Any], picture):
    """GFPGAN on the face aligned to its FFHQ template, pasted back into the crop.

    Returns (jpeg, aligned) where aligned says whether landmarks were found; the
    square method is the fallback. None declines a crop too small for a face.
    """
    import numpy
    from PIL import Image, ImageFilter
    from .face_geometry import FFHQ_TEMPLATE_512, FaceError, similarity_transform
    width, height = picture.size
    if min(width, height) < _MIN_ALIGNED_CROP_SIDE:
        return None
    landmarks = detect_landmarks(detect, picture)
    if landmarks is None:
        restored = restore_face(run, picture)
        return (restored, False) if restored else None
    try:
        a, b, tx, c, d, ty = similarity_transform(landmarks, FFHQ_TEMPLATE_512)
    except FaceError:
        restored = restore_face(run, picture)
        return (restored, False) if restored else None
    det = a * d - b * c
    # PIL maps output to input: crop -> template is inverted to warp the crop.
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    aligned = picture.transform((_FACE_SIZE, _FACE_SIZE), Image.Transform.AFFINE,
                                (ia, ib, -(ia * tx + ib * ty), ic, id_, -(ic * tx + id_ * ty)),
                                resample=Image.Resampling.BICUBIC)
    pixels = numpy.asarray(aligned, dtype=numpy.float32) / 255.0
    output = run(((pixels - 0.5) / 0.5).transpose(2, 0, 1)[None])
    output = numpy.clip((numpy.asarray(output)[0].transpose(1, 2, 0) + 1) / 2, 0, 1)
    face = Image.fromarray((output * 255 + 0.5).astype(numpy.uint8))
    # Enlarge the crop until the face reaches the restorer's resolution (at most 4x).
    k = max(1.0, min(4.0, (a * a + c * c) ** 0.5))
    size = (round(width * k), round(height * k))
    canvas = picture.resize(size, Image.Resampling.BICUBIC)
    forward = (a / k, b / k, tx, c / k, d / k, ty)
    pasted = face.transform(size, Image.Transform.AFFINE, forward, resample=Image.Resampling.BICUBIC)
    mask = Image.new("L", (_FACE_SIZE, _FACE_SIZE), 0)
    mask.paste(255, (24, 24, _FACE_SIZE - 24, _FACE_SIZE - 24))
    mask = mask.filter(ImageFilter.GaussianBlur(16)).transform(
        size, Image.Transform.AFFINE, forward, resample=Image.Resampling.BILINEAR)
    canvas.paste(pasted, (0, 0), mask)
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=92)
    return out.getvalue(), True


def restore_face(run: Callable[[Any], Any], picture):
    """GFPGAN on the padded square; the crop's area scaled to at least its own size."""
    import numpy
    from PIL import Image
    width, height = picture.size
    if min(width, height) < _MIN_FACE_SIDE:
        return None
    square, box, side = face_square(picture)
    pixels = numpy.asarray(square.resize((_FACE_SIZE, _FACE_SIZE), Image.Resampling.BICUBIC),
                           dtype=numpy.float32) / 255.0
    output = run(((pixels - 0.5) / 0.5).transpose(2, 0, 1)[None])
    output = numpy.clip((numpy.asarray(output)[0].transpose(1, 2, 0) + 1) / 2, 0, 1)
    restored = Image.fromarray((output * 255 + 0.5).astype(numpy.uint8))
    scale = max(1.0, _FACE_SIZE / side)
    if scale == 1.0:
        restored = restored.resize((side, side), Image.Resampling.BICUBIC)
    else:
        restored = restored.resize((round(side * scale),) * 2, Image.Resampling.BICUBIC)
    crop = restored.crop(tuple(round(v * scale) for v in box))
    if crop.size[0] < width or crop.size[1] < height:
        crop = crop.resize((max(width, crop.size[0]), max(height, crop.size[1])),
                           Image.Resampling.BICUBIC)
    out = io.BytesIO()
    crop.save(out, format="JPEG", quality=92)
    return out.getvalue()


def reid_pixels(picture):
    """The re-ID model's input: 128 x 256, BGR, 0..255, NCHW."""
    import numpy
    from PIL import Image
    pixels = numpy.asarray(picture.resize(_REID_SIZE, Image.Resampling.BILINEAR), dtype=numpy.float32)
    return pixels[:, :, ::-1].transpose(2, 0, 1)[None].copy()


def text_inputs(ids: list[int], pad_id: int, buckets=_TEXT_BUCKETS):
    """Token IDs padded to the smallest static bucket, with the attention mask."""
    import numpy
    length = next(b for b in buckets if len(ids) <= b) if len(ids) <= buckets[-1] \
        else buckets[-1]
    ids = ids[:length]
    mask = [1] * len(ids) + [0] * (length - len(ids))
    ids = ids + [pad_id] * (length - len(ids))
    return length, numpy.array([ids], dtype=numpy.int64), numpy.array([mask], dtype=numpy.int64)


class Models:
    """The compiled models; any of them may be absent."""

    def __init__(self, *, tags_dir: str | None, face_dir: str | None, text_dir: str | None,
                 devices: list[str], cache_dir: str | None,
                 text_devices: list[str] | None = None, reid_dir: str | None = None,
                 rerank_dir: str | None = None, rerank_devices: list[str] | None = None,
                 face_detector: str | None = None):
        import openvino as ov
        core = ov.Core()
        if cache_dir:
            core.set_property({"CACHE_DIR": cache_dir})
        self.devices: dict[str, str] = {}
        self.tagger = self.enhancer = self.reid = self.face_detector = None
        self.encoders: dict[int, Any] = {}
        self.rerankers: dict[int, Any] = {}
        if tags_dir:
            model = core.read_model(Path(tags_dir) / "ram_plus.xml")
            model.reshape({model.inputs[0].any_name: [1, 3, _TAG_SIZE, _TAG_SIZE]})
            self.tagger, self.devices["tags"] = _compile(core, model, devices)
            vocabulary = json.loads((Path(tags_dir) / "tags.json").read_text())
            self.tag_names, self.tag_thresholds = vocabulary["tags"], vocabulary["thresholds"]
            if len(self.tag_names) != len(self.tag_thresholds):
                raise RuntimeError("RAM++ tag list and thresholds differ in length")
        if face_dir:
            model = core.read_model(Path(face_dir) / "gfpgan.xml")
            model.reshape({model.inputs[0].any_name: [1, 3, _FACE_SIZE, _FACE_SIZE]})
            self.enhancer, self.devices["enhance"] = _compile(core, model, devices)
        if face_detector:
            model = core.read_model(face_detector)
            model.reshape({model.inputs[0].any_name: [1, 3, 640, 640]})
            compiled, self.devices["face_detector"] = _compile(core, model, devices)
            self.face_detector = named_outputs(compiled)
        if reid_dir:
            model = core.read_model(Path(reid_dir) / f"{REID_MODEL}.xml")
            model.reshape({model.inputs[0].any_name: [1, 3, _REID_SIZE[1], _REID_SIZE[0]]})
            self.reid, self.devices["reid"] = _compile(core, model, devices)
        if text_dir:
            from tokenizers import Tokenizer
            self.tokenizer = Tokenizer.from_file(str(Path(text_dir) / "tokenizer.json"))
            self.tokenizer.no_padding()
            self.tokenizer.no_truncation()
            self.pad_id = self.tokenizer.token_to_id("<pad>") or 0
            for length in _TEXT_BUCKETS:
                model = core.read_model(Path(text_dir) / "e5.xml")
                model.reshape({port.any_name: [1, length] for port in model.inputs})
                self.encoders[length], self.devices["embeddings"] = _compile(
                    core, model, text_devices or devices)
        if rerank_dir:
            from tokenizers import Tokenizer
            self.pair_tokenizer = Tokenizer.from_file(str(Path(rerank_dir) / "tokenizer.json"))
            self.pair_tokenizer.no_padding()
            # Long descriptions lose their tail, never the query.
            self.pair_tokenizer.enable_truncation(_RERANK_BUCKETS[-1], strategy="only_second")
            self.rerank_pad_id = self.pair_tokenizer.token_to_id("<pad>") or 0
            for length in _RERANK_BUCKETS:
                model = core.read_model(Path(rerank_dir) / "rerank.xml")
                model.reshape({port.any_name: [1, length] for port in model.inputs})
                self.rerankers[length], self.devices["rerank"] = _compile(
                    core, model, rerank_devices or devices)

    def tags(self, jpeg: bytes) -> list[dict[str, Any]]:
        probabilities = self.tagger(tag_pixels(_picture(jpeg)))[0][0]
        return select_tags(probabilities, self.tag_names, self.tag_thresholds)

    def enhance(self, jpeg: bytes) -> tuple[bytes, bool] | None:
        run = lambda pixels: self.enhancer(pixels)[0]          # noqa: E731
        if self.face_detector is not None:
            return restore_aligned_face(run, self.face_detector, _picture(jpeg))
        restored = restore_face(run, _picture(jpeg))
        return (restored, False) if restored else None

    def reidentify(self, jpeg: bytes) -> list[float]:
        import numpy
        vector = numpy.asarray(self.reid(reid_pixels(_picture(jpeg)))[0][0], dtype=numpy.float64)
        norm = float(numpy.linalg.norm(vector))
        if not norm or norm != norm:
            raise RuntimeError("empty re-ID vector")
        return [round(float(v) / norm, 6) for v in vector]

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            ids = self.tokenizer.encode(text).ids
            length, input_ids, mask = text_inputs(ids, self.pad_id)
            compiled = self.encoders[length]
            # Inputs in export order: token IDs, then the attention mask (its
            # tensor name does not survive conversion).
            vectors.append([round(float(v), 6) for v in compiled([input_ids, mask])[0][0]])
        return vectors


    def rerank(self, query: str, documents: list[str]) -> list[float]:
        scores = []
        for document in documents:
            ids = self.pair_tokenizer.encode(query, document).ids
            length, input_ids, mask = text_inputs(ids, self.rerank_pad_id, _RERANK_BUCKETS)
            score = float(self.rerankers[length]([input_ids, mask])[0].reshape(-1)[0])
            if score != score or score in (float("inf"), float("-inf")):
                raise RuntimeError("non-finite rerank score")
            scores.append(round(score, 5))
        return scores


def build_app(models: Models) -> web.Application:
    counters = {"tag_requests": 0, "enhance_requests": 0, "enhanced": 0, "declined": 0,
                "embedding_requests": 0, "texts": 0, "rejected": 0, "failed": 0}
    locks = {"tags": asyncio.Lock(), "enhance": asyncio.Lock(), "embeddings": asyncio.Lock(),
             "reid": asyncio.Lock(), "rerank": asyncio.Lock()}
    counters.update(reid_requests=0, rerank_requests=0, reranked_documents=0, enhanced_aligned=0)

    async def run(role, function, *args):
        async with locks[role]:
            try:
                return await asyncio.to_thread(function, *args), None
            except InputError:
                counters["rejected"] += 1
                return None, web.json_response({"error": "invalid_input"}, status=400)
            except Exception:  # noqa: BLE001 - content-free error, nothing logged
                counters["failed"] += 1
                return None, web.json_response({"error": "inference_failed"}, status=500)

    async def read_image(request):
        if request.content_type != "multipart/form-data":
            raise InputError("multipart required")
        reader = await request.multipart()
        jpeg = None
        while (part := await reader.next()) is not None:
            if part.name == "image":
                jpeg = await part.read(decode=False)
                if len(jpeg) > _MAX_IMAGE_BYTES:
                    raise InputError("Image too large")
        if jpeg is None or not jpeg.startswith(b"\xff\xd8\xff"):
            raise InputError("JPEG required")
        return jpeg

    def unavailable():
        return web.json_response({"error": "model_not_loaded"}, status=404)

    async def tags(request: web.Request) -> web.Response:
        counters["tag_requests"] += 1
        if models.tagger is None:
            return unavailable()
        try:
            jpeg = await read_image(request)
        except InputError:
            counters["rejected"] += 1
            return web.json_response({"error": "invalid_request"}, status=400)
        result, error = await run("tags", models.tags, jpeg)
        return error or web.json_response({"tags": result, "model": TAG_MODEL})

    async def enhance(request: web.Request) -> web.Response:
        counters["enhance_requests"] += 1
        if models.enhancer is None:
            return unavailable()
        try:
            jpeg = await read_image(request)
        except InputError:
            counters["rejected"] += 1
            return web.json_response({"error": "invalid_request"}, status=400)
        result, error = await run("enhance", models.enhance, jpeg)
        if error is not None:
            return error
        if not result:
            counters["declined"] += 1
            return web.Response(status=204)
        jpeg, aligned = result
        counters["enhanced"] += 1
        counters["enhanced_aligned"] += aligned
        return web.Response(body=jpeg, content_type="image/jpeg")

    async def reid(request: web.Request) -> web.Response:
        counters["reid_requests"] += 1
        if models.reid is None:
            return unavailable()
        try:
            jpeg = await read_image(request)
        except InputError:
            counters["rejected"] += 1
            return web.json_response({"error": "invalid_request"}, status=400)
        vector, error = await run("reid", models.reidentify, jpeg)
        return error or web.json_response({"embedding": vector, "model": REID_MODEL,
                                           "dim": len(vector)})

    async def embeddings(request: web.Request) -> web.Response:
        counters["embedding_requests"] += 1
        if not models.encoders:
            return unavailable()
        try:
            body = await request.json()
            texts = body.get("input") if isinstance(body, dict) else None
            if isinstance(texts, str):
                texts = [texts]
            if (not isinstance(texts, list) or not 1 <= len(texts) <= _MAX_TEXTS
                    or any(not isinstance(t, str) or not t.strip() or len(t) > _MAX_TEXT_CHARS
                           for t in texts)):
                raise InputError("input must list 1 to 32 nonempty strings")
        except (InputError, ValueError):
            counters["rejected"] += 1
            return web.json_response({"error": "invalid_request"}, status=400)
        vectors, error = await run("embeddings", models.embed, texts)
        if error is not None:
            return error
        counters["texts"] += len(texts)
        return web.json_response({"object": "list", "model": TEXT_MODEL,
                                  "data": [{"object": "embedding", "index": i, "embedding": v}
                                           for i, v in enumerate(vectors)]})

    async def rerank(request: web.Request) -> web.Response:
        counters["rerank_requests"] += 1
        if not models.rerankers:
            return unavailable()
        try:
            body = await request.json()
            query = body.get("query") if isinstance(body, dict) else None
            documents = body.get("documents") if isinstance(body, dict) else None
            # Protect sends '' for a session without a description; it still gets a score.
            if (not isinstance(query, str) or not query.strip() or len(query) > _MAX_TEXT_CHARS
                    or not isinstance(documents, list) or not 1 <= len(documents) <= _MAX_DOCUMENTS
                    or any(not isinstance(d, str) or len(d) > _MAX_TEXT_CHARS for d in documents)):
                raise InputError("query and 1 to 32 documents required")
        except (InputError, ValueError):
            counters["rejected"] += 1
            return web.json_response({"error": "invalid_request"}, status=400)
        scores, error = await run("rerank", models.rerank, query, documents)
        if error is not None:
            return error
        counters["reranked_documents"] += len(documents)
        return web.json_response({"scores": scores, "model": RERANK_MODEL})

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "devices": models.devices,
                                  "models": {"tags": TAG_MODEL if models.tagger else None,
                                             "enhance": FACE_MODEL if models.enhancer else None,
                                             "embeddings": TEXT_MODEL if models.encoders else None,
                                             "reid": REID_MODEL if models.reid else None,
                                             "rerank": RERANK_MODEL if models.rerankers else None},
                                  **counters})

    app = web.Application(client_max_size=_MAX_IMAGE_BYTES + 131072)
    app.router.add_post("/v1/tags", tags)
    app.router.add_post("/v1/enhance", enhance)
    app.router.add_post("/v1/embeddings", embeddings)
    app.router.add_post("/v1/reid", reid)
    app.router.add_post("/v1/rerank", rerank)
    app.router.add_get("/healthz", health)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-vision-server")
    parser.add_argument("--tags", help="directory with ram_plus.xml and tags.json")
    parser.add_argument("--enhance", help="directory with gfpgan.xml")
    parser.add_argument("--embeddings", help="directory with e5.xml and tokenizer.json")
    parser.add_argument("--reid", help=f"directory with {REID_MODEL}.xml")
    parser.add_argument("--rerank", help="directory with rerank.xml and tokenizer.json")
    parser.add_argument("--face-detector", help="YuNet ONNX file; aligns faces before GFPGAN")
    parser.add_argument("--rerank-device", help="devices for the cross-encoder only")
    parser.add_argument("--device", default="NPU,GPU,CPU",
                        help="comma-separated OpenVINO devices, tried in order per model")
    parser.add_argument("--embeddings-device",
                        help="devices for E5 only; the NPU's FP16 attention masks overflow, so CPU")
    parser.add_argument("--cache-dir", help="writable OpenVINO compile cache")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8190)
    args = parser.parse_args(argv)
    def devices(value):
        return [d.strip().upper() for d in value.split(",") if d.strip()] if value else None

    models = Models(tags_dir=args.tags, face_dir=args.enhance, text_dir=args.embeddings,
                    devices=devices(args.device), cache_dir=args.cache_dir,
                    text_devices=devices(args.embeddings_device), reid_dir=args.reid,
                    rerank_dir=args.rerank, rerank_devices=devices(args.rerank_device),
                    face_detector=args.face_detector)
    web.run_app(build_app(models), host=args.host, port=args.port, access_log=None, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
