"""Synthetic recognizeKeyFrames timing shapes: guard boundaries and content-free diagnostics.

Protect 7.3.60's frameSelectionSchema requires only positive startTime,
endTime and keyMeta values; it does not bind key moments to the exported
interval. These tests pin what the worker serves and what health records.
"""

import json
from urllib.parse import urlencode

import pytest

from aikey.device import DeviceService
from aikey.worker import JobProcessor, WorkerError
from test_basic_descriptions import device_config, options, wire

pytest_plugins = ["test_worker"]


def shaped(start, end, moments):
    query = {"camera": "camera-fixture", "event": "event-fixture", "channel": "0",
             "start": str(start), "end": str(end), "type": "rotating", "mute": "true",
             "format": "ubv", "createEvent": "false"}
    return {"command": "recognizeKeyFrames", "payload": {
        "camera": "camera-fixture", "event": "event-fixture", "channel": 0,
        "start": start, "end": end, "type": "rotating", "mute": True, "format": "ubv",
        "createEvent": False, "ramType": "video", "postVLM": True, "keyMoments": moments,
        "reqUrl": "/internal/aiprocessors/video/export?" + urlencode(query),
        "resUrl": "/internal/aiprocessors/recognize-anything"}}


@pytest.mark.parametrize("start,end,moments,served", [
    (1000, 11000, [1000, 6000], True),          # a moment at the first exported frame
    (1000, 121000, [60000], True),              # exactly the 120 s bound
    (1000, 11000, [6000, 11000], False),        # a moment at the exclusive end
    (1000, 11000, [999, 6000], False),          # 1 ms before the export
    (1000, 11000, [6000, 11001], False),        # 1 ms after the export
    (5000, 5000, [5000], False),                # zero-length interval
    (6000, 5000, [5500], False),                # reversed interval
    (1000, 121001, [60000], False),             # 1 ms over the configured bound
])
def test_worker_guard_boundaries(services, tmp_path, start, end, moments, served):
    worker = JobProcessor(options(services), tmp_path)
    if served:
        assert worker._normalize_recognize_key_frames(shaped(start, end, moments))
    else:
        with pytest.raises(WorkerError):
            worker._normalize_recognize_key_frames(shaped(start, end, moments))


async def test_position_and_interval_details_are_counted_without_values(tmp_path):
    async def reject(body):
        raise WorkerError("recognizeKeyFrames requires at most 128 integer timestamps inside the video")
    device = DeviceService(device_config(), tmp_path, reject)
    shapes = [
        (1000, 11000, [2000, 6000]),                # all inside
        (1000, 11000, [6000, 11000]),               # one exactly at end
        (1000, 11000, [500, 6000]),                 # 500 ms before start
        (1000, 11000, [6000, 13500]),               # 2.5 s after end
        (1000, 11000, [12000, 14000]),              # all outside
        (5000, 5000, [5000]),                       # zero length
        (6000, 5000, [5500]),                       # reversed
        (1000, 200000, [60000]),                    # over the limit, under 5 min
        (1000, 400000, [60000]),                    # over 5 min
    ]
    for index, (start, end, moments) in enumerate(shapes):
        await device.handle_message(wire("recognizeKeyFrames", shaped(start, end, moments)["payload"],
                                         f"m{index}"))
    detail = device.status["recognize_key_frames"]
    assert detail["key_moment_position_counts"] == {
        "all_inside": 3, "some_outside": 3, "all_outside": 1, "any_at_end": 1,
        "before_start_up_to_1s": 1, "before_start_over_1s": 0,
        "after_end_up_to_1s": 0, "after_end_over_1s": 2}
    assert detail["interval_detail_counts"] == {
        "zero_length": 1, "reversed": 1, "over_limit_up_to_5min": 1, "over_5min": 1}
    encoded = json.dumps(device.status)
    for value in ("13500", "14000", "200000", "400000"):
        assert value not in encoded
