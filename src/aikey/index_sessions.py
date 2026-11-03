"""Private, input-bound session-description source for the E5 index space.

A description is eligible only when every native member camera is in the
current explicit authorization. Hashes bind text and all session associations;
no descriptions, camera/session IDs or vectors are returned in summaries.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .index_sources import MAX_MANIFEST, MAX_OBJECTS, _ID, _SHA, _pairs, _private, _read
from .index_source_export import _write

SCHEMA = 'aikey-session-source-archive/1'


class SessionSourceError(RuntimeError):
    pass


def _row(value, *, allow_missing=False):
    if (not isinstance(value, dict) or set(value) != {
            'session_id', 'description', 'camera_ids', 'first_seen', 'last_seen'}
            or not isinstance(value['session_id'], str) or not _ID.fullmatch(value['session_id'])
            or not (isinstance(value['description'], str) or allow_missing and value['description'] is None)
            or (isinstance(value['description'], str) and len(value['description']) > 8192)
            or (not allow_missing and not value['description'].strip())
            or not isinstance(value['camera_ids'], list) or not 1 <= len(value['camera_ids']) <= 32
            or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in value['camera_ids'])
            or len(set(value['camera_ids'])) != len(value['camera_ids'])
            or any(type(value[k]) is not int or not 0 <= value[k] <= 2**53-1
                   for k in ('first_seen', 'last_seen'))
            or value['first_seen'] > value['last_seen']):
        raise SessionSourceError('The session source row is invalid or ambiguous')
    result = dict(value, camera_ids=sorted(value['camera_ids']))
    try:
        if result['description'] is not None:
            result['description'].encode('utf-8')
    except UnicodeError:
        raise SessionSourceError('The session source text is invalid') from None
    return result


def association(row):
    return (hashlib.sha256(row['description'].encode()).hexdigest(),
            tuple(sorted(row['camera_ids'])), row['first_seen'], row['last_seen'])


def _parse(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not isinstance(value, dict) or set(value) != {'schema', 'authorized_cameras', 'sessions'}
                or value['schema'] != SCHEMA or not isinstance(value['authorized_cameras'], list)
                or not 1 <= len(value['authorized_cameras']) <= 256
                or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in value['authorized_cameras'])
                or len(set(value['authorized_cameras'])) != len(value['authorized_cameras'])
                or not isinstance(value['sessions'], list) or not 0 <= len(value['sessions']) <= MAX_OBJECTS):
            raise ValueError
        rows = {}
        for item in value['sessions']:
            row = _row(item)
            if row['session_id'] in rows or not set(row['camera_ids']) <= set(value['authorized_cameras']):
                raise ValueError
            rows[row['session_id']] = row
        return value, rows
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise SessionSourceError('The session source manifest is invalid') from None


class SessionTextArchive:
    space = 'e5-session-v1'

    def __init__(self, root: Path, *, manifest_sha256, authorized_cameras, association):
        self._directory = None
        try:
            if (not isinstance(manifest_sha256, str) or not _SHA.fullmatch(manifest_sha256)
                    or not isinstance(authorized_cameras, frozenset) or not authorized_cameras
                    or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in authorized_cameras)
                    or not callable(association)):
                raise SessionSourceError('A frozen session source and explicit camera scope are required')
            self._directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            _private(os.fstat(self._directory), directory=True)
            self._raw = _read(self._directory, 'manifest.json', MAX_MANIFEST)
            if hashlib.sha256(self._raw).hexdigest() != manifest_sha256:
                raise SessionSourceError('The session source differs from its frozen manifest')
            self._manifest, self._rows = _parse(self._raw)
            if not set(self._manifest['authorized_cameras']) <= authorized_cameras:
                raise SessionSourceError('The session source exceeds current camera authorization')
            self.manifest_sha256 = manifest_sha256
            self._association = association
        except BaseException:
            self.close()
            raise

    def validate_binding(self):
        if self._directory is None:
            raise SessionSourceError('The session source is closed')
        if _read(self._directory, 'manifest.json', MAX_MANIFEST) != self._raw:
            raise SessionSourceError('The session source manifest changed')
        _private(os.fstat(self._directory), directory=True)
        return self.manifest_sha256

    async def __call__(self, session_id):
        self.validate_binding()
        row = self._rows.get(session_id)
        if row is None:
            return None
        if self._association(session_id) != association(row):
            raise SessionSourceError('The session text or camera/date association changed')
        return row['description'].encode()

    def close(self):
        if self._directory is not None:
            os.close(self._directory)
            self._directory = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def export_session_archive(destination: Path, rows, *, authorized_cameras):
    if (not isinstance(rows, list) or not 0 <= len(rows) <= MAX_OBJECTS
            or not isinstance(authorized_cameras, frozenset) or not authorized_cameras
            or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in authorized_cameras)):
        raise SessionSourceError('The session export admission is invalid')
    selected, ids, missing = [], set(), 0
    for item in rows:
        row = _row(item, allow_missing=True)
        if row['session_id'] in ids:
            raise SessionSourceError('The session source projection is ambiguous')
        ids.add(row['session_id'])
        if set(row['camera_ids']) <= authorized_cameras:
            if not row['description'] or not row['description'].strip():
                missing += 1
            else:
                selected.append(row)
    manifest = {'schema': SCHEMA, 'authorized_cameras': sorted(authorized_cameras),
                'sessions': sorted(selected, key=lambda x: x['session_id'])}
    raw = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode()
    if len(raw) > MAX_MANIFEST:
        raise SessionSourceError('The session source export exceeds its byte budget')
    _parse(raw)  # No directory publication on invalid/empty/ambiguous data.
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    directory = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _private(os.fstat(directory), directory=True)
        receipt = {'schema': 'aikey-session-source-export/1', 'indexed': len(rows),
                   'exported': len(selected), 'outside_authorization': len(rows)-len(selected)-missing, 'no_source': missing,
                   'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                   'complete_coverage': len(rows) == len(selected)}
        _write(directory, 'receipt.json', (json.dumps(receipt, sort_keys=True)+'\n').encode())
        _write(directory, 'manifest.json', raw)
        return receipt
    finally:
        os.close(directory)
