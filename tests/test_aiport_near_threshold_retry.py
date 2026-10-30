"""A doubtful first reply may resample, never create or confirm a track."""

from io import BytesIO
import json

from PIL import Image
import pytest

from aikey.aiport_api_detection import ApiDetectionError, ApiObjectDetector
from aikey.aiport_tracking import TemporalTracker


FIRST = '2A1122334455'
SECOND = '2A1122334456'


def frame(color):
    data = BytesIO()
    Image.new('RGB', (64, 64), color).save(data, format='JPEG')
    return data.getvalue()


STILL = frame('black')
CHANGED = frame('white')


def detector(tmp_path, replies, *, cap=None):
    replies = iter(replies)
    calls = []

    def transport(*_args):
        calls.append(1)
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return {'done': True, 'message': {'content': reply}}

    config = {'provider': 'ollama', 'model': 'synthetic-vision',
              'base_url': 'http://127.0.0.1:11434', 'max_output_tokens': 128}
    return ApiObjectDetector(config, tmp_path, threshold=.8,
                             max_requests_per_hour=cap, transport=transport), calls


def reply(score, kind='person'):
    return json.dumps({'detections': [{'kind': kind, 'label': kind,
                                     'score': score, 'box': [.1, .2, .4, .8]}]})


@pytest.mark.parametrize('startup', [True, False])
def test_near_first_reply_recovers_only_with_two_new_above_threshold_samples(tmp_path, startup):
    replies = [reply(.6), reply(.9), reply(.92)]
    d, calls = detector(tmp_path, replies if startup else ['{"detections":[]}', *replies])
    if not startup:
        assert d.detect_for_camera(FIRST, STILL) == ()
        for _ in range(3):
            assert d.detect_for_camera(FIRST, STILL) == ()
    source = STILL if startup else CHANGED
    tracker = TemporalTracker(max_gap_seconds=20)
    first = d.detect_for_camera(FIRST, source)
    assert first == ()
    assert tracker.update(first, now=1) == ()
    second = d.detect_for_camera(FIRST, source)
    assert len(second) == 1 and second[0].score >= .8
    assert tracker.update(second, now=2) == ()
    third = d.detect_for_camera(FIRST, source)
    assert len(third) == 1 and third[0].score >= .8
    assert [c.edge for c in tracker.update(third, now=3)] == ['enter']
    for _ in range(10):
        assert d.detect_for_camera(FIRST, source) == ()
    assert len(calls) == (3 if startup else 4)


@pytest.mark.parametrize('following', [reply(.6), '{"detections":[]}'])
def test_retry_stops_after_second_doubtful_or_empty_reply(tmp_path, following):
    d, calls = detector(tmp_path, [reply(.6), following])
    for _ in range(12):
        assert d.detect_for_camera(FIRST, STILL) == ()
    assert len(calls) == 2


@pytest.mark.parametrize('first', [reply(.49), reply(.6, 'package'), '{"detections":[]}'])
def test_weak_package_and_empty_first_reply_do_not_retry(tmp_path, first):
    d, calls = detector(tmp_path, [first])
    for _ in range(12):
        assert d.detect_for_camera(FIRST, STILL) == ()
    assert len(calls) == 1


def test_existing_positive_then_low_confirmation_never_extends_pair(tmp_path):
    d, calls = detector(tmp_path, [reply(.9), reply(.6)])
    assert len(d.detect_for_camera(FIRST, STILL)) == 1
    for _ in range(12):
        assert d.detect_for_camera(FIRST, STILL) == ()
    assert len(calls) == 2


def test_near_retry_cannot_bypass_hourly_cap_or_confirm_one_positive(tmp_path):
    d, calls = detector(tmp_path, [reply(.6), reply(.9)], cap=2)
    tracker = TemporalTracker(max_gap_seconds=20)
    assert tracker.update(d.detect_for_camera(FIRST, STILL), now=1) == ()
    assert tracker.update(d.detect_for_camera(FIRST, STILL), now=2) == ()
    assert tracker.update(d.detect_for_camera(FIRST, STILL), now=3) == ()
    assert len(calls) == 2 and d.budget.remaining(FIRST) == 0


def test_retry_credit_is_isolated_and_reset_for_new_motion(tmp_path):
    d, calls = detector(tmp_path, [reply(.6), reply(.6), reply(.6), reply(.6), reply(.9)])
    assert d.detect_for_camera(FIRST, STILL) == ()
    assert d.detect_for_camera(SECOND, STILL) == ()
    assert d.detect_for_camera(FIRST, STILL) == ()
    assert d.detect_for_camera(SECOND, STILL) == ()
    assert d.detect_for_camera(FIRST, STILL) == ()
    d.motion.reset(FIRST)
    assert len(d.detect_for_camera(FIRST, STILL)) == 1
    assert len(calls) == 5


def test_invalid_retry_reply_ends_pair_without_further_request(tmp_path):
    d, calls = detector(tmp_path, [reply(.6), 'not JSON'])
    assert d.detect_for_camera(FIRST, STILL) == ()
    with pytest.raises(ApiDetectionError):
        d.detect_for_camera(FIRST, STILL)
    for _ in range(10):
        assert d.detect_for_camera(FIRST, STILL) == ()
    assert len(calls) == 2
