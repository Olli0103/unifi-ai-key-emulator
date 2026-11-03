"""Opaque model/camera selection and one-use enrollment in existing Port roles."""
import json
import re

import pytest

from aikey.config import atomic_private
from aikey.port_feature_models import SCHEMA
from test_camera_pool_controls import pool,PROFILE,ROOT  # noqa: F401
from test_device_controls import live as device_live,confirmation  # noqa: F401
from test_device_workflow import revision
from test_port_feature_workflow import model_settings
from test_ocr_provider_http import form


@pytest.fixture
async def features(pool,tmp_path):  # noqa: F811
    site,*_=pool
    paths=[]
    for kind in ('face','sound'):
        path=tmp_path/(kind+'-binding.json')
        atomic_private(path,json.dumps({'schema':SCHEMA,'kind':kind,'name':'Fictional '+kind+' set',
            'settings':model_settings(kind),'publication_check':'owned_file_sha256_only'}))
        paths.append(path)
    site.port_model_catalogs={PROFILE:paths}
    yield pool


async def review(features,kind='speech',**changes):
    _,client,_,headers,paths,_,_=features
    status=await (await client.get(ROOT+'/port-features')).json()
    row=status['profiles'][0]
    feature=next(value for value in row['features'] if value['feature']==kind)
    fields={'profile':PROFILE,'revision':row['revision'],'feature':kind,'mode':'configure',
        'choices':[feature['choices'][0]['choice']]}
    if kind!='speech':
        fields['model_choice']=feature['models'][0]['choice']
    if kind in ('speech','sound'):
        fields['event_limit']=12
    if kind=='sound':
        fields['types']=['alrmBark']
    fields.update(changes)
    response=await client.post(ROOT+'/device-workflows/port-feature-preview',json=fields,headers=headers)
    assert response.status==200,await response.text()
    return await response.json()


async def apply(features,reviewed):
    response=await features[1].post(ROOT+'/device-workflows/apply',json=confirmation(reviewed),headers=features[3])
    assert response.status==200,await response.text()
    return await response.json()


@pytest.mark.parametrize('kind',['speech','face','sound'])
async def test_initial_named_setup_then_reviewed_undo_no_paths_models_or_key_changes(features,kind):
    _,client,_,headers,paths,_,_=features
    if kind=='sound':
        await apply(features,await review(features))
    before={key:path.read_bytes() for key,path in paths.items()}
    reviewed=await review(features,kind)
    assert reviewed['details']['named_cameras']=='Fictional room 1'
    assert '/models/' not in json.dumps(reviewed)
    assert 'port_'+kind+'_processing' in reviewed['review']['required_consents']
    await apply(features,reviewed)
    undo=await client.post(ROOT+'/device-workflows/rollback-preview',json={
        'profile':PROFILE,'revision':revision(paths['aiport'])},headers=headers)
    assert undo.status==200,await undo.text()
    result=await apply(features,await undo.json())
    if kind=='face':
        restored=json.loads(paths['aiport'].read_bytes())
        assert restored.pop('face_model_space_ref') and restored==json.loads(before['aiport'])
        assert result['model_space_guard_retained']
    else:
        assert paths['aiport'].read_bytes()==before['aiport']
    assert paths['aikey'].read_bytes()==before['aikey']


@pytest.mark.parametrize('fault',['renamed','offline','new_id','consent','model_changed'])
async def test_changed_scope_or_binding_or_consent_is_spent_without_writes(features,fault):
    site,client,_,headers,paths,report,_=features
    reviewed=await review(features,'face')
    before=paths['aiport'].read_bytes()
    payload=confirmation(reviewed)
    if fault=='renamed':
        report['cameras'][0]['name']='Different fictional room'
    elif fault=='offline':
        report['cameras'][0]['state']='DISCONNECTED'
    elif fault=='new_id':
        report['cameras'][0]['id']='e'*24
    elif fault=='consent':
        payload['acknowledgements']=[]
    else:
        path=site.port_model_catalogs[PROFILE][0]
        binding=json.loads(path.read_bytes())
        binding['settings']['embedder_sha256']='e'*64
        atomic_private(path,json.dumps(binding))
    response=await client.post(ROOT+'/device-workflows/apply',json=payload,headers=headers)
    assert response.status==409 and paths['aiport'].read_bytes()==before
    assert (await client.post(ROOT+'/device-workflows/apply',json=payload,headers=headers)).status==409


async def test_native_speech_form_no_raw_camera_id_or_path_and_disable_during_inventory_outage(features):
    site,client,_,headers,paths,report,_=features
    html=await (await client.get('/port-features')).text()
    assert '/models/' not in html and all(row['id'] not in html for row in report['cameras'])
    chunk=html.split('<h3>Audio capture and speech presence</h3>')[1].split('</section>')[0]
    fields=form(chunk,'/port-features/preview')
    choice=re.search("name='(port_camera_[0-9a-f]{64})'",chunk)[1]
    fields.update(event_limit='12',**{choice:'yes'})
    response=await client.post('/port-features/preview',data=fields,headers=headers)
    assert response.status==200,await response.text()
    fields=form(await response.text(),'/devices/apply')
    fields.update(port_named_feature_camera_scope='yes',port_speech_processing='yes')
    assert (await client.post('/devices/apply',data=fields,headers=headers,allow_redirects=False)).status==303
    async def down():
        raise OSError('fictional outage')
    site.inventory_loader=down
    response=await client.post(ROOT+'/device-workflows/port-feature-preview',json={
        'profile':PROFILE,'revision':revision(paths['aiport']),'feature':'speech','mode':'disable'},headers=headers)
    assert response.status==200,await response.text()
    await apply(features,await response.json())
    assert 'live_speech_cameras' not in json.loads(paths['aiport'].read_bytes())


async def test_http_cannot_supply_model_paths_or_cross_profile_choice(features):
    _,client,_,headers,paths,_,_=features
    status=await (await client.get(ROOT+'/port-features')).json()
    face=status['profiles'][0]['features'][0]
    before=paths['aiport'].read_bytes()
    body={'profile':PROFILE,'revision':revision(paths['aiport']),'feature':'face','mode':'configure',
        'choices':[face['choices'][0]['choice']],'model_choice':face['models'][0]['choice'],
        'model_settings':model_settings('face')}
    response=await client.post(ROOT+'/device-workflows/port-feature-preview',json=body,headers=headers)
    assert response.status==409 and paths['aiport'].read_bytes()==before
