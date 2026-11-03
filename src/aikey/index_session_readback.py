"""Frozen E5 session-migration targets, separate from native UI rejection claims."""
from __future__ import annotations

import hashlib
import inspect
import json
import math

from .index_readback import ReadbackError, _canonical
from .index_sources import _ID, _SHA


class SessionReadbackPlan:
    schema = 'aikey-session-index-readback-plan/1'
    space = 'e5-session-v1'

    def __init__(self, value):
        try:
            value = json.loads(_canonical(value))
            base_keys = {'schema', 'from_revision', 'target_revision', 'source_archive_sha256', 'queries'}
            optional_keys = {'empty_index', 'from_profile_fingerprint', 'target_profile_fingerprint', 'unversioned_source'}
            full = ('from_profile_fingerprint' in value) if isinstance(value, dict) else False
            unknown = isinstance(value, dict) and value.get('unversioned_source') is True
            if (not isinstance(value, dict) or not base_keys <= set(value) or set(value)-base_keys-optional_keys
                    or ('target_profile_fingerprint' in value) != full
                    or (full and (any(not isinstance(value[k], str) or not _SHA.fullmatch(value[k])
                        for k in ('from_profile_fingerprint', 'target_profile_fingerprint'))
                        or value['from_profile_fingerprint'] == value['target_profile_fingerprint']))
                    or value['schema'] != self.schema
                    or any(not isinstance(value[k], str) or not _SHA.fullmatch(value[k])
                           for k in ('target_revision', 'source_archive_sha256'))
                    or (unknown and (value['from_revision'] is not None or not full))
                    or (not unknown and (not isinstance(value['from_revision'], str)
                        or not _SHA.fullmatch(value['from_revision'])))
                    or ('unversioned_source' in value and value['unversioned_source'] is not True)
                    or (value['from_revision'] == value['target_revision'] and not full)
                    or not isinstance(value['queries'], list) or not 2 <= len(value['queries']) <= 8):
                raise ValueError
            positive = negative = False
            for query in value['queries']:
                if (not isinstance(query, dict) or set(query) != {'text', 'min_cosine', 'min_confidence',
                        'expected_sessions', 'description_sha256', 'k', 'camera_ids', 'start_ms', 'end_ms'}
                        or not isinstance(query['text'], str) or not query['text'].strip()
                        or len(query['text']) > 1024 or any(ord(c) < 32 for c in query['text'])
                        or type(query['min_cosine']) not in (int, float)
                        or not math.isfinite(query['min_cosine']) or not -1 <= query['min_cosine'] <= 1
                        or type(query['min_confidence']) is not int or not 1 <= query['min_confidence'] <= 100
                        or type(query['k']) is not int or not 1 <= query['k'] <= 10
                        or not isinstance(query['expected_sessions'], list)
                        or len(query['expected_sessions']) > query['k']
                        or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in query['expected_sessions'])
                        or len(set(query['expected_sessions'])) != len(query['expected_sessions'])
                        or not isinstance(query['description_sha256'], dict)
                        or set(query['description_sha256']) != set(query['expected_sessions'])
                        or any(not isinstance(x, str) or not _SHA.fullmatch(x)
                               for x in query['description_sha256'].values())
                        or not isinstance(query['camera_ids'], list) or len(query['camera_ids']) > 32
                        or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in query['camera_ids'])
                        or len(set(query['camera_ids'])) != len(query['camera_ids'])
                        or any(x is not None and (type(x) is not int or not 0 <= x <= 2**53-1)
                               for x in (query['start_ms'], query['end_ms']))
                        or (query['start_ms'] is None) != (query['end_ms'] is None)
                        or (query['start_ms'] is not None and query['start_ms'] >= query['end_ms'])):
                    raise ValueError
                positive |= bool(query['expected_sessions'])
                negative |= not query['expected_sessions']
            empty = value.get('empty_index', False)
            if type(empty) is not bool or (empty and positive) or not negative or (not empty and not positive):
                raise ValueError
            self._raw = _canonical(value)
            if len(self._raw) > 32768:
                raise ValueError
            self.sha256 = hashlib.sha256(self._raw).hexdigest()
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise ReadbackError('The frozen session migration plan is invalid') from None

    @property
    def value(self):
        return json.loads(self._raw)

    def matches(self, *, source_archive_sha256, from_revision, target_revision):
        value = self.value
        return (value['source_archive_sha256'] == source_archive_sha256
                and value['from_revision'] == from_revision and value['target_revision'] == target_revision)


