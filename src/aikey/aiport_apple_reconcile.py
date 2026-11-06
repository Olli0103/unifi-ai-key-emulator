"""Conservative one-shot Apple host actor for already reviewed stable slots.

Only absent/stopped selected containers can be created/started. Existing live
instances, Key, addresses, firewall, pairing and private state are not mutated.
An intent is durable before a daemon mutation; an uncertain receipt fences
another apply. Failed own starts are stopped, never deleted with their data.
"""
from __future__ import annotations

from contextlib import contextmanager,ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

from .aiport_apple_plan import (RUNTIME_VERSION,build_manifest,
    create_arguments,encode,digest,private_directory)
from . import host_activation as activation
from .apple_supervisor import CONTAINER
from .apple_upgrade import run_argv,UpgradeError
from .config import atomic_private
from .device_workflow import _private
from .host_control_store import writer as control_writer
from .protocol import _json_object,_unique_object,_reject_constant,_finite_float

RECEIPT='reconcile.json'
_SCHEMA='aikey-apple-reconcile-receipt/1'
_NAME=re.compile('[a-z][a-z0-9-]{0,62}\\Z')
_TOKEN=re.compile('[0-9a-f]{32}\\Z')
_HASH=re.compile('[0-9a-f]{64}\\Z')
_STATES={'running','stopped','created','exited'}


class AppleRuntimeError(ValueError):
    pass


class AppleOutcomeUncertain(AppleRuntimeError):
    pass


def run_command(argv,timeout):
    # Do not forward model/provider/proxy credentials or container context
    # environment overrides to an unrelated host daemon client.
    env={name:os.environ[name] for name in ('HOME','USER','LOGNAME','TMPDIR') if name in os.environ}
    env['PATH']='/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin'
    try:
        result=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=timeout)
        if result.returncode or len(result.stdout.encode())>2*1024*1024:
            raise ValueError
    except (OSError,ValueError,subprocess.TimeoutExpired):
        raise AppleRuntimeError('apple_host_command_failed') from None
    return result.stdout


def listing(raw):
    if not isinstance(raw,str) or len(raw.encode())>2*1024*1024:
        raise AppleRuntimeError('apple_container_listing_unavailable')
    values=_decode(raw)
    if not isinstance(values,list) or len(values)>256:
        raise AppleRuntimeError('apple_container_listing_unavailable')
    result={}
    for value in values:
        if not isinstance(value,dict) or not isinstance(value.get('configuration'),dict):
            raise AppleRuntimeError('apple_container_listing_unavailable')
        nested=value['configuration'].get('id')
        name=value.get('id',nested)
        if (not isinstance(name,str) or not _NAME.fullmatch(name) or name in result
                or nested is not None and nested!=name
                or not isinstance(value.get('status'),dict)
                or value['status'].get('state') not in _STATES):
            raise AppleRuntimeError('apple_container_listing_ambiguous')
        result[name]=value
    return result


def _decode(raw):
    if not isinstance(raw,str) or len(raw.encode())>2*1024*1024:
        raise AppleRuntimeError('apple_runtime_json_refused')
    try:
        return json.loads(raw,object_pairs_hook=_unique_object,parse_constant=_reject_constant,parse_float=_finite_float)
    except (ValueError,TypeError,RuntimeError):
        raise AppleRuntimeError('apple_runtime_json_refused') from None


def qualified_version(output):
    if not isinstance(output,str) or len(output)>4096:
        return False
    return re.findall(r'(?<![A-Za-z0-9.])v?([0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?)(?![A-Za-z0-9.])',output)==[RUNTIME_VERSION]


