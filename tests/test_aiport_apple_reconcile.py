"""Fictional documented CLI state machine, never an installed Mac runtime."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from aikey.aiport_apple_plan import build_manifest
from aikey.aiport_apple_reconcile import (AppleRuntimeError,AppleOutcomeUncertain,
    reconcile,read_receipt,settle,qualified_version,image_info,listing)
from aikey import aiport_apple_reconcile as module
from test_aiport_apple_plan import fixture,hashes

ENV=['PATH=/usr/local/bin:/usr/bin:/bin','PYTHONDONTWRITEBYTECODE=1']


def entry(row,manifest,*,state='running',operation=None):
    labels={'org.aikey.instance':row['certificate_sha256']}
    if operation:
        labels['org.aikey.operation']=operation
    return {'id':row['name'],'status':{'state':state},'configuration':{
        'id':row['name'],'image':{'reference':manifest['image']},'labels':labels,'readOnly':True,
        'initProcess':{'executable':'local-aiport-candidate','arguments':row['argv'],
            'environment':ENV,'workingDirectory':'/app',
            'user':{'raw':{'userString':f"{manifest['uid']}:{manifest['gid']}"}}},
        'resources':{'cpus':manifest['cpus'],'memoryInBytes':manifest['memory_mib']<<20},
        'networks':[{'network':'default','options':{}}],'dns':{'nameservers':[manifest['dns']]},
        'publishedPorts':[{'hostAddress':row['host_ip'],'hostPort':443,'containerPort':8443,'proto':'tcp','count':1}],
        'mounts':[{'source':'tmpfs','destination':'/tmp','type':{'tmpfs':{}},
            'options':['rw','noexec','nosuid','size=64m']}]+[
            {'source':m['source'],'destination':m['destination'],'type':{'virtiofs':{}},
             'options':['ro'] if m['read_only'] else []} for m in row['volumes']]}}


class Host:
    def __init__(self,manifest,rows=()):
        self.manifest=manifest
        self.rows={r['id']:deepcopy(r) for r in rows}
        self.calls=[]
        self.probes=[]
        self.fail=None
        self.key_alive=True
        self.ready=True
        self.version='1.6.1'
        self.host_ready=True

    def run(self,argv,timeout):
        assert 0<timeout<=90
        args=argv[1:]
        self.calls.append(args)
        if args==['--version']:
            return 'container CLI version '+self.version+'\n'
        if args[:2]==['image','inspect']:
            return json.dumps([{'variants':[{'platform':{'os':'linux','architecture':'arm64'},
                'config':{'os':'linux','architecture':'arm64',
                    'config':{'Env':ENV,'WorkingDir':'/app','Labels':{'org.local-aikey.host-activation':'1'}}}}]}])
        if args[:1]==['list']:
            return json.dumps(list(self.rows.values()))
        if args[:1]==['create']:
            name=args[args.index('--name')+1]
            operation=next(arg.split('=',1)[1] for arg in args if arg.startswith('org.aikey.operation='))
            assert name not in self.rows
            row=next(r for r in self.manifest['instances'] if r['name']==name)
            self.rows[name]=entry(row,self.manifest,state='stopped',operation=operation)
            if self.fail=='create_ack':
                raise AppleRuntimeError('fictional create acknowledgement lost')
            return ''
        if args[:1]==['start']:
            self.rows[args[-1]]['status']['state']='running'
            if self.fail=='start_ack':
                raise AppleRuntimeError('fictional start acknowledgement lost')
            return ''
        if args[:1]==['stop']:
            assert args[1:3]==['--time','10']
            self.rows[args[-1]]['status']['state']='stopped'
            if self.fail=='stop_ack':
                raise AppleRuntimeError('fictional stop acknowledgement lost')
            return ''
        raise AssertionError(args)

    def probe(self,kind,instance):
        self.probes.append((kind,instance['host_ip']))
        if kind=='aiport' and not self.ready:
            raise OSError('fictional unavailable own peer')
        value={'adopted':True,'connected':self.key_alive} if kind=='aikey' else {
            'adopted':False,'control_connected':False}
        return {'observation':'runtime_health','tls_ca_and_leaf_pin_verified':True,
            'host_activation_schema':'aikey-host-activation/1',
            'runtime_configuration_revision':instance['config_sha256'],'runtime':value}

    def check(self,instance):
        assert instance['management_tcp']==443 and instance['container_tcp']==8443
        return self.host_ready

    def mutations(self):
        return [args for args in self.calls if args[0] in ('create','start','stop','delete','rm')]


def actor_fixture(tmp_path):
    plan,report,states,options=fixture(tmp_path)
    manifest=build_manifest(plan,report,states,**options)
    state=tmp_path/'host-controller'
    state.mkdir(mode=0o700)
    return manifest,plan,report,states,options,state


def invoke(inputs,host,**extra):
    return reconcile(*inputs,run=host.run,probe=host.probe,
        host_check=host.check,pause=lambda _:None,**extra)


def test_read_only_default_no_lock_receipt_or_identity_changes(tmp_path):
    inputs=actor_fixture(tmp_path)
    host=Host(inputs[0])
    before=hashes(tmp_path)
    result=invoke(inputs,host)
    assert result['mode']=='dry_run' and result['would_start']==[inputs[0]['instances'][0]['name']]
    assert not host.mutations() and hashes(tmp_path)==before
    assert not list(inputs[-1].iterdir())


def test_actual_actor_create_start_readback_then_noop_preserves_all_state(tmp_path):
    inputs=actor_fixture(tmp_path)
    host=Host(inputs[0])
    protected={str(path):path.read_bytes() for root in [*inputs[3].values(),inputs[4]['aikey_state']]
               for path in root.rglob('*') if path.is_file()}
    result=invoke(inputs,host,apply=True)
    name=inputs[0]['instances'][0]['name']
    assert result['mode']=='applied' and result['started']==[name]
    assert [args[0] for args in host.mutations()]==['create','start']
    receipt=read_receipt(inputs[-1])
    assert receipt['phase']=='completed' and all(action['state']=='observed' for action in receipt['actions'])
    assert all(Path(name).read_bytes()==raw for name,raw in protected.items())
    host.calls=[]
    result=invoke(inputs,host,apply=True)
    assert result['mode']=='unchanged' and result['preserved']==[name] and not host.mutations()


@pytest.mark.parametrize('fault',['private_mount','wildcard_port','unknown_port','image','user','writable_root',
    'environment','version','host_preflight','key_offline','config_changed','receipt_unavailable'])
def test_preflight_refusal_never_changes_prior_running_container(tmp_path,fault):
    inputs=actor_fixture(tmp_path)
    row=inputs[0]['instances'][0]
    host=Host(inputs[0])
    if fault in ('private_mount','wildcard_port','unknown_port'):
        foreign=entry(row,inputs[0])
        foreign['id']=foreign['configuration']['id']='unrelated-existing-service'
        foreign['configuration']['mounts']=[]
        foreign['configuration']['publishedPorts']=[]
        if fault=='private_mount':
            foreign['configuration']['mounts']=[{'source':str(Path(row['state_dir']).parent)}]
        else:
            foreign['configuration']['publishedPorts']=[{'hostPort':443,'hostAddress':'0.0.0.0' if fault=='wildcard_port' else None}]
        host.rows[foreign['id']]=foreign
    elif fault in ('image','user','writable_root','environment'):
        own=entry(row,inputs[0],state='stopped')
        if fault=='image':
            own['configuration']['image']['reference']='unreviewed:image'
        elif fault=='user':
            own['configuration']['initProcess']['user']['raw']['userString']='0:0'
        elif fault=='writable_root':
            own['configuration']['readOnly']=False
        else:
            own['configuration']['initProcess']['environment']=ENV+['FAKE_FIXTURE_VALUE=unreviewed']
        host.rows[own['id']]=own
    elif fault=='version':
        host.version='1.6.2'
    elif fault=='host_preflight':
        host.host_ready=False
    elif fault=='key_offline':
        host.key_alive=False
    elif fault=='config_changed':
        path=inputs[3][1]/'config.json'
        config=json.loads(path.read_bytes())
        config['firmware_version']='5.1.13'
        path.write_text(json.dumps(config))
        path.chmod(0o600)
    else:
        (inputs[-1]/'reconcile.json').write_text('invalid synthetic journal')
        (inputs[-1]/'reconcile.json').chmod(0o600)
    before=deepcopy(host.rows)
    with pytest.raises((ValueError,OSError)):
        invoke(inputs,host,apply=True)
    assert host.rows==before and not host.mutations()


@pytest.mark.parametrize('fault',['create_ack','start_ack'])
def test_lost_daemon_ack_fences_reapply_without_blind_retry_or_stop(tmp_path,fault):
    inputs=actor_fixture(tmp_path)
    host=Host(inputs[0])
    host.fail=fault
    with pytest.raises(AppleOutcomeUncertain):
        invoke(inputs,host,apply=True)
    receipt=read_receipt(inputs[-1])
    assert receipt['phase']=='uncertain' and receipt['actions'][-1]['state']=='requested'
    actions=deepcopy(host.mutations())
    assert not any(args[0] in ('stop','delete','rm') for args in actions)
    with pytest.raises(AppleOutcomeUncertain):
        invoke(inputs,host,apply=True)
    assert host.mutations()==actions
    assert invoke(inputs,host)['fenced_by_pending_intent']


def test_failed_ready_stops_only_own_start_keeps_container_and_identity(tmp_path):
    inputs=actor_fixture(tmp_path)
    host=Host(inputs[0])
    host.ready=False
    with pytest.raises(AppleRuntimeError,match='rolled_back'):
        invoke(inputs,host,apply=True)
    assert read_receipt(inputs[-1])['phase']=='rolled_back'
    assert [args[0] for args in host.mutations()]==['create','start','stop']
    assert all(row['status']['state']=='stopped' for row in host.rows.values())
    assert (inputs[3][1]/'device.key').exists()


def test_lost_final_receipt_does_not_automatically_stop_successful_new_runtime(tmp_path,monkeypatch):
    inputs=actor_fixture(tmp_path)
    host=Host(inputs[0])
    original=module.save_receipt
    def lose(directory,value):
        original(directory,value)
        if value['phase']=='completed':
            raise OSError('fictional terminal receipt acknowledgement lost')
    monkeypatch.setattr(module,'save_receipt',lose)
    with pytest.raises(AppleOutcomeUncertain):
        invoke(inputs,host,apply=True)
    assert [args[0] for args in host.mutations()]==['create','start']
    assert all(row['status']['state']=='running' for row in host.rows.values())
    assert read_receipt(inputs[-1])['phase']=='uncertain'


@pytest.mark.parametrize('fault',['create_ack','start_ack'])
def test_explicit_positive_settlement_does_not_retry_and_keeps_original_failure(tmp_path,fault):
    inputs=actor_fixture(tmp_path)
    host=Host(inputs[0])
    host.fail=fault
    with pytest.raises(AppleOutcomeUncertain):
        invoke(inputs,host,apply=True)
    actions=deepcopy(host.mutations())
    result=settle(*inputs,run=host.run,probe=host.probe)
    assert result['mode']=='reconciled' and result['daemon_mutations']==0
    assert result['original_failure_retained']
    assert result['outcome']==('all_running' if fault=='start_ack' else 'no_new_running')
    assert host.mutations()==actions and read_receipt(inputs[-1])['phase']=='reconciled'


@pytest.mark.parametrize('damage',['missing','stopped_start','owner_changed','key_offline','bad_version'])
def test_failed_settlement_keeps_pending_intent_and_does_not_guess_negative_effects(tmp_path,damage):
    inputs=actor_fixture(tmp_path)
    host=Host(inputs[0])
    host.fail='start_ack'
    with pytest.raises(AppleOutcomeUncertain):
        invoke(inputs,host,apply=True)
    name=inputs[0]['instances'][0]['name']
    if damage=='missing':
        del host.rows[name]
    elif damage=='stopped_start':
        host.rows[name]['status']['state']='stopped'
    elif damage=='owner_changed':
        host.rows[name]['configuration']['labels']['org.aikey.operation']='a'*32
    elif damage=='key_offline':
        host.key_alive=False
    else:
        host.version='1.6.1-beta'
    before=(inputs[-1]/'reconcile.json').read_bytes()
    actions=deepcopy(host.mutations())
    with pytest.raises(AppleOutcomeUncertain):
        settle(*inputs,run=host.run,probe=host.probe)
    assert (inputs[-1]/'reconcile.json').read_bytes()==before and host.mutations()==actions


@pytest.mark.parametrize('value',['1.6.2','1.6.1-beta','1.6.1+modified','1.6.10','1.6.1 1.6.1','unknown','1.6.1.extra'])
def test_runtime_version_guard_does_not_admit_unknown_or_ambiguous_builds(value):
    assert not qualified_version('container CLI version '+value)


def test_failed_addition_never_stops_or_recreates_prior_adopted_instance(tmp_path):
    plan,report,states,options=fixture(tmp_path)
    states[2]=states[1].parent/'slot-2'
    manifest=build_manifest(plan,report,states,**options)
    state=tmp_path/'host-controller'
    state.mkdir(mode=0o700)
    original=entry(manifest['instances'][0],manifest,state='running')
    host=Host(manifest,[original])
    def probe(kind,instance):
        if kind=='aiport' and instance['slot']==2:
            raise OSError('fictional new peer unavailable')
        observed=host.probe(kind,instance)
        if kind=='aiport':
            observed['runtime'].update(adopted=True,control_connected=True)
        return observed
    with pytest.raises(AppleRuntimeError,match='rolled_back'):
        reconcile(manifest,plan,report,states,options,state,apply=True,run=host.run,
            probe=probe,host_check=host.check,pause=lambda _:None)
    assert host.rows[original['id']]==original
    mutations=host.mutations()
    assert [command[0] for command in mutations]==['create','start','stop']
    assert all(command[-1]!=original['id'] for command in mutations)
    assert read_receipt(state)['baseline_readiness']=={original['id']:'connected'}


def test_authority_directory_swap_cannot_redirect_intent_or_start_a_runtime(tmp_path):
    inputs=actor_fixture(tmp_path)
    host=Host(inputs[0])
    current=inputs[-1]
    retained=tmp_path/'retained-authority'
    def swapped(instance):
        current.rename(retained)
        current.mkdir(mode=0o700)
        return True
    with pytest.raises(AppleOutcomeUncertain):
        reconcile(*inputs,apply=True,run=host.run,probe=host.probe,host_check=swapped,pause=lambda _:None)
    assert not host.mutations() and not list(current.iterdir())
    assert read_receipt(retained)['phase']=='prepared'


@pytest.mark.parametrize('raw',['null','{}','[{},{}]','[{"variants":null}]',
    '[{"variants":[{"platform":null,"config":{}}]}]',
    '[{"variants":[{"platform":{},"config":null}]}]',
    '[{"variants":[{"platform":{"os":"linux","architecture":"arm64"},"config":{"config":{"Env":[1]}}}]}]',
    '[{"extra":NaN}]','[{"extra":1e999}]','[{"variants":[],"variants":[]}]'])
def test_ambiguous_or_nonfinite_image_json_is_not_a_platform_witness(raw):
    with pytest.raises(AppleRuntimeError):
        image_info(raw,'example.invalid/image@sha256:'+'e'*64)


@pytest.mark.parametrize('raw',['null','{}','[1]','[{"configuration":null}]',
    '[{"configuration":{},"status":{},"unrelated":1e999}]',
    '[{"configuration":{},"configuration":{}}]'])
def test_invalid_whole_host_listing_is_not_treated_as_no_conflicts(raw):
    with pytest.raises(AppleRuntimeError):
        listing(raw)


def test_real_writer_process_is_blocked_during_start_and_after_lost_ack(tmp_path):
    inputs=actor_fixture(tmp_path)
    host=Host(inputs[0])
    host.fail='start_ack'
    code='''
from pathlib import Path
import sys
from aikey import aiport_config_store as module
original=module.acquire_file_lock
module.acquire_file_lock=lambda fd: original(fd,timeout=.1)
try:
    with module.AiPortConfigurationStore(Path(sys.argv[1]))._locked():
        print('entered')
except module.AiPortUnsafePolicyChange:
    print('reservation')
except module.AiPortConfigurationError:
    print('locked')
'''
    def child():
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
        result=subprocess.run([sys.executable,'-c',code,str(inputs[3][1]/'config.json')],
            env=env,capture_output=True,text=True,timeout=4)
        assert result.returncode==0 and not result.stderr
        return result.stdout.strip()
    during=[]
    original=host.run
    def observed(argv,timeout):
        if argv[1]=='start':
            during.append(child())
        return original(argv,timeout)
    with pytest.raises(AppleOutcomeUncertain):
        reconcile(*inputs,apply=True,run=observed,probe=host.probe,host_check=host.check,pause=lambda _:None)
    assert during==['locked'] and child()=='reservation'
    settle(*inputs,run=host.run,probe=host.probe)
    assert child()=='entered'


def test_image_without_startup_reservation_contract_never_gets_created(tmp_path):
    inputs=actor_fixture(tmp_path)
    host=Host(inputs[0])
    original=host.run
    def old_image(argv,timeout):
        value=original(argv,timeout)
        if argv[1:3]==['image','inspect']:
            parsed=json.loads(value)
            del parsed[0]['variants'][0]['config']['config']['Labels']
            return json.dumps(parsed)
        return value
    with pytest.raises(AppleRuntimeError,match='missing_activation_contract'):
        reconcile(*inputs,apply=True,run=old_image,probe=host.probe,host_check=host.check)
    assert not host.mutations()


@pytest.mark.parametrize('field,length',[('operation',32),('revision',64)])
def test_numeric_receipt_fields_cannot_be_coerced_into_an_owned_intent(tmp_path,field,length):
    inputs=actor_fixture(tmp_path)
    host=Host(inputs[0])
    reconcile(*inputs,apply=True,run=host.run,probe=host.probe,host_check=host.check,pause=lambda _:None)
    journal=inputs[-1]/'reconcile.json'
    value=json.loads(journal.read_bytes())
    value[field]=int('1'*length)
    journal.write_text(json.dumps(value))
    before=list(host.mutations())
    with pytest.raises(AppleRuntimeError,match='receipt_unavailable'):
        reconcile(*inputs,apply=True,run=host.run,probe=host.probe,host_check=host.check)
    assert host.mutations()==before


@pytest.mark.parametrize('kind',['equal','child','ancestor','key'])
@pytest.mark.parametrize('apply',[False,True])
def test_host_authority_cannot_be_in_or_overlap_guest_or_key_state(tmp_path,kind,apply):
    inputs=list(actor_fixture(tmp_path))
    root=inputs[3][1]
    if kind=='child':
        root=root/'host-authority'
        root.mkdir(mode=0o700)
    elif kind=='ancestor':
        root=root.parent
    elif kind=='key':
        root=inputs[4]['aikey_state']
    inputs[-1]=root
    before={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    host=Host(inputs[0])
    with pytest.raises(AppleRuntimeError,match='authority_must_be_outside'):
        reconcile(*inputs,apply=apply,run=host.run,probe=host.probe,host_check=host.check)
    assert not host.calls
    assert not (root/'reconcile.json').exists() and not (root/'.writer.lock').exists()
    assert all(path.read_bytes()==raw for path,raw in before.items())
