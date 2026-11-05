"""An early empty frame must not blind a whole continuing motion episode."""

from io import BytesIO
import json

from PIL import Image
import pytest

from aikey.aiport_api_detection import ApiDetectionError, ApiObjectDetector
from aikey.aiport_tracking import TemporalTracker


CAMERA = '2A1122334455'
OTHER = '2A1122334456'
EMPTY = '{"detections":[]}'
POSITIVE = json.dumps({'detections': [{'kind': 'person', 'label': 'person',
    'score': .92, 'box': [.1, .2, .4, .8]}]})


def frame(level):
    data = BytesIO()
    Image.new('RGB', (64, 64), (level,) * 3).save(data, format='JPEG')
    return data.getvalue()


STILL, EARLY, LATER = [frame(x) for x in (0, 80, 180)]


def detector(tmp_path, replies, *, cap=None):
    replies = iter(replies)
    calls = []

    def transport(*_args):
        calls.append(1)
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return {'done': True, 'message': {'content': r}}

    config = {'provider': 'ollama', 'model': 'synthetic-vision',
              'base_url': 'http://127.0.0.1:11434'}
    return ApiObjectDetector(config, tmp_path, threshold=.8,
        max_requests_per_hour=cap, transport=transport), calls


@pytest.fixture
def clock(monkeypatch):
    now = [100.]
    monkeypatch.setattr("aikey.aiport_api_detection.time.monotonic", lambda: now[0])
    return now


def quiet_start(d):
    for _ in range(4):
        assert d.detect_for_camera(CAMERA, STILL) == ()


def test_empty_first_motion_then_new_motion_needs_two_fresh_positives(tmp_path, clock):
    d, calls = detector(tmp_path, [EMPTY, EMPTY, POSITIVE, POSITIVE])
    quiet_start(d)
    tracker = TemporalTracker(max_gap_seconds=20)
    assert tracker.update(d.detect_for_camera(CAMERA, EARLY), now=1) == ()
    # The identical frame must not spend the recovery allowance.
    assert d.detect_for_camera(CAMERA, EARLY) == ()
    clock[0] += 2
    first = d.detect_for_camera(CAMERA, LATER)
    assert len(first) == 1
    assert tracker.update(first, now=2) == ()
    assert [c.edge for c in tracker.update(d.detect_for_camera(CAMERA, LATER), now=3)] == ['enter']
    for image in (EARLY, LATER) * 8:
        assert d.detect_for_camera(CAMERA, image) == ()
    assert len(calls) == 4
    assert d.diagnostic_counts(CAMERA)['empty_motion_recoveries'] == 1


@pytest.mark.parametrize('recovery', [EMPTY, 'not JSON', POSITIVE.replace('.92', '.6')])
def test_recovery_cannot_start_another_recovery_or_near_threshold_extension(tmp_path, recovery, clock):
    # json.dumps emits 0.92; replace the suffix, keeping the leading zero.
    d, calls = detector(tmp_path, [EMPTY, EMPTY, recovery])
    quiet_start(d)
    assert d.detect_for_camera(CAMERA, EARLY) == ()
    clock[0] += 2
    if recovery == 'not JSON':
        with pytest.raises(ApiDetectionError):
            d.detect_for_camera(CAMERA, LATER)
    else:
        assert d.detect_for_camera(CAMERA, LATER) == ()
    for image in (EARLY, LATER) * 8:
        assert d.detect_for_camera(CAMERA, image) == ()
    assert len(calls) == 3


def test_empty_positive_empty_never_confirms_or_extends(tmp_path, clock):
    d, calls = detector(tmp_path, [EMPTY, EMPTY, POSITIVE, EMPTY])
    quiet_start(d)
    tracker = TemporalTracker(max_gap_seconds=20)
    for n, image in enumerate([EARLY, LATER, LATER, EARLY, LATER]):
        clock[0] += 2
        assert tracker.update(d.detect_for_camera(CAMERA, image), now=n) == ()
    assert len(calls) == 4