def image_info(raw,reference):
    value=_decode(raw)
    if not isinstance(value,list) or len(value)!=1 or not isinstance(value[0],dict):
        raise AppleRuntimeError('apple_image_inspection_unavailable')
    variants=value[0].get('variants')
    if not isinstance(variants,list) or len(variants)!=1 or not isinstance(variants[0],dict):
        raise AppleRuntimeError('apple_image_platform_ambiguous')
    variant=variants[0]
    platform=variant.get('platform',{})
    config=variant.get('config',{})
    if not isinstance(platform,dict) or not isinstance(config,dict) or not isinstance(config.get('config',{}),dict):
        raise AppleRuntimeError('apple_image_inspection_unavailable')
    inner=config.get('config',{})
    if not isinstance(inner.get('Labels'),dict) or inner['Labels'].get(activation.IMAGE_LABEL)!='1':
        raise AppleRuntimeError('apple_image_missing_activation_contract')
    os_name=platform.get('os',config.get('os'))
    arch=platform.get('architecture',config.get('architecture'))
    if ((os_name,arch)!=('linux','arm64') or any(config.get(key,expected)!=expected
            for key,expected in (('os',os_name),('architecture',arch)))
            or not isinstance(inner.get('Env',[]),list)
            or any(not isinstance(item,str) for item in inner.get('Env',[]))):
        raise AppleRuntimeError('apple_image_not_linux_arm64')
    return {'platform':('linux','arm64'),'env':inner.get('Env',[]),
            'workdir':inner.get('WorkingDir') or '/','reference':reference}


def inspect_selected(entry,instance,manifest,image):
    """Reuse existing conservative recreation contracts, without recreating."""
    actual=deepcopy(entry)
    config=actual['configuration']
    labels=config.pop('labels',None)
    if labels is None:
        labels={}
    if (not isinstance(labels,dict) or set(labels)-{'org.aikey.instance','org.aikey.operation'}
            or 'org.aikey.instance' in labels and labels['org.aikey.instance']!=instance['certificate_sha256']
            or 'org.aikey.operation' in labels and (not isinstance(labels['org.aikey.operation'],str)
                or not _TOKEN.fullmatch(labels['org.aikey.operation']))):
        raise AppleRuntimeError('apple_container_owner_drift')
    init=config.get('initProcess',{})
    if not isinstance(init,dict):
        raise AppleRuntimeError('apple_container_process_drift')
    executable=init.get('executable')
    if (executable not in ('local-aiport-candidate','/usr/local/bin/local-aiport-candidate')
            or init.get('environment',[])!=image['env']
            or (init.get('workingDirectory') or image['workdir'])!=image['workdir']):
        raise AppleRuntimeError('apple_container_process_drift')
    expected={'configuration':{'image':{'reference':manifest['image']},
        'readOnly':True,'resources':{'cpus':manifest['cpus'],'memoryInBytes':manifest['memory_mib']<<20},
        'networks':[{'network':'default','options':{}}],
        'dns':{'nameservers':[manifest['dns']]},
        'publishedPorts':([] if manifest.get('transport')=='pf-rdr' else
            [{'hostAddress':instance['host_ip'],'hostPort':443,'containerPort':8443,'proto':'tcp','count':1}]),
        'mounts':[{'destination':'/tmp','source':'tmpfs','type':{'tmpfs':{}},
            'options':['rw','noexec','nosuid','size=64m']}]+[
            {'source':row['source'],'destination':row['destination'],'type':{'virtiofs':{}},
             'options':['ro'] if row['read_only'] else []} for row in instance['volumes']],
        'initProcess':{'executable':executable,'arguments':instance['argv'],
            'user':{'raw':{'userString':f"{manifest['uid']}:{manifest['gid']}"}},
            'environment':image['env'],'workingDirectory':image['workdir']}}}
    if config.get('image',{}).get('reference')!=manifest['image']:
        raise AppleRuntimeError('apple_container_image_drift')
    try:
        observed=run_argv(actual,image,new_name=instance['name'],new_image=manifest['image'])
        required=run_argv(expected,image,new_name=instance['name'],new_image=manifest['image'])
    except (UpgradeError,KeyError,TypeError,ValueError,AttributeError):
        raise AppleRuntimeError('apple_container_configuration_drift') from None
    if observed!=required:
        raise AppleRuntimeError('apple_container_configuration_drift')
    return labels


