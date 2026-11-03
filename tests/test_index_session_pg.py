"""Real isolated session SQL + versioned E5 HTTP + pinned native TLS lifecycle."""
import hashlib
import json

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey.admin_runtime import RuntimeObserver
from aikey import camera_inventory
from aikey.embedding_profile import ensure_embedding_profile, ensure_session_embedding_profile
from aikey.encoding_identity import CLIP_ENCODING, TEXT_ENCODING
from aikey.index_authority import IndexAuthority, IndexAuthorityError
from aikey.index_session_readback import SessionReadbackPlan
from aikey.index_session_store import SessionPgStore
from aikey.index_sessions import SessionTextArchive, export_session_archive
from aikey.index_text_encoder import TextEmbedder
from aikey.index_text_rebuild import SessionRebuild
from aikey.index_rebuild import RebuildError, migration_plan
from aikey.search_backup import _COUNTS_SQL
from test_index_native import authorized, index_session
from test_index_session_readback import specification
from test_index_rebuild_pg import DSN, connect
from test_native_camera_status import controller

pytestmark = pytest.mark.skipif(not DSN, reason='AIKEY_TEST_PG_DSN is not set')
OLD, NEW = 'a'*64, 'b'*64
SESSIONS = ['session-a','session-b','session-c']
TEXTS = [f'Fictional toy car on a paper road. Case {i}.' for i in range(3)]
AUTH = frozenset({'camera-a'})


def vector(i,width=384):
    return [float(n==i) for n in range(width)]


@pytest.fixture
def store():
    with connect() as conn:
        assert conn.info.dbname == 'aikey_rebuild_test' and conn.info.encoding == 'utf-8'
        existing = conn.execute("SELECT to_regnamespace('aikey_session_rebuild'),"
            "to_regclass('public.\"smartDetectSessionsSearch\"'),to_regclass('public.\"ramDetections\"')").fetchone()
        assert existing == (None,None,None), 'refuse nonempty or foreign fixture state before DDL'
        conn.execute('CREATE EXTENSION IF NOT EXISTS vector')
        conn.execute('CREATE TABLE public."ramDetections"(id varchar PRIMARY KEY,embedding vector(768))')
        conn.execute('INSERT INTO public."ramDetections" VALUES (%s,%s::vector)',
                     ('visual-canary',json.dumps(vector(0,768))))
        conn.execute('CREATE TABLE public."smartDetectSessionsSearch"('
            '"sessionId" varchar PRIMARY KEY,description text,"cameraIds" varchar[] NOT NULL,'
            '"firstSeen" bigint NOT NULL,"lastSeen" bigint NOT NULL,"descEmbedding" vector(384),'
            '"descBm25" text NOT NULL DEFAULT \'fictional-unchanged-bm25\')')
        for i,name in enumerate(SESSIONS):
            conn.execute('INSERT INTO public."smartDetectSessionsSearch" VALUES (%s,%s,%s,%s,%s,%s::vector)',
                (name,TEXTS[i],['camera-a'],100+i,200+i,json.dumps(vector(i))))
        conn.execute('INSERT INTO public."smartDetectSessionsSearch" VALUES (%s,%s,%s,%s,%s,NULL)',
                     ('never-indexed','Fictional unindexed row.',['camera-a'],1,2))
    yield SessionPgStore(connect)
    with connect() as conn:
        conn.execute('DROP SCHEMA IF EXISTS aikey_session_rebuild CASCADE')
        conn.execute('DROP TABLE public."smartDetectSessionsSearch"')
        conn.execute('DROP TABLE public."ramDetections"')


def snapshot():
    with connect() as conn:
        rows=conn.execute('SELECT "sessionId",description,"cameraIds","firstSeen","lastSeen",'
                          '"descEmbedding"::text,"descBm25" FROM public."smartDetectSessionsSearch" '
                          'ORDER BY "sessionId"').fetchall()
        visual=conn.execute('SELECT embedding::text FROM public."ramDetections"').fetchall()
    return rows,visual


