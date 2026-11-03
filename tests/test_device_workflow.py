"""Complete saved-controller transactions; no real devices or service activation."""
from dataclasses import replace
import hashlib
import json
import ssl

import pytest

from aikey.aiport_candidate import CandidateError,CandidateService
from aikey.aiport_config_store import AiPortConfigurationStore,AiPortUnsafePolicyChange
from aikey.config import ConfigError,atomic_private,initialize,load_startup_config
from aikey.config_store import ConfigurationStore,UnsafeConfigurationChange
from aikey.controller_trust import DIRECTORY,selected
from aikey.device_workflow import (DeviceWorkflowError,DeviceWorkflowStore,
    DeviceOutcomeUncertain,DeviceRevisionConflict)
from aikey import device_workflow as module
from aikey import device_workflow_fence as fence
from aikey.tls import ensure_identity_certificate


def setup(tmp_path,kind):
    peer=tmp_path/'peer'
    peer.mkdir(mode=0o700)
    old_cert,_=ensure_identity_certificate(peer,'020000000099')
    newer=tmp_path/'newer'
    newer.mkdir(mode=0o700)
    new_cert,_=ensure_identity_certificate(newer,'020000000098')
    root=tmp_path/'profile'
    root.mkdir(mode=0o700)
    path=root/'config.json'
    old_pin=hashlib.sha256(ssl.PEM_cert_to_DER_cert(old_cert.read_text())).hexdigest()
    new_pin=hashlib.sha256(ssl.PEM_cert_to_DER_cert(new_cert.read_text())).hexdigest()
    if kind=='aikey':
        config=initialize(path,root,controller_host='127.0.0.1')
        config['controller']['expected_fingerprint']=old_pin
        store=ConfigurationStore(path)
        profile='aikey'
    else:
        ensure_identity_certificate(root,'020000000088')
        atomic_private(root/'identity.json',json.dumps({'mac':'020000000088'}))
        config={'mac':'020000000088','controller_ip':'192.168.50.1','controller_pin':old_pin,
                'device_ip':'192.168.50.20','firmware_version':'5.1.12'}
        store=AiPortConfigurationStore(path)
        profile='aiport:fixture'
    atomic_private(root/'controller-ca.pem',old_cert.read_bytes())
    atomic_private(path,json.dumps(config))
    workflow=DeviceWorkflowStore(profile,store,root)
    selection={'host':'127.0.0.1' if kind=='aikey' else '192.168.50.1',
               'certificate_pem':new_cert.read_text(),'fingerprint':new_pin}
    return workflow,path,selection


