"""Local OpenVINO vision server with fake compiled models. No real images or weights."""

import io

import numpy
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
import pytest

from aikey.vision_server import (Models, build_app, restore_face, select_tags, tag_pixels,
                                 text_inputs)


def _jpeg(width=64, height=48, color=(120, 90, 60)):
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="JPEG")
    return out.getvalue()


def _form(data):
    form = FormData()
    form.add_field("image", data, filename="image.jpg", content_type="image/jpeg")
    return form


class _Fake(Models):
    def __init__(self, *, tagger=True, enhancer=True, encoders=True, reid=True):
        self.devices = {"tags": "NPU", "enhance": "NPU", "embeddings": "NPU", "reid": "NPU"}
        self.reid = (lambda pixels: [numpy.arange(1, 257, dtype=numpy.float32)[None]]) if reid else None
        self.tag_names, self.tag_thresholds = ["dog", "grass", "car"], [0.6, 0.7, 0.9]
        self.tagger = (lambda pixels: [numpy.array([[0.95, 0.72, 0.5]])]) if tagger else None
        self.enhancer = (lambda pixels: [numpy.zeros_like(pixels)]) if enhancer else None
        self.pad_id = 1
        self.tokenizer = type("T", (), {"encode": staticmethod(
            lambda text: type("E", (), {"ids": list(range(len(text)))})())})()
        self.encoders = ({128: self._encoder(), 512: self._encoder()} if encoders else {})

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
                                    "reid": "person-reidentification-retail-0288"}
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
