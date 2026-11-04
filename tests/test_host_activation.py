"""Late-start reservation across file writers/startup/backup, synthetic only."""
import hashlib
import json
import os

import pytest

from aikey import host_activation as module
from aikey.aiport_candidate import CandidateError,CandidateService
from aikey.config import atomic_private,load_startup_config,ConfigError
from aikey.host_activation import (HostActivationError,SCHEMA,read,publish,path,
    check_writable,check_startup)
from aikey.state_backup_preflight import inventory,validate
from test_device_workflow_backup import fixture
from test_device_workflow import revision
from test_state_backup_preflight import backup_of


def record(config,**changes):
    return {'schema':SCHEMA,'operation':'f'*32,'authority':'a'*64,'revision':'b'*64,
        'configuration_revision':revision(config),'phase':'prepared'}|changes


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_prepared_reservation_fences_normal_and_device_reconciliation_writers(tmp_path,kind):
    workflow,config,_,_=fixture(tmp_path,kind)
    prepared=record(config)
    publish(config,prepared)
    before=config.read_bytes()
    for arguments in ({},{'allow_device_pending':True}):
        with pytest.raises((ValueError,RuntimeError),match='host activation'):
            with workflow.store._locked(**arguments):
                pytest.fail('ordinary writer bypassed pending host activation')
    assert config.read_bytes()==before
    status=workflow.transaction_status()
    assert status['host_activation_pending'] and not status['rollback_available']
    # Host-only metadata settlement is admitted under the same OS lock.
    with workflow.store._locked(allow_host_pending=True):
        publish(config,dict(prepared,phase='settled'))
    with workflow.store._locked():
        pass
    assert not workflow.transaction_status()['host_activation_pending']


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_startup_accepts_exact_reviewed_bytes_but_not_later_unreviewed_change(tmp_path,kind,monkeypatch):
    _,config,_,_=fixture(tmp_path,kind)
    publish(config,record(config))
    initialized=[]
    def inert(self,value,directory,**kwargs):
        initialized.append(kwargs['startup_revision'])
    monkeypatch.setattr(CandidateService,'__init__',inert)
    if kind=='aikey':
        _,loaded=load_startup_config(config)
        assert loaded==revision(config)
    else:
        CandidateService.from_configuration_file(config)
        assert initialized==[revision(config)]
    changed=json.loads(config.read_bytes())
    if kind=='aikey':
        changed['worker']['max_queue']=7
    else:
        changed['firmware_version']='5.1.13'
    atomic_private(config,json.dumps(changed))
    with pytest.raises(ConfigError if kind=='aikey' else CandidateError):
        load_startup_config(config) if kind=='aikey' else CandidateService.from_configuration_file(config)
    assert len(initialized)==(0 if kind=='aikey' else 1)


def test_another_operation_or_authority_cannot_settle_a_prepared_reservation(tmp_path):
    _,config,_,_=fixture(tmp_path,'aiport')
    original=record(config)
    publish(config,original)
    before=path(config).read_bytes()
    for changes in ({'operation':'e'*32},{'authority':'c'*64},{'configuration_revision':'d'*64}):
        with pytest.raises(HostActivationError,match='another_intent'):
            publish(config,dict(original,phase='settled',**changes))
        assert path(config).read_bytes()==before


@pytest.mark.parametrize('fault',['mode','symlink','hardlink','schema','operation_type','nonfinite'])
def test_corrupt_or_unbound_reservation_never_becomes_absence(tmp_path,fault):
    _,config,_,_=fixture(tmp_path,'aiport')
    value=record(config)
    if fault=='schema':
        value['schema']='unknown'
    elif fault=='operation_type':
        value['operation']=int('1'*32)
    raw=json.dumps(value)
    if fault=='nonfinite':
        raw=raw[:-1]+', "unknown":1e999}'
    atomic_private(path(config),raw)
    if fault=='mode':
        path(config).chmod(0o644)
    elif fault=='symlink':
        target=config.parent/'retained-reservation'
        path(config).rename(target)
        path(config).symlink_to(target)
    elif fault=='hardlink':
        os.link(path(config),config.parent/'retained-hardlink')
    with pytest.raises(HostActivationError):
        read(config)
    with pytest.raises(HostActivationError):
        check_writable(config)
    with pytest.raises(HostActivationError):
        check_startup(config,revision(config))


def test_bad_producer_record_refuses_before_any_marker_write(tmp_path):
    _,config,_,_=fixture(tmp_path,'aiport')
    with pytest.raises(HostActivationError):
        publish(config,record(config,operation=123))
    assert not path(config).exists()


def test_pending_backup_is_blocked_and_settled_marker_cannot_be_hidden_by_manifest(tmp_path):
    _,config,_,profiles=fixture(tmp_path,'aiport')
    value=record(config)
    publish(config,value)
    assert not inventory(profiles)['ready_for_backup']
    publish(config,dict(value,phase='settled'))
    assert inventory(profiles)['ready_for_backup']
    backup=backup_of(tmp_path,profiles)
    assert validate(backup,profiles)['restorable']
    label=next(iter(profiles))
    target=backup/label.replace(':','_')
    name=path(config).name
    (target/name).unlink()
    manifest=json.loads((backup/'manifest.json').read_bytes())
    del manifest['profiles'][label]['files'][name]
    (backup/'manifest.json').write_text(json.dumps(manifest))
    assert not validate(backup,profiles)['restorable']


def test_lost_marker_publication_ack_retains_fence_without_changing_config(tmp_path,monkeypatch):
    _,config,_,_=fixture(tmp_path,'aiport')
    original=module.atomic_private
    before=hashlib.sha256(config.read_bytes()).hexdigest()
    def lost(target,raw):
        original(target,raw)
        raise OSError('fictional marker publication acknowledgement lost')
    monkeypatch.setattr(module,'atomic_private',lost)
    with pytest.raises(OSError):
        publish(config,record(config))
    assert read(config)['phase']=='prepared' and hashlib.sha256(config.read_bytes()).hexdigest()==before
    with pytest.raises(HostActivationError):
        check_writable(config)
