"""Exact native search-snapshot source recovery, never approximate substitution."""
import pytest

from aikey.index_source_lookup import SourceLookupError, lookup_source_object
from aikey.index_source_export import SourceExportError, export_crop_archive
from test_index_source_export import AUTH, ROWS, native

EVENT = {'id': 'event-a', 'camera': 'camera-a', 'start': 1000000, 'end': 1005000}
MATCH = {'objectId': 'object-a', 'eventId': 'event-a', 'cameraId': 'camera-a',
         'thumbnailId': 'original-a'}


class Search:
    def __init__(self, pages):
        self.pages, self.calls = iter(pages), []

    async def source_detections(self, term, **params):
        self.calls.append((term, params))
        return next(self.pages)


async def test_second_native_representation_joins_all_three_identifiers():
    client = Search([{'detections': []}, {'detections': [MATCH]}])
    result = await lookup_source_object(client, ROWS[0], EVENT)
    assert result == {**{k: MATCH[k] for k in ('objectId', 'eventId', 'cameraId')},
                      'croppedId': 'original-a'}
    assert [c[0] for c in client.calls] == ['image', 'person']
    assert all(c[1] == {'start': 940000, 'end': 1065000, 'camera_id': 'camera-a',
                        'offset': 0, 'limit': 1000} for c in client.calls)


@pytest.mark.parametrize('field,value', [('eventId', 'other'), ('cameraId', 'other'),
                                        ('thumbnailId', '../enhanced'), ('thumbnailId', None)])
async def test_wrong_association_never_becomes_missing_or_substitute(field, value):
    client = Search([{'detections': [dict(MATCH, **{field: value})]}])
    with pytest.raises(SourceLookupError, match='association'):
        await lookup_source_object(client, ROWS[0], EVENT)
    assert len(client.calls) == 1


@pytest.mark.parametrize('value', [{}, {'detections': None}, {'detections': [None]},
                                  {'detections': [MATCH, MATCH]}])
async def test_bad_response_and_duplicate_identity_are_refused(value):
    with pytest.raises(SourceLookupError):
        await lookup_source_object(Search([value]), ROWS[0], EVENT)


async def test_pagination_is_complete_even_when_match_is_on_first_page(monkeypatch):
    monkeypatch.setattr('aikey.index_source_lookup.PAGE_SIZE', 1)
    client = Search([{'detections': [MATCH]}, {'detections': [MATCH]}])
    with pytest.raises(SourceLookupError, match='pagination'):
        await lookup_source_object(client, ROWS[0], EVENT)
    assert [c[1]['offset'] for c in client.calls] == [0, 1]


async def test_page_budget_and_transport_fail_closed(monkeypatch):
    monkeypatch.setattr('aikey.index_source_lookup.PAGE_SIZE', 1)
    monkeypatch.setattr('aikey.index_source_lookup.MAX_PAGES', 1)
    with pytest.raises(SourceLookupError, match='budget'):
        await lookup_source_object(Search([{'detections': [MATCH]}]), ROWS[0], EVENT)
    class Refused:
        async def source_detections(self, *args, **kwargs):
            raise RuntimeError('authentication refused')
    with pytest.raises(RuntimeError, match='authentication'):
        await lookup_source_object(Refused(), ROWS[0], EVENT)


@pytest.mark.parametrize('patch', [{'end': None}, {'start': True}, {'end': 1},
                                  {'end': 1000000000}, {'camera': 'other'}])
async def test_bad_event_never_starts_search(patch):
    client = Search([])
    with pytest.raises(SourceLookupError):
        await lookup_source_object(client, ROWS[0], dict(EVENT, **patch))
    assert not client.calls


async def test_no_match_is_explicitly_unresolved():
    client = Search([{'detections': []}]*6)
    assert await lookup_source_object(client, ROWS[0], EVENT) is None
    assert len(client.calls) == 6


@pytest.mark.parametrize('wrong', [False, True])
async def test_export_validates_resolver_then_reads_original_bytes(tmp_path, wrong):
    client = native(tmp_path)
    client.value['metadata']['detectedThumbnails'] = []
    async def resolve(row, event):
        return {'objectId': 'object-a', 'eventId': 'event-a',
                'cameraId': 'other' if wrong else 'camera-a', 'croppedId': 'original-a'}
    client.source_object = resolve
    target = tmp_path/'resolved'
    if wrong:
        with pytest.raises(SourceExportError, match='search-snapshot'):
            await export_crop_archive(target, ROWS, client, authorized_cameras=AUTH)
        assert not (target/'manifest.json').exists()
        assert client.calls == [('event', 'event-a')]
    else:
        receipt = await export_crop_archive(target, ROWS, client, authorized_cameras=AUTH)
        assert receipt['complete_coverage']
        assert client.calls == [('event', 'event-a'), ('crop', 'original-a')]