def check_sessions(query, rows, *, native=False):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ReadbackError('The session migration response is invalid')
    ids, qualified = set(), []
    threshold = query['min_confidence'] if native else query['min_cosine']
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('session_id'), str)
                or not _ID.fullmatch(row['session_id']) or row['session_id'] in ids
                or not isinstance(row.get('camera_ids'), list) or not 1 <= len(row['camera_ids']) <= 32
                or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in row['camera_ids'])
                or len(set(row['camera_ids'])) != len(row['camera_ids'])
                or any(type(row.get(k)) is not int or not 0 <= row[k] <= 2**53-1
                       for k in ('first_seen', 'last_seen'))
                or row['first_seen'] > row['last_seen']
                or type(row.get('similarity')) not in (int, float) or not math.isfinite(row['similarity'])
                or not ((0 if native else -1) <= row['similarity'] <= (100 if native else 1))
                or not isinstance(row.get('description_sha256'), str)
                or not _SHA.fullmatch(row['description_sha256'])):
            raise ReadbackError('The session migration response is invalid')
        ids.add(row['session_id'])
        if ((query['camera_ids'] and not set(row['camera_ids']) & set(query['camera_ids']))
                or (query['start_ms'] is not None and not (
                    row['first_seen'] < query['end_ms'] and row['last_seen'] >= query['start_ms']))):
            raise ReadbackError('The session migration response violates its camera/date constraint')
        expected_hash = query['description_sha256'].get(row['session_id'])
        if expected_hash is not None and row['description_sha256'] != expected_hash:
            raise ReadbackError('A native session description differs from the frozen source')
        if row['similarity'] >= threshold:
            qualified.append(row)
        elif not native:
            raise ReadbackError('The staged session query violated its cosine threshold')
    if not native and any(a['similarity'] < b['similarity'] for a, b in zip(rows, rows[1:])):
        raise ReadbackError('The staged session ranking is not descending')
    expected = set(query['expected_sessions'])
    actual = {r['session_id'] for r in qualified[:query['k']]}
    passed = expected <= actual if expected else not qualified
    return {'passed': passed, 'returned_raw': len(rows), 'above_frozen_score': len(qualified),
            'expected': len(expected), 'matched_at_k': len(actual & expected), 'negative': not expected}


def _native_rows(response):
    if not isinstance(response, dict) or not isinstance(response.get('sessions'), list):
        raise ReadbackError('The native session search response is invalid')
    result = []
    for row in response['sessions']:
        if not isinstance(row, dict) or not isinstance(row.get('description'), str) or len(row['description']) > 8192:
            raise ReadbackError('The native session search response is invalid')
        try:
            digest = hashlib.sha256(row['description'].encode('utf-8')).hexdigest()
        except UnicodeError:
            raise ReadbackError('The native session description is invalid') from None
        result.append({'session_id': row.get('id'), 'camera_ids': row.get('cameras'),
            'first_seen': row.get('firstSeen'), 'last_seen': row.get('lastSeen'),
            'similarity': row.get('confidence'), 'description_sha256': digest})
    return result


async def session_readback(plan, native, *, runtime):
    if type(plan) is not SessionReadbackPlan:
        raise ReadbackError('An exact frozen session migration plan is required')
    async def observe():
        value = runtime()
        if inspect.isawaitable(value):
            value = await value
        if (not isinstance(value, dict) or value.get('connected') is not True
                or value.get('profile') != plan.space or value.get('revision') != plan.value['target_revision']
                or (plan.value.get('target_profile_fingerprint') is not None
                    and value.get('profile_fingerprint') != plan.value['target_profile_fingerprint'])):
            raise ReadbackError('The running E5 query encoder does not match this session generation')
    await observe()
    results = []
    for query in plan.value['queries']:
        outcome = check_sessions(query, _native_rows(await native.search_sessions(query['text'])), native=True)
        if not outcome['passed']:
            raise ReadbackError('The frozen native session migration target was not met')
        if query['expected_sessions']:
            reload = check_sessions(query, _native_rows(await native.search_sessions(query['text'])), native=True)
            if not reload['passed']:
                raise ReadbackError('The native session target did not survive fresh search reload')
        results.append(outcome)
    await observe()
    return {'schema': 'aikey-session-index-native-readback/1', 'plan_sha256': plan.sha256,
            'target_revision': plan.value['target_revision'], 'passed': True, 'queries': results,
            'runtime_before_after_match': True,
            'scope': 'frozen_score_target_raw_low_confidence_results_not_hidden_no_ui_rejection_claim'}