def profiles(root,hybrid,unknown=False):
    if hybrid:
        ensure_embedding_profile(root,{'profile':'clip-basic-v1','model':'clip-ViT-L-14',
            'dimensions':768,'backend':'local-clip-onnx','source':'http://127.0.0.1:8180','revision':OLD,**CLIP_ENCODING})
    text={'profile':'e5-session-v1','model':'multilingual-e5-small','dimensions':384,'backend':'http',
          'source':'http://127.0.0.1:8182/v1/embeddings','revision':None if unknown else OLD,
          'prefixes':{'query':'query: ','document':'passage: '},**TEXT_ENCODING}
    ensure_session_embedding_profile(root,text,legacy_root=not hybrid)
    rootfile=root/'search-profile.json'
    rootfile.write_bytes(b' \n'+rootfile.read_bytes()+b'\n')


@pytest.mark.parametrize('hybrid,unknown,empty',[(True,False,False),(False,False,False),
                                                 (True,True,False),(False,True,False),(True,False,True)])
async def test_real_session_migration_retains_visual_bm25_null_rows_and_exact_profile_rollback(
        store,tmp_path,monkeypatch,hybrid,unknown,empty):
    profiles(tmp_path,hybrid,unknown)
    if empty:
        with connect() as conn:
            conn.execute('DELETE FROM public."smartDetectSessionsSearch" WHERE "descEmbedding" IS NOT NULL')
    indexed=0 if empty else 3
    root_before=(tmp_path/'search-profile.json').read_bytes()
    child_before=(tmp_path/'session-search'/'search-profile.json').read_bytes()
    before=snapshot()
    with connect() as conn:
        counts = conn.execute(_COUNTS_SQL).fetchone()[0]
        assert counts['session_embeddings'] == indexed and counts['embeddings'] == 1
    target=tmp_path/'export'
    receipt=export_session_archive(target,store.source_rows(),authorized_cameras=AUTH)
    assert receipt['exported']==indexed and receipt['complete_coverage']
    requests=[]
    async def health(request):
        return web.json_response({'embedding_revision':NEW,'embedding_encoding':TEXT_ENCODING,
                                  'models':{'embeddings':'multilingual-e5-small'}})
    async def embeddings(request):
        values=(await request.json())['input']
        requests.extend(values)
        vectors=[]
        for text in values:
            if text.startswith('passage: '):
                vectors.append(vector(100+TEXTS.index(text.removeprefix('passage: '))))
            else:
                vectors.append(vector(100 if text=='query: a red toy car' else 200))
        return web.json_response({'model':'multilingual-e5-small','revision':NEW,'encoding':TEXT_ENCODING,
            'data':[{'index':i,'embedding':v} for i,v in enumerate(vectors)]})
    model=web.Application()
    model.router.add_get('/healthz',health)
    model.router.add_post('/v1/embeddings',embeddings)
    native_calls=[]
    async def native_search(request):
        authorized(request)
        assert set(request.query)=={'query'}  # Do not invent a native threshold/filter parameter.
        positive=request.query['query']=='a red toy car'
        native_calls.append(positive)
        i=0 if positive else 1
        return web.json_response({'sessions':[] if empty else [{'id':SESSIONS[i],'description':TEXTS[i],
            'cameras':['camera-a'],'firstSeen':100+i,'lastSeen':200+i,'confidence':99 if positive else 0}]})
    async def key_health(request):
        return web.json_response({'service':'local-aikey','search':{'connected':True,
            'profile':'clip-basic-v1' if hybrid else 'e5-session-v1','session_encoder_revision':NEW,
            'session_encoder_profile_fingerprint':generation().target_profile['fingerprint']}})
    authority=tmp_path/'native-authority'
    authority.mkdir(mode=0o700)
    routes=[web.get('/proxy/protect/api/detection-sessions/search',native_search),web.get('/healthz',key_health)]
    async with TestServer(model) as server:
        endpoint=str(server.make_url('/v1/embeddings'))
        def generation():
            return SessionRebuild(store,tmp_path/'session-search',target_revision=NEW,target_source=endpoint)
        def source():
            return SessionTextArchive(target,manifest_sha256=receipt['manifest_sha256'],
                                      authorized_cameras=AUTH,association=store.source_association)
        encoder=TextEmbedder(endpoint,NEW)
        try:
            with source() as archive:
                assert (await generation().stage(encoder,archive,batch=1,max_batches=1))['embedded']==min(1,indexed)
            assert snapshot()==before
            with source() as archive:
                run=generation()
                assert (await run.stage(encoder,archive,batch=1))['embedded']==indexed
                run.verify()
                spec=specification()
                spec['source_archive_sha256']=receipt['manifest_sha256']
                spec['queries'][0]['description_sha256']={'session-a':hashlib.sha256(TEXTS[0].encode()).hexdigest()}
                if unknown:
                    spec.update(from_revision=None,unversioned_source=True,
                        from_profile_fingerprint=json.loads(child_before)['fingerprint'],
                        target_profile_fingerprint=run.target_profile['fingerprint'])
                if empty:
                    spec['empty_index']=True
                    for query in spec['queries']:
                        query.update(expected_sessions=[],description_sha256={})
                plan=SessionReadbackPlan(spec)
                assert (await run.qualify(encoder,plan))['passed']
                from aikey.index_readback import ReadbackError
                changed_plan=plan.value
                changed_plan['queries'][0]['text']='A changed fictional quality target'
                before_calls=list(requests)
                with pytest.raises(ReadbackError,match='targets are frozen'):
                    await run.qualify(encoder,SessionReadbackPlan(changed_plan))
                assert requests==before_calls
                assert migration_plan(tmp_path/'session-search')['generations'][0]['qualification']['quality_recorded']
                # A genuine acquired runtime/migration lock, never a manually true stop flag.
                with IndexAuthority(tmp_path,runtime=True):
                    with pytest.raises(IndexAuthorityError,match="holds index authority"):
                        IndexAuthority(tmp_path)
                backup={'verified':True,'counts':{'session_embeddings':indexed,'tables':{'smartDetectSessionsSearch':indexed+1}}}
                if not hybrid and not unknown:
                    def write_then_fail(path, value):
                        from aikey.index_rebuild import _atomic_json
                        _atomic_json(path, value)
                        raise OSError('Fictional post-profile failure')
                    with IndexAuthority(tmp_path) as stopped:
                        with pytest.raises(RebuildError, match='previous vectors and profile were restored'):
                            generation().cutover(backup=backup,key_quiesced=stopped.check,
                                source=archive,readback_plan=plan,write_profile=write_then_fail)
                    assert snapshot() == before
                    assert (tmp_path/'search-profile.json').read_bytes() == root_before
                    assert (tmp_path/'session-search'/'search-profile.json').read_bytes() == child_before
                with IndexAuthority(tmp_path) as stopped:
                    result=generation().cutover(backup=backup,key_quiesced=stopped.check,
                                                source=archive,readback_plan=plan)
                assert result['state']=='awaiting_native_readback'
            current=snapshot()
            assert current[1]==before[1]
            assert all(now[:5]==old[:5] and now[-1]==old[-1] for now,old in zip(current[0],before[0]))
            assert next(r for r in current[0] if r[0]=='never-indexed')[5] is None
            async with controller(authority,monkeypatch,additional_routes=routes) as (reader,seen,_):
                (tmp_path/'device.crt').write_bytes((authority/'device.crt').read_bytes())
                (tmp_path/'device.crt').chmod(0o600)
                runtime_url=f'https://127.0.0.1:{camera_inventory._INTEGRATION_PORT}/healthz'
                observer=RuntimeObserver({'key':('aikey',tmp_path)},{'key':runtime_url})
                native=index_session(reader)
                async with native:
                    checked=await generation().confirm_native(plan,native,
                        runtime=lambda: observer.read_session_identity('key'))
                    assert checked['passed'] and checked['queries'][1]['returned_raw']==(0 if empty else 1)
                    assert checked['queries'][1]['above_frozen_score']==0
                    if not empty and hybrid and not unknown:
                        from aikey.index_readback import ReadbackError
                        with pytest.raises(ReadbackError,match='running E5 query encoder'):
                            await generation().confirm_native(plan,native,runtime=lambda: {})
                        with pytest.raises(RebuildError,match='Successful native readback'):
                            generation().finalize_native(logout_acknowledged=True)
                        checked=await generation().confirm_native(plan,native,
                            runtime=lambda: observer.read_session_identity('key'))
                assert native.logout_acknowledged
                assert generation().finalize_native(logout_acknowledged=native.logout_acknowledged)['native_verified']
                expected_calls = [True,False] if empty else [True,True,False]
                if not empty and hybrid and not unknown:
                    expected_calls *= 2
                assert native_calls==expected_calls
                assert seen.count(('POST','/api/auth/login'))==1
            if hybrid:
                assert (tmp_path/'search-profile.json').read_bytes()==root_before
            else:
                assert json.loads((tmp_path/'search-profile.json').read_text())['revision']==NEW
            if not empty:
                with connect() as conn:
                    conn.execute('UPDATE public."smartDetectSessionsSearch" SET description=%s WHERE "sessionId"=%s',
                                 ('Changed fictional description after cutover.',SESSIONS[1]))
                changed = snapshot()
                with IndexAuthority(tmp_path) as stopped:
                    with pytest.raises(RebuildError, match='unversioned' if unknown else 'supply an embedder'):
                        await generation().rollback(key_quiesced=stopped.check)
                assert snapshot() == changed
                # No encoder is guessed for an unversioned old model; reconcile source before exact rollback.
                with connect() as conn:
                    conn.execute('UPDATE public."smartDetectSessionsSearch" SET description=%s WHERE "sessionId"=%s',
                                 (TEXTS[1],SESSIONS[1]))
            with IndexAuthority(tmp_path) as stopped:
                assert (await generation().rollback(key_quiesced=stopped.check))['rows']==indexed
            assert snapshot()==before
            assert (tmp_path/'search-profile.json').read_bytes()==root_before
            assert (tmp_path/'session-search'/'search-profile.json').read_bytes()==child_before
            assert len([x for x in requests if x.startswith('passage: ')])==indexed
        finally:
            await encoder.close()


