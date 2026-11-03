"""Private text exports, exact live joins and immutable profile-space separation."""
from copy import deepcopy
import hashlib
import json
import os

import pytest

from aikey.embedding_profile import (
    EmbeddingProfileError, _with_fingerprint, ensure_embedding_profile, ensure_session_embedding_profile,
)
from aikey.encoding_identity import CLIP_ENCODING, TEXT_ENCODING
from aikey.index_sessions import SessionTextArchive, SessionSourceError, association, export_session_archive

ROW = {'session_id': 'session-a', 'description': 'Fictional toy car on a paper road.',
       'camera_ids': ['camera-a'], 'first_seen': 100, 'last_seen': 200}
AUTH = frozenset({'camera-a'})


def source(tmp_path):
    target = tmp_path/'export'
    receipt = export_session_archive(target, [deepcopy(ROW)], authorized_cameras=AUTH)
    return target, receipt


async def test_session_text_is_exact_private_and_current_source_bound(tmp_path):
    target, receipt = source(tmp_path)
    before = {p: p.read_bytes() for p in target.iterdir()}
    with SessionTextArchive(target, manifest_sha256=receipt['manifest_sha256'], authorized_cameras=AUTH,
            association=lambda _: association(ROW)) as archive:
        assert await archive('session-a') == ROW['description'].encode()
        assert await archive('not-exported') is None
    assert {p: p.read_bytes() for p in target.iterdir()} == before
    assert all(p.stat().st_mode & 0o077 == 0 for p in [target,*target.iterdir()])
    assert ROW['description'] not in str(receipt)
    with pytest.raises(SessionSourceError, match='closed'):
        await archive('session-a')


@pytest.mark.parametrize('field,value', [('description','Different fictional scene'),
    ('camera_ids',['camera-b']), ('first_seen',99), ('last_seen',201)])
async def test_changed_body_or_any_session_join_field_refuses(tmp_path,field,value):
    target, receipt = source(tmp_path)
    changed = dict(ROW, **{field:value})
    with SessionTextArchive(target, manifest_sha256=receipt['manifest_sha256'], authorized_cameras=AUTH,
            association=lambda _: association(changed)) as archive:
        with pytest.raises(SessionSourceError, match='association changed'):
            await archive('session-a')


async def test_multi_camera_session_is_excluded_whole_not_partially_redacted_into_consent(tmp_path):
    target = tmp_path/'export'
    foreign = dict(ROW, session_id='outside-session', camera_ids=['camera-a','camera-b'],
                   description='Fictional unapproved camera description.')
    missing = dict(ROW, session_id='missing-text', description=None)
    receipt = export_session_archive(target, [ROW,foreign,missing], authorized_cameras=AUTH)
    assert receipt['indexed'] == 3 and receipt['exported'] == 1
    assert receipt['outside_authorization'] == receipt['no_source'] == 1
    assert not receipt['complete_coverage']
    raw = (target/'manifest.json').read_text()
    assert foreign['description'] not in raw
    with pytest.raises(SessionSourceError, match='exceeds current camera authorization'):
        SessionTextArchive(target, manifest_sha256=receipt['manifest_sha256'],
                            authorized_cameras=frozenset({'camera-b'}), association=lambda _: association(ROW))


@pytest.mark.parametrize('kind', ['symlink','fifo','hardlink','mode','changed'])
async def test_unsafe_or_changed_text_manifest_is_not_consumed(tmp_path,kind):
    target, receipt = source(tmp_path)
    path = target/'manifest.json'
    with SessionTextArchive(target, manifest_sha256=receipt['manifest_sha256'], authorized_cameras=AUTH,
                           association=lambda _: association(ROW)) as archive:
        if kind == 'changed':
            path.write_bytes(path.read_bytes()+b' ')
        elif kind == 'mode':
            path.chmod(0o644)
        else:
            retained = target/'retained-manifest'
            path.rename(retained)
            if kind == 'symlink':
                path.symlink_to(retained)
            elif kind == 'fifo':
                os.mkfifo(path,0o600)
            else:
                os.link(retained,path)
        with pytest.raises((RuntimeError,OSError)):
            await archive('session-a')


@pytest.mark.parametrize('change', ['duplicate','bad-id','not-text','bad-camera','bad-time','extra'])
def test_invalid_projection_has_no_export_side_effects(tmp_path,change):
    rows = [deepcopy(ROW)]
    if change == 'duplicate':
        rows *= 2
    elif change == 'bad-id':
        rows[0]['session_id']='../outside'
    elif change == 'not-text':
        rows[0]['description']=123
    elif change == 'bad-camera':
        rows[0]['camera_ids']=['camera-a','camera-a']
    elif change == 'bad-time':
        rows[0]['first_seen']=300
    elif change == 'extra':
        rows[0]['path']='/outside'
    target = tmp_path/'refused'
    with pytest.raises(SessionSourceError):
        export_session_archive(target,rows,authorized_cameras=AUTH)
    assert not target.exists()


def test_hybrid_profile_changes_cannot_mix_e5_with_visual_space(tmp_path):
    visual = {'profile':'clip-basic-v1','model':'clip-ViT-L-14','dimensions':768,
              'backend':'local-clip-onnx','source':'http://127.0.0.1:8180','revision':'a'*64,**CLIP_ENCODING}
    text = {'profile':'e5-session-v1','model':'multilingual-e5-small','dimensions':384,
            'backend':'http','source':'http://127.0.0.1:8182/v1/embeddings','revision':'b'*64,
            'prefixes':{'query':'query: ','document':'passage: '},**TEXT_ENCODING}
    ensure_embedding_profile(tmp_path,visual)
    old_visual = (tmp_path/'search-profile.json').read_bytes()
    ensure_session_embedding_profile(tmp_path,text)
    session = tmp_path/'session-search'/'search-profile.json'
    original = session.read_bytes()
    for changes in ({'source':'http://127.0.0.1:9999/v1/embeddings'}, {'revision':'c'*64},
                    {'preprocessing':'fictional-different-v2'}, {'dimensions':768}):
        with pytest.raises(EmbeddingProfileError):
            ensure_session_embedding_profile(tmp_path,dict(text,**changes))
    assert session.read_bytes() == original
    assert (tmp_path/'search-profile.json').read_bytes() == old_visual
    assert json.loads(original) == _with_fingerprint(text)


async def test_empty_session_archive_has_no_fictitious_source_or_quality(tmp_path):
    target = tmp_path/'empty'
    receipt = export_session_archive(target,[],authorized_cameras=AUTH)
    assert receipt['complete_coverage'] and receipt['exported'] == receipt['indexed'] == 0
    with SessionTextArchive(target,manifest_sha256=receipt['manifest_sha256'],
                           authorized_cameras=AUTH,association=lambda _: None) as archive:
        assert await archive('missing') is None


async def test_duplicate_manifest_keys_cannot_override_authorization(tmp_path):
    target, _ = source(tmp_path)
    path = target/'manifest.json'
    path.write_text(path.read_text().rstrip()[:-1]+',"authorized_cameras":["camera-b"]}')
    with pytest.raises(SessionSourceError,match='manifest is invalid'):
        SessionTextArchive(target,manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                           authorized_cameras=AUTH,association=lambda _: association(ROW))