def conflicts(rows,instance):
    """Include unrelated running containers; never stop them to clear a bind."""
    for name,row in rows.items():
        if name==instance['name'] or row['status']['state']!='running':
            continue
        config=row['configuration']
        ports=config.get('publishedPorts',[])
        mounts=config.get('mounts',[])
        if ports is None:
            ports=[]
        if mounts is None:
            mounts=[]
        if not isinstance(ports,list) or not isinstance(mounts,list):
            raise AppleRuntimeError('apple_foreign_runtime_observation_ambiguous')
        for port in ports:
            if (not isinstance(port,dict) or type(port.get('hostPort')) is not int
                    or not 1<=port['hostPort']<=65535
                    or type(port.get('count',1)) is not int or not 1<=port.get('count',1)<=65535
                    or port['hostPort']+port.get('count',1)>65536
                    or port.get('proto','tcp') not in ('tcp','udp')):
                raise AppleRuntimeError('apple_foreign_port_observation_ambiguous')
            if port.get('proto','tcp')=='udp':
                continue
            if port['hostPort']<=443<port['hostPort']+port.get('count',1) and not isinstance(port.get('hostAddress'),str):
                raise AppleRuntimeError('apple_foreign_port_observation_ambiguous')
            if (port['hostPort']<=443<port['hostPort']+port.get('count',1)
                    and port.get('hostAddress') in ('0.0.0.0','::',instance['host_ip'])):
                raise AppleRuntimeError('apple_management_bind_conflict')
        for mount in mounts:
            if not isinstance(mount,dict):
                raise AppleRuntimeError('apple_foreign_runtime_observation_ambiguous')
            source=mount.get('source')
            if isinstance(source,str) and Path(source).is_absolute():
                actual,own=Path(source).resolve(),Path(instance['state_dir'])
                if actual==own or actual in own.parents or own in actual.parents:
                    raise AppleRuntimeError('apple_identity_already_running_elsewhere')


@contextmanager
def writer(directory,*,mutate=True):
    with control_writer(directory,mutate=mutate,error=AppleRuntimeError,validate=private_directory) as result:
        yield result


@contextmanager
def authority(directory,manifest,*,mutate):
    """Same configuration mutexes used by all supported Admin writers."""
    from .aiport_config_store import AiPortConfigurationStore
    from .config_store import ConfigurationStore
    directory=private_directory(directory)
    roots={Path(volume['source']) for row in manifest['instances'] for volume in row['volumes']}
    if manifest['aikey'] is not None:
        roots.add(Path(manifest['aikey']['state_dir']))
    if any(directory==root or directory in root.parents or root in directory.parents for root in roots):
        raise AppleRuntimeError('apple_host_authority_must_be_outside_guest_mounts')
    with writer(directory,mutate=mutate) as scope:
        with ExitStack() as guards:
            if mutate:
                profiles=[(row['state_dir'],'aiport') for row in manifest['instances']]
                if manifest['aikey'] is not None:
                    profiles.append((manifest['aikey']['state_dir'],'aikey'))
                for root,kind in sorted(profiles):
                    config=Path(root)/'config.json'
                    store=(AiPortConfigurationStore(config,runtime_state_dir=Path('/state'))
                           if kind=='aiport' else ConfigurationStore(config))
                    guards.enter_context(store._locked(allow_host_pending=True))
            yield scope


def activation_value(instance,manifest,receipt,directory,phase):
    return {'schema':activation.SCHEMA,'operation':receipt['operation'],
        'authority':digest(str(directory).encode()),'revision':manifest['revision'],
        'configuration_revision':instance['config_sha256'],'phase':phase}


def finish_reservations(instances,manifest,receipt,directory,bound):
    for instance in instances:
        bound()
        config=Path(instance['state_dir'])/'config.json'
        record=activation.read(config)
        if record is not None and record['phase']=='prepared':
            expected=activation_value(instance,manifest,receipt,directory,'prepared')
            if record!=expected:
                raise AppleOutcomeUncertain('apple_activation_reservation_owned_elsewhere')
            activation.publish(config,dict(expected,phase='settled'))


