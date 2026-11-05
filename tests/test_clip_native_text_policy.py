"""Query policy is versioned across HTTP, staging and runtime; synthetic vectors only."""
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aikey.clip import ClipClient, ClipError, validate_find_anything_config
from aikey.clip_server import build_app
from aikey.encoding_identity import clip_encoding
from aikey.index_rebuild import Rebuild, RebuildError
from aikey.search import SearchService, EmbeddingError
from test_index_rebuild import setup, NEW
from test_clip_configuration_guard import saved_clip
from aikey.config_store import UnsafeConfigurationChange


@pytest.mark.parametrize('policy,expected', [
    ('literal', ['a train', 'dog', 'a blue car', 'a person walking in a hallway']),
    ('native-short-photo', ['a photo of a train', 'a photo of dog', 'a blue car',
                            'a person walking in a hallway']),
])
async def test_policy_is_applied_once_and_declared_on_all_routes(policy, expected):
    calls = []
    def text(values):
        calls.extend(values)
        return [[1.] + [0.] * 767 for _ in values]
    def image(*args):
        return [[1.] + [0.] * 767]
    async with TestClient(TestServer(build_app(text, image, revision='f'*64, text_policy=policy))) as c:
        response = await c.post('/v1/text', json={'texts': ['a train', 'dog', 'a blue car',
                                                         'a person walking in a hallway']})
        assert response.status == 200
        assert calls == expected
        assert (await response.json())['encoding'] == clip_encoding(policy)
        assert (await (await c.get('/healthz')).json())['encoding'] == clip_encoding(policy)
        client = ClipClient({'clip_server': str(c.make_url('')).rstrip('/'), 'text_policy': policy},
                            expected_revision='f'*64, require_encoding=True)
        try:
            await client.embed_text('a train')
            assert calls[-1] == expected[0]
            client.expected_encoding = clip_encoding('literal' if policy != 'literal' else 'native-short-photo')
            with pytest.raises(ClipError, match='contract differs'):
                await client.embed_text('a train')
        finally:
            await client.close()


def test_target_contract_changes_fingerprint_without_relabeling_old_profile(tmp_path):
    store = setup(tmp_path)
    before = (tmp_path/'search-profile.json').read_bytes()
    literal = Rebuild(store, tmp_path, target_revision=NEW, target_source='http://127.0.0.1:8181')
    native = Rebuild(store, tmp_path, target_revision=NEW, target_source='http://127.0.0.1:8181',
                     target_encoding=clip_encoding('native-short-photo'))
    assert literal.target_profile['fingerprint'] != native.target_profile['fingerprint']
    assert native.target_profile['tokenizer'] == clip_encoding('native-short-photo')['tokenizer']
    assert (tmp_path/'search-profile.json').read_bytes() == before
    with pytest.raises(RebuildError, match='Unsupported'):
        Rebuild(store, tmp_path, target_revision=NEW, target_source='http://127.0.0.1:8181',
                target_encoding=dict(clip_encoding(), tokenizer='unknown'))


def test_legacy_profile_cannot_silently_adopt_new_text_policy(tmp_path):
    store, path, profile = saved_clip(tmp_path)
    before = path.read_bytes(), profile.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        store.preview(store.snapshot().revision, {'find_anything': {'text_policy': 'native-short-photo'}})
    config = json.loads(path.read_text())
    config['find_anything']['text_policy'] = 'native-short-photo'
    with pytest.raises(EmbeddingError, match='migration'):
        SearchService(config, profile.parent)
    assert (path.read_bytes(), profile.read_bytes()) == before


@pytest.mark.parametrize('policy', ['unknown', None, True, 1, []])
def test_unknown_policy_rejected(policy):
    with pytest.raises(ClipError):
        validate_find_anything_config({'clip_server': 'http://127.0.0.1:8180', 'text_policy': policy})


def test_migrated_runtime_and_admin_use_pinned_policy(tmp_path):
    from aikey.embedding_profile import _with_fingerprint
    store, path, profile = saved_clip(tmp_path)
    identity = json.loads(profile.read_text())
    identity.pop('fingerprint')
    identity.update(clip_encoding('native-short-photo'), encoding_required=True)
    profile.write_text(json.dumps(_with_fingerprint(identity)))
    config = json.loads(path.read_text())
    config['find_anything']['text_policy'] = 'native-short-photo'
    path.write_text(json.dumps(config))
    service = SearchService(config, profile.parent)
    service._check_profile()
    assert service._identity()['tokenizer'] == identity['tokenizer']
    store.preview(store.snapshot().revision, {})
    with pytest.raises(UnsafeConfigurationChange):
        store.preview(store.snapshot().revision, {'find_anything': {'text_policy': 'literal'}})


async def test_stage_and_resume_preserve_native_contract_under_writer_lock(tmp_path):
    from aikey import index_rebuild
    from test_index_rebuild import Embedder, OBJECTS, vector
    from test_index_sources import archive, open_archive
    root, manifest_path, _, raw, manifest = archive(tmp_path)
    manifest['objects'] = [dict(manifest['objects'][0], object_id=o, crop_id='crop-'+o) for o in OBJECTS]
    manifest_path.write_text(json.dumps(manifest))
    store = setup(tmp_path)
    class Encoder(Embedder):
        async def embed(self, image):
            assert image == raw
            self.calls.append(image)
            return vector(100)
    run = Rebuild(store, tmp_path, target_revision=NEW, target_source='http://127.0.0.1:8181',
                  target_encoding=clip_encoding('native-short-photo'))
    embedder = Encoder(NEW)
    with open_archive(root) as source:
        with pytest.raises(index_rebuild.MixedRevisionError, match='text policy'):
            await run.stage(embedder, source, batch=2, max_batches=1)
        embedder.encoding_identity = clip_encoding('native-short-photo')
        await run.stage(embedder, source, batch=2, max_batches=1)
        staged = json.loads((run.directory/'search-profile.json').read_text())
        assert staged['tokenizer'] == clip_encoding('native-short-photo')['tokenizer']
        with pytest.raises(RebuildError, match='identity changed'):
            Rebuild(store, tmp_path, target_revision=NEW, target_source='http://127.0.0.1:8181')
        again = Rebuild(store, tmp_path, target_revision=NEW, target_source='http://127.0.0.1:8181',
                        target_encoding=clip_encoding('native-short-photo'))
        await again.stage(embedder, source, batch=2)
        assert len(embedder.calls) == 5
        assert again.target_profile == staged


async def test_same_weight_rollback_checks_previous_not_target_policy(tmp_path):
    from test_index_rebuild import Embedder
    store = setup(tmp_path)
    run = Rebuild(store, tmp_path, target_revision=NEW, target_source='http://127.0.0.1:8181',
                  target_encoding=clip_encoding('native-short-photo'))
    retained = Embedder(NEW)
    retained.encoding_identity = clip_encoding('literal')
    await run._check_embedder(retained, NEW, target=False)
    with pytest.raises(RebuildError, match='text policy'):
        await run._check_embedder(retained, NEW)
