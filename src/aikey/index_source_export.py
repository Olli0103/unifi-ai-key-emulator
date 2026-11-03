"""Export exact authorized original native object crops, without inference.

The caller supplies an immutable read-only index projection and an authenticated
native reader. Only exact native event/object/camera joins are accepted. A
partial/refused export has no manifest and is never an admissible rebuild source.
"""
from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import time

from PIL import Image, UnidentifiedImageError

from .index_sources import MAX_IMAGE, MAX_MANIFEST, MAX_OBJECTS, SCHEMA, _ID, _manifest, _private


class SourceExportError(RuntimeError):
    """Fixed diagnostic without source IDs, paths, media or response bodies."""


def _write(root, name, data):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=root)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(root)


def _jpeg(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_IMAGE:
        raise SourceExportError('An original crop exceeds the export bounds')
    try:
        with Image.open(BytesIO(raw)) as image:
            if (image.format != 'JPEG' or not 0 < image.width <= 4096
                    or not 0 < image.height <= 4096):
                raise ValueError
            image.load()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise SourceExportError('An original crop is not a bounded JPEG') from None


async def export_crop_archive(destination: Path, rows: list[dict], native, *,
                              authorized_cameras: frozenset[str], max_bytes=512*1024*1024,
                              timeout=600, clock=time.monotonic):
    """Read only named events/crops; no scanning, fallback, retry or camera grant.

    ``native.event`` and ``native.crop`` may return None only for a definitive
    not-found result. Authentication, transport, malformed data or mismatched
    object association must raise. A missing historical source is counted, not
    replaced with a nearby frame or enhanced derivative. Output is private and
    exclusive. Only terminal, fsynced exports publish manifest.json.
    """
    if (not isinstance(rows, list) or not 0 <= len(rows) <= MAX_OBJECTS
            or not isinstance(authorized_cameras, frozenset) or not authorized_cameras
            or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in authorized_cameras)
            or type(max_bytes) is not int or not 1 <= max_bytes <= 8*1024**3
            or type(timeout) is not int or not 1 <= timeout <= 3600):
        raise SourceExportError('The export admission is invalid')
    ids = set()
    selected = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'object_id', 'event_id', 'camera_id'}
                or any(not isinstance(v, str) or not _ID.fullmatch(v) for v in row.values())
                or row['object_id'] in ids):
            raise SourceExportError('The index projection is invalid or ambiguous')
        ids.add(row['object_id'])
        if row['camera_id'] in authorized_cameras:
            selected.append(dict(row))
    if rows and not selected:
        raise SourceExportError('The export has no currently authorized objects')
    selected.sort(key=lambda x: x['object_id'])
    # Admission and full projection validation precede filesystem and network effects.
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    directory = images = None
    deadline = clock() + timeout
    counters = {'indexed': len(rows), 'authorized': len(selected), 'exported': 0,
                'outside_authorization': len(rows)-len(selected), 'no_source': 0, 'image_bytes': 0}
    try:
        directory = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _private(os.fstat(directory), directory=True)
        os.mkdir('images', 0o700, dir_fd=directory)
        images = os.open('images', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        _private(os.fstat(images), directory=True)
        result = []
        saved = set()
        async with asyncio.timeout(timeout):
            for row in selected:
                if clock() >= deadline:
                    raise SourceExportError('The source export deadline expired')
                event = await native.event(row['event_id'])
                if event is None:
                    counters['no_source'] += 1
                    continue
                if (not isinstance(event, dict) or event.get('id') != row['event_id']
                        or event.get('camera') != row['camera_id']
                        or not isinstance(event.get('metadata'), dict)
                        or not isinstance(event['metadata'].get('detectedThumbnails'), list)):
                    raise SourceExportError('The native event association is invalid')
                matches = [x for x in event['metadata']['detectedThumbnails']
                           if isinstance(x, dict) and x.get('objectId') == row['object_id']]
                if not matches:
                    counters['no_source'] += 1
                    continue
                if len(matches) != 1:
                    raise SourceExportError('The native object association is ambiguous')
                crop = matches[0].get('croppedId')
                if not isinstance(crop, str) or not _ID.fullmatch(crop):
                    raise SourceExportError('The native original crop reference is invalid')
                raw = await native.crop(crop)
                if raw is None:
                    counters['no_source'] += 1
                    continue
                _jpeg(raw)
                if counters['image_bytes'] + len(raw) > max_bytes:
                    raise SourceExportError('The source export byte budget is exhausted')
                digest = hashlib.sha256(raw).hexdigest()
                if digest not in saved:
                    _write(images, digest+'.jpg', raw)
                    saved.add(digest)
                result.append(dict(row, crop_id=crop, input_sha256=digest))
                counters['exported'] += 1
                counters['image_bytes'] += len(raw)
            if clock() >= deadline:
                raise SourceExportError('The source export deadline expired')
            manifest = {'schema': SCHEMA, 'source': 'protect-original-object-crops',
                        'authorized_cameras': sorted(authorized_cameras), 'objects': result}
            raw = (json.dumps(manifest, sort_keys=True, separators=(',', ':'))+'\n').encode()
            _manifest(raw)  # Includes duplicate crop/association refusal, before terminal publication.
            if len(raw) > MAX_MANIFEST:
                raise SourceExportError('The source manifest exceeds its bounds')
            receipt = {'schema': 'aikey-index-source-export/1', **counters,
                       'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                       'complete_coverage': counters['exported'] == counters['indexed']}
            _write(directory, 'receipt.json', (json.dumps(receipt, sort_keys=True)+'\n').encode())
            _write(directory, 'manifest.json', raw)  # Last: the rebuild's admission marker.
            return receipt
    except (OSError, TimeoutError) as exc:
        if isinstance(exc, TimeoutError):
            raise SourceExportError('The source export deadline expired') from None
        raise SourceExportError('The private source export could not be completed') from None
    finally:
        if images is not None:
            os.close(images)
        if directory is not None:
            os.close(directory)
