"""An AI Port needs neither an AI Key config nor a placeholder identity."""
import json

import pytest

from aikey import control_site
from aikey.admin_security import AdminSecurity
from aikey.config import atomic_private
from aikey.control_site import ControlSite
from test_aiport_config_store import fixture
from test_control_site_key_only import sign_in
from test_control_site_secrets import PASSWORD


def port_fixture(tmp_path,*,named):
    store,path,current=fixture(tmp_path/'port')
    atomic_private(path.parent/'identity.json',json.dumps({'mac':current['mac']}))
    site=ControlSite(aiport_config=None if named else path,
        aiport_instances={'front_door':path} if named else None,
        signing_key=b'q'*32,password_record=AdminSecurity.create_password_record(PASSWORD),port=8765,
        audit_log=tmp_path/'audit.jsonl')
    return site,store,path,'aiport:front_door' if named else 'aiport'


@pytest.mark.parametrize('named',[False,True])
async def test_port_only_sign_in_profile_pages_provider_save_and_exact_undo_without_key_files(tmp_path,named):
    site,_,path,profile=port_fixture(tmp_path,named=named)
    before=path.read_bytes()
    static={name:(path.parent/name).read_bytes() for name in ('device.key','device.crt','identity.json')}
    server,client,csrf=await sign_in(site)
    try:
        assert site.aikey is None and site.aikey_state_dir is None
        assert set(site._backup_profiles())=={profile}
        for url in ('/','/devices','/camera-pairing','/onvif-cameras','/processing','/runtime-status',
                    '/model-registry','/data-retention','/state-backup-preflight','/diagnostics'):
            response=await client.get(url)
            assert response.status==200,(url,await response.text())
        html=await (await client.get('/')).text()
        assert 'AI Port-only configuration' in html and "name='profile' value='aikey'" not in html
        assert "href='/caption-setup'" not in html and "href='/face-identities'" not in html
        response=await client.get('/api/admin/v1/profiles')
        assert [row['id'] for row in (await response.json())['profiles']]==[profile]
        for suffix in ('configuration','identity','runtime'):
            response=await client.get('/api/admin/v1/profiles/aikey/'+suffix)
            assert response.status==404 and (await response.json())['error']=='unknown_profile'
        for url in ('/caption-setup','/caption-preflight','/worker-limits','/face-identities','/speech-permissions','/index-migration'):
            assert (await client.get(url)).status==404
        fields={'csrf':csrf,'profile':profile,'revision':site.aiports[profile].snapshot().revision,
            'provider':'ollama','model':'synthetic-independent-port-model','base_url':'http://127.0.0.1:11434',
            'max_output_tokens':'128','threshold':'.7','smart_types':'person'}
        response=await client.post('/provider',data=fields,headers={'Origin':site.origin},allow_redirects=False)
        assert response.status==303,await response.text()
        after=json.loads(path.read_bytes())
        assert after['live_pool_detector']['provider_config']['model']==fields['model']
        response=await client.post('/provider/rollback',data={'csrf':csrf,'profile':profile,
            'revision':site.aiports[profile].snapshot().revision},headers={'Origin':site.origin},allow_redirects=False)
        assert response.status==303 and path.read_bytes()==before
        response=await client.post('/provider',data=dict(fields,profile='aikey'),headers={'Origin':site.origin})
        assert response.status==400 and path.read_bytes()==before
        assert all((path.parent/name).read_bytes()==raw for name,raw in static.items())
        assert not (tmp_path/'key').exists() and not (tmp_path/'aikey').exists()
    finally:
        await client.close()
        await server.close()


def test_real_cli_port_only_but_explicit_invalid_key_and_decoder_are_not_silently_ignored(tmp_path,monkeypatch):
    _,path,_=fixture(tmp_path/'port')
    admin=tmp_path/'admin'
    assert control_site.main(['init','--state-dir',str(admin),'--generate'])==0
    calls=[]
    async def no_server(site,port):
        calls.append(site)
        assert site.aikey is None and list(site.aiports)==['aiport:front_door']
        site.app()
    monkeypatch.setattr(control_site,'_run',no_server)
    args=['run','--state-dir',str(admin),'--aiport-instance','front_door='+str(path)]
    assert control_site.main(args)==0 and len(calls)==1
    assert control_site.main(args+['--aikey-config',str(tmp_path/'missing-key.json')])==2
    assert control_site.main(args+['--camera-pool-decoder','aikey=/bin/ffmpeg'])==2
    assert len(calls)==1 and not (tmp_path/'missing-key.json').exists()


def test_missing_profiles_or_unattached_key_authority_are_refused(tmp_path):
    options={'signing_key':b'q'*32,'password_record':'unused','port':8765}
    with pytest.raises(ValueError,match='At least one'):
        ControlSite(**options)
    _,path,_=fixture(tmp_path/'port')
    for extra in ({'aikey_state_dir':tmp_path/'unattached-key'},
                  {'search_state_dir':tmp_path/'unattached-search'},
                  {'runtime_health_urls':{'aikey':'https://127.0.0.1:8443/healthz'}}):
        with pytest.raises(ValueError):
            ControlSite(aiport_config=path,**options,**extra)
    assert not (tmp_path/'unattached-key').exists() and not (tmp_path/'unattached-search').exists()