async def test_changed_session_source_cannot_be_verified_or_reused_as_another_generation(store,tmp_path):
    profiles(tmp_path,True)
    receipt=export_session_archive(tmp_path/'export',store.source_rows(),authorized_cameras=AUTH)
    source=SessionTextArchive(tmp_path/'export',manifest_sha256=receipt['manifest_sha256'],
                             authorized_cameras=AUTH,association=store.source_association)
    class Encoder:
        async def revision(self):
            return NEW
        async def embed(self,raw):
            return vector(100+TEXTS.index(raw.decode()))
    run=SessionRebuild(store,tmp_path/'session-search',target_revision=NEW,
                       target_source='http://127.0.0.1:8183/v1/embeddings',generation='123456abcdef')
    try:
        await run.stage(Encoder(),source)
        before=snapshot()
        with connect() as conn:
            conn.execute('UPDATE public."smartDetectSessionsSearch" SET description=%s WHERE "sessionId"=%s',
                         ('Changed fictional description.','session-b'))
        with pytest.raises(RebuildError,match='frozen index source changed'):
            run.verify()
        assert snapshot()[1]==before[1]
        assert run.cancel()['state']=='cancelled'
        fresh=export_session_archive(tmp_path/'fresh-export',store.source_rows(),authorized_cameras=AUTH)
        assert fresh['manifest_sha256']!=receipt['manifest_sha256']
        newer=SessionRebuild(store,tmp_path/'session-search',target_revision=NEW,
                            target_source='http://127.0.0.1:8183/v1/embeddings',generation='abcdef123456')
        assert newer.state=='staging' and run.journal_path.exists()
    finally:
        source.close()


