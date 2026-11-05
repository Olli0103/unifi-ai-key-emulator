"""Read native search-snapshot associations absent from event thumbnail lists.

Protect creates additional smart-detect objects for key-moment search snapshots.
Their exact IDs/event/camera/original thumbnail are returned by detection-nls,
but are not necessarily included in event.metadata.detectedThumbnails.
Search recall is never treated as completeness: unresolved rows still block the
rebuild. Search may perform text inference; no image is synthesized or enhanced.
"""
from __future__ import annotations

from .index_sources import _ID

TERMS = ('image', 'person', 'animal', 'vehicle', 'package', 'face')
PAGE_SIZE = 1000
MAX_PAGES = 10


class SourceLookupError(RuntimeError):
    """Fixed diagnostics without source identifiers or response bodies."""


async def lookup_source_object(native, row, event):
    if (event.get('id') != row['event_id'] or event.get('camera') != row['camera_id']
            or any(not isinstance(v, str) or not _ID.fullmatch(v) for v in row.values())):
        raise SourceLookupError('The source lookup association is invalid')
    start, end = event.get('start'), event.get('end')
    if (type(start) is not int or type(end) is not int
            or not 60000 <= start <= end <= 2**53-60001 or end-start > 86400000):
        raise SourceLookupError('The source lookup requires bounded completed event times')
    for term in TERMS:
        matches = []
        seen = set()
        for page in range(MAX_PAGES):
            value = await native.source_detections(term, start=start-60000, end=end+60000,
                camera_id=row['camera_id'], offset=page*PAGE_SIZE, limit=PAGE_SIZE)
            if (not isinstance(value, dict) or not isinstance(value.get('detections'), list)
                    or len(value['detections']) > PAGE_SIZE):
                raise SourceLookupError('The source lookup response is invalid')
            detections = value['detections']
            for item in detections:
                if not isinstance(item, dict):
                    raise SourceLookupError('The source lookup response is invalid')
                ident = item.get('objectId')
                if not isinstance(ident, str) or not _ID.fullmatch(ident) or ident in seen:
                    raise SourceLookupError('The source lookup pagination is ambiguous')
                seen.add(ident)
                if ident != row['object_id']:
                    continue
                if (item.get('eventId') != row['event_id']
                        or item.get('cameraId') != row['camera_id']
                        or not isinstance(item.get('thumbnailId'), str)
                        or not _ID.fullmatch(item['thumbnailId'])):
                    raise SourceLookupError('The source lookup association is invalid')
                matches.append({'objectId': ident, 'eventId': item['eventId'],
                                'cameraId': item['cameraId'], 'croppedId': item['thumbnailId']})
            if len(detections) < PAGE_SIZE:
                break
        else:
            raise SourceLookupError('The source lookup page budget is exhausted')
        if matches:
            return matches[0]
    return None
