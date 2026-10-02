"""Local OpenVINO vision server with fake compiled models. No real images or weights."""

import io

import numpy
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
import pytest

from aikey.vision_server import (Models, build_app, named_outputs, restore_aligned_face,
                                 restore_face, select_tags, tag_pixels, text_inputs)


def _jpeg(width=64, height=48, color=(120, 90, 60)):
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="JPEG")
    return out.getvalue()


def _form(data):
    form = FormData()
    form.add_field("image", data, filename="image.jpg", content_type="image/jpeg")
    return form


class _Fake(Models):
    def __init__(self, *, tagger=True, enhancer=True, encoders=True, reid=True, rerank=True):
        self.devices = {"tags": "NPU", "enhance": "NPU", "embeddings": "NPU", "reid": "NPU",
                        "rerank": "NPU"}
        self.reid = (lambda pixels: [numpy.arange(1, 257, dtype=numpy.float32)[None]]) if reid else None
        self.tag_names, self.tag_thresholds = ["dog", "grass", "car"], [0.6, 0.7, 0.9]
        self.tagger = (lambda pixels: [numpy.array([[0.95, 0.72, 0.5]])]) if tagger else None
        self.enhancer = (lambda pixels: [numpy.zeros_like(pixels)]) if enhancer else None
        self.face_detector = None
        self.detector = None
        self.pad_id = 1
        self.tokenizer = type("T", (), {"encode": staticmethod(
            lambda text: type("E", (), {"ids": list(range(len(text)))})())})()
        self.encoders = ({128: self._encoder(), 512: self._encoder()} if encoders else {})
        self.rerank_pad_id = 1
        self.pair_tokenizer = type("P", (), {"encode": staticmethod(
            lambda query, document: type("E", (), {"ids": list(range(len(query) + len(document)))})())})()
        self.rerankers = ({128: self._reranker(), 256: self._reranker()} if rerank else {})

    @staticmethod
    def _reranker():
        def run(feed):
            ids, mask = feed
            assert ids.shape == mask.shape and ids.shape[1] in (128, 256)
            return [numpy.array([int(mask.sum()) / 10.0 - 3.0], dtype=numpy.float32)]
        return run

    @staticmethod
    def _encoder():
        class Port:
            def __init__(self, name):
                self.any_name = name

        class Compiled:
            inputs = [Port("input_ids"), Port("attention_mask")]

            def __call__(self, feed):
                ids, mask = feed                      # positional: IDs, then the mask
                assert ids.shape == mask.shape and set(mask.ravel().tolist()) <= {0, 1}
                length = int(mask.sum())
                return [numpy.full((1, 384), length / 1000.0)]
        return Compiled()


@pytest.fixture
async def client():
    async with TestClient(TestServer(build_app(_Fake()))) as test_client:
        yield test_client


def test_tags_above_their_class_threshold_come_strongest_first():
    assert select_tags([0.95, 0.72, 0.5], ["dog", "grass", "car"], [0.6, 0.7, 0.9]) == [
        {"tag": "dog", "confScore": 0.95}, {"tag": "grass", "confScore": 0.72}]
    pixels = tag_pixels(Image.new("RGB", (640, 360)))
    assert pixels.shape == (1, 3, 384, 384) and pixels.dtype == numpy.float32


async def test_a_jpeg_gets_ram_tags(client):
    response = await client.post("/v1/tags", data=_form(_jpeg()))
    body = await response.json()
    assert response.status == 200 and body["model"] == "ram-plus-swin-large-14m"
    assert [t["tag"] for t in body["tags"]] == ["dog", "grass"]


async def test_non_jpeg_input_is_refused_without_inference(client):
    png = io.BytesIO()
    Image.new("RGB", (8, 8)).save(png, format="PNG")
    for data in (png.getvalue(), b"\xff\xd8\xffnot really"):
        response = await client.post("/v1/tags", data=_form(data))
        assert response.status == 400
    assert (await (await client.get("/healthz")).json())["rejected"] == 2


def test_a_restored_face_is_at_least_the_crop_size_and_tiny_crops_decline():
    calls = []

    def run(pixels):
        calls.append(pixels.shape)
        return numpy.zeros_like(pixels)
    for size in ((100, 140), (700, 600)):
        data = restore_face(run, Image.new("RGB", size, (200, 150, 100)))
        with Image.open(io.BytesIO(data)) as result:
            assert result.format == "JPEG" and result.size[0] >= size[0] and result.size[1] >= size[1]
    assert calls == [(1, 3, 512, 512)] * 2
    assert restore_face(run, Image.new("RGB", (20, 40))) is None