async def test_atomic_session_swap_refuses_late_source_or_stage_change(store,tmp_path):
    profiles(tmp_path, True)
    receipt=export_session_archive(tmp_path/'export',store.source_rows(),authorized_cameras=AUTH)
    class Encoder:
        async def revision(self):
            return NEW
        async def embed(self, raw):
            return vector(100+TEXTS.index(raw.decode()))
    run=SessionRebuild(store,tmp_path/'session-search',target_revision=NEW,
                       target_source='http://127.0.0.1:8183/v1/embeddings')
    with SessionTextArchive(tmp_path/'export',manifest_sha256=receipt['manifest_sha256'],
            authorized_cameras=AUTH,association=store.source_association) as source:
        await run.stage(Encoder(),source)
    identity, staged = store.source_identity(), store.staged_identity(run.tag)
    before = snapshot()
    with pytest.raises(RebuildError,match='changed before the atomic swap'):
        store.cutover(run.tag,NEW,expected_source_identity='0'*64,expected_staged_identity=staged)
    assert snapshot() == before and not store.has_snapshot(run.tag)
    with pytest.raises(RebuildError,match='changed before the atomic swap'):
        store.cutover(run.tag,NEW,expected_source_identity=identity,expected_staged_identity='0'*64)
    assert snapshot() == before and not store.has_snapshot(run.tag)
