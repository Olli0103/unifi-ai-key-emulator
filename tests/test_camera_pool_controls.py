"""Fresh native-form and JSON capture consent, without controller/media writes."""
from copy import deepcopy
import json
import os
import re
import sys
import time

import pytest

from aikey.config import atomic_private
from aikey import device_workflow as workflow_module
from test_device_controls import live as device_live, confirmation  # noqa: F401
from test_device_workflow import revision
from test_aiport_config_store import CAMERAS
from test_ocr_provider_http import form

ROOT = '/api/admin/v1'
PROFILE = 'aiport:front_door'
ADDED = '2A1122334499'


@pytest.fixture
async def pool(device_live):  # noqa: F811
    site, client, credentials, headers, paths, _ = device_live
    path = paths['aiport']
    raw = json.loads(path.read_bytes())
    raw.update(paired_streams=[{'camera_mac': camera, 'source_ip': raw['controller_ip'],
        'ffmpeg_path': sys.executable} for camera in CAMERAS], live_pool_detector={
        'checkpoint_path': '/models/fixture.pth', 'checkpoint_sha256': 'a' * 64,
        'threshold': .3, 'smart_types': ['person']})
    atomic_private(path, json.dumps(raw))
    report = {'schema': 'aikey-camera-preflight/1', 'protect_version': '7.3.74', 'fetched_at': int(time.time()),
        'cameras': [{'id': f'{index + 1:024x}', 'mac': camera, 'name': f'Fictional room {index + 1}',
            'model': 'UVC G3 Instant', 'state': 'CONNECTED', 'smart_detect_types': ['person'],
            'audio_detect_types': []} for index, camera in enumerate((*CAMERAS, ADDED))]}
    calls = []
    async def load():
        calls.append(True)
        return deepcopy(report)
    site.inventory_loader = load
    yield site, client, credentials, headers, paths, report, calls


async def review(pool, choices=None):
    _, client, _, headers, paths, _, _ = pool
    response = await client.get(ROOT + '/camera-pools')
    assert response.status == 200
    row = (await response.json())['profiles'][0]
    if not row['available']:
        # Exercise the contract directly too, so a refused adapter does not
        # hide a broken planner/binding behind an uninformative boolean.
        controls=pool[0].admin_api.camera_pool_controls
        state=controls.devices.stores[PROFILE].camera_pool_status()
        await controls._choices(pool[2].cookie,PROFILE,state)
        controls._decoder(PROFILE,state)
    assert row['available']
    response = await client.post(ROOT + '/device-workflows/camera-pool-preview', json={
        'profile': PROFILE, 'revision': revision(paths['aiport']),
        'choices': [choice['choice'] for choice in row['choices']] if choices is None else choices}, headers=headers)
    assert response.status == 200, await response.text()
    return await response.json()


async def test_named_allowlist_edit_and_explicit_undo_are_not_native_pairing(pool):
    site, client, _, headers, paths, _, _ = pool
    before = paths['aiport'].read_bytes()
    key_before = paths['aikey'].read_bytes()
    reviewed = await review(pool)
    assert 'Fictional room 3' in reviewed['details']['capture_cameras']
    assert all(camera not in json.dumps(reviewed) for camera in (*CAMERAS, ADDED))
    response = await client.post(ROOT + '/device-workflows/apply', json=confirmation(reviewed), headers=headers)
    assert response.status == 200, await response.text()
    assert len(json.loads(paths['aiport'].read_bytes())['paired_streams']) == 3
    undo = await client.post(ROOT + '/device-workflows/rollback-preview', json={
        'profile': PROFILE, 'revision': revision(paths['aiport'])}, headers=headers)
    assert undo.status == 200, await undo.text()
    undo = await undo.json()
    assert 'restore_prior_camera_capture_and_feature_permissions' in undo['review']['required_consents']
    response = await client.post(ROOT + '/device-workflows/apply', json=confirmation(undo), headers=headers)
    assert response.status == 200, await response.text()
    assert paths['aiport'].read_bytes() == before and paths['aikey'].read_bytes() == key_before
    assert not site.admin_api.operations


