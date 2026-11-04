"""Invented host/slot inputs only; planning never executes or changes identity."""
from copy import deepcopy
import hashlib
import json
import socket
import shutil

import pytest

from aikey.aiport_apple_plan import build_manifest,create_arguments,encode
from aikey.config import initialize,atomic_private
from test_aiport_nas_reconcile import inputs


def fixture(tmp_path):
    plan,states,options,_,report=inputs(tmp_path,selected_slot=1)
    key=tmp_path/'key'
    key.mkdir(mode=0o700)
    config=initialize(key/'config.json',key,controller_host=options['controller_ip'])
    config['device']['ip']=plan['ai_key']['host_ip']
    atomic_private(key/'config.json',json.dumps(config))
    settings={name:options[name] for name in ('controller_ip','controller_pin','uid','gid')}
    settings.update(image='example.invalid/local-aiport@sha256:'+'e'*64,
                    dns='192.168.10.1',aikey_state=key)
    return plan,report,states,settings


def hashes(root):
    return {str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def test_manifest_is_pure_reproducible_complete_and_preserves_both_identities(tmp_path,monkeypatch):
    plan,report,states,settings=fixture(tmp_path)
    before=hashes(tmp_path)
    def forbidden(*args,**kwargs):
        pytest.fail('pure manifest opened a network connection')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    result=build_manifest(plan,report,states,**settings)
    assert result==build_manifest(plan,report,states,**settings)
    assert before==hashes(tmp_path)
    assert result['aikey']['host_ip']==plan['ai_key']['host_ip']
    assert result['native_pairing']=='disabled' and not result['models_loaded']
    assert len(result['instances'])==1
    row=result['instances'][0]
    assert row['slot']==1 and row['management_tcp']==443 and row['container_tcp']==8443
    assert row['host_ip']==plan['instances'][0]['host_ip']
    assert row['mac']==json.loads((states[1]/'config.json').read_bytes())['mac']
    assert row['config_sha256']==hashlib.sha256((states[1]/'config.json').read_bytes()).hexdigest()
    assert row['identity']['device.key']==hashlib.sha256((states[1]/'device.key').read_bytes()).hexdigest()
    unhashed=deepcopy(result)
    assert unhashed.pop('revision')==hashlib.sha256(encode(unhashed)).hexdigest()


def test_only_fixed_reviewed_argv_and_own_mounts_no_extra_stream_listener(tmp_path):
    plan,report,states,settings=fixture(tmp_path)
    models=tmp_path/'models'
    models.mkdir(mode=0o700)
    manifest=build_manifest(plan,report,states,**settings,models_dir=models)
    args=create_arguments(manifest,manifest['instances'][0],'f'*32)
    assert args[0]=='create' and '--rm' not in args
    assert args[args.index('--publish')+1]==plan['instances'][0]['host_ip']+':443:8443/tcp'
    assert args.count('--publish')==1 and '10001' not in args and '7447' not in args
    assert str(states[1])+':/state' in args and str(models)+':/models:ro' in args
    assert str(settings['aikey_state']) not in ' '.join(args)
    assert '--read-only' in args and '--entrypoint' in args and settings['image'] in args


@pytest.mark.parametrize('changes',[{'uid':0},{'gid':0},{'uid':True},{'cpus':True},
    {'cpus':0},{'cpus':33},{'memory_mib':255},{'memory_mib':32769},
    {'image':'example.invalid/local-aiport:mutable'},{'image':'-command@sha256:'+'e'*64},
    {'dns':'127.0.0.1'},{'dns':'public-resolver.invalid'},{'dns':'8.8.8.8'},
    {'name_prefix':'../../escape'},{'controller_pin':'not-a-pin'}])
def test_invalid_operator_options_refuse_without_changes(tmp_path,changes):
    plan,report,states,settings=fixture(tmp_path)
    before=hashes(tmp_path)
    with pytest.raises(ValueError):
        build_manifest(plan,report,states,**(settings|changes))
    assert before==hashes(tmp_path)


@pytest.mark.parametrize('fault',['stale','moved_camera','wrong_address','duplicate_state','missing_state',
    'no_key','wrong_key','model_contains_state','model_inside_state','symlink_model','public_state'])
def test_unreviewed_inventory_identity_and_cross_profile_mounts_refuse(tmp_path,fault):
    plan,report,states,settings=fixture(tmp_path)
    extra={}
    if fault=='stale':
        report['fetched_at']-=301
    elif fault=='moved_camera':
        report['cameras'].pop()
    elif fault=='wrong_address':
        plan['instances'][0]['host_ip']='192.168.10.200'
        plan['instances'][0]['apple_publish']='192.168.10.200:443:8443/tcp'
    elif fault=='duplicate_state':
        states[2]=states[1]
    elif fault=='missing_state':
        states[1]=tmp_path/'does-not-exist'
    elif fault=='no_key':
        settings['aikey_state']=None
    elif fault=='wrong_key':
        config=json.loads((settings['aikey_state']/'config.json').read_bytes())
        config['device']['ip']='192.168.10.201'
        atomic_private(settings['aikey_state']/'config.json',json.dumps(config))
    elif fault=='model_contains_state':
        extra['models_dir']=states[1].parent
    elif fault=='model_inside_state':
        models=states[1]/'models'
        models.mkdir(mode=0o700)
        extra['models_dir']=models
    elif fault=='symlink_model':
        (tmp_path/'alias').symlink_to(settings['aikey_state'],target_is_directory=True)
        extra['models_dir']=tmp_path/'alias'
    else:
        states[1].chmod(0o755)
    before=hashes(tmp_path)
    with pytest.raises((OSError,ValueError,RuntimeError)):
        build_manifest(plan,report,states,**settings,**extra)
    assert hashes(tmp_path)==before
    assert not (tmp_path/'does-not-exist').exists()


def test_no_key_profile_does_not_require_or_invent_key_identity(tmp_path):
    plan,report,states,settings=fixture(tmp_path)
    plan['ai_key']['host_ip']=None
    settings['aikey_state']=None
    result=build_manifest(plan,report,states,**settings)
    assert result['aikey'] is None


def test_new_runtime_private_reference_is_checked_before_any_host_start(tmp_path):
    plan,report,states,settings=fixture(tmp_path)
    config=json.loads((states[1]/'config.json').read_bytes())
    config['face_model_space_ref']='port-face-space.'+'f'*64+'.json'
    atomic_private(states[1]/'config.json',json.dumps(config))
    before=hashes(tmp_path)
    with pytest.raises((ValueError,OSError)):
        build_manifest(plan,report,states,**settings)
    assert before==hashes(tmp_path)


@pytest.mark.parametrize('target',['second_port','key'])
def test_copied_identity_or_private_key_cannot_cross_profile_slots(tmp_path,target):
    plan,report,states,settings=fixture(tmp_path)
    source=states[1]
    if target=='second_port':
        destination=source.parent/'slot-2'
        states[2]=destination
        config=json.loads((destination/'config.json').read_bytes())
        config['mac']=json.loads((source/'config.json').read_bytes())['mac']
    else:
        destination=settings['aikey_state']
        config=json.loads((destination/'config.json').read_bytes())
        config['device']['mac']=json.loads((source/'config.json').read_bytes())['mac']
    atomic_private(destination/'config.json',json.dumps(config))
    for name in ('device.key','device.crt','identity.json'):
        shutil.copyfile(source/name,destination/name)
        (destination/name).chmod(0o600)
    before=hashes(tmp_path)
    with pytest.raises(ValueError,match='share_identity'):
        build_manifest(plan,report,states,**settings)
    assert before==hashes(tmp_path)
