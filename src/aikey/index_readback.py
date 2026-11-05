"""Frozen positive/negative native search contract for an index migration.

Expectations are operator-provided labels, not automatic truth or a hardware
comparison. The same plan is used for staged quality and post-restart native
readback. Similarity is fixed; this code never widens a failed negative query.
"""
from __future__ import annotations

import hashlib
import json
import math
import inspect

from .index_sources import _ID, _SHA

SCHEMA = 'aikey-index-readback-plan/1'


class ReadbackError(RuntimeError):
    """Fixed diagnostic, never a private query, event or object identifier."""


def _canonical(value):
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()


class NativeReadbackPlan:
    space = 'clip-basic-v1'

    def __init__(self, value):
        try:
            # Deep-copy into independent data so a caller cannot retain mutable authority.
            value = json.loads(_canonical(value))
            base_keys = {'schema', 'from_revision', 'target_revision', 'source_archive_sha256', 'queries'}
            optional_keys = {'empty_index', 'from_profile_fingerprint', 'target_profile_fingerprint'}
            full = ('from_profile_fingerprint' in value) if isinstance(value, dict) else False
            if (not isinstance(value, dict) or not base_keys <= set(value) or set(value)-base_keys-optional_keys
                    or ('target_profile_fingerprint' in value) != full
                    or (full and (any(not isinstance(value[k], str) or not _SHA.fullmatch(value[k])
                        for k in ('from_profile_fingerprint', 'target_profile_fingerprint'))
                        or value['from_profile_fingerprint'] == value['target_profile_fingerprint']))
                    or value['schema'] != SCHEMA
                    or any(not isinstance(value[k], str) or not _SHA.fullmatch(value[k])
                           for k in ('from_revision', 'target_revision', 'source_archive_sha256'))
                    or (value['from_revision'] == value['target_revision'] and not full)
                    or not isinstance(value['queries'], list) or not 2 <= len(value['queries']) <= 8):
                raise ValueError
            positives = negatives = 0
            for query in value['queries']:
                if (not isinstance(query, dict) or set(query) != {
                        'text', 'min_similarity', 'min_cosine', 'expected_objects', 'k',
                        'camera_id', 'start_ms', 'end_ms'}
                        or not isinstance(query['text'], str) or not query['text'].strip()
                        or len(query['text']) > 1024 or any(ord(c) < 32 for c in query['text'])
                        or type(query['min_similarity']) is not int
                        or not 1 <= query['min_similarity'] <= 100
                        or type(query['min_cosine']) not in (int, float)
                        or not math.isfinite(query['min_cosine']) or not -1 <= query['min_cosine'] <= 1
                        or type(query['k']) is not int or not 1 <= query['k'] <= 10
                        or not isinstance(query['expected_objects'], list)
                        or len(query['expected_objects']) > query['k']
                        or any(not isinstance(x, str) or not _ID.fullmatch(x)
                               for x in query['expected_objects'])
                        or len(set(query['expected_objects'])) != len(query['expected_objects'])
                        or (query['camera_id'] is not None and (not isinstance(query['camera_id'], str)
                            or not _ID.fullmatch(query['camera_id'])))
                        or any(x is not None and (type(x) is not int or not 0 <= x <= 2**53-1)
                               for x in (query['start_ms'], query['end_ms']))
                        or (query['start_ms'] is None) != (query['end_ms'] is None)
                        or (query['start_ms'] is not None and query['start_ms'] > query['end_ms'])):
                    raise ValueError
                positives += bool(query['expected_objects'])
                negatives += not query['expected_objects']
            empty = value.get('empty_index', False)
            if type(empty) is not bool or (empty and positives) or not negatives or (not empty and not positives):
                raise ValueError
            raw = _canonical(value)
            if len(raw) > 32768:
                raise ValueError
            self._raw = raw
            self.sha256 = hashlib.sha256(raw).hexdigest()
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise ReadbackError('The frozen native readback plan is invalid') from None

    @property
    def value(self):
        return json.loads(self._raw)

    def matches(self, *, source_archive_sha256, from_revision, target_revision):
        plan = self.value
        return (plan['source_archive_sha256'] == source_archive_sha256
                and plan['from_revision'] == from_revision and plan['target_revision'] == target_revision)


