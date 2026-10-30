"""The outer HTTP reply cannot discard ambiguity before schema selection."""
import json
import traceback

import pytest

from aikey import aiport_api_detection as detection


CANARY = 'JSON-PRIVATE-REPLY-CANARY'
BAD = [
    ('duplicate_top', b'{"message":{},"message":{"content":"ignored"}}'),
    ('duplicate_nested', b'{"detections":[{"score":0.01,"score":0.99}]}'),
    ('escaped_duplicate', b'{"message":{},"m\\u0065ssage":{"content":"ignored"}}'),
    ('nan', b'{"metadata":{"score":NaN}}'),
    ('infinity', b'{"metadata":{"score":Infinity}}'),
    ('negative_infinity', b'{"metadata":{"score":-Infinity}}'),
    ('exponent_overflow', b'{"metadata":{"score":1e9999}}'),
    ('integer_overflow', b'{"metadata":{"count":' + b'9' * 400 + b'}}'),
    # C JSON recursion budgets are platform-dependent: this Mac accepts 8192
    # levels but rejects 30000. Stay below the 64-KiB transport cap and exercise
    # a real decoder failure on the qualified Mac/Linux Python 3.12 hosts.
    ('nesting', b'{"metadata":' + b'[' * 30000 + b'0' + b']' * 30000 + b'}'),
    ('encoding', b'{"private":"' + CANARY.encode() + b'\xff"}'),
    ('array', b'[{"private":"' + CANARY.encode() + b'"}]'),
    ('syntax', b'{"private":"' + CANARY.encode() + b'",]'),
]


class Reply:
    status = 200

    def __init__(self, body):
        self.body = body
        self.closed = False
        self.reads = []

    def __enter__(self):
        return self

    def read(self, limit):
        self.reads.append(limit)
        return self.body

    def __exit__(self, *args):
        self.closed = True


def invoke(monkeypatch, name, body):
    reply = Reply(body)
    calls = []

    class Opener:
        def open(self, request, **kwargs):
            calls.append(request)
            return reply

    monkeypatch.setattr(detection, 'build_opener', lambda *handlers: Opener())
    def run():
        if name == 'provider':
            return detection._post('http://127.0.0.1:8181/v1/infer', {}, {'model': 'synthetic'})
        return detection._post_jpeg('http://127.0.0.1:8181/v1/detect', b'\xff\xd8\xff' + b'0' * 32)
    return run, reply, calls


@pytest.mark.parametrize('name', ['provider', 'score'])
@pytest.mark.parametrize('case,body', BAD, ids=[name for name, _ in BAD])
def test_invalid_or_ambiguous_outer_reply_is_fixed_failure_without_raw_cause(
    monkeypatch, name, case, body
):
    run, reply, calls = invoke(monkeypatch, name, body)
    with pytest.raises(detection.ApiDetectionError) as caught:
        run()
    assert str(caught.value) == ('api_detection_request_failed' if name == 'provider' else 'score_unavailable')
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert CANARY not in ''.join(traceback.format_exception(caught.value))
    assert reply.closed and reply.reads == [detection._MAX_REPLY_BYTES + 1] and len(calls) == 1


@pytest.mark.parametrize('name', ['provider', 'score'])
@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-32'])
def test_finite_unambiguous_reply_preserves_normal_values_and_closes(monkeypatch, name, encoding):
    value = {'done': True, 'metadata': {'usage': 17, 'ratio': .25, 'unset': None},
             'message': {'content': 'Größe'},
             'detections': [{'kind': 'person', 'score': .9, 'box': [.1, .2, .4, .8]}]}
    run, reply, calls = invoke(monkeypatch, name, json.dumps(value, ensure_ascii=False).encode(encoding))
    result = run()
    assert result == value and result['done'] is True and type(result['metadata']['usage']) is int
    assert reply.closed and len(calls) == 1