async def test_enhance_returns_a_jpeg_or_declines(client):
    response = await client.post("/v1/enhance", data=_form(_jpeg(120, 120)))
    assert response.status == 200 and response.content_type == "image/jpeg"
    response = await client.post("/v1/enhance", data=_form(_jpeg(16, 16)))
    assert response.status == 204


def test_text_is_padded_to_the_smallest_static_bucket():
    length, ids, mask = text_inputs([5, 6, 7], pad_id=1)
    assert length == 128 and ids[0, :4].tolist() == [5, 6, 7, 1] and int(mask.sum()) == 3
    length, ids, mask = text_inputs(list(range(300)), pad_id=1)
    assert length == 512 and int(mask.sum()) == 300
    length, ids, mask = text_inputs(list(range(900)), pad_id=1)
    assert length == 512 and int(mask.sum()) == 512                  # truncated


async def test_embeddings_follow_the_openai_shape(client):
    response = await client.post("/v1/embeddings", json={"input": ["query: a", "passage: bb"],
                                                         "model": "intfloat/multilingual-e5-small"})
    body = await response.json()
    assert response.status == 200 and [d["index"] for d in body["data"]] == [0, 1]
    assert len(body["data"][0]["embedding"]) == 384
    for bad in ({"input": []}, {"input": [""]}, {"input": ["x"] * 33}, [1]):
        assert (await client.post("/v1/embeddings", json=bad)).status == 400


async def test_a_missing_model_answers_404_and_health_lists_devices():
    async with TestClient(TestServer(build_app(_Fake(tagger=False, encoders=False)))) as c:
        assert (await c.post("/v1/tags", data=_form(_jpeg()))).status == 404
        assert (await c.post("/v1/embeddings", json={"input": ["x"]})).status == 404
        health = await (await c.get("/healthz")).json()
        assert health["models"] == {"tags": None, "enhance": "gfpgan-1.4", "embeddings": None,
                                    "reid": "person-reidentification-retail-0288",
                                    "rerank": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
                                    "detect": None}
        assert health["devices"]["enhance"] == "NPU"


async def test_a_person_crop_gets_a_normalized_reid_vector(client):
    response = await client.post("/v1/reid", data=_form(_jpeg(60, 140)))
    body = await response.json()
    assert response.status == 200 and body["dim"] == 256
    assert body["model"] == "person-reidentification-retail-0288"
    assert abs(sum(v * v for v in body["embedding"]) - 1) < 1e-4


def test_reid_input_is_bgr_128_by_256():
    from aikey.vision_server import reid_pixels
    pixels = reid_pixels(Image.new("RGB", (40, 90), (255, 0, 0)))
    assert pixels.shape == (1, 3, 256, 128)
    assert pixels[0, 2].max() == 255 and pixels[0, 0].max() == 0          # red lands in channel 2


async def test_rerank_scores_every_document_in_order_including_empty_ones(client):
    response = await client.post("/v1/rerank", json={"query": "cat", "documents": ["a cat", "", "x" * 200]})
    body = await response.json()
    assert response.status == 200 and body["model"] == "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    assert body["scores"] == [round(8 / 10 - 3, 5), round(3 / 10 - 3, 5), round(203 / 10 - 3, 5)]
    health = await (await client.get("/healthz")).json()
    assert health["rerank_requests"] == 1 and health["reranked_documents"] == 3


async def test_malformed_rerank_requests_are_refused(client):
    for bad in ({"query": "", "documents": ["a"]}, {"query": "q", "documents": []},
                {"query": "q", "documents": ["a"] * 33}, {"query": "q", "documents": [None]},
                {"query": "q"}, ["q"]):
        assert (await client.post("/v1/rerank", json=bad)).status == 400


async def test_rerank_without_the_model_answers_404():
    async with TestClient(TestServer(build_app(_Fake(rerank=False)))) as c:
        assert (await c.post("/v1/rerank", json={"query": "q", "documents": ["a"]})).status == 404