def revision(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_journal_publication_ack_loss_is_uncertain_before_config_write(tmp_path,kind,monkeypatch):
    workflow,path,selection=setup(tmp_path,kind)
    before=path.read_bytes()
    preview=workflow.preview_controller(revision(path),selection)
    original=module.atomic_private
    def lost_ack(target,raw):
        original(target,raw)
        if target==workflow.journal:
            raise OSError('synthetic journal publication acknowledgement loss')
    monkeypatch.setattr(module,'atomic_private',lost_ack)
    with pytest.raises(DeviceOutcomeUncertain):
        workflow.apply(preview,acknowledgements=set(preview.required_consents))
    assert path.read_bytes()==before and fence.read(path)['state']=='prepared'
    monkeypatch.setattr(module,'atomic_private',original)
    result=workflow.reconcile(revision(path))
    assert result['outcome']=='pre_save_revision_observed_intent_retired'
    assert path.read_bytes()==before


def apply(workflow,path,selection):
    preview=workflow.preview_controller(revision(path),selection)
    return workflow.apply(preview,acknowledgements=set(preview.required_consents))


def test_saved_host_change_recomputes_runtime_media_origins_without_retargeting_other_roles(tmp_path):
    workflow,path,selection=setup(tmp_path,'aikey')
    before=json.loads(path.read_bytes())
    selection=dict(selection,host='controller.fixture.invalid')
    apply(workflow,path,selection)
    saved=json.loads(path.read_bytes())
    assert saved['controller_origins']==before['controller_origins']
    actual,source_revision=load_startup_config(path)
    assert source_revision==revision(path)
    assert all('controller.fixture.invalid' in origin for origin in actual['controller_origins'])
    assert actual['controller_media_origin'].startswith('https://controller.fixture.invalid:')
    assert saved['inference']==before['inference'] and saved['embeddings']==before['embeddings']


def test_selected_port_trust_verifies_canonical_pem_without_rotating_identity(tmp_path):
    from aikey.aiport_instance_state import _verify_existing
    workflow,path,selection=setup(tmp_path,'aiport')
    apply(workflow,path,selection)
    config=json.loads(path.read_bytes())
    expected={name:config[name] for name in ('controller_ip','device_ip','controller_pin','firmware_version')}
    before={name:(path.parent/name).read_bytes() for name in ('device.crt','device.key','identity.json')}
    _verify_existing(path.parent,expected,selection['certificate_pem'].replace('\n','\r\n').encode())
    assert all((path.parent/name).read_bytes()==raw for name,raw in before.items())


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_review_save_readback_and_byte_exact_rollback_preserve_adopted_identity_files(tmp_path,kind):
    workflow,path,selection=setup(tmp_path,kind)
    before=path.read_bytes()
    static={n:(path.parent/n).read_bytes() for n in ('device.crt','device.key','identity.json','controller-ca.pem')}
    preview=workflow.preview_controller(revision(path),selection)
    assert path.read_bytes()==before and not (path.parent/DIRECTORY).exists()
    public=json.dumps(preview.public())
    assert 'certificate_pem' not in public and 'BEGIN CERTIFICATE' not in public
    assert '_encoded' not in repr(preview) and selection['certificate_pem'] not in repr(preview)
    result=workflow.apply(preview,acknowledgements=set(preview.required_consents))
    assert result['outcome']=='saved_and_read_back' and result['activation']=='saved_not_active_runtime'
    assert revision(path)==preview.resulting_revision
    current=json.loads(path.read_bytes())
    assert selected(current,kind=kind,state_dir=path.parent).fingerprint==selection['fingerprint']
    assert all((path.parent/n).read_bytes()==raw for n,raw in static.items())
    assert workflow.transaction_status()['rollback_available']
    undo=workflow.preview_rollback(revision(path))
    workflow.rollback(undo,acknowledgements=set(undo.required_consents))
    assert path.read_bytes()==before
    assert all((path.parent/n).read_bytes()==raw for n,raw in static.items())
    assert list((path.parent/DIRECTORY).iterdir())  # retained; no trust/key deletion
    assert fence.read(path)['state']=='rolled_back' and not workflow.transaction_status()['rollback_available']


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_missing_consent_stale_config_or_forged_review_never_publishes(tmp_path,kind):
    workflow,path,selection=setup(tmp_path,kind)
    preview=workflow.preview_controller(revision(path),selection)
    with pytest.raises(DeviceWorkflowError):
        workflow.apply(preview,acknowledgements=set())
    with pytest.raises(DeviceRevisionConflict):
        workflow.apply(replace(preview,_encoded=b'fictional altered candidate'),
                       acknowledgements=set(preview.required_consents))
    current=json.loads(path.read_bytes())
    if kind=='aikey':
        current['worker']['max_queue']=3
    else:
        current['firmware_version']='5.1.13'
    atomic_private(path,json.dumps(current))
    saved=path.read_bytes()
    with pytest.raises(DeviceRevisionConflict):
        workflow.apply(preview,acknowledgements=set(preview.required_consents))
    assert path.read_bytes()==saved and not (path.parent/DIRECTORY).exists() and not workflow.journal.exists()


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_uncertain_published_save_fences_other_writes_and_startup_until_explicit_reconciliation(tmp_path,kind,monkeypatch):
    workflow,path,selection=setup(tmp_path,kind)
    before=path.read_bytes()
    preview=workflow.preview_controller(revision(path),selection)
    original=module.atomic_private
    def lost_ack(target,raw):
        original(target,raw)
        if target==path:
            raise OSError('synthetic lost publication acknowledgement')
    monkeypatch.setattr(module,'atomic_private',lost_ack)
    with pytest.raises(DeviceOutcomeUncertain):
        workflow.apply(preview,acknowledgements=set(preview.required_consents))
    assert revision(path)==preview.resulting_revision and fence.read(path)['state']=='prepared'
    assert workflow.transaction_status()['pending'] and workflow.transaction_status()['reconcile_available']
    with pytest.raises(fence.DeviceIntentError):
        fence.check(path)
    with pytest.raises(UnsafeConfigurationChange if kind=='aikey' else AiPortUnsafePolicyChange):
        with workflow.store._locked():
            pytest.fail('unreconciled configuration writer admitted')
    if kind=='aikey':
        with pytest.raises(ConfigError):
            load_startup_config(path)
    else:
        with pytest.raises(CandidateError,match='reconciliation'):
            CandidateService.from_configuration_file(path)
    persisted=path.read_bytes()
    resolved=workflow.reconcile(revision(path))
    assert resolved['configuration_rewritten'] is False and resolved['runtime_restarted'] is False
    assert path.read_bytes()==persisted and fence.read(path)['state']=='saved'
    fence.check(path)
    monkeypatch.setattr(module,'atomic_private',original)
    undo=workflow.preview_rollback(revision(path))
    workflow.rollback(undo,acknowledgements=set(undo.required_consents))
    assert path.read_bytes()==before


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_abandoned_prewrite_attempt_retains_previous_successful_undo(tmp_path,kind,monkeypatch):
    workflow,path,selection=setup(tmp_path,kind)
    baseline=path.read_bytes()
    apply(workflow,path,selection)
    prior=fence.read(path)
    saved=path.read_bytes()
    alternate=tmp_path/'alternate'
    alternate.mkdir(mode=0o700)
    cert,_=ensure_identity_certificate(alternate,'020000000077')
    selection=dict(selection,certificate_pem=cert.read_text(),
                   fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest())
    preview=workflow.preview_controller(revision(path),selection)
    original=module.atomic_private
    def refused(target,raw):
        if target==path:
            raise OSError('synthetic prewrite refusal')
        original(target,raw)
    monkeypatch.setattr(module,'atomic_private',refused)
    with pytest.raises(DeviceOutcomeUncertain):
        workflow.apply(preview,acknowledgements=set(preview.required_consents))
    assert path.read_bytes()==saved
    assert fence.read(path)['previous']['before']==prior['before']
    workflow.reconcile(revision(path))
    assert fence.read(path)==prior and workflow.transaction_status()['rollback_available']
    monkeypatch.setattr(module,'atomic_private',original)
    undo=workflow.preview_rollback(revision(path))
    workflow.rollback(undo,acknowledgements=set(undo.required_consents))
    assert path.read_bytes()==baseline


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_later_unrelated_changes_cannot_be_overwritten_by_device_undo(tmp_path,kind):
    workflow,path,selection=setup(tmp_path,kind)
    apply(workflow,path,selection)
    current=json.loads(path.read_bytes())
    if kind=='aikey':
        workflow.store.apply(revision(path),{'worker':{'max_queue':3}})
    else:
        current['firmware_version']='5.1.13'
        atomic_private(path,json.dumps(current))
    saved=path.read_bytes()
    with pytest.raises(DeviceRevisionConflict):
        workflow.preview_rollback(revision(path))
    assert not workflow.transaction_status()['rollback_available'] and path.read_bytes()==saved


def test_separate_search_certificate_is_reviewed_and_retained_in_its_own_channel(tmp_path):
    workflow,path,selection=setup(tmp_path,'aikey')
    search_root=tmp_path/'search'
    search_root.mkdir(mode=0o700)
    cert,_=ensure_identity_certificate(search_root,'020000000055')
    search_pin=hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    atomic_private(path.parent/'controller-search-ca.pem',cert.read_bytes())
    config=json.loads(path.read_bytes())
    config['controller'].update(search_ca_file=str(path.parent/'controller-search-ca.pem'),
                                search_expected_fingerprint=search_pin)
    atomic_private(path,json.dumps(config))
    before=path.read_bytes()
    preview=workflow.preview_controller(revision(path),selection)
    assert preview.fingerprints==(selection['fingerprint'],search_pin)
    workflow.apply(preview,acknowledgements=set(preview.required_consents))
    current=json.loads(path.read_bytes())
    assert selected(current,kind='aikey',state_dir=path.parent,channel='search').fingerprint==search_pin
    from aikey.tls import client_context
    context=client_context(current,ca_file=current['controller']['search_ca_file'],channel='search')
    roots={hashlib.sha256(raw).hexdigest() for raw in context.get_ca_certs(binary_form=True)}
    assert search_pin in roots and selection['fingerprint'] not in roots
    undo=workflow.preview_rollback(revision(path))
    workflow.rollback(undo,acknowledgements=set(undo.required_consents))
    assert path.read_bytes()==before


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_uncertain_rollback_is_reconciled_without_repeating_the_replacement(tmp_path,kind,monkeypatch):
    workflow,path,selection=setup(tmp_path,kind)
    baseline=path.read_bytes()
    apply(workflow,path,selection)
    undo=workflow.preview_rollback(revision(path))
    original=module.atomic_private
    writes=[]
    def lost_ack(target,raw):
        original(target,raw)
        if target==path:
            writes.append(raw)
            raise OSError('synthetic lost rollback acknowledgement')
    monkeypatch.setattr(module,'atomic_private',lost_ack)
    with pytest.raises(DeviceOutcomeUncertain):
        workflow.rollback(undo,acknowledgements=set(undo.required_consents))
    assert path.read_bytes()==baseline and len(writes)==1
    assert fence.read(path)['operation']=='controller_rollback'
    workflow.reconcile(revision(path))
    assert path.read_bytes()==baseline and len(writes)==1
    assert fence.read(path)['state']=='rolled_back'


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_changed_identity_refuses_the_old_review_before_any_trust_publication(tmp_path,kind):
    workflow,path,selection=setup(tmp_path,kind)
    preview=workflow.preview_controller(revision(path),selection)
    original=path.read_bytes()
    atomic_private(path.parent/'identity.json',json.dumps({'mac':'020000000001'}))
    with pytest.raises(DeviceWorkflowError):
        workflow.apply(preview,acknowledgements=set(preview.required_consents))
    assert path.read_bytes()==original and not (path.parent/DIRECTORY).exists()


@pytest.mark.parametrize('host',['https://controller.invalid','controller.invalid:7442',' a.test',
    'a..test','-a.test','a-.test','999.1.2.3','127.1','a'*64+'.test','','abc@127.0.0.1'])
def test_controller_authority_is_a_bounded_host_not_url_port_or_ambiguous_numeric_address(tmp_path,host):
    workflow,path,selection=setup(tmp_path,'aikey')
    original=path.read_bytes()
    with pytest.raises(DeviceWorkflowError,match='invalid_controller_settings'):
        workflow.preview_controller(revision(path),dict(selection,host=host))
    assert path.read_bytes()==original and not (path.parent/DIRECTORY).exists()
