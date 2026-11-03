"""Reproducible, inspectable media contracts; no inference or real footage."""

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import wave

from PIL import Image
import pytest


def generator():
    path = Path(__file__).resolve().parents[1] / "tools/benchmark_fixture_corpus.py"
    spec = importlib.util.spec_from_file_location("fixture_corpus", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_media_are_reproducible_decodable_and_bound(tmp_path):
    module = generator()
    first, second = tmp_path / "a", tmp_path / "b"
    assert module.generate(first) == module.generate(second)
    files = {p.relative_to(first) for p in first.rglob("*") if p.is_file()}
    assert files == {p.relative_to(second) for p in second.rglob("*") if p.is_file()}
    assert all((first / p).read_bytes() == (second / p).read_bytes() for p in files)
    inputs = json.loads((first / "inputs.json").read_text())["cases"]
    gold = json.loads((first / "gold.json").read_text())
    assert len(inputs) == len({c["case_id"] for c in inputs}) == 24
    assert len({c["input_sha256"] for c in inputs}) == 24
    assert sum(c["expected"] == "person" for c in gold["cases"]) == 8
    assert sum(c["expected"] == "none" for c in gold["cases"]) == 16
    assert {c for row in inputs for c in row["conditions"]} == module.CONDITIONS
    for row, labelled in zip(inputs, gold["cases"], strict=True):
        assert set(row) == {"case_id", "file_name", "input_sha256", "camera_class", "conditions"}
        assert all(labelled[k] == v for k, v in row.items())
        path = first / "images" / row["file_name"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["input_sha256"]
        with Image.open(path) as image:
            image.load()
            assert image.format == "PNG" and image.mode == "RGB" and image.size == (512, 384)
            assert not image.info  # No labels or source metadata in scored images.
    for row in gold["audio"]:
        path = first / "audio" / row["file_name"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["input_sha256"]
        assert row["expected"] == ""
        with wave.open(str(path), "rb") as sound:
            assert sound.getparams()[:4] == (1, 2, 16000, 32000)
            assert sound.getcomptype() == "NONE"
            values = struct.unpack("<32000h", sound.readframes(32000))
            assert max(abs(v) for v in values) <= 900
            assert any(values) == (row["id"] == "synthetic-noise")


def test_existing_output_is_not_replaced(tmp_path):
    marker = tmp_path / "existing.txt"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        generator().generate(tmp_path)
    assert marker.read_text() == "keep"


def test_unknown_subject_refuses():
    with pytest.raises(ValueError, match="Unknown synthetic subject"):
        generator().frame("unlabelled", "day")