def read_receipt(directory):
    path=directory/RECEIPT
    try:
        raw=_private(path,65536)
    except FileNotFoundError:
        return None
    value=_json_object(raw)
    fields={'schema','revision','operation','phase','before','baseline_readiness','actions','failure','reconciliation'}
    if (set(value)!=fields or value['schema']!=_SCHEMA
            or not isinstance(value['revision'],str) or not _HASH.fullmatch(value['revision'])
            or not isinstance(value['operation'],str) or not _TOKEN.fullmatch(value['operation'])
            or value['phase'] not in ('prepared','completed','rolled_back','uncertain','reconciled')
            or not isinstance(value['before'],dict) or not isinstance(value['baseline_readiness'],dict)
            or not isinstance(value['actions'],list)
            or len(value['actions'])>128
            or value['failure'] not in (None,'operation_failed','recovery_incomplete')):
        raise AppleRuntimeError('apple_reconcile_receipt_unavailable')
    for name,state in value['before'].items():
        if not isinstance(name,str) or not _NAME.fullmatch(name) or state not in ('absent',*_STATES):
            raise AppleRuntimeError('apple_reconcile_receipt_unavailable')
    if (set(value['baseline_readiness'])!={name for name,state in value['before'].items() if state=='running'}
            or any(status not in ('connected','awaiting_adoption') for status in value['baseline_readiness'].values())):
        raise AppleRuntimeError('apple_reconcile_receipt_unavailable')
    for action in value['actions']:
        if (not isinstance(action,dict) or set(action)!={'name','action','state'}
                or action['name'] not in value['before'] or action['action'] not in ('create','start','stop','transport_apply','transport_remove')
                or action['state'] not in ('requested','acknowledged','observed')):
            raise AppleRuntimeError('apple_reconcile_receipt_unavailable')
    if value['phase'] in ('completed','rolled_back') and any(a['state']!='observed' for a in value['actions']):
        raise AppleRuntimeError('apple_reconcile_receipt_unavailable')
    reconciled=value['reconciliation']
    if value['phase']=='reconciled':
        if (not isinstance(reconciled,dict) or set(reconciled)!={'outcome','states','readiness'}
                or reconciled['outcome'] not in ('all_running','no_new_running','partial_runtime_state')
                or not isinstance(reconciled['states'],dict) or set(reconciled['states'])!=set(value['before'])
                or any(state not in ('absent',*_STATES) for state in reconciled['states'].values())
                or not isinstance(reconciled['readiness'],dict)
                or set(reconciled['readiness'])!={name for name,state in reconciled['states'].items() if state=='running'}
                or any(status not in ('connected','awaiting_adoption') for status in reconciled['readiness'].values())):
            raise AppleRuntimeError('apple_reconcile_receipt_unavailable')
    elif reconciled is not None:
        raise AppleRuntimeError('apple_reconcile_receipt_unavailable')
    return value


def save_receipt(directory,value):
    raw=encode(value)
    if len(raw)>65536:
        raise AppleRuntimeError('apple_reconcile_receipt_too_large')
    atomic_private(directory/RECEIPT,raw)
    descriptor=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if read_receipt(directory)!=value:
        raise AppleOutcomeUncertain('apple_receipt_publication_uncertain')


def fresh_health(kind,instance,*,host_ip=None,port=None):
    """Existing CA+exact-leaf reader, fresh cache, no cookie/provider secret."""
    import asyncio
    from .admin_runtime import RuntimeObserver
    directory=Path(instance['state_dir'])
    port=port if port is not None else (8080 if kind=='aikey' else 443)
    host_ip=host_ip or instance['host_ip']
    observer=RuntimeObserver({'profile':(kind,directory)},
        {'profile':f"https://{host_ip}:{port}/healthz"})
    return asyncio.run(observer.read('profile'))


