"""Frozen local speech/CLIP trial, real CPU models and real loopback routes.

Never loads gold or targets into the inference process. The external owner must
provide isolated resources, exact checkpoint/runtime binding and a hard deadline.
Model loading, one neutral warmup and visual-index construction are separately
timed, never disguised as per-query latency or native notification performance.
"""
import argparse
import asyncio
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import time
import wave

import aiohttp
from aiohttp import web
from aikey.clip import ClipClient
from aikey.clip_server import build_app as clip_app, onnx_encoders, weights_revision
from aikey.speech import SpeechProvider
from aikey.whisper_server import build_app as speech_app, openvino_transcriber
from benchmark_trial_io import save, save_json, strict


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def checked(root,row):
    name=Path(row['file_name'])
    assert not name.is_absolute() and '..' not in name.parts
    raw=(root/name).read_bytes()
    assert digest(raw)==row['input_sha256']
    return raw


def rank(query, vectors, k, cutoff):
    """Fixed cosine cutoff, stable ID tie-breaking, no gold-dependent tuning."""
    if not 0<=cutoff<=1 or type(k) is not int or not 1<=k<=100:
        raise ValueError('Unbounded ranking policy')
    scores=[(identity,math.fsum(a*b for a,b in zip(query,vector,strict=True)))
            for identity,vector in vectors.items()]
    if any(not math.isfinite(v) for _,v in scores):
        raise ValueError('Nonfinite ranking')
    return sorted(scores,key=lambda x:(-x[1],x[0]))[:k], [identity for identity,value in
        sorted(scores,key=lambda x:(-x[1],x[0])) if value>=cutoff][:k]


def silence():
    output=BytesIO()
    with wave.open(output,'wb') as wav:
        wav.setparams((1,2,16000,8000,'NONE','not compressed'))
        wav.writeframes(b'\0'*16000)
    return output.getvalue()


async def speech_call(session,base,raw,duration_ms,output,identity):
    provider=SpeechProvider({'provider':'openai-compatible','model':'large-v3-turbo-int8',
                             'base_url':base+'/v1','language':'de'})
    form=aiohttp.FormData()
    for key,value in provider.form_fields():
        form.add_field(key,value)
    form.add_field('file',raw,filename='input.wav',content_type='audio/wav')
    async with session.post(base+'/v1/audio/transcriptions',data=form,
                            allow_redirects=False) as response:
        body=await response.content.read(2*1024*1024+1)
        save(output/(identity+'-http.bin'),body)
        if response.status!=200 or len(body)>2*1024*1024:
            raise ValueError('Speech HTTP/schema failure')
        reply=strict(body)
        segments=provider.parse(reply,duration_ms)
        # This endpoint does not echo a model/revision. Identity comes from the
        # sealed model artifact and admitted CPU runtime, not invented metadata.
        return {'predicted':' '.join(text for _,_,text in segments),
                'response_sha256':digest(body),'response_identity':'controller-bound-not-server-echo'}


