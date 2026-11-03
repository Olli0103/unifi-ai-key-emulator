"""Real private files and JPEG decoding at the migration source boundary."""
import hashlib
from io import BytesIO
import json
import os

from PIL import Image
import pytest

from aikey.index_sources import ObjectCropArchive, SCHEMA, SourceArchiveError


def archive(tmp_path):
    root = tmp_path / 'export'
    root.mkdir(mode=0o700)
    images = root / 'images'
    images.mkdir(mode=0o700)
    out = BytesIO()
    Image.new('RGB', (32, 24), 'red').save(out, format='JPEG')
    raw = out.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    image = images / (digest + '.jpg')
    image.write_bytes(raw)
    image.chmod(0o600)
    manifest = {'schema': SCHEMA, 'source': 'protect-original-object-crops',
                'authorized_cameras': ['camera-a'], 'objects': [
                    {'object_id': 'object-a', 'event_id': 'event-a', 'camera_id': 'camera-a',
                     'crop_id': 'crop-a', 'input_sha256': digest}]}
    path = root / 'manifest.json'
    path.write_text(json.dumps(manifest))
    path.chmod(0o600)
    return root, path, image, raw, manifest


def open_archive(root, **kwargs):
    return ObjectCropArchive(root,
        manifest_sha256=kwargs.pop('digest', hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()),
        authorized_cameras=kwargs.pop('cameras', frozenset({'camera-a'})),
        association=kwargs.pop('association', lambda _: ('event-a', 'camera-a')), **kwargs)


async def test_exact_crop_and_association_are_read_without_network(tmp_path):
    root, path, image, raw, _ = archive(tmp_path)
    before = {p: p.read_bytes() for p in (path, image)}
    with open_archive(root) as source:
        assert await source('object-a') == raw
        assert await source('not-exported') is None
        assert await source('object-a') == raw
    assert {p: p.read_bytes() for p in (path, image)} == before
    with pytest.raises(SourceArchiveError, match='closed'):
        await source('object-a')


@pytest.mark.parametrize('association', [None, ('wrong-event', 'camera-a'),
                                      ('event-a', 'wrong-camera'), ['event-a', 'camera-a']])
async def test_missing_or_changed_index_join_refuses_before_read(tmp_path, association):
    root, _, image, _, _ = archive(tmp_path)
    image.unlink()  # Association refusal precedes any image read.
    with open_archive(root, association=lambda _: association) as source:
        with pytest.raises(SourceArchiveError, match='association differs'):
            await source('object-a')


def test_archive_cannot_grant_camera_permission(tmp_path):
    root, *_ = archive(tmp_path)
    with pytest.raises(SourceArchiveError, match='exceeds current camera authorization'):
        open_archive(root, cameras=frozenset({'different-camera'}))


async def test_manifest_and_image_are_rechecked_each_read(tmp_path):
    root, path, image, raw, _ = archive(tmp_path)
    with open_archive(root) as source:
        assert await source('object-a') == raw
        image.write_bytes(raw + b'changed')
        with pytest.raises(SourceArchiveError, match='crop changed'):
            await source('object-a')
        image.write_bytes(raw)
        path.write_bytes(path.read_bytes() + b' ')
        with pytest.raises(SourceArchiveError, match='manifest changed'):
            await source('object-a')


@pytest.mark.parametrize('member', ['root', 'images', 'manifest'])
def test_nonprivate_archive_members_refuse(tmp_path, member):
    root, path, image, *_ = archive(tmp_path)
    target = {'root': root, 'images': root/'images', 'manifest': path, 'image': image}[member]
    target.chmod(0o755 if target.is_dir() else 0o644)
    with pytest.raises(SourceArchiveError, match='not owner-private'):
        open_archive(root)


@pytest.mark.parametrize('replacement', ['symlink', 'hardlink', 'fifo', 'mode', 'missing'])
async def test_unsafe_crop_members_refuse_without_following_or_blocking(tmp_path, replacement):
    root, _, image, raw, _ = archive(tmp_path)
    outside = tmp_path/'outside'
    outside.write_bytes(raw)
    outside.chmod(0o600)
    image.unlink()
    if replacement == 'symlink':
        image.symlink_to(outside)
    elif replacement == 'hardlink':
        os.link(outside, image)
    elif replacement == 'fifo':
        os.mkfifo(image, 0o600)
    elif replacement == 'mode':
        image.write_bytes(raw)
        image.chmod(0o644)
    with open_archive(root) as source:
        with pytest.raises(SourceArchiveError):
            await source('object-a')
    assert outside.read_bytes() == raw


@pytest.mark.parametrize('mutation', ['duplicate', 'traversal', 'outside-camera', 'crop-alias',
                                      'unexpected', 'bad-hash', 'not-a-list', 'wrong-schema'])