def check_ranked(query, rows, *, native=False):
    """Validate a whole response before evaluating top-k; never salvage a sibling."""
    if not isinstance(rows, list) or len(rows) > 10_000:
        raise ReadbackError('The index quality response is invalid')
    clean = []
    ids = set()
    for row in rows:
        if (not isinstance(row, dict)
                or any(not isinstance(row.get(k), str) or not _ID.fullmatch(row[k])
                       for k in ('object_id', 'event_id', 'camera_id'))
                or row['object_id'] in ids
                or type(row.get('start_ms')) is not int or not 0 <= row['start_ms'] <= 2**53-1
                or type(row.get('similarity')) not in (int, float)
                or not math.isfinite(row['similarity'])
                or not (-1 <= row['similarity'] <= (100 if native else 1))):
            raise ReadbackError('The index quality response is invalid')
        ids.add(row['object_id'])
        # Queries encode their native camera/time constraints; do not post-filter
        # out wrong-camera/wrong-time results and call the native filter correct.
        if ((query['camera_id'] is not None and row['camera_id'] != query['camera_id'])
                or (query['start_ms'] is not None and not query['start_ms'] <= row['start_ms'] < query['end_ms'])):
            raise ReadbackError('The index quality response violates its camera or date filter')
        clean.append(row)
    threshold = query['min_similarity'] if native else query['min_cosine']
    if any(row['similarity'] < threshold for row in clean):
        raise ReadbackError('The index quality response violates its fixed similarity threshold')
    if not native and any(a['similarity'] < b['similarity'] for a, b in zip(clean, clean[1:])):
        raise ReadbackError('The index quality ranking is not descending')
    expected = set(query['expected_objects'])
    actual = {r['object_id'] for r in clean[:query['k']]}
    passed = expected <= actual if expected else not clean
    return {'passed': passed, 'returned': len(clean), 'expected': len(expected),
            'matched_at_k': len(expected & actual), 'negative': not expected}


async def native_readback(plan: NativeReadbackPlan, native, *, runtime):
    """Run fresh native searches and exact-event reloads under the frozen plan.

    ``runtime`` is a trusted pinned current-runtime observer, not a user-entered
    revision or a saved configuration file. It is re-observed after all queries.
    Native-session logout must separately be acknowledged before a coordinator
    publishes a final successful receipt.
    """
    if type(plan) is not NativeReadbackPlan:
        raise ReadbackError('An exact frozen native readback plan is required')
    target = plan.value['target_revision']
    async def runtime_check():
        state = runtime()
        if inspect.isawaitable(state):
            state = await state
        if (not isinstance(state, dict) or state.get('connected') is not True
                or state.get('revision') != target or state.get('profile') != 'clip-basic-v1'
                or (plan.value.get('target_profile_fingerprint') is not None
                    and state.get('profile_fingerprint') != plan.value['target_profile_fingerprint'])):
            raise ReadbackError('The running query encoder does not match the migrated index')
    await runtime_check()
    outcomes = []
    for query in plan.value['queries']:
        scope = {k: query[k] for k in ('camera_id', 'start_ms', 'end_ms') if query[k] is not None}
        response = await native.search(query['text'], min_similarity=query['min_similarity'], **scope)
        if not isinstance(response, dict) or not isinstance(response.get('detections'), list):
            raise ReadbackError('The native search response is invalid')
        conditions = response.get('searchCondition')
        if query['start_ms'] is not None and (not isinstance(conditions, dict)
                or conditions.get('startTime') != query['start_ms']
                or conditions.get('endTime') != query['end_ms']):
            raise ReadbackError('The native date filter does not match the frozen plan')
        if query['camera_id'] is not None and (not isinstance(conditions, dict)
                or conditions.get('cameraIds') != [query['camera_id']]):
            raise ReadbackError('The native camera filter does not match the frozen plan')
        rows = []
        for row in response['detections']:
            if not isinstance(row, dict):
                raise ReadbackError('The native search response is invalid')
            rows.append({'object_id': row.get('objectId'), 'event_id': row.get('eventId'),
                         'camera_id': row.get('cameraId'), 'start_ms': row.get('detectedAt'),
                         'event_start_ms': row.get('eventStart'),
                         'similarity': row.get('vectorSimilarity')})
        outcome = check_ranked(query, rows, native=True)
        if not outcome['passed']:
            raise ReadbackError('The frozen native search quality target was not met')
        for row in rows[:query['k']]:
            if row['object_id'] not in query['expected_objects']:
                continue
            event = await native.event(row['event_id'])
            if (not isinstance(event, dict) or event.get('id') != row['event_id']
                    or event.get('camera') != row['camera_id']
                    or type(row['event_start_ms']) is not int or event.get('start') != row['event_start_ms']
                    or not isinstance(event.get('metadata'), dict)
                    or not isinstance(event['metadata'].get('detectedThumbnails'), list)
                    or sum(isinstance(t, dict) and t.get('objectId') == row['object_id']
                           for t in event['metadata']['detectedThumbnails']) != 1):
                raise ReadbackError('A native result did not survive exact-event reload')
        outcomes.append(outcome)
    await runtime_check()
    return {'schema': 'aikey-index-native-readback/1', 'plan_sha256': plan.sha256,
            'target_revision': target, 'passed': True, 'queries': outcomes,
            'runtime_before_after_match': True}


def load_readback_plan(value):
    from .index_session_readback import SessionReadbackPlan
    if isinstance(value, dict) and value.get('schema') == SessionReadbackPlan.schema:
        return SessionReadbackPlan(value)
    return NativeReadbackPlan(value)


async def readback_for_plan(plan, native, *, runtime):
    from .index_session_readback import SessionReadbackPlan, session_readback
    if type(plan) is SessionReadbackPlan:
        return await session_readback(plan, native, runtime=runtime)
    return await native_readback(plan, native, runtime=runtime)
