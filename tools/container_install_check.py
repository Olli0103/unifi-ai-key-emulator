"""Synthetic installed-image checks, never native Protect or release approval.

Stock Docker/Actions own build and process containment. This tool neither
starts Docker, pulls/pushes images, nor supervises arbitrary child processes.
Only ``probe`` constructs fixtures, inside an explicitly isolated Linux image.
Private generated identity bytes and their baseline never leave /state.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import ssl
import stat
import sysconfig
import tomllib

SOURCE='aikey-container-install-source/2'
PHASE='aikey-container-install-phase/1'
RECORD='aikey-container-install-record/1'
SUMMARY='aikey-container-install-summary/1'
STATE=Path('/state')
BASELINE='.synthetic-install-baseline.json'
HASH=re.compile('[0-9a-f]{64}\\Z')
REVISION=re.compile('[0-9a-f]{40}\\Z')
MODULE=re.compile(r'aikey/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py\Z')
PROFILES=('aikey','aiport')
CASES=('create','reopen','wrong-source','wrong-arch')


class CheckError(ValueError):
    """Only fixed safe codes cross the public output boundary."""


def require(condition,code):
    if not condition:
        raise CheckError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git_context(repository,revision,nonce):
    require(isinstance(repository,str) and re.fullmatch('[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}',repository),
            'container_public_repository_refused')
    require(isinstance(revision,str) and REVISION.fullmatch(revision),'container_source_revision_refused')
    require(isinstance(nonce,str) and re.fullmatch('[0-9]{1,20}-[0-9]{1,8}-(amd64|arm64)',nonce),
            'container_fixture_scope_refused')
    return 'https://github.com/'+repository+'.git#'+revision


def _pairs(items):
    result={}
    for key,value in items:
        require(key not in result,'container_json_ambiguous')
        result[key]=value
    return result


def _constant(_):
    raise CheckError('container_json_nonfinite')


def read(path,limit=256*1024):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and 0<=info.st_size<=limit,
                'container_input_not_bounded_regular')
        raw=source.read(limit+1)
    require(len(raw)<=limit,'container_input_too_large')
    return raw


def load(path):
    value=json.loads(read(path),object_pairs_hook=_pairs,parse_constant=_constant)
    require(isinstance(value,dict),'container_json_object_required')
    return value


def publish(path,value):
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
    require(len(raw)<=256*1024,'container_receipt_too_large')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    fd=os.open(Path(path).parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    require(load(path)==value,'container_receipt_publication_uncertain')


def checked_source(value):
    fields={'schema','revision','version','modules','license_files','probe_sha256'}
    require(set(value)==fields and value['schema']==SOURCE,'container_source_schema_refused')
    require(isinstance(value['revision'],str) and REVISION.fullmatch(value['revision']),
            'container_source_revision_refused')
    require(isinstance(value['version'],str) and re.fullmatch('[0-9A-Za-z.+!-]{1,64}',value['version']),
            'container_source_version_refused')
    for key in ('probe_sha256',):
        require(isinstance(value[key],str) and HASH.fullmatch(value[key]),'container_source_digest_refused')
    licenses=value['license_files']
    require(isinstance(licenses,dict) and 1<=len(licenses)<=32,
            'container_source_licenses_refused')
    require(all(isinstance(name,str) and re.fullmatch(r'[A-Z][A-Za-z0-9_.-]{0,127}',name)
                and isinstance(sha,str) and HASH.fullmatch(sha)
                for name,sha in licenses.items()),'container_source_licenses_refused')
    modules=value['modules']
    require(isinstance(modules,dict) and 1<=len(modules)<=512,'container_source_modules_refused')
    require(all(isinstance(name,str) and MODULE.fullmatch(name) and isinstance(sha,str) and HASH.fullmatch(sha)
                for name,sha in modules.items()),'container_source_modules_refused')
    return value


def manifest(root,revision):
    root=Path(root).resolve(strict=True)
    require(isinstance(revision,str) and REVISION.fullmatch(revision),'container_source_revision_refused')
    modules={}
    for path in sorted((root/'src/aikey').rglob('*.py')):
        require(not path.is_symlink(),'container_source_symlink_refused')
        modules[path.relative_to(root/'src').as_posix()]=digest(read(path,2*1024*1024))
    project=tomllib.loads(read(root/'pyproject.toml').decode())['project']
    names=project.get('license-files')
    require(isinstance(names,list) and 1<=len(names)<=32
            and all(isinstance(name,str) and re.fullmatch(r'[A-Z][A-Za-z0-9_.-]{0,127}',name)
                    for name in names) and len(names)==len(set(names)),
            'container_source_licenses_refused')
    licenses={name:digest(read(root/name)) for name in names}
    return checked_source({'schema':SOURCE,'revision':revision,'version':project['version'],
        'modules':modules,'license_files':licenses,
        'probe_sha256':digest(read(root/'tools/container_install_check.py'))})


def installed(value):
    """Refuse editable/checkout shadowing before importing the application."""
    checked_source(value)
    require(digest(read(Path(__file__).resolve()))==value['probe_sha256'],'container_probe_source_mismatch')
    spec=importlib.util.find_spec('aikey')
    purelib=Path(sysconfig.get_path('purelib')).resolve()
    require(spec is not None and spec.origin is not None,'container_package_origin_refused')
    origin=Path(spec.origin).resolve()
    package=origin.parent
    require(origin==purelib/'aikey/__init__.py' and package.is_dir(),'container_package_origin_refused')
    actual={}
    for path in package.rglob('*.py'):
        require(not path.is_symlink(),'container_installed_source_mismatch')
        actual[path.relative_to(purelib).as_posix()]=digest(read(path,2*1024*1024))
    require(actual==value['modules'],'container_installed_source_mismatch')
    distribution=importlib.metadata.distribution('local-aikey')
    require(distribution.version==value['version'],'container_installed_version_mismatch')
    licenses=[path for path in distribution.files or ()
              if re.fullmatch(r'local_aikey-[0-9A-Za-z.+!-]+\.dist-info/licenses/[A-Z][A-Za-z0-9_.-]{0,127}',str(path))]
    require(len(licenses)==len(value['license_files'])
            and {Path(path).name for path in licenses}==set(value['license_files']),
            'container_installed_license_mismatch')
    require(all(digest(read(distribution.locate_file(path)))==value['license_files'][Path(path).name]
                for path in licenses),'container_installed_license_mismatch')
    return len(actual)


def isolated_environment(architecture):
    require(platform.system()=='Linux','container_linux_required')
    expected={'amd64':{'x86_64'},'arm64':{'aarch64','arm64'}}
    require(architecture in expected and platform.machine() in expected[architecture],
            'container_architecture_mismatch')
    require(os.geteuid()==10001 and os.getegid()==10001,'container_nonroot_identity_required')
    require(os.statvfs('/').f_flag&os.ST_RDONLY,'container_readonly_root_required')
    require({path.name for path in Path('/sys/class/net').iterdir()}=={'lo'},'container_network_none_required')
    status=read(Path('/proc/self/status'),65536).decode()
    match=re.search(r'^CapEff:\s+([0-9a-f]+)$',status,re.M)
    require(match is not None and int(match[1],16)==0,'container_capabilities_must_be_empty')
    require(re.search(r'^NoNewPrivs:\s+1$',status,re.M) is not None,'container_no_new_privileges_required')


def state_root(path):
    path=Path(path)
    require(path.is_absolute() and path.resolve()==path and not path.is_symlink(),'container_state_root_refused')
    info=path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid() and not info.st_mode&0o077,
            'container_state_root_not_private')
    return path


def snapshot(root):
    result={}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(),'container_fixture_symlink_refused')
        if path.is_dir():
            continue
        if path==root/BASELINE:
            continue
        info=path.lstat()
        require(info.st_uid==os.geteuid() and not info.st_mode&0o077,'container_fixture_not_private')
        result[path.relative_to(root).as_posix()]=digest(read(path,2*1024*1024))
        require(len(result)<=128,'container_fixture_too_large')
    return result


async def open_profile(root,profile,*,create):
    """Construct/close the saved profile without starting its native service."""
    from aikey.config import initialize,load_startup_config
    from aikey.tls import ensure_identity_certificate
    if profile=='aikey':
        path=root/'key/config.json'
        if create:
            initialize(path,path.parent,controller_host='127.0.0.1',device_ip='127.0.0.1')
        config,revision=load_startup_config(path)
        require(config['search']['enabled'] is False and config['database']['enabled'] is False,
                'container_fixture_profile_not_isolated')
        require(revision==digest(read(path)),'container_fixture_startup_revision_mismatch')
    else:
        from aikey.aiport_deployment import plan_ai_ports
        from aikey.aiport_instance_state import provision_slot
        from aikey.aiport_candidate import CandidateService
        controller=root/'controller'
        path=root/'port'
        if create:
            controller.mkdir(mode=0o700)
            cert,_=ensure_identity_certificate(controller,'020000000001')
            pin=digest(ssl.PEM_cert_to_DER_cert(cert.read_text()))
            plan=plan_ai_ports({'schema':'aikey-camera-preflight/1','cameras':[
                {'id':f'{number:024x}','model':'UVC G3 Instant','state':'CONNECTED',
                 'processing_class':'legacy_ingress_needed'} for number in (1,2)]},
                device_ips=['10.254.254.20'],ai_key_ip=None)
            provision_slot(plan,1,path,controller_ip='10.254.254.1',controller_cert_file=cert,controller_pin=pin)
        service=CandidateService.from_configuration_file(path/'config.json')
        try:
            require(not service.adoption.adopted and service.task is None and service.runner is None,
                    'container_fixture_unadopted_required')
        finally:
            await service.stop()
        # A normal installed controller credential parser must reject bad data.
        from aikey.aiport_credentials import CredentialStore,CredentialError
        try:
            CredentialStore(path).rotate({'username':'fixture','hashedPassword':'invalid'})
        except CredentialError:
            pass
        else:
            raise CheckError('container_invalid_credential_was_accepted')


async def profile_probe(root,profile,phase,value):
    root=state_root(root)
    require(profile in PROFILES and phase in ('create','reopen'),'container_fixture_options_refused')
    if phase=='create':
        require(not any(root.iterdir()),'container_fixture_state_must_be_empty')
        await open_profile(root,profile,create=True)
        before=snapshot(root)
        require(len(before)>=5,'container_fixture_identity_incomplete')
        publish(root/BASELINE,{'schema':'aikey-synthetic-install-state/1','revision':value['revision'],
            'profile':profile,'files':before})
    else:
        saved=load(root/BASELINE)
        require(set(saved)=={'schema','revision','profile','files'} and saved['schema']=='aikey-synthetic-install-state/1'
                and saved['revision']==value['revision'] and saved['profile']==profile,
                'container_fixture_baseline_refused')
        before=saved['files']
        require(isinstance(before,dict) and before==snapshot(root),'container_fixture_state_changed')
        await open_profile(root,profile,create=False)
        require(before==snapshot(root),'container_fixture_reopen_changed_state')
    checks=0
    if phase=='create':
        from aikey.lab import run_lab
        lab=await run_lab()
        require(lab.get('passed') is True and lab.get('real_controller_contacted') is False
                and lab.get('real_inference_performed') is False,'container_loopback_lab_failed')
        checks=len(lab['checks'])
        require(before==snapshot(root),'container_loopback_lab_changed_fixture')
    return {'schema':PHASE,'phase_passed':True,'profile':profile,'phase':phase,
        'architecture':value['architecture'],'revision':value['revision'],
        'version':value['version'],'module_count':len(value['modules']),
        'saved_private_files':len(before),'loopback_lab_checks':checks,
        'real_controller_contacted':False,'real_inference_performed':False,
        'native_verification':'needs_evidence'}


def record(response,exit_code,profile,case,architecture,revision):
    require(profile in PROFILES and case in CASES and architecture in ('amd64','arm64')
            and REVISION.fullmatch(revision),'container_result_identity_refused')
    require(response.get('schema')==PHASE,'container_phase_schema_refused')
    if case in ('create','reopen'):
        fields={'schema','phase_passed','profile','phase','architecture','revision','version','module_count',
                'saved_private_files','loopback_lab_checks','real_controller_contacted',
                'real_inference_performed','native_verification'}
        require(type(exit_code) is int and exit_code==0 and response.get('phase_passed') is True
                and set(response)==fields
                and response.get('profile')==profile and response.get('phase')==case
                and response.get('architecture')==architecture and response.get('revision')==revision
                and isinstance(response.get('version'),str) and re.fullmatch('[0-9A-Za-z.+!-]{1,64}',response['version'])
                and type(response.get('module_count')) is int and 1<=response['module_count']<=512
                and type(response.get('saved_private_files')) is int and 5<=response['saved_private_files']<=128
                and type(response.get('loopback_lab_checks')) is int
                and (0<response['loopback_lab_checks']<=128 if case=='create' else response['loopback_lab_checks']==0)
                and response.get('real_controller_contacted') is False
                and response.get('real_inference_performed') is False
                and response.get('native_verification')=='needs_evidence','container_positive_phase_refused')
    else:
        expected={'wrong-source':'container_installed_source_mismatch','wrong-arch':'container_architecture_mismatch'}
        require(type(exit_code) is int and exit_code==2 and response.get('phase_passed') is False
                and set(response)=={'schema','phase_passed','error','native_verification'}
                and response.get('native_verification')=='needs_evidence'
                and response.get('error')==expected[case],'container_negative_control_refused')
    return {'schema':RECORD,'revision':revision,'architecture':architecture,'profile':profile,
            'case':case,'exit_code':exit_code,'response':response}


def checked_records(directory,revision,architecture):
    """Validate completed cases without pretending that cleanup has happened."""
    directory=Path(directory)
    records=[]
    for profile in PROFILES:
        for case in CASES:
            saved=load(directory/(profile+'-'+case+'.json'))
            require(set(saved)=={'schema','revision','architecture','profile','case','exit_code','response'}
                    and saved['schema']==RECORD,'container_saved_record_refused')
            require(saved==record(saved['response'],saved['exit_code'],profile,case,architecture,revision),
                    'container_saved_record_identity_mismatch')
            records.append(saved)
    positives=[row['response'] for row in records if row['case'] in ('create','reopen')]
    require(len({row['version'] for row in positives})==1
            and len({row['module_count'] for row in positives})==1,'container_cross_phase_source_mismatch')
    for profile in PROFILES:
        own=[row for row in positives if row['profile']==profile]
        require(len({row['saved_private_files'] for row in own})==1,'container_cross_phase_state_mismatch')
    return records


def summary(directory,revision,architecture):
    directory=Path(directory)
    records=checked_records(directory,revision,architecture)
    cleanup=load(directory/'cleanup.json')
    require(set(cleanup)=={'schema','revision','architecture','containers','volumes','image_tags'}
            and cleanup['schema']=='aikey-container-install-cleanup/1'
            and cleanup['revision']==revision and cleanup['architecture']==architecture
            and all(type(cleanup[key]) is int and cleanup[key]==0 for key in ('containers','volumes','image_tags')),
            'container_cleanup_not_verified')
    return {'schema':SUMMARY,'passed':True,'revision':revision,'architecture':architecture,
        'cases_finished':len(records),'own_fixture_containers_remaining':0,'own_fixture_volumes_remaining':0,
        'own_fixture_image_tags_remaining':0,'real_controller_contacted':False,'real_inference_performed':False,
        'native_verification':'needs_evidence','release_approved':False,
        'profiles':{profile:{case:next(row['response'] for row in records
                         if row['profile']==profile and row['case']==case) for case in CASES} for profile in PROFILES}}


def parser():
    root=argparse.ArgumentParser(description=__doc__)
    sub=root.add_subparsers(dest='command',required=True)
    context=sub.add_parser('context')
    context.add_argument('--repository',required=True)
    context.add_argument('--revision',required=True)
    context.add_argument('--nonce',required=True)
    make=sub.add_parser('manifest')
    make.add_argument('--source',type=Path,required=True)
    make.add_argument('--revision',required=True)
    make.add_argument('--output',type=Path,required=True)
    probe=sub.add_parser('probe')
    probe.add_argument('--expected',type=Path,required=True)
    probe.add_argument('--architecture',choices=('amd64','arm64'),required=True)
    probe.add_argument('--profile',choices=PROFILES,required=True)
    probe.add_argument('--phase',choices=('create','reopen'),required=True)
    save=sub.add_parser('record')
    save.add_argument('--response',type=Path,required=True)
    save.add_argument('--exit-code',type=int,required=True)
    save.add_argument('--profile',choices=PROFILES,required=True)
    save.add_argument('--case',choices=CASES,required=True)
    save.add_argument('--output',type=Path,required=True)
    finish=sub.add_parser('summary')
    finish.add_argument('--results',type=Path,required=True)
    finish.add_argument('--output',type=Path,required=True)
    for item in (save,finish):
        item.add_argument('--architecture',choices=('amd64','arm64'),required=True)
        item.add_argument('--revision',required=True)
    return root


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        if args.command=='context':
            print(git_context(args.repository,args.revision,args.nonce))
            return 0
        if args.command=='manifest':
            result=manifest(args.source,args.revision)
            publish(args.output,result)
            return 0
        if args.command=='probe':
            value=checked_source(load(args.expected))
            isolated_environment(args.architecture)
            installed(value)
            result=asyncio.run(profile_probe(STATE,args.profile,args.phase,dict(value,architecture=args.architecture)))
            print(json.dumps(result,sort_keys=True))
            return 0
        if args.command=='record':
            result=record(load(args.response),args.exit_code,args.profile,args.case,args.architecture,args.revision)
        else:
            result=summary(args.results,args.revision,args.architecture)
        publish(args.output,result)
        return 0
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,AssertionError,ImportError) as error:
        code=(error.args[0] if isinstance(error,CheckError) and error.args else 'container_install_check_refused')
        print(json.dumps({'schema':PHASE,'phase_passed':False,'error':code,'native_verification':'needs_evidence'}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
