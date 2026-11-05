"""Frozen native readback decisions, with whole-response rejection."""
from copy import deepcopy

import pytest

from aikey.index_readback import NativeReadbackPlan, ReadbackError, SCHEMA, native_readback

OLD, NEW, SOURCE = 'a'*64, 'b'*64, 'c'*64


def specification():
    query = {'text': 'a red toy car', 'min_similarity': 53, 'min_cosine': 0.8,
             'expected_objects': ['object-a'], 'k': 2,
             'camera_id': None, 'start_ms': None, 'end_ms': None}
    return {'schema': SCHEMA, 'from_revision': OLD, 'target_revision': NEW,
            'source_archive_sha256': SOURCE, 'queries': [query,
                dict(query, text='a violet toy submarine', expected_objects=[])]}


class Native:
    def __init__(self):
        self.rows = [{'objectId': 'object-a', 'eventId': 'event-a', 'cameraId': 'camera-a',
                      'eventStart': 100, 'detectedAt': 120, 'vectorSimilarity': 55}]
        self.event_value = {'id': 'event-a', 'camera': 'camera-a', 'start': 100,
                            'metadata': {'detectedThumbnails': [{'objectId': 'object-a'}]}}
        self.calls = []
        self.conditions = {}
        self.negative_rows = []

    async def search(self, text, *, min_similarity, **scope):
        self.calls.append(('search', text, min_similarity))
        return {'detections': deepcopy(self.rows if text == 'a red toy car' else self.negative_rows),
                'searchCondition': dict(self.conditions)}

    async def event(self, ident):
        self.calls.append(('event', ident))
        return deepcopy(self.event_value)


def runtime():
    return {'connected': True, 'revision': NEW, 'profile': 'clip-basic-v1'}


async def test_exact_positive_negative_and_event_reload_remain_bound_to_runtime():
    plan = NativeReadbackPlan(specification())
    native = Native()
    checks = []
    async def observe():
        checks.append(True)
        return runtime()
    receipt = await native_readback(plan, native, runtime=observe)
    assert receipt['passed'] and receipt['plan_sha256'] == plan.sha256
    assert len(checks) == 2
    assert native.calls == [('search', 'a red toy car', 53), ('event', 'event-a'),
                            ('search', 'a violet toy submarine', 53)]
    assert [x['negative'] for x in receipt['queries']] == [False, True]
    assert 'object-a' not in str(receipt) and 'toy car' not in str(receipt)


@pytest.mark.parametrize('mutation', ['source', 'revision', 'no-negative', 'no-positive',
    'zero-threshold', 'boolean-threshold', 'bad-cosine', 'duplicate-object', 'extra-field', 'bad-time'])
def test_bad_plan_never_becomes_authority(mutation):
    value = specification()
    if mutation == 'source':
        value['source_archive_sha256'] = None
    elif mutation == 'revision':
        value['target_revision'] = OLD
    elif mutation == 'no-negative':
        value['queries'][1]['expected_objects'] = ['object-a']
    elif mutation == 'no-positive':
        value['queries'][0]['expected_objects'] = []
    elif mutation == 'zero-threshold':
        value['queries'][0]['min_similarity'] = 0
    elif mutation == 'boolean-threshold':
        value['queries'][0]['min_similarity'] = True
    elif mutation == 'bad-cosine':
        value['queries'][0]['min_cosine'] = float('nan')
    elif mutation == 'duplicate-object':
        value['queries'][0]['expected_objects'] *= 2
    elif mutation == 'extra-field':
        value['queries'][0]['accept_anything'] = True
    elif mutation == 'bad-time':
        value['queries'][0]['end_ms'] = 100
    with pytest.raises(ReadbackError, match='plan is invalid'):
        NativeReadbackPlan(value)


async def test_plan_is_deep_frozen_and_correlated_to_source_and_revisions():
    value = specification()
    plan = NativeReadbackPlan(value)
    value['queries'][0]['min_similarity'] = 1
    returned = plan.value
    returned['queries'][0]['text'] = 'changed'
    assert plan.value['queries'][0]['min_similarity'] == 53
    assert plan.value['queries'][0]['text'] == 'a red toy car'
    assert plan.matches(source_archive_sha256=SOURCE, from_revision=OLD, target_revision=NEW)
    assert not plan.matches(source_archive_sha256='d'*64, from_revision=OLD, target_revision=NEW)


@pytest.mark.parametrize('mutation', ['wrong-object', 'duplicate', 'malformed-sibling',
                                      'nonfinite', 'below-threshold', 'wrong-event', 'wrong-camera'])
