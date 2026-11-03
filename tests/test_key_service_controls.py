"""Named-camera consent and honest broad controller-job scope for local roles."""
from copy import deepcopy
import json
import time

import pytest

from test_device_controls import live as _base_live,confirmation  # noqa: F401
from test_device_workflow import revision
from test_ocr_provider_http import form

ROOT='/api/admin/v1'


@pytest.fixture
async def live(_base_live):  # noqa: F811
    site,client,credentials,headers,paths,_=_base_live
    report={'schema':'aikey-camera-preflight/1','protect_version':'7.3.74','fetched_at':int(time.time()),
        'cameras':[{'id':f'{index+1:024x}','mac':f'2A112233440{index+1}','name':f'Fictional room {index+1}',
            'model':'UVC G3 Instant','state':'CONNECTED','smart_detect_types':['person'],'audio_detect_types':[]} for index in range(2)]}
    async def inventory():
        return deepcopy(report)
    site.inventory_loader=inventory
    yield site,client,credentials,headers,paths,report


async def reviewed(live,role='face_recognition',**changes):
    _,client,_,headers,paths,_=live
    status=await (await client.get(ROOT+'/key-services')).json()
    settings=({'server':'http://127.0.0.1:8181','choices':[status['camera_choices'][0]['choice']],
        'max_faces':8,'process_native_faces':False} if role=='face_recognition' else {'server':'http://127.0.0.1:8182'}
        if role=='face_enhancement' else {'reid_server':'http://127.0.0.1:8183','rerank_server':''})
    settings.update(changes)
    response=await client.post(ROOT+'/device-workflows/key-service-preview',json={
        'role':role,'mode':'configure','revision':revision(paths['aikey']),'settings':settings},headers=headers)
    assert response.status==200,await response.text()
    return await response.json()


@pytest.mark.parametrize('role',['face_recognition','face_enhancement','deep_understanding'])
async def test_initial_setup_saved_only_current_exact_undo_and_no_port_change(live,role):
    _,client,_,headers,paths,_=live
    before={key:path.read_bytes() for key,path in paths.items()}
    review=await reviewed(live,role)
    if role=='face_recognition':
        assert review['details']['processing_scope']=='Fictional room 1'
        assert 'face_recognition_named_camera_scope' in review['review']['required_consents']
    else:
        assert 'no local per-camera allowlist' in review['details']['processing_scope']
        assert 'controller_requested_deep_or_enhancement_scope' in review['review']['required_consents']
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(review),headers=headers)
    assert response.status==200,await response.text()
    assert role in json.loads(paths['aikey'].read_bytes()) and paths['aiport'].read_bytes()==before['aiport']
    undo=await client.post(ROOT+'/device-workflows/rollback-preview',json={'profile':'aikey','revision':revision(paths['aikey'])},headers=headers)
    assert undo.status==200,await undo.text()
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(await undo.json()),headers=headers)
    assert response.status==200,await response.text()
    assert all(path.read_bytes()==before[key] for key,path in paths.items())


@pytest.mark.parametrize('fault',['renamed','offline','new_id','inventory_failed','missing_consent'])
async def test_recognition_confirmation_cannot_silently_change_camera_scope(live,fault):
    site,client,_,headers,paths,report=live
    review=await reviewed(live)
    before=paths['aikey'].read_bytes()
    payload=confirmation(review)
    if fault=='renamed':
        report['cameras'][0]['name']='Different fictional room'
    elif fault=='offline':
        report['cameras'][0]['state']='DISCONNECTED'
    elif fault=='new_id':
        report['cameras'][0]['id']='e'*24
    elif fault=='inventory_failed':
        async def down():
            raise OSError('fictional inventory failure')
        site.inventory_loader=down
    else:
        payload['acknowledgements'].remove('face_recognition_named_camera_scope')
    response=await client.post(ROOT+'/device-workflows/apply',json=payload,headers=headers)
    assert response.status==409 and paths['aikey'].read_bytes()==before
    assert (await client.post(ROOT+'/device-workflows/apply',json=payload,headers=headers)).status==409


async def test_native_forms_use_opaque_camera_choices_and_disable_survives_inventory_outage(live):
    site,client,_,headers,paths,report=live
    response=await client.get('/local-feature-services')
    html=await response.text()
    assert response.status==200 and all(row['id'] not in html for row in report['cameras'])
    chunk=html.split('<h2>Local named recognition</h2>')[1].split('</section>')[0]
    fields=form(chunk,'/local-feature-services/preview')
    import re
    choice=re.search("name='(face_camera_[0-9a-f]{64})'",chunk)[1]
    fields.update(server='http://127.0.0.1:8181',max_faces='8',**{choice:'yes'})
    response=await client.post('/local-feature-services/preview',data=fields,headers=headers)
    assert response.status==200,await response.text()
    html=await response.text()
    assert 'Fictional room 1' in html
    fields=form(html,'/devices/apply')
    fields.update(local_service_destination_and_role_contract='yes',face_recognition_named_camera_scope='yes')
    assert (await client.post('/devices/apply',data=fields,headers=headers,allow_redirects=False)).status==303
    async def down():
        raise OSError('fictional outage')
    site.inventory_loader=down
    response=await client.post(ROOT+'/device-workflows/key-service-preview',json={
        'revision':revision(paths['aikey']),'role':'face_recognition','mode':'disable'},headers=headers)
    assert response.status==200
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(await response.json()),headers=headers)
    assert response.status==200 and 'face_recognition' not in json.loads(paths['aikey'].read_bytes())