def readiness(observed,instance,*,key=False,require_activation=False):
    if (not isinstance(observed,dict) or observed.get('observation')!='runtime_health'
            or observed.get('tls_ca_and_leaf_pin_verified') is not True
            or observed.get('runtime_configuration_revision')!=instance['config_sha256']
            or not isinstance(observed.get('runtime'),dict)):
        raise AppleRuntimeError('apple_runtime_readback_unavailable_or_stale')
    if require_activation and observed.get('host_activation_schema')!=activation.SCHEMA:
        raise AppleRuntimeError('apple_started_runtime_missing_activation_contract')
    value=observed['runtime']
    adopted=value.get('adopted')
    connected=value.get('connected' if key else 'control_connected')
    if type(adopted) is not bool or type(connected) is not bool or adopted and not connected:
        raise AppleRuntimeError('apple_adopted_runtime_disconnected')
    if key and (not adopted or not connected):
        raise AppleRuntimeError('existing_key_not_adopted_and_connected')
    return 'connected' if adopted and connected else 'awaiting_adoption'


def reconcile(manifest,plan,report,states,options,host_state,*,apply=False,
              run=run_command,probe=fresh_health,host_check=None,clock=time.monotonic,
              pause=time.sleep,timeout=600,transport=None):
    """No-op/dry-run or exact create/start, with owned-start-only rollback.

    ``host_check`` must be the CLI's concrete route/address/firewall preflight,
    never an untrusted boolean from a manifest. It is called for every start.
    This actor does not allocate addresses or grant firewall authority.
    """
    if type(apply) is not bool or type(timeout) is not int or not 30<=timeout<=1800:
        raise AppleRuntimeError('invalid_apple_reconcile_options')
    if build_manifest(plan,report,states,**options)!=manifest:
        raise AppleRuntimeError('apple_manifest_or_identity_changed')
    if (manifest.get('transport')=='pf-rdr') != (transport is not None):
        raise AppleRuntimeError('apple_transport_adapter_required')
    deadline=clock()+timeout
    def command(args,limit=15):
        bound()
        remaining=deadline-clock()
        if remaining<=0:
            raise AppleRuntimeError('apple_reconcile_deadline')
        return run([CONTAINER]+args,min(limit,remaining))
    def current():
        if build_manifest(plan,report,states,**options)!=manifest:
            raise AppleRuntimeError('apple_manifest_or_identity_changed')
    def key_guard():
        if manifest['aikey'] is not None:
            current()
            readiness(probe('aikey',manifest['aikey']),manifest['aikey'],key=True)
    def observe():
        return listing(command(['list','--all','--format','json']))
    with authority(host_state,manifest,mutate=apply) as (directory,bound):
        previous=read_receipt(directory)
        reserved=any((activation.read(Path(row['state_dir'])/'config.json') or {}).get('phase')=='prepared'
                     for row in manifest['instances'])
        fenced=reserved or previous is not None and previous['phase'] in ('prepared','uncertain')
        if apply and fenced:
            raise AppleOutcomeUncertain('apple_pending_reconciliation_required')
        current()
        version=command(['--version'])
        if not qualified_version(version):
            raise AppleRuntimeError('apple_runtime_version_not_qualified')
        image=image_info(command(['image','inspect',manifest['image']]),manifest['image'])
        rows=observe()
        instances={row['name']:row for row in manifest['instances']}
        before={name:rows[name]['status']['state'] if name in rows else 'absent' for name in instances}
        statuses={}
        for name,instance in instances.items():
            conflicts(rows,instance)
            if name in rows:
                inspect_selected(rows[name],instance,manifest,image)
                if before[name]=='running':
                    statuses[name]=readiness(probe('aiport',instance),instance)
        key_guard()
        baseline_statuses=dict(statuses)
        def existing_guard():
            key_guard()
            for preserved,status in baseline_statuses.items():
                latest=readiness(probe('aiport',instances[preserved]),instances[preserved])
                if status=='connected' and latest!='connected':
                    raise AppleRuntimeError('apple_existing_instance_lost_adoption')
        pending=[name for name,state in before.items() if state!='running']
        if not apply or not pending:
            return {'mode':'dry_run' if not apply else 'unchanged','revision':manifest['revision'],
                'preserved':sorted(name for name in before if before[name]=='running'),
                'would_start':pending,'readiness':statuses,'native_pairing':'unchanged',
                'fenced_by_pending_intent':fenced}
        if host_check is None:
            raise AppleRuntimeError('apple_host_route_firewall_preflight_required')
        receipt={'schema':_SCHEMA,'revision':manifest['revision'],'operation':secrets.token_hex(16),
            'phase':'prepared','before':before,'baseline_readiness':baseline_statuses,'actions':[],
            'failure':None,'reconciliation':None}
        attempted=[]
        operations_complete=False
        def publish():
            bound()
            save_receipt(directory,receipt)
        def mutation(name,action,args,limit):
            bound()
            if clock()>=deadline:
                raise AppleRuntimeError('apple_reconcile_deadline')
            receipt['actions'].append({'name':name,'action':action,'state':'requested'})
            publish()
            if action=='transport_apply':
                transport.apply(instances[name],receipt['operation'])
            elif action=='transport_remove':
                transport.remove(instances[name],receipt['operation'])
            else:
                command(args,limit)
            receipt['actions'][-1]['state']='acknowledged'
            publish()
        def observed():
            receipt['actions'][-1]['state']='observed'
            publish()
        try:
            publish()
            for name in pending:
                current()
                existing_guard()
                rows=observe()
                conflicts(rows,instances[name])
                if (name in rows)!= (before[name]!='absent'):
                    raise AppleRuntimeError('apple_instance_appeared_or_disappeared')
                if name in rows:
                    inspect_selected(rows[name],instances[name],manifest,image)
                    if rows[name]['status']['state']=='running':
                        raise AppleRuntimeError('apple_instance_changed_before_start')
                if host_check(instances[name]) is not True:
                    raise AppleRuntimeError('apple_host_preflight_refused')
                current()
                bound()
                activation.publish(Path(instances[name]['state_dir'])/'config.json',
                    activation_value(instances[name],manifest,receipt,directory,'prepared'))
                if before[name]=='absent':
                    mutation(name,'create',create_arguments(manifest,instances[name],receipt['operation']),90)
                    rows=observe()
                    if name not in rows:
                        raise AppleOutcomeUncertain('apple_created_instance_not_observed')
                    labels=inspect_selected(rows[name],instances[name],manifest,image)
                    if labels.get('org.aikey.operation')!=receipt['operation']:
                        raise AppleOutcomeUncertain('apple_created_instance_owner_changed')
                    if rows[name]['status']['state']=='running':
                        raise AppleOutcomeUncertain('apple_create_unexpectedly_running')
                    observed()
                existing_guard()
                if host_check(instances[name]) is not True:
                    raise AppleRuntimeError('apple_host_preflight_refused')
                current()
                attempted.append(name)
                mutation(name,'start',['start',name],90)
                rows=observe()
                if name not in rows or rows[name]['status']['state']!='running':
                    raise AppleOutcomeUncertain('apple_started_instance_not_observed')
                labels=inspect_selected(rows[name],instances[name],manifest,image)
                if before[name]=='absent' and labels.get('org.aikey.operation')!=receipt['operation']:
                    raise AppleOutcomeUncertain('apple_started_instance_owner_changed')
                observed()
                if transport is not None:
                    mutation(name,'transport_apply',None,90)
                    transport.verify(instances[name],receipt['operation'])
                    observed()
                for attempt in range(12):
                    try:
                        statuses[name]=readiness(probe('aiport',instances[name]),instances[name],require_activation=True)
                        break
                    except (OSError,ValueError):
                        if attempt==11 or clock()+2>=deadline:
                            raise AppleRuntimeError('apple_started_runtime_not_ready') from None
                        pause(2)
                existing_guard()
                current()
            operations_complete=True
            receipt['phase']='completed'
            publish()
            finish_reservations(manifest['instances'],manifest,receipt,directory,bound)
            return {'mode':'applied','revision':manifest['revision'],'started':pending,
                'preserved':[name for name in before if before[name]=='running'],'readiness':statuses,
                'native_pairing':'unchanged','identity_data_deleted':False}
        except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
            # Never claim no effects after a requested but unacknowledged
            # mutation. A fresh explicit observation is required, not retry.
            receipt['phase']='uncertain'
            receipt['failure']='operation_failed'
            try:
                if operations_complete or any(row['state']!='observed' for row in receipt['actions']):
                    raise AppleOutcomeUncertain('apple_daemon_outcome_uncertain')
                for name in reversed(attempted):
                    rows=observe()
                    if name not in rows:
                        raise AppleOutcomeUncertain('apple_rollback_instance_missing')
                    labels=inspect_selected(rows[name],instances[name],manifest,image)
                    if before[name]=='absent' and labels.get('org.aikey.operation')!=receipt['operation']:
                        raise AppleOutcomeUncertain('apple_rollback_owner_changed')
                    if transport is not None and any(a['name']==name and a['action']=='transport_apply'
                            for a in receipt['actions']):
                        mutation(name,'transport_remove',None,90)
                        observed()
                    if rows[name]['status']['state']=='running':
                        mutation(name,'stop',['stop','--time','10',name],30)
                        after=observe()
                        if name not in after or after[name]['status']['state']=='running':
                            raise AppleOutcomeUncertain('apple_rollback_stop_not_observed')
                        inspect_selected(after[name],instances[name],manifest,image)
                        observed()
                existing_guard()
                receipt['phase']='rolled_back'
            except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
                receipt['phase']='uncertain'
                receipt['failure']='recovery_incomplete'
            try:
                publish()
                if receipt['phase']=='rolled_back':
                    finish_reservations(manifest['instances'],manifest,receipt,directory,bound)
            except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
                receipt['phase']='uncertain'  # Retain possible prepared intent; never retry.
            if receipt['phase']=='uncertain':
                raise AppleOutcomeUncertain('apple_reconcile_outcome_uncertain') from None
            raise AppleRuntimeError('apple_own_new_starts_rolled_back_state_retained') from None


