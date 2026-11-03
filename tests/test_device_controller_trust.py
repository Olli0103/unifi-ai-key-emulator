"""Both actual client-context builders keep independent synthetic mTLS identities."""
import asyncio
import hashlib
import json
import ssl
from types import SimpleNamespace

import pytest

from aikey.aiport_candidate import CandidateService, parse_config
from aikey.config import atomic_private, initialize, readiness, validate_config
from aikey.config_store import ConfigurationStore, UnsafeConfigurationChange
from aikey.controller_trust import DIRECTORY,ControllerTrustError,install,prepare,selected
from aikey.state_backup_preflight import _profile_spec
from aikey.tls import client_context,ensure_identity_certificate,server_context


def profiles(tmp_path):
    controller=tmp_path/'controller'
    controller.mkdir(mode=0o700)
    peer,_=ensure_identity_certificate(controller,'020000000099')
    pem=peer.read_text()
    pin=hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    key=tmp_path/'key'
    key.mkdir(mode=0o700)
    key_config=initialize(key/'config.json',key,controller_host='127.0.0.1')
    port=tmp_path/'port'
    port.mkdir(mode=0o700)
    ensure_identity_certificate(port,'020000000088')
    port_config={'controller_ip':'192.168.50.1','controller_pin':pin,'device_ip':'192.168.50.20',
                 'mac':'020000000088','firmware_version':'5.1.12'}
    for kind,root,config in (('aikey',key,key_config),('aiport',port,port_config)):
        atomic_private(root/'controller-ca.pem',pem)
        directory=root/DIRECTORY
        directory.mkdir(mode=0o700)
        mac=config['device']['mac'] if kind=='aikey' else config['mac']
        host=config['controller']['host'] if kind=='aikey' else config['controller_ip']
        trust=prepare(host=host,device_mac=mac,purpose='control',certificate_pem=pem,fingerprint=pin)
        install(directory,trust)
        if kind=='aikey':
            config['controller'].update(trust_ref=trust.reference,expected_fingerprint=pin)
        else:
            config['controller_trust_ref']=trust.reference
        atomic_private(root/'config.json',json.dumps(config))
    return controller,key,key_config,port,port_config


def test_both_profile_contexts_use_actual_selected_roots_and_distinct_mtls_identities(tmp_path):
    controller,key,key_config,port,port_config=profiles(tmp_path)
    saved={(root.name,name):(root/name).read_bytes() for root in (key,port)
           for name in ('config.json','device.key','device.crt','controller-ca.pem')}
    key_context=client_context(validate_config(key_config,base=key))
    port_context=CandidateService._client_context(SimpleNamespace(
        config=parse_config((port/'config.json').read_bytes(),state_dir=port),state_dir=port))
    context=server_context(controller)
    context.verify_mode=ssl.CERT_REQUIRED
    for root in (key,port):
        context.load_verify_locations(root/'device.crt')
    seen=[]
    async def scenario():
        async def serve(reader,writer):
            peer=writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
            seen.append(hashlib.sha256(peer).hexdigest())
            writer.write(b'fixture')
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server=await asyncio.start_server(serve,'127.0.0.1',0,ssl=context)
        try:
            for client in (key_context,port_context):
                reader,writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1],
                                                           ssl=client,server_hostname='127.0.0.1')
                assert await reader.read()==b'fixture'
                writer.close()
                await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(scenario())
    expected=[hashlib.sha256(ssl.PEM_cert_to_DER_cert((root/'device.crt').read_text())).hexdigest()
              for root in (key,port)]
    assert seen==expected and len(set(seen))==2
    assert all((root/name).read_bytes()==saved[(root.name,name)] for root in (key,port)
               for name in ('config.json','device.key','device.crt','controller-ca.pem'))


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_broken_selected_object_never_falls_back_to_a_working_legacy_root(tmp_path,kind):
    _,key,key_config,port,port_config=profiles(tmp_path)
    root,config=(key,key_config) if kind=='aikey' else (port,port_config)
    trust=selected(config,kind=kind,state_dir=root)
    atomic_private(root/DIRECTORY/trust.reference,b'corrupted')
    with pytest.raises(ControllerTrustError):
        if kind=='aikey':
            client_context(config)
        else:
            CandidateService._client_context(SimpleNamespace(config=config,state_dir=root))
    if kind=='aikey':
        assert not readiness(config)['checks']['controller_trust_file']


def test_generic_provider_configuration_cannot_select_or_retarget_reviewed_device_trust(tmp_path):
    _,key,key_config,_,_=profiles(tmp_path)
    store=ConfigurationStore(key/'config.json')
    revision=store.snapshot().revision
    for patch in ({'controller':{'host':'other.invalid'}},
                  {'controller':{'trust_ref':'controller-trust.'+'a'*64+'.json'}},
                  {'controller':{'trust_ref':None}}):
        with pytest.raises(UnsafeConfigurationChange):
            store.preview(revision,patch)
    assert store.snapshot().revision==revision


def test_backup_contract_tracks_selected_objects_and_all_retained_versions(tmp_path):
    _,key,_,port,_=profiles(tmp_path)
    for kind,root in (('aikey',key),('aiport',port)):
        spec=_profile_spec(kind,root)
        assert spec['required']['control_trust_object'].parent==root/DIRECTORY
        assert spec['required']['control_trust_object'].is_file()
        assert spec['journals']['controller_trust_history']==root/DIRECTORY
