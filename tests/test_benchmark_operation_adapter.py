"""Fixture responses exercise the real tracker, event encoder and owned HTTP sink."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'tools'))
import benchmark_operation_adapter as adapter


@pytest.mark.parametrize('kind',['positive','negative','invalid'])
def test_two_samples_reach_sink_or_remain_explicit_failure(tmp_path,kind):
    root=tmp_path/'inputs'
    output=tmp_path/'output'
    root.mkdir()
    output.mkdir()
    encoded=BytesIO()
    Image.new('RGB',(160,160)).save(encoded,format='JPEG')
    raw=encoded.getvalue()
    frame=hashlib.sha256(raw).hexdigest()
    (root/'frame.jpg').write_bytes(raw)
    unit={'frame_sha256':frame,'samples':2,'scene':'held-static-closed-loop'}
    binding=hashlib.sha256(json.dumps(unit,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    manifest={'schema':'owned-operation-execution/1','driver_sha256':hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest(),
        'samples_per_window':2,'retries':0,'request_timeout_s':120,'maximum_window_seconds':300,
        'case_count':1,'model':'fixture-model','provider_config':{'provider':'openai-compatible',
        'base_url':'http://127.0.0.1:8000/v3','model':'fixture-model','max_output_tokens':1024,'temperature':0}}
    (root/'execution-manifest.json').write_text(json.dumps(manifest))
    (root/'inputs.json').write_text(json.dumps({'cases':[{'id':'unit','kind':'operation','file_name':'frame.jpg',
        'frame_sha256':frame,'input_sha256':binding,'camera_class':'synthetic','conditions':['day']}]}))
    calls=[]
    def transport(u,h,p):
        calls.append(p)
        detections=[{'kind':'person','label':'person','score':.95,'box':[.1,.1,.7,.9]}] if kind=='positive' else []
        text='{}' if kind=='invalid' else json.dumps({'detections':detections})
        return {'model':'fixture-model','choices':[{'finish_reason':'stop','message':{'content':text}}]}
    adapter.run(root,output,0,transport=transport)
    result=json.loads((output/'unit-result.json').read_bytes())
    assert len(calls)==(1 if kind=='invalid' else 2)
    assert result['camera_days']>0
    if kind=='positive':
        assert result['alert_count']==1 and result['latency_ms']>0 and result['status']=='valid'
        assert json.loads((output/'unit-pipeline.json').read_bytes())['events'][0]['payload']['objectTypes']==['person']
    elif kind=='negative':
        assert result['alert_count']==0 and result['latency_ms'] is None and result['status']=='valid'
    else:
        assert result['status']=='error' and result['error_code']=='pipeline_request_or_parse_failed'
