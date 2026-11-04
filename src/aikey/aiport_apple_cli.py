"""Private reviewed Apple host plans and one-shot conservative reconciliation."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re

from .aiport_apple_plan import ApplePlanError,build_manifest,encode,private_directory
from .aiport_apple_reconcile import AppleRuntimeError,AppleOutcomeUncertain,reconcile,settle
from .aiport_apple_host import HostPreflight
from .aiport_nas_reconcile import ReconcileError
from .aiport_instance_state import InstanceStateError
from .camera_inventory import fetch_inventory,InventoryError
from .device_workflow import _private
from .protocol import _json_object


def parser():
    root=argparse.ArgumentParser(description=__doc__)
    commands=root.add_subparsers(dest='command',required=True)
    for name in ('plan','run'):
        command=commands.add_parser(name)
        command.add_argument('--plan',type=Path,required=True,help='Existing private capacity/listener deployment plan')
        command.add_argument('--slot-state',action='append',required=True,metavar='SLOT=DIRECTORY')
        command.add_argument('--controller-ip',required=True)
        command.add_argument('--controller-pin',required=True)
        command.add_argument('--image',required=True,help='Already installed immutable IMAGE@sha256:DIGEST')
        command.add_argument('--uid',type=int,default=os.geteuid())
        command.add_argument('--gid',type=int,default=os.getegid())
        command.add_argument('--dns',required=True)
        command.add_argument('--cpus',type=int,default=2)
        command.add_argument('--memory-mib',type=int,default=2048)
        command.add_argument('--name-prefix',default='local-aiport')
        command.add_argument('--models-dir',type=Path)
        command.add_argument('--aikey-state',type=Path)
        source=command.add_mutually_exclusive_group(required=True)
        source.add_argument('--inventory',type=Path,help='Private fresh inventory; not independent native proof')
        source.add_argument('--live-inventory',action='store_true',help='Fetch current pinned Integration API inventory')
        command.add_argument('--api-key-file',type=Path)
        command.add_argument('--web-trust-file',type=Path)
        command.add_argument('--web-cert-file',type=Path)
        if name=='plan':
            command.add_argument('--output',type=Path,required=True,help='New private manifest; never overwrite different bytes')
        else:
            command.add_argument('--manifest',type=Path,required=True)
            command.add_argument('--state-dir',type=Path,required=True,help='One existing private host-writer authority directory')
            mode=command.add_mutually_exclusive_group()
            mode.add_argument('--apply',action='store_true')
            mode.add_argument('--reconcile',action='store_true',help='Positive observation only; no daemon retry/stop')
            command.add_argument('--timeout',type=int,default=600)
    return root


def _inputs(args):
    states={}
    for assignment in args.slot_state:
        number,separator,directory=assignment.partition('=')
        if not separator or not re.fullmatch('[1-9][0-9]{0,2}',number) or int(number) in states or not directory:
            raise ApplePlanError('invalid_apple_slot_state')
        states[int(number)]=Path(directory).expanduser().absolute()
    plan=_json_object(_private(args.plan.expanduser().absolute(),2*1024*1024))
    credentials=(args.api_key_file,args.web_trust_file,args.web_cert_file)
    if args.live_inventory:
        if any(value is None for value in credentials):
            raise ApplePlanError('apple_complete_pinned_inventory_settings_required')
        report=asyncio.run(fetch_inventory(args.controller_ip,api_key_file=args.api_key_file,
            trust_file=args.web_trust_file,cert_file=args.web_cert_file))
    else:
        if any(value is not None for value in credentials):
            raise ApplePlanError('apple_unused_inventory_credentials_refused')
        report=_json_object(_private(args.inventory.expanduser().absolute(),2*1024*1024))
    options={name:getattr(args,name) for name in ('controller_ip','controller_pin','image','uid','gid','dns',
        'cpus','memory_mib','name_prefix','models_dir','aikey_state')}
    for name in ('models_dir','aikey_state'):
        if options[name] is not None:
            options[name]=options[name].expanduser().absolute()
    return plan,report,states,options


def _publish(path,value):
    path=path.expanduser().absolute()
    parent=private_directory(path.parent)
    if path.name in ('.','..') or path.is_symlink():
        raise ApplePlanError('apple_manifest_output_not_safe')
    raw=encode(value)
    try:
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        if _private(path,2*1024*1024)!=raw:
            raise ApplePlanError('apple_manifest_already_exists_with_other_bytes') from None
        created=False
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    else:
        created=True
    try:
        with os.fdopen(descriptor,'wb' if created else 'rb') as output:
            if created:
                output.write(raw)
                output.flush()
            os.fsync(output.fileno())
        descriptor=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if _private(path,2*1024*1024)!=raw:
            raise ValueError
    except (OSError,ValueError):
        raise AppleOutcomeUncertain('apple_manifest_publication_uncertain') from None
    return created


def _code(error):
    # Shared readers carry human-readable fixed messages, not Apple error
    # subclasses. Preserve actionable admission reasons without echoing an
    # unknown exception, private path or lower-layer payload.
    if isinstance(error,(ReconcileError,InstanceStateError)):
        known={
            'A fresh, supported pinned Protect inventory is required':'apple_fresh_supported_inventory_required',
            'A complete AI Port plan is required':'apple_complete_capacity_plan_required',
            'Protect camera inventory or capacity changed; review a new plan':'apple_camera_inventory_or_capacity_changed',
            'Plan, camera inventory or slot identity no longer matches':'apple_plan_inventory_or_slot_identity_changed',
            'Configured AI Port camera is outside its verified slot':'apple_configured_camera_outside_reviewed_slot',
            'Selected NAS slot lacks verified camera capacity':'apple_selected_slot_capacity_unverified',
            'A valid addressed AI Port plan slot is required':'apple_selected_slot_requires_assigned_address',
            'Invalid AI Port plan slot':'apple_selected_slot_invalid',
            'ONVIF ingest is not implemented; this slot cannot be provisioned':'apple_onvif_ingest_unsupported',
            'Existing AI Port identity differs from the planned slot':'apple_slot_identity_changed',
            'Existing AI Port identity is incomplete or invalid':'apple_slot_identity_invalid',
            'A pinned controller certificate is required':'apple_pinned_controller_certificate_required',
            'Controller certificate does not match pin or validity':'apple_controller_certificate_pin_or_validity_refused',
        }
        return known.get(error.args[0] if error.args and isinstance(error.args[0],str) else None,
                         'apple_inventory_or_slot_preflight_refused')
    if (isinstance(error,(ApplePlanError,AppleRuntimeError)) and error.args
            and isinstance(error.args[0],str) and re.fullmatch('[a-z][a-z0-9_]{0,95}',error.args[0])):
        return error.args[0]
    return 'apple_instance_operation_refused'


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        plan,report,states,options=_inputs(args)
        expected=build_manifest(plan,report,states,**options)
        if args.command=='plan':
            created=_publish(args.output,expected)
            result={'mode':'manifest_created' if created else 'manifest_unchanged','revision':expected['revision'],
                'listeners':[{'slot':row['slot'],'host_ip':row['host_ip'],'host_tcp':443,'container_tcp':8443}
                             for row in expected['instances']],
                'artifact_scope':'private_operator_metadata','container_commands':0,'identity_files_changed':False}
        else:
            manifest=_json_object(_private(args.manifest.expanduser().absolute(),2*1024*1024))
            if manifest!=expected:
                raise AppleRuntimeError('apple_manifest_or_identity_changed')
            host_state=private_directory(args.state_dir.expanduser().absolute())
            if args.reconcile:
                result=settle(manifest,plan,report,states,options,host_state)
            else:
                checks=HostPreflight(args.controller_ip)
                result=reconcile(manifest,plan,report,states,options,host_state,apply=args.apply,
                    host_check=checks,timeout=args.timeout)
                blocked=[]
                if not args.apply:
                    for instance in manifest['instances']:
                        if instance['name'] not in result['would_start']:
                            continue
                        try:
                            checks(instance)
                        except (OSError,ValueError,RuntimeError) as error:
                            blocked.append({'slot':instance['slot'],'reason':_code(error)})
                result['host_observations']=checks.observations
                result['host_blockers']=blocked
                result['ready_for_apply']=(not blocked and not result.get('fenced_by_pending_intent',False)) if not args.apply else None
        print(json.dumps(dict(result,schema='aikey-apple-operator/1',models_loaded_by_operator=False,
            native_pairing_changed=False,native_verification='needs_evidence'),sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({'error':'apple_operator_interrupted_inspect_pending_intent','automatic_retry':False,
            'outcome_uncertain':True,'native_verification':'needs_evidence'}))
        return 130
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError,InventoryError) as error:
        print(json.dumps({'error':_code(error),'automatic_retry':False,
            'outcome_uncertain':isinstance(error,AppleOutcomeUncertain),'native_verification':'needs_evidence'}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