@pytest.mark.parametrize('fault',['renamed','reidentified','offline','onvif','inventory_failed','duplicate_name','expired'])
async def test_changed_observation_cannot_enable_capture_and_old_token_is_spent(pool,fault):
    site, client, _, headers, paths, report, _ = pool
    reviewed = await review(pool)
    before = paths['aiport'].read_bytes()
    if fault == 'renamed':
        report['cameras'][-1]['name'] = 'A different room'
    elif fault == 'reidentified':
        report['cameras'][-1]['id'] = 'e' * 24
    elif fault == 'offline':
        report['cameras'][-1]['state'] = 'DISCONNECTED'
    elif fault == 'onvif':
        report['cameras'][-1]['source_kind'] = 'onvif'
    elif fault == 'inventory_failed':
        async def down():
            raise OSError('synthetic inventory outage')
        site.inventory_loader = down
    elif fault == 'duplicate_name':
        report['cameras'][-1]['name'] = report['cameras'][0]['name']
    else:
        site.admin_api.device_controls.clock = lambda: 1e20
    response = await client.post(ROOT + '/device-workflows/apply', json=confirmation(reviewed), headers=headers)
    assert response.status == 409
    assert paths['aiport'].read_bytes() == before
    response = await client.post(ROOT + '/device-workflows/apply', json=confirmation(reviewed), headers=headers)
    assert response.status == 409


async def test_capacity_and_raw_camera_ids_do_not_bypass_opaque_choices(pool):
    _, client, _, headers, paths, report, _ = pool
    before = paths['aiport'].read_bytes()
    response = await client.post(ROOT + '/device-workflows/camera-pool-preview', json={
        'profile': PROFILE, 'revision': revision(paths['aiport']), 'choices': [ADDED]}, headers=headers)
    assert response.status == 409
    report['cameras'].append(dict(report['cameras'][-1], id='f' * 24, mac='2A1122334488', name='Fictional fourth room'))
    for camera in report['cameras']:
        camera['model']='UVC G4 Pro'
    response = await client.get(ROOT + '/camera-pools')
    row = (await response.json())['profiles'][0]
    response = await client.post(ROOT + '/device-workflows/camera-pool-preview', json={
        'profile': PROFILE, 'revision': revision(paths['aiport']),
        'choices': [choice['choice'] for choice in row['choices']]}, headers=headers)
    assert response.status == 409 and paths['aiport'].read_bytes() == before


async def test_inventory_outage_still_allows_explicit_disable_all_but_not_undo(pool):
    site, client, _, headers, paths, _, _ = pool
    async def down():
        raise OSError('synthetic inventory outage')
    site.inventory_loader = down
    response = await client.get('/camera-pairing')
    html = await response.text()
    assert response.status == 200 and 'Review disabling all saved local capture' in html
    fields = form(html, '/camera-pairing/preview')
    response = await client.post('/camera-pairing/preview', data=fields, headers=headers)
    assert response.status == 200, await response.text()
    html = await response.text()
    assert 'all local capture disabled' in html
    fields = form(html, '/devices/apply')
    for consent in ('reviewed_camera_capture_destination_and_cost','native_pairing_is_separate'):
        fields[consent]='yes'
    response = await client.post('/devices/apply', data=fields, headers=headers, allow_redirects=False)
    assert response.status == 303, await response.text()
    assert 'paired_streams' not in json.loads(paths['aiport'].read_bytes())
    response = await client.post(ROOT + '/device-workflows/rollback-preview', json={
        'profile': PROFILE, 'revision': revision(paths['aiport'])}, headers=headers)
    assert response.status == 409


async def test_get_inventory_observation_is_shared_not_repeated_for_every_profile(pool):
    _, client, _, _, _, _, calls = pool
    await client.get('/camera-pairing')
    await client.get('/camera-pairing')
    assert len(calls)==1


def passive(pool):
    site,_,_,_,paths,_,_=pool
    path=paths['aiport']
    current=json.loads(path.read_bytes())
    current.pop('paired_streams')
    current.pop('live_pool_detector')
    atomic_private(path,json.dumps(current))
    site.camera_pool_decoders[PROFILE]=sys.executable
    return path


def setup_fields(provider='openai'):
    return {'provider':provider,'model':'synthetic-object-model',
        'base_url':'https://api.openai.com/v1' if provider=='openai' else 'http://127.0.0.1:11434',
        'allow_remote':provider=='openai','allow_insecure_http':False,'max_output_tokens':256,
        'threshold':.8,'api_key':'synthetic-never-issued-private-camera-key' if provider=='openai' else ''}