def test_recovery_expires_and_does_not_cross_camera(tmp_path, monkeypatch):
    now = [100.]
    monkeypatch.setattr('aikey.aiport_api_detection.time.monotonic', lambda: now[0])
    d, calls = detector(tmp_path, [EMPTY, EMPTY, EMPTY])
    quiet_start(d)
    assert d.detect_for_camera(OTHER, STILL) == ()
    assert d.detect_for_camera(CAMERA, EARLY) == ()
    assert d.detect_for_camera(OTHER, STILL) == ()
    now[0] += 10
    for image in (LATER, EARLY) * 4:
        assert d.detect_for_camera(CAMERA, image) == ()
    assert len(calls) == 3


def test_recovery_respects_original_hourly_budget(tmp_path, clock):
    d, calls = detector(tmp_path, [EMPTY, EMPTY, POSITIVE], cap=3)
    quiet_start(d)
    tracker = TemporalTracker(max_gap_seconds=20)
    for n, image in enumerate([EARLY, LATER, LATER, EARLY, LATER]):
        clock[0] += 2
        assert tracker.update(d.detect_for_camera(CAMERA, image), now=n) == ()
    assert len(calls) == 3
    assert d.budget.remaining(CAMERA) == 0


@pytest.mark.parametrize('first', ['not JSON', POSITIVE.replace('.92', '.49'),
    '{"detections":[{"kind":"unsupported"}]}'])
def test_invalid_and_rejected_first_results_do_not_create_recovery(tmp_path, first):
    d, calls = detector(tmp_path, [EMPTY, first])
    quiet_start(d)
    if first == 'not JSON':
        with pytest.raises(ApiDetectionError):
            d.detect_for_camera(CAMERA, EARLY)
    else:
        assert d.detect_for_camera(CAMERA, EARLY) == ()
    for image in (LATER, EARLY) * 4:
        assert d.detect_for_camera(CAMERA, image) == ()
    assert len(calls) == 2


def test_provider_backoff_cancels_recovery(tmp_path, clock):
    d, calls = detector(tmp_path, [EMPTY, EMPTY])
    quiet_start(d)
    assert d.detect_for_camera(CAMERA, EARLY) == ()
    clock[0] += 2
    d._provider_retry_at = float('inf')
    assert d.detect_for_camera(CAMERA, LATER) == ()
    assert d.backoff_skips == 1
    d._provider_retry_at = 0
    for image in (EARLY, LATER) * 4:
        assert d.detect_for_camera(CAMERA, image) == ()
    assert len(calls) == 2


def test_other_camera_cannot_spend_or_remove_recovery(tmp_path, clock):
    d, calls = detector(tmp_path, [EMPTY, EMPTY, EMPTY, POSITIVE, POSITIVE])
    quiet_start(d)
    assert d.detect_for_camera(CAMERA, EARLY) == ()
    assert d.detect_for_camera(OTHER, STILL) == ()
    clock[0] += 2
    assert len(d.detect_for_camera(CAMERA, LATER)) == 1
    assert len(d.detect_for_camera(CAMERA, LATER)) == 1
    assert len(calls) == 5
    assert 'empty_motion_recoveries' not in d.diagnostic_counts(OTHER)


def test_quiet_scene_clears_recovery_and_normal_motion_remains_available(tmp_path):
    d, calls = detector(tmp_path, [EMPTY, EMPTY, POSITIVE, POSITIVE])
    quiet_start(d)
    assert d.detect_for_camera(CAMERA, EARLY) == ()
    for _ in range(3):
        assert d.detect_for_camera(CAMERA, EARLY) == ()
    assert len(d.detect_for_camera(CAMERA, LATER)) == 1
    assert len(d.detect_for_camera(CAMERA, LATER)) == 1
    assert 'empty_motion_recoveries' not in d.diagnostic_counts(CAMERA)
    assert len(calls) == 4
