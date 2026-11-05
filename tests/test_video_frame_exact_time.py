"""Decode the requested time, including exports padded beyond a task's end."""

import asyncio
from io import BytesIO
import shutil
from types import SimpleNamespace

from PIL import Image
import pytest

from aikey.worker import JobProcessor, WorkerError
from test_basic_descriptions import options

pytest_plugins = ["test_worker"]


@pytest.fixture
async def colored_video(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("Real ffmpeg executable unavailable")
    output = tmp_path / "three-colors.mp4"
    process = await asyncio.create_subprocess_exec(
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=32x32:r=4:d=2",
        "-f", "lavfi", "-i", "color=c=green:s=32x32:r=4:d=2",
        "-f", "lavfi", "-i", "color=c=blue:s=32x32:r=4:d=2",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "mpeg4", "-y", str(output))
    assert await process.wait() == 0
    return ffmpeg, output.read_bytes()


@pytest.mark.parametrize("operation", [
    "recognizeKeyFrames", "recognizeFaces", "indexKeyFrames", "reverify",
])
@pytest.mark.parametrize("use_header", [False, True])
async def test_task_end_does_not_select_later_export_padding(
        services, tmp_path, colored_video, operation, use_header):
    ffmpeg, video = colored_video
    config = options(services)
    config["worker"]["ffmpeg_path"] = ffmpeg
    worker = JobProcessor(config, tmp_path)
    job = SimpleNamespace(operation=operation, payload={"end": 4000, "_end": 4000})
    # The event ends three seconds after the actual export start. Export
    # padding contains a different scene; a header overrides the URL's start.
    headers = {"X-Start-Timestamp": "1000"} if use_header else {}
    url = "http://127.0.0.1/export?start=" + ("3000" if use_header else "1000")
    try:
        frame = await worker._video_frame(video, headers, url, job, timestamp=4000)
        with Image.open(BytesIO(frame)) as image:
            red, green, blue = image.convert("RGB").getpixel((16, 16))
        assert green > 100 and red < 50 and blue < 50
    finally:
        await worker.stop()


async def test_exact_file_end_still_returns_last_decodable_frame(
        services, tmp_path, colored_video):
    ffmpeg, video = colored_video
    config = options(services)
    config["worker"]["ffmpeg_path"] = ffmpeg
    worker = JobProcessor(config, tmp_path)
    job = SimpleNamespace(operation="indexKeyFrames", payload={"end": 7000})
    try:
        frame = await worker._video_frame(
            video, {"x-start-timestamp": "1000"},
            "http://127.0.0.1/export?start=1000", job, timestamp=7000)
        with Image.open(BytesIO(frame)) as image:
            red, green, blue = image.convert("RGB").getpixel((16, 16))
        assert blue > 200 and red < 50 and green < 50
    finally:
        await worker.stop()


@pytest.mark.parametrize("failure", ["broken_mp4", "missing_interior_frame"])
async def test_decode_failure_does_not_use_an_unrelated_last_frame(
        services, tmp_path, colored_video, monkeypatch, failure):
    ffmpeg, video = colored_video
    config = options(services)
    config["worker"]["ffmpeg_path"] = ffmpeg
    worker = JobProcessor(config, tmp_path)
    job = SimpleNamespace(operation="indexKeyFrames", payload={
        "end": 7000 if failure == "broken_mp4" else 8000})
    if failure == "broken_mp4":
        video = b"\x00\x00\x00\x18ftyp" + b"\x00" * 16
    original = asyncio.create_subprocess_exec
    attempts = []

    async def record(*args, **kwargs):
        attempts.append(args)
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record)
    try:
        with pytest.raises(WorkerError, match="bounded video frame"):
            await worker._video_frame(
                video, {"x-start-timestamp": "1000"},
                "http://127.0.0.1/export?start=1000", job, timestamp=7000)
        assert len(attempts) == 1
    finally:
        await worker.stop()