@pytest.mark.parametrize('provider',['openai','ollama'])
async def test_passive_port_initial_setup_review_save_and_undo_never_share_key_identity(pool,provider,caplog):
    site,client,_,headers,paths,_,_=pool
    path=passive(pool)
    before=path.read_bytes()
    key_before=paths['aikey'].read_bytes()
    identity={name:(path.parent/name).read_bytes() for name in ('device.key','device.crt','identity.json')}
    response=await client.get(ROOT+'/camera-pools')
    row=(await response.json())['profiles'][0]
    assert row['available'] and row['initial_detector_required']
    setup=setup_fields(provider)
    response=await client.post(ROOT+'/device-workflows/camera-pool-preview',json={
        'profile':PROFILE,'revision':revision(path),'choices':[row['choices'][0]['choice']],'setup':setup},headers=headers)
    assert response.status==200,await response.text()
    reviewed=await response.json()
    assert path.read_bytes()==before and not list(path.parent.glob('provider-key-*'))
    if setup['api_key']:
        assert setup['api_key'] not in json.dumps(reviewed) and setup['api_key'] not in repr(site.admin_api.device_controls.grants)
        assert 'explicit_remote_camera_frames_and_cost' in reviewed['review']['required_consents']
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(reviewed),headers=headers)
    assert response.status==200,await response.text()
    current=json.loads(path.read_bytes())
    assert len(current['paired_streams'])==1 and current['live_pool_detector']['provider_config']['provider']==provider
    keys=list(path.parent.glob('provider-key-*'))
    assert len(keys)==(1 if setup['api_key'] else 0)
    if keys:
        assert keys[0].read_text()==setup['api_key']+'\n' and keys[0].stat().st_mode&0o777==0o600
        assert str(keys[0])==current['live_pool_detector']['provider_config']['api_key_file']
    assert all((path.parent/name).read_bytes()==raw for name,raw in identity.items())
    assert paths['aikey'].read_bytes()==key_before
    undo=await client.post(ROOT+'/device-workflows/rollback-preview',json={'profile':PROFILE,'revision':revision(path)},headers=headers)
    assert undo.status==200,await undo.text()
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(await undo.json()),headers=headers)
    assert response.status==200,await response.text()
    assert path.read_bytes()==before and list(path.parent.glob('provider-key-*'))==keys
    html=await (await client.get('/camera-pairing')).text()
    assert "name='setup_api_key'" in html and 'value=' not in html.split("name='setup_api_key'")[1].split('>')[0]
    if setup['api_key']:
        assert setup['api_key'] not in html and setup['api_key'] not in caplog.text
        assert setup['api_key'] not in site.audit.path.read_text()


@pytest.mark.parametrize('fault',['no_key','key_ref','other_profile','invalid_threshold','missing_remote_ack'])
async def test_invalid_initial_setup_cannot_publish_key_or_config(pool,fault):
    _,client,_,headers,paths,_,_=pool
    path=passive(pool)
    before=path.read_bytes()
    row=(await (await client.get(ROOT+'/camera-pools')).json())['profiles'][0]
    setup=setup_fields()
    if fault=='no_key':
        setup['api_key']=''
    elif fault=='key_ref':
        setup['api_key_file']=str(paths['aikey'].parent/'management-password')
    elif fault=='other_profile':
        setup['profile']='aikey'
    elif fault=='invalid_threshold':
        setup['threshold']=True
    response=await client.post(ROOT+'/device-workflows/camera-pool-preview',json={
        'profile':PROFILE,'revision':revision(path),'choices':[row['choices'][0]['choice']],'setup':setup},headers=headers)
    if fault=='missing_remote_ack':
        assert response.status==200
        fields=confirmation(await response.json())
        fields['acknowledgements'].remove('explicit_remote_camera_frames_and_cost')
        response=await client.post(ROOT+'/device-workflows/apply',json=fields,headers=headers)
    assert response.status==409
    assert path.read_bytes()==before and not list(path.parent.glob('provider-key-*'))


