"""Synthetic early door motion must not consume the only later sample."""
from io import BytesIO
import json

from PIL import Image
import pytest

from aikey.aiport_api_detection import ApiObjectDetector
from aikey.aiport_inference import FairInference
from aikey.aiport_tracking import TemporalTracker

CAMERA = '2A1122334455'


def frame(level):
    out = BytesIO()
    Image.new('RGB', (64, 64), (level,) * 3).save(out, format='JPEG')
    return out.getvalue()


@pytest.mark.parametrize(('latency', 'captured'), [(0, True), (5, True), (0, False)])
def test_neighboring_empty_frames_do_not_spend_recovery(tmp_path, monkeypatch, latency, captured):
    now = [100.]
    monkeypatch.setattr('aikey.aiport_api_detection.time.monotonic', lambda: now[0])
    calls = []
    visible = frame(240)

    def transport(_url, _headers, payload):
        import base64
        image = base64.b64decode(payload['messages'][-1]['images'][0])
        calls.append(image)
        now[0] += latency
        detections = ([{'kind': 'person', 'label': 'person', 'score': .92,
                        'box': [.1, .2, .4, .8]}] if image == visible else [])
        return {'done': True, 'message': {'content': json.dumps({'detections': detections})}}

    d = ApiObjectDetector({'provider': 'ollama', 'model': 'synthetic-vision',
        'base_url': 'http://127.0.0.1:11434'}, tmp_path, threshold=.8,
        max_requests_per_hour=4, transport=transport)
    def detect(image, stamp):
        if captured:
            return d.detect_observed_for_camera(CAMERA, image, stamp)
        return d.detect_for_camera(CAMERA, image)

    tracker = TemporalTracker(max_gap_seconds=20)
    for capture, level in [(100000, 0), (100500, 0), (101000, 0), (101500, 0),
                           (102000, 80), (102500, 160), (103000, 80), (103500, 160)]:
        now[0] = max(now[0], capture / 1000)
        assert detect(frame(level), capture) == ()
    assert len(calls) == 2  # Startup plus early motion, never its adjacent frames.
    now[0] = max(now[0], 104.)
    first = detect(visible, 104000)
    assert len(first) == 1 and tracker.update(first, now=1) == ()
    second = detect(visible, 104500)
    assert [c.edge for c in tracker.update(second, now=2)] == ['enter']
    assert len(calls) == 4 and d.budget.remaining(CAMERA) == 0


@pytest.mark.asyncio
async def test_pool_delivers_capture_time_to_opted_in_detector():
    calls = []

    class Detector:
        def detect_for_camera(self, camera, frame):
            raise AssertionError('observation time was dropped')

        def detect_observed_for_camera(self, camera, frame, timestamp):
            calls.append((camera, frame, timestamp))
            return ()

    async def on_result(*args, **kwargs):
        pass

    pool = FairInference([CAMERA], load_detector=Detector, on_result=on_result,
                         max_frames_per_camera=None, preserve_first_pending=True)
    await pool.observe(CAMERA, b'frame', generation=1, captured_ms=1000000,
                       captured_monotonic_ms=100000)
    await pool.join()
    await pool.close()
    assert calls == [(CAMERA, b'frame', 100000)]