def test_malformed_or_ambiguous_manifests_refuse(tmp_path, mutation):
    root, path, _, _, manifest = archive(tmp_path)
    row = manifest['objects'][0]
    if mutation == 'duplicate':
        manifest['objects'].append(dict(row))
    elif mutation == 'traversal':
        row['object_id'] = '../outside'
    elif mutation == 'outside-camera':
        row['camera_id'] = 'outside-camera'
    elif mutation == 'crop-alias':
        manifest['objects'].append(dict(row, object_id='object-b'))
    elif mutation == 'unexpected':
        row['path'] = '/outside'
    elif mutation == 'bad-hash':
        row['input_sha256'] = 'no-digest'
    elif mutation == 'not-a-list':
        manifest['objects'] = {}
    elif mutation == 'wrong-schema':
        manifest['schema'] = 'unrecognized'
    path.write_text(json.dumps(manifest))
    with pytest.raises(SourceArchiveError, match='manifest is invalid'):
        open_archive(root)


@pytest.mark.parametrize('suffix', [',"objects":[]}', ',"extra":NaN}'])
def test_duplicate_keys_and_nonfinite_json_refuse(tmp_path, suffix):
    root, path, *_ = archive(tmp_path)
    path.write_text(path.read_text()[:-1] + suffix)
    with pytest.raises(SourceArchiveError, match='manifest is invalid'):
        open_archive(root)


def test_wrong_admitted_digest_does_not_open_a_changed_manifest(tmp_path):
    root, *_ = archive(tmp_path)
    with pytest.raises(SourceArchiveError, match='differs from the admitted'):
        open_archive(root, digest='0'*64)


async def test_content_addressed_nonjpeg_refuses_even_with_matching_digest(tmp_path):
    root, path, _, _, manifest = archive(tmp_path)
    raw = b'not a camera image'
    digest = hashlib.sha256(raw).hexdigest()
    image = root/'images'/(digest+'.jpg')
    image.write_bytes(raw)
    image.chmod(0o600)
    manifest['objects'][0]['input_sha256'] = digest
    path.write_text(json.dumps(manifest))
    with open_archive(root) as source:
        with pytest.raises(SourceArchiveError, match='unreadable'):
            await source('object-a')


async def test_directory_rename_cannot_redirect_an_open_archive(tmp_path):
    root, _, _, raw, _ = archive(tmp_path)
    with open_archive(root) as source:
        old = root.with_name('retained-export')
        root.rename(old)
        root.mkdir(mode=0o700)
        (root/'images').mkdir(mode=0o700)
        assert await source('object-a') == raw


async def test_real_archive_stages_without_changing_global_approval_or_live_index(tmp_path):
    from aikey import index_rebuild
    from test_index_rebuild import Embedder, NEW, setup
    root, _, _, raw, _ = archive(tmp_path)
    state = tmp_path/'state'
    state.mkdir(mode=0o700)
    store = setup(state, objects=['obj-0'])
    # Use the fixture's exact native-style association for the matching store row.
    path = root/'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['objects'][0]['object_id'] = 'obj-0'
    path.write_text(json.dumps(manifest))
    old_vectors = dict(store.live)
    old_profile = (state/'search-profile.json').read_bytes()
    class JPEGEncoder(Embedder):
        async def embed(self, image):
            assert image == raw
            self.calls.append(image)
            return [1.0] + [0.0]*767
    assert index_rebuild.APPROVED_IMAGE_SOURCE is None
    with open_archive(root) as source:
        run = index_rebuild.Rebuild(store, state, target_revision=NEW,
                                   target_source='http://127.0.0.1:8181')
        result = await run.stage(JPEGEncoder(NEW), source)
        assert result == {'embedded': 1, 'no_source': 0, 'state': 'staged'}
        assert run.verify()['uncovered'] == 0
        assert run.journal['source_archive_sha256'] == source.manifest_sha256
        assert store.live == old_vectors
        assert (state/'search-profile.json').read_bytes() == old_profile
        # Image source approval is not permission for a native cutover.
        with pytest.raises(index_rebuild.RebuildError, match='No approved'):
            run.cutover(backup={}, key_quiesced=lambda: True)
    assert index_rebuild.APPROVED_NATIVE_READBACK is None


async def test_resume_cannot_switch_source_archives(tmp_path):
    from aikey import index_rebuild
    from test_index_rebuild import Embedder, NEW, setup
    root, path, _, _, manifest = archive(tmp_path)
    state = tmp_path/'state'
    state.mkdir(mode=0o700)
    store = setup(state)
    with open_archive(root) as source:
        run = index_rebuild.Rebuild(store, state, target_revision=NEW,
                                   target_source='http://127.0.0.1:8181')
        # The first batch records explicit unavailable historical sources.
        await run.stage(Embedder(NEW), source, batch=1, max_batches=1)
    before = run.journal_path.read_bytes()
    manifest['objects'][0]['event_id'] = 'another-event'
    path.write_text(json.dumps(manifest))
    with open_archive(root, association=lambda _: ('another-event', 'camera-a')) as changed:
        with pytest.raises(index_rebuild.RebuildError, match='frozen source'):
            await run.stage(Embedder(NEW), changed, batch=1)
    assert run.journal_path.read_bytes() == before