async def evaluate(root,output,models,kind):
    manifest=strict((root/'execution-manifest.json').read_bytes())
    assert manifest['schema']=='owned-local-modal-execution/1'
    assert manifest['kind']==kind and kind in {'speech','retrieval'}
    assert manifest['driver_sha256']==digest(Path(__file__).read_bytes())
    assert manifest['request_timeout_s']==120 and manifest['retries']==0
    assert manifest['warmups']==1
    rows=strict((root/'inputs.json').read_bytes())['cases']
    assert len(rows)==manifest['case_count'] and all(row['kind']==kind for row in rows)
    assert all('expected' not in row and 'relevant' not in row for row in rows)
    started=time.monotonic()
    if kind=='speech':
        app=speech_app(openvino_transcriber(str(models),'CPU','/cache'),default_language='de')
        revision=None
    else:
        revision=weights_revision(str(models))
        assert revision==manifest['encoder_revision']
        app=clip_app(*onnx_encoders(str(models),threads=4),revision=revision)
    save_json(output/'loaded.json',{'seconds':time.monotonic()-started,'device':'CPU'})
    runner=web.AppRunner(app,access_log=None)
    await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0)
    await site.start()
    port=site._server.sockets[0].getsockname()[1]
    base='http://127.0.0.1:'+str(port)
    client=None
    try:
        async with aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=120)) as session:
            if kind=='retrieval':
                client=ClipClient({'clip_server':base},timeout_s=120,
                                  expected_revision=revision,require_encoding=True)
                warm=await client.embed_text('neutral warmup')
                save_json(output/'warmup.json',{'excluded':True,'response_sha256':digest(json.dumps(warm).encode())})
                items=strict((root/'visual-index.json').read_bytes())['items']
                vectors={}
                encoded={}
                index_started=time.monotonic()
                for item in items:
                    raw=checked(root,item)
                    from PIL import Image
                    with Image.open(BytesIO(raw)) as image:
                        jpeg=BytesIO()
                        image.convert('RGB').save(jpeg,format='JPEG',quality=92)
                    encoded[item['id']]=digest(jpeg.getvalue())
                    vectors[item['id']]=(await client.embed_regions(jpeg.getvalue(),[[0,0,1,1]]))[0]
                save_json(output/'visual-vectors.json',vectors)
                save_json(output/'encoded-image-sha256.json',encoded)
                save_json(output/'index-built.json',{'items':len(vectors),'seconds':time.monotonic()-index_started})
            else:
                await speech_call(session,base,silence(),500,output,'warmup')
                save_json(output/'warmup.json',{'excluded':True,'model_calls':1})
            for row in rows:
                identity=row['id']
                save_json(output/(identity+'-started.json'),{'id':identity,'client_attempts':1})
                wall,cpu=time.monotonic_ns(),time.process_time_ns()
                result={'id':identity,'input_sha256':row['input_sha256'],'client_attempts':1,
                        'provider_charge_usd':None,'response_sha256':None}
                try:
                    if kind=='speech':
                        result.update(await speech_call(session,base,checked(root,row),
                            round(row['duration_s']*1000),output,identity))
                    else:
                        query=await client.embed_text(row['query'])
                        scores,ranked=rank(query,vectors,row['k'],manifest['cosine_cutoff'])
                        artifact={'query_embedding':query,'top_scores_before_cutoff':scores,'ranked':ranked}
                        body=(json.dumps(artifact,sort_keys=True,allow_nan=False)+'\n').encode()
                        save(output/(identity+'-vectors.json'),body)
                        result.update(ranked=ranked,response_sha256=digest(body),
                                      response_identity='derived-normalized-encoder-and-ranking-artifact')
                    result['status']='valid'
                except (aiohttp.ClientError,TimeoutError,OSError,ValueError,RuntimeError) as exc:
                    result.update(status='error',error_code='modal_request_or_parse_failed',
                                  failure_type=type(exc).__name__)
                result.update(latency_ms=(time.monotonic_ns()-wall)/1e6,
                              cpu_ms=(time.process_time_ns()-cpu)/1e6)
                save_json(output/(identity+'-result.json'),result)
                print(json.dumps({k:result[k] for k in ('id','status','latency_ms')}),flush=True)
                if result['status']=='error':
                    # An uncertain timed-out worker is never followed by another
                    # input; the owner settles/drains it under the run deadline.
                    break
            async with session.get(base+'/healthz',allow_redirects=False) as response:
                save(output/'health-terminal.json',await response.read())
    finally:
        if client is not None:
            await client.close()
        await runner.cleanup()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('models',type=Path)
    parser.add_argument('kind',choices=('speech','retrieval'))
    parser.add_argument('--admission',type=Path)
    args=parser.parse_args()
    if args.admission is not None:
        deadline=time.monotonic()+30
        while not args.admission.is_file() and time.monotonic()<deadline:
            time.sleep(.1)
        admitted=strict(args.admission.read_bytes())
        assert admitted=={'manifest_sha256':digest((args.root/'execution-manifest.json').read_bytes())}
    asyncio.run(evaluate(args.root,args.output,args.models,args.kind))