async def test_wrong_or_ambiguous_native_results_are_never_salvaged(mutation):
    native = Native()
    if mutation == 'wrong-object':
        native.rows[0]['objectId'] = 'other-object'
    elif mutation == 'duplicate':
        native.rows *= 2
    elif mutation == 'malformed-sibling':
        native.rows.append({'objectId': 'broken'})
    elif mutation == 'nonfinite':
        native.rows[0]['vectorSimilarity'] = float('nan')
    elif mutation == 'below-threshold':
        native.rows[0]['vectorSimilarity'] = 52
    elif mutation == 'wrong-event':
        native.event_value['id'] = 'other-event'
    elif mutation == 'wrong-camera':
        native.event_value['camera'] = 'other-camera'
    with pytest.raises(ReadbackError):
        await native_readback(NativeReadbackPlan(specification()), native, runtime=runtime)
    assert len([x for x in native.calls if x[0] == 'search']) == 1


async def test_negative_query_never_gets_a_lower_threshold_retry():
    native = Native()
    native.negative_rows = deepcopy(native.rows)
    with pytest.raises(ReadbackError, match='quality target was not met'):
        await native_readback(NativeReadbackPlan(specification()), native, runtime=runtime)
    assert [x[2] for x in native.calls if x[0] == 'search'] == [53, 53]


@pytest.mark.parametrize('stage', ['before', 'after'])
async def test_running_old_or_disconnected_encoder_refuses(stage):
    native = Native()
    seen = []
    def observe():
        seen.append(True)
        state = runtime()
        if stage == 'before' or len(seen) == 2:
            state['revision'] = OLD
        return state
    with pytest.raises(ReadbackError, match='running query encoder'):
        await native_readback(NativeReadbackPlan(specification()), native, runtime=observe)
    assert bool(native.calls) == (stage == 'after')


async def test_native_time_order_is_not_invented_as_similarity_sorting():
    native = Native()
    native.rows.append(dict(native.rows[0], objectId='object-b', vectorSimilarity=60))
    assert (await native_readback(NativeReadbackPlan(specification()), native, runtime=runtime))['passed']


async def test_date_uses_detection_time_not_event_start_and_is_half_open():
    value = specification()
    value['queries'][0].update(start_ms=110, end_ms=130)
    native = Native()
    native.conditions = {'startTime': 110, 'endTime': 130}
    assert (await native_readback(NativeReadbackPlan(value), native, runtime=runtime))['passed']
    native.rows[0]['detectedAt'] = 130
    with pytest.raises(ReadbackError, match='date filter'):
        await native_readback(NativeReadbackPlan(value), native, runtime=runtime)


async def test_missing_native_filter_acknowledgment_is_not_a_filter_pass():
    value = specification()
    value['queries'][0]['camera_id'] = 'camera-a'
    native = Native()
    with pytest.raises(ReadbackError, match='camera filter'):
        await native_readback(NativeReadbackPlan(value), native, runtime=runtime)
    native.conditions = {'cameraIds': ['camera-a']}
    assert (await native_readback(NativeReadbackPlan(value), native, runtime=runtime))['passed']


async def test_same_weight_readback_also_requires_runtime_full_profile_identity():
    value = specification()
    value.update(from_revision=NEW, from_profile_fingerprint='d'*64, target_profile_fingerprint='e'*64)
    plan = NativeReadbackPlan(value)
    class Never:
        async def search(self, *args, **kwargs):
            raise AssertionError('No query with the wrong full runtime identity')
    with pytest.raises(ReadbackError, match='running query encoder'):
        await native_readback(plan, Never(), runtime=runtime)


async def test_explicit_camera_request_evidence_does_not_fabricate_server_echo_or_hide_wrong_rows():
    value = specification()
    value['queries'][0]['camera_id'] = 'camera-a'
    class Explicit(Native):
        async def search(self, text, *, min_similarity, **scope):
            result = await super().search(text, min_similarity=min_similarity, **scope)
            if scope:
                result['_aikey_request_scope'] = dict(camera_id=scope.get('camera_id'),
                    start_ms=scope.get('start_ms'), end_ms=scope.get('end_ms'))
            return result
    native = Explicit()
    assert (await native_readback(NativeReadbackPlan(value), native, runtime=runtime))['passed']
    native.conditions = {'cameraIds': ['camera-b']}
    with pytest.raises(ReadbackError, match='camera filter'):
        await native_readback(NativeReadbackPlan(value), native, runtime=runtime)
    native.conditions = {}
    native.rows[0]['cameraId'] = 'camera-b'
    with pytest.raises(ReadbackError, match='camera or date filter'):
        await native_readback(NativeReadbackPlan(value), native, runtime=runtime)
