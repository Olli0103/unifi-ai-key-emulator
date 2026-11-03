"""Local CLIP ViT-L/14 text and image embedding server (#2, #21).

``POST /v1/text`` takes ``{"texts": [...]}`` and ``POST /v1/image`` takes one
JPEG plus optional normalized xyxy ``regions``. Both return 768-value,
L2-normalized embeddings from one checkpoint, so query and index vectors
share a space. It runs ONNX exports of OpenAI's CLIP ViT-L/14 with
onnxruntime on the CPU. Nothing is logged or stored; only request counters
are kept. The server listens on a host-only or container network address.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from pathlib import Path
from typing import Callable

from aiohttp import BodyPartReader, web
from aiohttp.http_exceptions import BadHttpMessage

from .clip import DIMENSIONS, MODEL, ClipError, normalize
from .encoding_identity import CLIP_ENCODING
from .multipart_input import read_part


_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_REGION_BYTES = 65536
_MAX_TEXTS = 8
_MAX_TEXT_CHARS = 1024
_MAX_REGIONS = 16
_CONTEXT = 77                     # CLIP text context length
_SIZE = 224
_MEAN = (0.48145466, 0.4578275, 0.40821073)
_STD = (0.26862954, 0.26130258, 0.27577711)

TextEncoder = Callable[[list[str]], list[list[float]]]
ImageEncoder = Callable[[bytes, list | None], list[list[float]]]


class InputError(ValueError):
    """The request is not bounded text or a bounded JPEG with valid regions."""


def parse_regions(raw: str | None) -> list[tuple[float, float, float, float]] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise InputError("regions must be JSON") from exc
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_REGIONS:
        raise InputError("1 to 16 regions")
    regions = []
    for box in value:
        if (not isinstance(box, list) or len(box) != 4
                or any(type(v) not in (int, float) for v in box)):
            raise InputError("Each region is [x1, y1, x2, y2]")
        x1, y1, x2, y2 = (float(v) for v in box)
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise InputError("Regions are normalized xyxy boxes")
        regions.append((x1, y1, x2, y2))
    return regions


def preprocess(picture):
    """CLIP's own preprocessing: shortest side 224 bicubic, center crop, normalize."""
    import numpy
    from PIL import Image
    picture = picture.convert("RGB")
    width, height = picture.size
    scale = _SIZE / min(width, height)
    picture = picture.resize((max(_SIZE, round(width * scale)), max(_SIZE, round(height * scale))),
                             Image.Resampling.BICUBIC)
    width, height = picture.size
    left, top = (width - _SIZE) // 2, (height - _SIZE) // 2
    picture = picture.crop((left, top, left + _SIZE, top + _SIZE))
    pixels = numpy.asarray(picture, dtype=numpy.float32) / 255.0
    pixels = (pixels - numpy.array(_MEAN, dtype=numpy.float32)) / numpy.array(_STD, dtype=numpy.float32)
    return pixels.transpose(2, 0, 1)


