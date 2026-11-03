"""Real HTTP checkpoint/encoding assertions and correct document/query prefixes."""
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey.encoding_identity import TEXT_ENCODING
from aikey.index_text_encoder import TextEmbedder, text_endpoint
from aikey.search import EmbeddingError

NEW = 'b'*64


def vector(width=384):
    return [1.0]+[0.0]*(width-1)


async def test_text_target_checks_health_and_every_response_and_keeps_prefixes_separate():
    requests = []
    async def health(request):
        return web.json_response({'embedding_revision':NEW,'embedding_encoding':TEXT_ENCODING,
                                  'models':{'embeddings':'multilingual-e5-small'}})
    async def embed(request):
        value = await request.json()
        requests.append(value['input'])
        return web.json_response({'model':'multilingual-e5-small','revision':NEW,'encoding':TEXT_ENCODING,
            'data':[{'index':i,'embedding':vector()} for i in range(len(value['input']))]})
    app = web.Application()
    app.router.add_get('/healthz',health)
    app.router.add_post('/v1/embeddings',embed)
    async with TestServer(app) as server:
        encoder = TextEmbedder(str(server.make_url('/v1/embeddings')),NEW)
        try:
            assert await encoder.revision() == NEW
            assert len(await encoder.embed(b'Fictional document.')) == 384
            assert len(await encoder.embed_text('Fictional query?')) == 384
            assert requests == [['passage: Fictional document.'],['query: Fictional query?']]
        finally:
            await encoder.close()


@pytest.mark.parametrize('change',['revision','encoding','missing','width','reply-revision'])
async def test_unversioned_changed_or_wrong_width_text_targets_are_not_promoted(change):
    calls = []
    async def health(request):
        value = {'embedding_revision':NEW,'embedding_encoding':dict(TEXT_ENCODING),
                 'models':{'embeddings':'multilingual-e5-small'}}
        if change == 'revision':
            value['embedding_revision']='c'*64
        elif change == 'encoding':
            value['embedding_encoding']['tokenizer']='fictional-different-tokenizer'
        elif change == 'missing':
            del value['embedding_revision']
        return web.json_response(value)
    async def embed(request):
        calls.append(True)
        return web.json_response({'model':'multilingual-e5-small','revision':'c'*64 if change=='reply-revision' else NEW,
            'encoding':TEXT_ENCODING,'data':[{'index':0,'embedding':vector(768 if change=='width' else 384)}]})
    app = web.Application()
    app.router.add_get('/healthz',health)
    app.router.add_post('/v1/embeddings',embed)
    async with TestServer(app) as server:
        encoder = TextEmbedder(str(server.make_url('/v1/embeddings')),NEW)
        try:
            if change in ('width','reply-revision'):
                assert await encoder.revision() == NEW
                with pytest.raises(EmbeddingError):
                    await encoder.embed(b'Fictional document.')
                assert len(calls) == 1
            else:
                with pytest.raises(ValueError):
                    await encoder.revision()
                assert not calls
        finally:
            await encoder.close()


@pytest.mark.parametrize('url',['https://provider.invalid','http://8.8.8.8','http://127.0.0.1/path',
                                'http://user:secret@127.0.0.1','http://127.0.0.1?token=fictional'])
def test_text_target_cannot_inherit_external_or_credential_url_authority(url):
    with pytest.raises(ValueError):
        text_endpoint(url)
