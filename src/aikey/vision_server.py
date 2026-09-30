"""Local OpenVINO vision models for the AI Key: open-vocabulary tags, faces, text.

One small server holds three optional models, each compiled for the first
device in ``--device`` that accepts it (the NAS NPU, then the iGPU, then the
CPU):

- ``POST /v1/tags``: one JPEG, answered with RAM++ tags above their per-class
  thresholds, as ``{"tags": [{"tag", "confScore"}], "model"}``.
- ``POST /v1/enhance``: one JPEG face crop, answered with a GFPGAN-restored
  JPEG at least as large as the crop, or 204 to decline.
- ``POST /v1/embeddings``: OpenAI-compatible multilingual-e5-small vectors
  (384 values, mean-pooled and L2-normalized). Callers add E5's ``query:`` or
  ``passage:`` prefix themselves.
- ``POST /v1/reid``: one JPEG person crop, answered with Intel's
  person-reidentification-retail-0288 vector (256 values, L2-normalized) for
  deep-mode session grouping.

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
_TEXT_BUCKETS = (128, 512)
TAG_MODEL = "ram-plus-swin-large-14m"
FACE_MODEL = "gfpgan-1.4"
TEXT_MODEL = "intfloat/multilingual-e5-small"
REID_MODEL = "person-reidentification-retail-0288"
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


def text_inputs(ids: list[int], pad_id: int):
    """Token IDs padded to the smallest static bucket, with the attention mask."""
    import numpy
    length = next(b for b in _TEXT_BUCKETS if len(ids) <= b) if len(ids) <= _TEXT_BUCKETS[-1] \
        else _TEXT_BUCKETS[-1]
    ids = ids[:length]
    mask = [1] * len(ids) + [0] * (length - len(ids))
    ids = ids + [pad_id] * (length - len(ids))
    return length, numpy.array([ids], dtype=numpy.int64), numpy.array([mask], dtype=numpy.int64)


class Models:
    """The compiled models; any of them may be absent."""

    def __init__(self, *, tags_dir: str | None, face_dir: str | None, text_dir: str | None,
                 devices: list[str], cache_dir: str | None,
                 text_devices: list[str] | None = None, reid_dir: str | None = None):
        import openvino as ov
        core = ov.Core()
        if cache_dir:
            core.set_property({"CACHE_DIR": cache_dir})
        self.devices: dict[str, str] = {}
        self.tagger = self.enhancer = self.reid = None
        self.encoders: dict[int, Any] = {}
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

    def tags(self, jpeg: bytes) -> list[dict[str, Any]]:
        probabilities = self.tagger(tag_pixels(_picture(jpeg)))[0][0]
        return select_tags(probabilities, self.tag_names, self.tag_thresholds)

    def enhance(self, jpeg: bytes) -> bytes | None:
        return restore_face(lambda pixels: self.enhancer(pixels)[0], _picture(jpeg))

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


def build_app(models: Models) -> web.Application:
    counters = {"tag_requests": 0, "enhance_requests": 0, "enhanced": 0, "declined": 0,
                "embedding_requests": 0, "texts": 0, "rejected": 0, "failed": 0}
    locks = {"tags": asyncio.Lock(), "enhance": asyncio.Lock(), "embeddings": asyncio.Lock(),
             "reid": asyncio.Lock()}
    counters["reid_requests"] = 0

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
        counters["enhanced"] += 1
        return web.Response(body=result, content_type="image/jpeg")

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

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "devices": models.devices,
                                  "models": {"tags": TAG_MODEL if models.tagger else None,
                                             "enhance": FACE_MODEL if models.enhancer else None,
                                             "embeddings": TEXT_MODEL if models.encoders else None,
                                             "reid": REID_MODEL if models.reid else None},
                                  **counters})

    app = web.Application(client_max_size=_MAX_IMAGE_BYTES + 131072)
    app.router.add_post("/v1/tags", tags)
    app.router.add_post("/v1/enhance", enhance)
    app.router.add_post("/v1/embeddings", embeddings)
    app.router.add_post("/v1/reid", reid)
    app.router.add_get("/healthz", health)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-vision-server")
    parser.add_argument("--tags", help="directory with ram_plus.xml and tags.json")
    parser.add_argument("--enhance", help="directory with gfpgan.xml")
    parser.add_argument("--embeddings", help="directory with e5.xml and tokenizer.json")
    parser.add_argument("--reid", help=f"directory with {REID_MODEL}.xml")
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
                    text_devices=devices(args.embeddings_device), reid_dir=args.reid)
    web.run_app(build_app(models), host=args.host, port=args.port, access_log=None, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
