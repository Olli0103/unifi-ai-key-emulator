"""Pure private Apple instance manifests; never create state, containers or ports.

The fresh inventory/capacity guard is shared with the NAS actor. Existing
identities are verified, not provisioned on a dry run. Images are immutable OCI
references. Native adoption/pairing and host address/firewall authority are not
invented by a manifest or an offline file hash.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import stat

from .aiport_candidate import load_config
from .aiport_ingest import normalize_mac
from .aiport_instance_state import provision_slot
from .aiport_nas_compose import _verified_state
from .aiport_nas_reconcile import verify_camera_plan
from .device_workflow import _private
from .device_workflow_fence import check as check_device_intent
from .protocol import _json_object
from .host_control_store import canonical_path, private_directory as control_directory

SCHEMA='aikey-apple-instance-manifest/1'
RUNTIME_VERSION='1.6.1'
_IMAGE=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,190}@sha256:[0-9a-f]{64}\Z')
_PREFIX=re.compile(r'[a-z][a-z0-9-]{0,36}\Z')
_IDENTITY_FILES=('device.crt','device.key','identity.json')


class ApplePlanError(ValueError):
    """Fixed operator error; no private bytes or argument echo."""


def encode(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def path_value(value):
    return canonical_path(value, ApplePlanError)


def private_directory(value):
    return control_directory(value, ApplePlanError)


def identity(directory,*,key=False):
    directory=private_directory(directory)
    result={}
    for name in _IDENTITY_FILES:
        path=directory/name
        if key and name=='identity.json' and not path.exists() and not path.is_symlink():
            continue
        result[name]=digest(_private(path,32768))
    return result


def _profile(directory):
    directory=private_directory(directory)
    check_device_intent(directory/'config.json',durable=False)
    raw=_private(directory/'config.json',4096)
    config=load_config(directory/'config.json',check_decoder_executable=False)
    check_device_intent(directory/'config.json',durable=False)
    # The parser must validate the same bytes recorded in this manifest.
    if _json_object(raw)!=config:
        # Config parsing canonicalizes MAC spellings/defaults. Preserve and
        # fingerprint raw bytes, but validate against a second identical read.
        from .aiport_candidate import parse_config
        if parse_config(raw,state_dir=directory,check_decoder_executable=False)!=config:
            raise ApplePlanError('apple_configuration_snapshot_changed')
    if _private(directory/'config.json',4096)!=raw:
        raise ApplePlanError('apple_configuration_snapshot_changed')
    return raw,config


def build_manifest(plan,report,states,*,controller_ip,controller_pin,image,uid,gid,dns,
                   cpus=2,memory_mib=2048,name_prefix='local-aiport',models_dir=None,
                   aikey_state=None,now=None,transport="apple-publish"):
    """Generate the exact selected listener/configuration/identity contract.

    ``states`` selects existing slots, not new filesystem authority. Preparing
    new identities remains the existing reviewed rollout/provision operation.
    No camera is paired or moved and no old slot/state is removed here.
    """
    if (not isinstance(states,dict) or not 1<=len(states)<=16
            or any(type(slot) is not int or slot<1 for slot in states)
            or type(uid) is not int or uid!=os.geteuid() or uid<=0
            or type(gid) is not int or gid<=0
            or type(cpus) is not int or not 1<=cpus<=32
            or type(memory_mib) is not int or not 256<=memory_mib<=32768
            or not isinstance(image,str) or not _IMAGE.fullmatch(image)
            or not isinstance(name_prefix,str) or not _PREFIX.fullmatch(name_prefix)
            or not isinstance(controller_pin,str) or not re.fullmatch('[0-9a-f]{64}',controller_pin)):
        raise ApplePlanError('invalid_apple_instance_options')
    try:
        address=ipaddress.IPv4Address(dns)
        if not address.is_private or address.is_multicast or address.is_unspecified or address.is_loopback:
            raise ValueError
    except (TypeError,ValueError):
        raise ApplePlanError('apple_dns_requires_explicit_private_ipv4') from None
    if transport not in ('apple-publish','pf-rdr'):
        raise ApplePlanError('invalid_apple_transport')
    states={slot:private_directory(path) for slot,path in states.items()}
    if len(set(states.values()))!=len(states):
        raise ApplePlanError('apple_state_directory_reused')
    verify_camera_plan(plan,report,states,controller_ip,now=now)
    models=None
    if models_dir is not None:
        models=path_value(models_dir)
        info=models.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=uid or info.st_mode&0o022
                or models in states.values()):
            raise ApplePlanError('apple_model_mount_not_safe')
    key=None
    if plan['ai_key']['host_ip'] is not None:
        if aikey_state is None:
            raise ApplePlanError('existing_key_health_identity_required')
        key_root=private_directory(aikey_state)
        if key_root in states.values() or key_root==models:
            raise ApplePlanError('apple_key_state_must_be_independent')
        key_raw=_private(key_root/'config.json',1024*1024)
        key_config=_json_object(key_raw)
        if key_config.get('device',{}).get('ip')!=plan['ai_key']['host_ip']:
            raise ApplePlanError('apple_key_address_changed')
        key={'state_dir':str(key_root),'host_ip':plan['ai_key']['host_ip'],
             'mac':normalize_mac(key_config['device']['mac']),
             'identity':identity(key_root,key=True),'config_sha256':digest(key_raw)}
    elif aikey_state is not None:
        raise ApplePlanError('unplanned_key_health_identity')
    roots=list(states.values())+([models] if models is not None else [])+([Path(key['state_dir'])] if key else [])
    if any(first==second or first in second.parents or second in first.parents
           for index,first in enumerate(roots) for second in roots[index+1:]):
        raise ApplePlanError('apple_private_mounts_must_be_disjoint')
    instances=[]
    used_macs={key['mac']} if key else set()
    used_keys={key['identity']['device.key']} if key else set()
    used_certificates={key['identity']['device.crt']} if key else set()
    for slot,directory in sorted(states.items()):
        _verified_state(directory,uid)
        raw,config=_profile(directory)
        verified=provision_slot(plan,slot,directory,controller_ip=controller_ip,
            controller_cert_file=directory/'controller-ca.pem',controller_pin=controller_pin,
            firmware_version=config['firmware_version'])
        # Own independent trust/ONVIF/face-space objects must be ready before
        # starting a processor; the same private device consumer verifies them.
        from .device_workflow import DeviceWorkflowStore
        from .aiport_config_store import AiPortConfigurationStore
        consumer=DeviceWorkflowStore('aiport',AiPortConfigurationStore(directory/'config.json',
            runtime_state_dir=Path('/state')),directory)
        consumer._verify_operation_resources(config,'controller')
        name=name_prefix+'-slot-'+str(slot)
        host=verified['host_ip']
        certificate=_private(directory/'device.crt',32768)
        pin=digest(ssl.PEM_cert_to_DER_cert(certificate.decode()))
        bound_identity=identity(directory)
        if bound_identity['device.crt']!=digest(certificate):
            raise ApplePlanError('apple_identity_snapshot_changed')
        mac=normalize_mac(config['mac'])
        if (mac in used_macs or bound_identity['device.key'] in used_keys
                or bound_identity['device.crt'] in used_certificates):
            raise ApplePlanError('apple_independent_instances_must_not_share_identity')
        used_macs.add(mac)
        used_keys.add(bound_identity['device.key'])
        used_certificates.add(bound_identity['device.crt'])
        volumes=[{'source':str(directory),'destination':'/state','read_only':False}]
        if models is not None:
            volumes.append({'source':str(models),'destination':'/models','read_only':True})
        instances.append({'slot':slot,'name':name,'host_ip':host,'mac':config['mac'],
            'state_dir':str(directory),'identity':bound_identity,'certificate_sha256':pin,
            'config_sha256':digest(raw),'volumes':volumes,'management_tcp':443,
            'container_tcp':8443,'argv':['--config','/state/config.json','--bind','0.0.0.0','--port','8443']})
    result={'schema':SCHEMA,'runtime_version':RUNTIME_VERSION,'image':image,
        'controller_ip':controller_ip,'controller_pin':controller_pin,'uid':uid,'gid':gid,
        'dns':str(address),'cpus':cpus,'memory_mib':memory_mib,'instances':instances,'aikey':key,
        'native_pairing':'disabled','host_discovery_udp':'needs_evidence_not_published',
        'camera_stream_listeners':'needs_evidence_not_published','models_loaded':False}
    if transport=='pf-rdr':
        result['transport']='pf-rdr'
    result['revision']=digest(encode(result))
    return result


def create_arguments(manifest,instance,operation):
    """Typed argv only. The actor verifies/rebuilds the manifest before use."""
    if not isinstance(operation,str) or not re.fullmatch('[0-9a-f]{32}',operation):
        raise ApplePlanError('invalid_apple_operation')
    args=['create','--name',instance['name'],'--platform','linux/arm64',
        '--user',f"{manifest['uid']}:{manifest['gid']}",
        '--read-only','--network','default','--tmpfs','/tmp:rw,noexec,nosuid,size=64m',
        '--cpus',str(manifest['cpus']),'--memory',str(manifest['memory_mib'])+'M',
        '--dns',manifest['dns'],
        '--label','org.aikey.instance='+instance['certificate_sha256'],
        '--label','org.aikey.operation='+operation,'--entrypoint','local-aiport-candidate']
    if manifest.get('transport','apple-publish')=='apple-publish':
        args+=['--publish',f"{instance['host_ip']}:443:8443/tcp"]
    for mount in instance['volumes']:
        args+=['--volume',mount['source']+':'+mount['destination']+(':ro' if mount['read_only'] else '')]
    return args+[manifest['image']]+instance['argv']