async def initial_review(pool):
    _,client,_,headers,paths,_,_=pool
    path=passive(pool)
    row=(await (await client.get(ROOT+'/camera-pools')).json())['profiles'][0]
    response=await client.post(ROOT+'/device-workflows/camera-pool-preview',json={
        'profile':PROFILE,'revision':revision(path),'choices':[row['choices'][0]['choice']],
        'setup':setup_fields()},headers=headers)
    assert response.status==200,await response.text()
    return await response.json()


async def test_initial_key_fsync_failure_cannot_publish_configuration_or_retry_same_token(pool,monkeypatch):
    _,client,_,headers,paths,_,_=pool
    reviewed=await initial_review(pool)
    path=paths['aiport']
    before=path.read_bytes()
    original=os.fsync
    def key_fsync_failure(descriptor):
        info=os.fstat(descriptor)
        if any((file.stat().st_dev,file.stat().st_ino)==(info.st_dev,info.st_ino)
               for file in path.parent.glob('provider-key-*')):
            raise OSError('synthetic credential durability failure')
        return original(descriptor)
    monkeypatch.setattr(os,'fsync',key_fsync_failure)
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(reviewed),headers=headers)
    assert response.status==409 and path.read_bytes()==before
    assert not (path.parent/('.'+path.name+'.device-workflow.json')).exists()
    retained=list(path.parent.glob('provider-key-*'))
    assert len(retained)==1 and retained[0].stat().st_mode&0o777==0o600
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(reviewed),headers=headers)
    assert response.status==409 and list(path.parent.glob('provider-key-*'))==retained


async def test_lost_initial_config_ack_retains_key_and_requires_it_for_reconciliation(pool,monkeypatch):
    _,client,_,headers,paths,_,_=pool
    reviewed=await initial_review(pool)
    path=paths['aiport']
    original=workflow_module.atomic_private
    def lost_ack(target,raw):
        original(target,raw)
        if target==path:
            raise OSError('synthetic initial-save acknowledgment loss')
    monkeypatch.setattr(workflow_module,'atomic_private',lost_ack)
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(reviewed),headers=headers)
    assert response.status==503
    saved=path.read_bytes()
    credential=next(path.parent.glob('provider-key-*'))
    private=credential.read_bytes()
    credential.unlink()  # synthetic missing-resource recovery boundary
    response=await client.post(ROOT+'/device-workflows/reconcile-preview',json={
        'profile':PROFILE,'revision':revision(path)},headers=headers)
    fields=confirmation(await response.json())
    response=await client.post(ROOT+'/device-workflows/apply',json=fields,headers=headers)
    assert response.status==409 and path.read_bytes()==saved
    atomic_private(credential,private)
    response=await client.post(ROOT+'/device-workflows/reconcile-preview',json={
        'profile':PROFILE,'revision':revision(path)},headers=headers)
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(await response.json()),headers=headers)
    assert response.status==200 and path.read_bytes()==saved


async def test_native_initial_setup_never_returns_entered_key_or_private_reference(pool,caplog):
    site,client,_,headers,paths,_,_=pool
    path=passive(pool)
    html=await (await client.get('/camera-pairing')).text()
    fields=form(html,'/camera-pairing/preview')
    choice=re.search("name='(camera_[0-9a-f]{64})'",html)[1]
    setup=setup_fields()
    fields.update({choice:'yes','setup_provider':setup['provider'],'setup_model':setup['model'],
        'setup_base_url':setup['base_url'],'setup_api_key':setup['api_key'],'setup_allow_remote':'yes',
        'setup_threshold':'0.8','setup_max_output_tokens':'256'})
    response=await client.post('/camera-pairing/preview',data=fields,headers=headers)
    assert response.status==200,await response.text()
    html=await response.text()
    assert setup['api_key'] not in html and 'provider-key-' not in html
    fields=form(html,'/devices/apply')
    for name in ('reviewed_camera_capture_destination_and_cost','native_pairing_is_separate','explicit_remote_camera_frames_and_cost'):
        fields[name]='yes'
    response=await client.post('/devices/apply',data=fields,headers=headers,allow_redirects=False)
    assert response.status==303 and response.headers['Location']=='/devices?saved=1'
    assert 'paired_streams' in json.loads(path.read_bytes())
    html=await (await client.get('/camera-pairing')).text()
    assert setup['api_key'] not in html and setup['api_key'] not in caplog.text
    assert setup['api_key'] not in site.audit.path.read_text()