def weights_revision(model_dir: str) -> str:
    """SHA-256 over the exact encoder files, so an index can pin its weights."""
    import hashlib
    root = Path(model_dir)
    digest = hashlib.sha256()
    for name in ("onnx/text_model.onnx", "onnx/vision_model.onnx", "tokenizer.json"):
        digest.update(name.encode() + b"\0")
        with open(root / name, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


def onnx_encoders(model_dir: str, *, threads: int = 4) -> tuple[TextEncoder, ImageEncoder]:
    import numpy
    import onnxruntime
    from PIL import Image
    from tokenizers import Tokenizer
    root = Path(model_dir)
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = threads
    options.log_severity_level = 3
    text = onnxruntime.InferenceSession(str(root / "onnx" / "text_model.onnx"), options,
                                        providers=["CPUExecutionProvider"])
    vision = onnxruntime.InferenceSession(str(root / "onnx" / "vision_model.onnx"), options,
                                          providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
    tokenizer.no_padding()
    tokenizer.enable_truncation(_CONTEXT)

    def encode_text(texts):
        vectors = []
        for value in texts:
            # One unpadded sequence at a time: CLIP pools at its end token.
            ids = numpy.array([tokenizer.encode(value.lower()).ids], dtype=numpy.int64)
            vectors.append(text.run(["text_embeds"], {"input_ids": ids})[0][0].tolist())
        return vectors

    def encode_image(jpeg, regions):
        try:
            with Image.open(io.BytesIO(jpeg)) as picture:
                picture.load()
                width, height = picture.size
                crops = []
                for x1, y1, x2, y2 in regions or [(0.0, 0.0, 1.0, 1.0)]:
                    box = (int(x1 * width), int(y1 * height),
                           max(int(x1 * width) + 1, round(x2 * width)),
                           max(int(y1 * height) + 1, round(y2 * height)))
                    crops.append(preprocess(picture.crop(box)))
        except (OSError, ValueError) as exc:
            raise InputError("Unreadable JPEG") from exc
        batch = numpy.stack(crops).astype(numpy.float32)
        return vision.run(["image_embeds"], {"pixel_values": batch})[0].tolist()

    return encode_text, encode_image


def build_app(encode_text: TextEncoder, encode_image: ImageEncoder, *,
              revision: str | None = None) -> web.Application:
    lock = asyncio.Lock()
    operations = set()
    counters = {"text_requests": 0, "image_requests": 0, "texts": 0, "regions": 0,
                "rejected": 0, "failed": 0}

    def reply(vectors):
        return web.json_response({"model": MODEL, "dim": DIMENSIONS, "encoding": dict(CLIP_ENCODING),
                                  **({"revision": revision} if revision else {}),
                                  "embeddings": [[round(v, 7) for v in normalize(vector)]
                                                 for vector in vectors]})

    async def run_owned(request, function, args):
        async with lock:
            if request.transport is None or request.transport.is_closing():
                return None, web.Response(status=499)
            try:
                return await asyncio.to_thread(function, *args), None
            except InputError:
                counters["rejected"] += 1
                return None, web.json_response({"error": "invalid_input"}, status=400)
            except (ClipError, Exception):
                counters["failed"] += 1
                return None, web.json_response({"error": "embedding_failed"}, status=500)

    def completed(work):
        operations.discard(work)
        if not work.cancelled():
            work.exception()

    async def run(request, function, *args):
        # One shared text/image model lock belongs to the actual operation,
        # never the cancellable HTTP waiter around its non-cancellable thread.
        work = asyncio.create_task(run_owned(request, function, args))
        operations.add(work)
        work.add_done_callback(completed)
        return await asyncio.shield(work)

    async def drain(_app):
        if operations:
            await asyncio.shield(asyncio.gather(*tuple(operations), return_exceptions=True))

    async def text(request: web.Request) -> web.Response:
        counters["text_requests"] += 1
        try:
            body = await request.json()
            texts = body.get("texts") if isinstance(body, dict) else None
            if (not isinstance(texts, list) or not 1 <= len(texts) <= _MAX_TEXTS
                    or any(not isinstance(t, str) or not t.strip() or len(t) > _MAX_TEXT_CHARS
                           for t in texts)):
                raise InputError("texts must list 1 to 8 nonempty strings")
        except (InputError, ValueError):
            counters["rejected"] += 1
            return web.json_response({"error": "invalid_request"}, status=400)
        vectors, error = await run(request, encode_text, [t.strip() for t in texts])
        if error is not None:
            return error
        counters["texts"] += len(texts)
        return reply(vectors)

    async def image(request: web.Request) -> web.Response:
        counters["image_requests"] += 1
        jpeg, regions = None, None
        try:
            if request.content_type != "multipart/form-data":
                raise InputError("multipart required")
            reader = await request.multipart()
            seen = set()
            while (part := await reader.next()) is not None:
                if (not isinstance(part, BodyPartReader)
                        or part.name not in {"image", "regions"} or part.name in seen):
                    raise InputError("Unexpected or repeated part")
                seen.add(part.name)
                if part.name == "image":
                    jpeg = await read_part(part, _MAX_IMAGE_BYTES)
                elif part.name == "regions":
                    regions = parse_regions((await read_part(part, _MAX_REGION_BYTES)).decode())
            if jpeg is None or not jpeg.startswith(b"\xff\xd8\xff"):
                raise InputError("JPEG required")
        except (ValueError, AssertionError, BadHttpMessage, RuntimeError):
            counters["rejected"] += 1
            return web.json_response({"error": "invalid_request"}, status=400)
        vectors, error = await run(request, encode_image, jpeg, regions)
        if error is not None:
            return error
        counters["regions"] += len(vectors)
        return reply(vectors)

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "model": MODEL, "dim": DIMENSIONS,
                                  "encoding": dict(CLIP_ENCODING),
                                  **({"revision": revision} if revision else {}), **counters})

    app = web.Application(client_max_size=_MAX_IMAGE_BYTES + 131072)
    app.router.add_post("/v1/text", text)
    app.router.add_post("/v1/image", image)
    app.router.add_get("/healthz", health)
    app.on_cleanup.append(drain)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-clip-server")
    parser.add_argument("--models", required=True, help="directory with onnx/ and tokenizer.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8180)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args(argv)
    app = build_app(*onnx_encoders(args.models, threads=args.threads),
                    revision=weights_revision(args.models))
    web.run_app(app, host=args.host, port=args.port, access_log=None, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