def _settle(manifest,plan,report,states,options,host_state,*,run,probe,transport=None):
    """Explicit positive persisted-state observation, never daemon retry/stop.

    Absence cannot retire a possibly late create/start. Only a matching existing
    owner/identity and the requested operation's positive postcondition settle
    an unacknowledged mutation. Partial state is labeled partial, not success.
    """
    if build_manifest(plan,report,states,**options)!=manifest:
        raise AppleOutcomeUncertain('apple_original_intent_and_manifest_required')
    if (manifest.get('transport')=='pf-rdr') != (transport is not None):
        raise AppleOutcomeUncertain('apple_transport_adapter_required')
    with authority(host_state,manifest,mutate=True) as (directory,bound):
        receipt=read_receipt(directory)
        if (receipt is None or receipt['phase'] not in ('prepared','uncertain','reconciled','completed','rolled_back')
                or receipt['revision']!=manifest['revision']
                or build_manifest(plan,report,states,**options)!=manifest):
            raise AppleOutcomeUncertain('apple_original_intent_and_manifest_required')
        def command(args):
            bound()
            return run([CONTAINER]+args,15)
        version=command(['--version'])
        if not qualified_version(version):
            raise AppleOutcomeUncertain('apple_runtime_version_not_qualified')
        image=image_info(command(['image','inspect',manifest['image']]),manifest['image'])
        rows=listing(command(['list','--all','--format','json']))
        instances={row['name']:row for row in manifest['instances']}
        if set(instances)!=set(receipt['before']):
            raise AppleOutcomeUncertain('apple_intent_scope_changed')
        for instance in instances.values():
            reservation=activation.read(Path(instance['state_dir'])/'config.json')
            if reservation is not None and reservation['phase']=='prepared' and reservation!=activation_value(
                    instance,manifest,receipt,directory,'prepared'):
                raise AppleOutcomeUncertain('apple_activation_reservation_owned_elsewhere')
        seen={}
        ready={}
        for name,instance in instances.items():
            bound()
            conflicts(rows,instance)
            before=receipt['before'][name]
            actions=[action for action in receipt['actions'] if action['name']==name]
            if name not in rows:
                if before!='absent' or actions:
                    raise AppleOutcomeUncertain('apple_missing_instance_cannot_prove_no_late_mutation')
                seen[name]='absent'
                continue
            labels=inspect_selected(rows[name],instance,manifest,image)
            if before=='absent' and labels.get('org.aikey.operation')!=receipt['operation']:
                raise AppleOutcomeUncertain('apple_observed_instance_not_owned_by_intent')
            state=rows[name]['status']['state']
            if before=='running' and state!='running':
                raise AppleOutcomeUncertain('apple_prior_running_instance_not_preserved')
            for action in actions:
                if action['state']=='observed':
                    continue
                if (action['action']=='start' and state!='running'
                        or action['action']=='stop' and state=='running'):
                    raise AppleOutcomeUncertain('apple_requested_postcondition_not_observed')
            seen[name]=state
            if transport is not None:
                transport_actions=[a for a in actions if a['action'].startswith('transport_')]
                if any(a['state']!='observed' and a['action']=='transport_remove' for a in transport_actions):
                    raise AppleOutcomeUncertain('apple_transport_removal_requires_privileged_recovery')
                if state=='running':
                    transport.verify(instance,receipt['operation'] if transport_actions else '0'*32)
            if state=='running':
                ready[name]=readiness(probe('aiport',instance),instance,require_activation=before!='running')
                if receipt['baseline_readiness'].get(name)=='connected' and ready[name]!='connected':
                    raise AppleOutcomeUncertain('apple_prior_adopted_instance_not_preserved')
        if manifest['aikey'] is not None:
            readiness(probe('aikey',manifest['aikey']),manifest['aikey'],key=True)
        if build_manifest(plan,report,states,**options)!=manifest:
            raise AppleOutcomeUncertain('apple_manifest_changed_during_reconciliation')
        outcome=('all_running' if all(state=='running' for state in seen.values()) else
            'no_new_running' if not any(state=='running' and receipt['before'][name]!='running'
                                      for name,state in seen.items()) else 'partial_runtime_state')
        if transport is not None:
            for name,instance in instances.items():
                if seen[name]=='running' and any(a['name']==name and a['action']=='transport_apply'
                        for a in receipt['actions']):
                    bound()
                    transport.settle(instance,receipt['operation'])
        updated=deepcopy(receipt)
        updated.update(phase='reconciled',reconciliation={'outcome':outcome,'states':seen,'readiness':ready})
        bound()
        try:
            save_receipt(directory,updated)
            finish_reservations(instances.values(),manifest,receipt,directory,bound)
        except (OSError,ValueError,RuntimeError):
            raise AppleOutcomeUncertain('apple_settlement_receipt_uncertain') from None
        return {'mode':'reconciled','outcome':outcome,'revision':manifest['revision'],
            'readiness':ready,'daemon_mutations':0,'configuration_rewritten':False,
            'original_failure_retained':updated['failure'] is not None}


def settle(manifest,plan,report,states,options,host_state,*,run=run_command,probe=fresh_health,transport=None):
    """Keep failed reconciliation explicitly uncertain; never clear on error."""
    try:
        return _settle(manifest,plan,report,states,options,host_state,run=run,probe=probe,transport=transport)
    except AppleOutcomeUncertain:
        raise
    except (OSError,ValueError,RuntimeError,KeyError,TypeError):
        raise AppleOutcomeUncertain('apple_settlement_refused_intent_retained') from None
