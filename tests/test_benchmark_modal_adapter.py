"""Real crop/normalizer boundaries, transport supplied explicitly as a fixture."""
from io import BytesIO
import sys
from pathlib import Path

from PIL import Image
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'tools'))
from benchmark_modal_adapter import plate
from aikey.aiport_api_detection import ApiDetectionError

CONFIG={'provider':'openai-compatible','base_url':'http://127.0.0.1:8000/v3',
        'model':'owned-test','max_output_tokens':256,'temperature':0}


@pytest.mark.parametrize('reply,expected',[
    ('{"plate":"QA AB123"}','QA AB123'),('{"plate":null}',''),('{"plate":"QA A?123"}','QA A?123')])
def test_actual_crop_and_normalizer(tmp_path,reply,expected):
    raw=BytesIO()
    Image.new('RGB',(640,320)).save(raw,format='PNG')
    payloads=[]
    def transport(u,h,p):
        payloads.append(p)
        return {'model':'owned-test','choices':[{'finish_reason':'stop','message':{'content':reply}}]}
    value=plate(raw.getvalue(),tmp_path,'fixture',transport=transport,config=CONFIG)
    assert value['status']=='valid' and value['predicted']==expected
    assert value['adapter_counts']['failed']==0 and len(payloads)==1
    assert payloads[0]['max_tokens']==256 and payloads[0]['temperature']==0
    assert len(value['request_sha256'])==len(value['response_sha256'])==64


@pytest.mark.parametrize('mode',['transport','model','parse'])
def test_failure_is_never_a_correct_negative(tmp_path,mode):
    raw=BytesIO()
    Image.new('RGB',(640,320)).save(raw,format='PNG')
    calls=[]
    def transport(u,h,p):
        calls.append(p)
        if mode=='transport':
            raise ApiDetectionError('api_detection_request_failed')
        return {'model':'wrong' if mode=='model' else 'owned-test',
                'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
    value=plate(raw.getvalue(),tmp_path,'fixture',transport=transport,config=CONFIG)
    assert value['status']=='error' and 'predicted' not in value
    assert len(calls)==1 and value['adapter_counts']['failed']==1
