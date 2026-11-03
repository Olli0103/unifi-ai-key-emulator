"""Two actual inference samples of a held synthetic scene to an owned HTTP sink.

This is a closed-loop synthetic pipeline window, NOT camera-day field exposure,
Protect acceptance, mobile delivery or ONVIF ingest. No source pixels are labelled
inside this process. Scoring observed event counts against gold happens later.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
import time
from urllib.request import ProxyHandler, Request, build_opener

from aikey import aiport_api_detection as detection
from aikey.aiport_camera_engine import CameraPolicyEngine
from aikey.aiport_smart_events import smart_event_payload
from aikey.aiport_smart_settings import parse_smart_settings
from aikey.providers import validate_inference_config
from benchmark_trial_io import save_json, strict

CAMERA='2A0000000001'  # invented, local engine only; never sent to Protect


def run(root,output,index,*,transport=None):
    root,output=Path(root),Path(output)
    manifest=strict((root/'execution-manifest.json').read_bytes())
    assert manifest['schema']=='owned-operation-execution/1'
    assert manifest['driver_sha256']==hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    assert manifest['samples_per_window']==2 and manifest['retries']==0
    assert manifest['request_timeout_s']==120 and manifest['maximum_window_seconds']==300
    rows=strict((root/'inputs.json').read_bytes())['cases']
    assert len(rows)==manifest['case_count'] and type(index) is int and 0<=index<len(rows)
    row=rows[index]
    assert set(row)=={'id','kind','file_name','frame_sha256','input_sha256','camera_class','conditions'}
    name=Path(row['file_name'])
    assert not name.is_absolute() and '..' not in name.parts
    raw=(root/name).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==row['frame_sha256']
    unit={'frame_sha256':row['frame_sha256'],'samples':2,'scene':'held-static-closed-loop'}
    assert hashlib.sha256(json.dumps(unit,sort_keys=True,separators=(',',':')).encode()).hexdigest()==row['input_sha256']
    provider=validate_inference_config(manifest['provider_config'],require_api_key=False)
    events=[]
    class Sink(BaseHTTPRequestHandler):
        def do_POST(self):
            body=self.rfile.read(int(self.headers['Content-Length']))
            value=strict(body)
            assert self.path=='/owned-alert' and value['deviceID']==CAMERA
            assert value['edgeType']=='enter' and value['objectTypes']==['person']
            events.append({'received_monotonic_ns':time.monotonic_ns(),'payload':value})
            self.send_response(200)
            self.send_header('Content-Length','2')
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self,*args):
            pass
    sink=ThreadingHTTPServer(('127.0.0.1',0),Sink)
    thread=Thread(target=sink.serve_forever,daemon=True)
    thread.start()
    engine=CameraPolicyEngine([CAMERA],max_events_per_camera=1,max_track_gap_seconds=30)
    engine.replace_policy(CAMERA,parse_smart_settings({'deviceID':CAMERA,
        'enableSmartDetect':['person'],'eventStartMSec':1000,'eventStopMSec':3000},camera_mac=CAMERA))
    save_json(output/(row['id']+'-started.json'),{'id':row['id'],'samples':2,'automatic_retries':0})
    started=time.monotonic_ns()
    observed=[]
    error=None
    try:
        for sample in range(2):
            url,headers,payload=detection.build_detection_request(provider,raw,structured_output=True)
            assert url=='http://127.0.0.1:8000/v3/chat/completions'
            assert not any(k.lower() in {'authorization','cookie'} for k in headers)
            reply=(transport or (lambda u,h,p:detection._post(u,h,p,timeout=120)))(url,headers,payload)
            save_json(output/(row['id']+f'-sample-{sample+1}.json'),reply)
            assert reply.get('model')==manifest['model']
            text=provider.parse_response(reply)
            strict(text)
            rejected={}
            values=detection.parse_detections(text,threshold=.5,rejected=rejected)
            if rejected:
                raise ValueError('Rejected detection sibling')
            observed.append({'sample':sample+1,'observations':len(values)})
            candidates=engine.observe(CAMERA,tuple(values),now=time.monotonic())
            for candidate in candidates:
                if candidate.change.edge!='enter':
                    continue
                value=smart_event_payload(CAMERA,candidate.change,edge='enter',
                    clock_wall_ms=time.time_ns()//1000000,zone_ids=candidate.zone_ids)
                encoded=json.dumps(value).encode()
                request=Request('http://127.0.0.1:'+str(sink.server_port)+'/owned-alert',
                    data=encoded,headers={'Content-Type':'application/json'},method='POST')
                with build_opener(ProxyHandler({})).open(request,timeout=5) as response:
                    assert response.status==200 and response.read()==b'{}'
    except (detection.ApiDetectionError,OSError,ValueError,RuntimeError,AssertionError) as exc:
        error=type(exc).__name__
    finally:
        ended=time.monotonic_ns()
        sink.shutdown()
        sink.server_close()
        thread.join(timeout=5)
    window=(ended-started)/1e9
    assert 0<window<=300
    evidence={'samples':observed,'events':events,'exposure_seconds':window,
              'exposure_scope':'held-static-synthetic-closed-loop-not-field',
              'start_monotonic_ns':started,'end_monotonic_ns':ended}
    save_json(output/(row['id']+'-pipeline.json'),evidence)
    result={'id':row['id'],'input_sha256':row['input_sha256'],'client_attempts':1,
        'model_sample_attempts':len(observed),'provider_charge_usd':None,
        'response_sha256':hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest(),
        'latency_ms':(events[0]['received_monotonic_ns']-started)/1e6 if events else None,
        'camera_days':window/86400,'alert_count':len(events),'window_ms':window*1000,
        'status':'error' if error else 'valid'}
    if error:
        result['error_code']='pipeline_request_or_parse_failed'
        result['failure_type']=error
    save_json(output/(row['id']+'-result.json'),result)
    return {k:result[k] for k in ('id','status','latency_ms')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('index',type=int)
    args=parser.parse_args()
    print(json.dumps(run(args.root,args.output,args.index)))
