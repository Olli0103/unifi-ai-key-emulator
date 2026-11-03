"""Actual multipart server/client/parser boundaries, not ASR quality evidence."""
import asyncio
import json
from pathlib import Path
import sys

import aiohttp
from aiohttp import web
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'tools'))
from benchmark_local_modal import rank, silence, speech_call
from aikey.whisper_server import build_app


def test_cutoff_negatives_and_ties_are_fixed_not_label_dependent():
    scores,ranked=rank([1.,0.],{'z':[.25,.5],'a':[.25,.5],'absent':[-1.,0.]},5,.25)
    assert ranked==['a','z'] and scores[0][0]=='a'
    assert rank([-1.,0.],{'only':[1.,0.]},5,.25)[1]==[]
    with pytest.raises(ValueError):
        rank([1.],{'bad':[1.,0.]},5,.25)


@pytest.mark.parametrize('text',['','Erfundener Prüfsatz.'])
def test_real_loopback_route_with_injected_transcriber(tmp_path,text):
    observed=[]
    def transcribe(samples,language):
        observed.append((len(samples),language))
        return ([(0,.5,text)] if text else []),'de'
    async def scenario():
        runner=web.AppRunner(build_app(transcribe),access_log=None)
        await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0)
        await site.start()
        port=site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession(trust_env=False) as session:
                return await speech_call(session,f'http://127.0.0.1:{port}',silence(),500,tmp_path,'fixture')
        finally:
            await runner.cleanup()
    value=asyncio.run(scenario())
    assert value['predicted']==text and observed==[(8000,'de')]
    assert value['response_identity']=='controller-bound-not-server-echo'
    stored=json.loads((tmp_path/'fixture-http.bin').read_bytes())
    assert 'model' not in stored and len(value['response_sha256'])==64
