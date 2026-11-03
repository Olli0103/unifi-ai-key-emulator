"""Write-only ONVIF credentials and metadata-only UI/API; no ingest fiction."""
import json
import re
import socket

import pytest

from aikey.config import atomic_private
from test_device_controls import live as device_live, confirmation  # noqa: F401
from test_device_workflow import revision
from test_ocr_provider_http import form
from test_onvif_catalog import USERNAME, PASSWORD

ROOT='/api/admin/v1'
PROFILE='aiport:front_door'


def fields(paths,**changes):
    return dict({'profile':PROFILE,'revision':revision(paths['aiport']),'operation':'save','camera':'new',
        'name':'Fictional ONVIF entrance','host':'192.168.50.80','port':443,'transport':'https',
        'certificate_pin':'d'*64,'username':USERNAME,'password':PASSWORD},**changes)


async def review(live,**changes):
    _,client,_,headers,paths,_=live
    response=await client.post(ROOT+'/device-workflows/onvif-preview',json=fields(paths,**changes),headers=headers)
    assert response.status==200,await response.text()
    result=await response.json()
    assert PASSWORD not in json.dumps(result) and USERNAME not in json.dumps(result)
    return result


async def test_save_status_and_exact_undo_never_expose_credentials_or_claim_native_support(device_live,caplog,monkeypatch):  # noqa: F811
    site,client,_,headers,paths,_=device_live
    def no_dns(*args,**kwargs):
        pytest.fail('Metadata registration must not resolve a camera/model/controller')
    monkeypatch.setattr(socket,'getaddrinfo',no_dns)
    before=paths['aiport'].read_bytes()
    key_before=paths['aikey'].read_bytes()
    reviewed=await review(device_live)
    assert not list(paths['aiport'].parent.glob('onvif-*'))
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(reviewed),headers=headers)
    assert response.status==200,await response.text()
    response=await client.get(ROOT+'/onvif-cameras')
    result=await response.json()
    assert result['network_requests']==0 and result['ingest']=='not_implemented'
    camera=result['profiles'][0]['configured_cameras'][0]
    assert camera['processing_enabled'] is False and camera['ptz']=='not_implemented'
    assert 'username_ref' not in json.dumps(result) and PASSWORD not in json.dumps(result) and USERNAME not in json.dumps(result)
    undo=await client.post(ROOT+'/device-workflows/rollback-preview',json={
        'profile':PROFILE,'revision':revision(paths['aiport'])},headers=headers)
    assert undo.status==200,await undo.text()
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(await undo.json()),headers=headers)
    assert response.status==200,await response.text()
    assert paths['aiport'].read_bytes()==before and paths['aikey'].read_bytes()==key_before
    assert len(list(paths['aiport'].parent.glob('onvif-*')))==3
    for url in ('/devices','/onvif-cameras',ROOT+'/onvif-cameras'):
        response=await client.get(url)
        html=await response.text()
        assert response.status==200 and PASSWORD not in html and USERNAME not in html
        assert response.headers['Cache-Control']=='no-store'
    assert PASSWORD not in caplog.text and PASSWORD not in site.audit.path.read_text()


@pytest.mark.parametrize('fault',['missing_consent','stale','wrong_profile','forged_reference','invalid_tls','retarget_without_credentials'])
async def test_invalid_or_stale_onvif_edit_cannot_publish(device_live,fault):  # noqa: F811
    _,client,_,headers,paths,_=device_live
    before=paths['aiport'].read_bytes()
    changes={}
    if fault=='stale':
        changes['revision']='0'*64
    elif fault=='wrong_profile':
        changes['profile']='aikey'
    elif fault=='forged_reference':
        changes['password_ref']='/private/other-profile'
    elif fault=='invalid_tls':
        changes['transport']='https-insecure'
    elif fault=='retarget_without_credentials':
        reviewed=await review(device_live)
        assert (await client.post(ROOT+'/device-workflows/apply',json=confirmation(reviewed),headers=headers)).status==200
        status=await (await client.get(ROOT+'/onvif-cameras')).json()
        camera=status['profiles'][0]['configured_cameras'][0]
        changes=dict(camera=camera['id'],host='different.invalid',username='',password='')
        before=paths['aiport'].read_bytes()
    response=await client.post(ROOT+'/device-workflows/onvif-preview',json=fields(paths,**changes),headers=headers)
    if fault=='missing_consent':
        assert response.status==200
        payload=confirmation(await response.json())
        payload['acknowledgements']=[]
        response=await client.post(ROOT+'/device-workflows/apply',json=payload,headers=headers)
    assert response.status==409 and paths['aiport'].read_bytes()==before


async def test_native_form_write_only_password_and_plain_http_confirmation(device_live,caplog):  # noqa: F811
    site,client,credentials,headers,paths,_=device_live
    response=await client.get('/onvif-cameras')
    html=await response.text()
    assert '<script' not in html and 'not implemented' in html
    payload=form(html,'/onvif-cameras/preview')
    payload.update(name='Fictional plaintext test',host='192.168.50.80',port='80',transport='http',
        certificate_pin='',username=USERNAME,password=PASSWORD)
    response=await client.post('/onvif-cameras/preview',data=payload,headers=headers)
    assert response.status==200,await response.text()
    html=await response.text()
    assert PASSWORD not in html and USERNAME not in html and 'onvif-password-' not in html
    payload=form(html,'/devices/apply')
    for consent in ('onvif_metadata_only_no_ingest','onvif_private_credential_references','onvif_unencrypted_credentials_if_future_ingest'):
        payload[consent]='yes'
    response=await client.post('/devices/apply',data=payload,headers=headers,allow_redirects=False)
    assert response.status==303 and response.headers['Location']=='/devices?saved=1'
    html=await (await client.get('/onvif-cameras')).text()
    assert PASSWORD not in html and USERNAME not in html
    for field in ('username','password'):
        assert not re.search("name='"+field+"'[^>]*value=",html)
    assert PASSWORD not in caplog.text and PASSWORD not in site.audit.path.read_text()
    assert credentials.csrf_token in html


async def test_clear_all_metadata_can_recover_missing_credentials_without_deleting_files(device_live):  # noqa: F811
    _,client,_,headers,paths,_=device_live
    reviewed=await review(device_live)
    assert (await client.post(ROOT+'/device-workflows/apply',json=confirmation(reviewed),headers=headers)).status==200
    password=next(paths['aiport'].parent.glob('onvif-password-*'))
    retained=password.read_bytes()
    password.unlink()  # isolated missing-file fixture, not real credentials
    response=await client.post(ROOT+'/device-workflows/onvif-preview',json={
        'profile':PROFILE,'revision':revision(paths['aiport']),'operation':'clear','camera':'all'},headers=headers)
    assert response.status==200,await response.text()
    response=await client.post(ROOT+'/device-workflows/apply',json=confirmation(await response.json()),headers=headers)
    assert response.status==200,await response.text()
    status=await (await client.get(ROOT+'/onvif-cameras')).json()
    assert status['profiles'][0]['configured_cameras']==[]
    assert not password.exists()
    atomic_private(password,retained)
    undo=await client.post(ROOT+'/device-workflows/rollback-preview',json={
        'profile':PROFILE,'revision':revision(paths['aiport'])},headers=headers)
    assert undo.status==200,await undo.text()
