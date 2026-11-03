"""One frozen, unlabelled OCR input through the real crop adapter; never retries.

The external controller binds checkpoint/runtime, CPU limits and process cleanup.
This helper does not claim to attest them or to measure native notifications.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from aikey import aiport_api_detection as detection
from benchmark_person_adapter import save, save_json, strict


def plate(raw, output, identity, *, transport, config):
    """A failed transport is not a valid empty/no-plate prediction."""
    observed = {}

    def capture(url, headers, payload):
        if url != 'http://127.0.0.1:8000/v3/chat/completions':
            raise ValueError('Unfrozen endpoint')
        if any(k.lower() in {'authorization', 'cookie'} for k in headers):
            raise ValueError('Unexpected credentials')
        observed['request_sha256'] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        reply = transport(url, headers, payload)
        # Preserve the whole decoded response privately, not just usable text.
        encoded = (json.dumps(reply, sort_keys=True, allow_nan=False)+'\n').encode()
        save(output/(identity+'-reply.json'), encoded)
        observed['response_sha256'] = hashlib.sha256(encoded).hexdigest()
        if not isinstance(reply, dict) or reply.get('model') != config['model']:
            raise ValueError('Model identity mismatch')
        return reply

    detector = detection.ApiObjectDetector(
        dict(config), output, threshold=.5, transport=capture,
        plate_cameras=frozenset({'owned-synthetic'}), ocr_provider_config=dict(config))
    counts = dict.fromkeys(('requests','failed','read','no_plate'),0)
    started = time.monotonic()
    predicted = detector._plate_from_crop('owned-synthetic',raw,(0,0,1,1),counts)
    result = dict(observed, latency_ms=round((time.monotonic()-started)*1000,3),
                  adapter_counts=counts)
    if counts['failed']:
        result.update(status='error',error_code='ocr_request_or_parse_failed')
    else:
        result.update(status='valid',predicted=predicted or '')
    return result


def run(root, output, index):
    root,output=Path(root),Path(output)
    manifest=strict((root/'execution-manifest.json').read_bytes())
    assert manifest['schema']=='owned-modal-execution/1'
    assert manifest['driver_sha256']==hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    assert manifest['retries']==0 and manifest['request_timeout_s']==120
    rows=strict((root/'inputs.json').read_bytes())['cases']
    assert len(rows)==manifest['case_count'] and type(index) is int and 0<=index<len(rows)
    row=rows[index]
    assert row['kind']=='plate' and set(row)<= {'id','kind','file_name','input_sha256','camera_class','conditions'}
    path=Path(row['file_name'])
    assert not path.is_absolute() and '..' not in path.parts
    raw=(root/path).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==row['input_sha256']
    save_json(output/(row['id']+'-started.json'),{'id':row['id'],'client_attempts':1})
    original=detection._reply_object
    received=False

    def raw_reply(body, failure):
        nonlocal received
        save(output/(row['id']+'-http.bin'),body)
        received=True
        return original(body,failure)

    detection._reply_object=raw_reply
    try:
        result=plate(raw,output,row['id'],config=manifest['provider_config'],
            transport=lambda u,h,p:detection._post(u,h,p,timeout=120))
    finally:
        detection._reply_object=original
    result.update(id=row['id'],input_sha256=row['input_sha256'],client_attempts=1,
                  provider_charge_usd=None,http_body_received=received)
    result.setdefault('response_sha256',None)
    save_json(output/(row['id']+'-result.json'),result)
    return {key:result[key] for key in ('id','status','latency_ms')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('index',type=int)
    args=parser.parse_args()
    print(json.dumps(run(args.root,args.output,args.index)))