def _yunet(face=True):
    """Fake YuNet outputs: one face whose landmarks sit in the crop's middle."""
    def run(pixels):
        assert pixels.shape == (1, 3, 640, 640)
        out = {}
        for stride in (8, 16, 32):
            n = (640 // stride) ** 2
            out[f"cls_{stride}"] = numpy.zeros((1, n, 1), numpy.float32)
            out[f"obj_{stride}"] = numpy.zeros((1, n, 1), numpy.float32)
            out[f"bbox_{stride}"] = numpy.zeros((1, n, 4), numpy.float32)
            out[f"kps_{stride}"] = numpy.zeros((1, n, 10), numpy.float32)
        if face:
            index = 10 * 20 + 10                                       # stride 32, row 10, col 10
            out["cls_32"][0, index, 0] = out["obj_32"][0, index, 0] = 0.95
            out["bbox_32"][0, index] = [0.5, 0.5, 1.6, 1.8]
            out["kps_32"][0, index] = [-1.0, -1.2, 2.0, -1.2, 0.5, 0.2, -0.6, 1.4, 1.6, 1.4]
        return out
    return run


def test_an_aligned_face_is_restored_and_pasted_back_at_least_crop_size():
    calls = []

    def run(pixels):
        calls.append(pixels.shape)
        return numpy.zeros_like(pixels)
    picture = Image.new("RGB", (160, 200), (200, 150, 100))
    jpeg, aligned = restore_aligned_face(run, _yunet(), picture)
    assert aligned and calls == [(1, 3, 512, 512)]
    with Image.open(io.BytesIO(jpeg)) as result:
        assert result.format == "JPEG" and result.size[0] >= 160 and result.size[1] >= 200


def test_without_landmarks_the_square_method_is_used_and_tiny_crops_decline():
    run = lambda pixels: numpy.zeros_like(pixels)                          # noqa: E731
    jpeg, aligned = restore_aligned_face(run, _yunet(face=False), Image.new("RGB", (120, 120)))
    assert not aligned and jpeg.startswith(b"\xff\xd8")
    assert restore_aligned_face(run, _yunet(), Image.new("RGB", (60, 90))) is None


def test_the_face_detector_runs_one_inference_per_call_for_all_its_outputs():
    class Port:
        def __init__(self, name):
            self.any_name = name

    class Compiled:
        outputs = [Port(f"{kind}_{stride}") for kind in ("cls", "obj", "bbox", "kps")
                   for stride in (8, 16, 32)]

        def __init__(self):
            self.calls = 0

        def __call__(self, pixels):
            self.calls += 1
            return {port: (self.calls, port.any_name, float(pixels)) for port in self.outputs}

    compiled = Compiled()
    detect = named_outputs(compiled)
    for call in (1, 2, 3):
        result = detect(call / 10)
        assert compiled.calls == call                                      # one inference per call
        assert result == {port.any_name: (call, port.any_name, call / 10) for port in Compiled.outputs}


def _yolox_output(entries):
    """Raw YOLOX output with given (grid index, stride, dx, dy, log w, log h, obj, class, prob)."""
    raw = numpy.full((1, 8400, 85), -20.0, dtype=numpy.float32)
    raw[0, :, 4:] = 0.0
    for index, dx, dy, lw, lh, obj, cls, prob in entries:
        raw[0, index, :4] = [dx, dy, lw, lh]
        raw[0, index, 4] = obj
        raw[0, index, 5 + cls] = prob
    return raw


def test_yolox_output_is_grid_decoded_scored_and_mapped_to_protect_kinds():
    from aikey.vision_server import decode_yolox
    # stride 8 cell (row 10, col 20) is index 820; stride 32 starts at 6400 + 6400/4... = 8000
    raw = _yolox_output([(820, 0.5, 0.5, 2.0, 3.0, 0.9, 0, 0.8),          # person, 0.72
                         (821, 0.5, 0.5, 2.0, 3.0, 0.9, 0, 0.7),          # overlapping duplicate
                         (8000 + 45, 0.5, 0.5, 1.0, 1.0, 0.5, 15, 0.5),   # cat, 0.25
                         (900, 0.5, 0.5, 1.0, 1.0, 0.9, 60, 0.9)])       # dining table: ignored
    found = decode_yolox(raw, ratio=0.5, width=1280, height=1280)
    assert [(f["kind"], f["label"], f["score"]) for f in found] == [
        ("person", "person", 0.72), ("animal", "cat", 0.25)]
    x1, y1, x2, y2 = found[0]["box"]
    cx, cy = (20.5 * 8) / 0.5 / 1280, (10.5 * 8) / 0.5 / 1280
    assert abs((x1 + x2) / 2 - cx) < 1e-3 and abs((y1 + y2) / 2 - cy) < 1e-3


async def test_detect_letterboxes_into_640_and_answers_404_without_the_model(client):
    from aikey.vision_server import detect_pixels
    assert (await client.post("/v1/detect", data=_form(_jpeg()))).status == 404
    pixels, ratio = detect_pixels(Image.new("RGB", (1280, 720), (10, 20, 30)))
    assert pixels.shape == (1, 3, 640, 640) and ratio == 0.5
    assert pixels[0, 0, 0, 0] == 30 and pixels[0, 0, 639, 0] == 114        # BGR, grey padding below
