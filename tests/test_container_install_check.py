"""Synthetic image/provenance/receipt contracts; actual Docker runs are CI-only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ENTRY=Path(__file__).resolve().parents[1]/'tools/container_install_check.py'
SPEC=importlib.util.spec_from_file_location('container_install_check',ENTRY)
check=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)
REV='f'*40


def source_fixture(tmp_path):
    root=tmp_path/'source'
    (root/'src/aikey').mkdir(parents=True)
    (root/'src/aikey/__init__.py').write_text('VALUE = 1\n')
    (root/'tools').mkdir()
    (root/'tools/container_install_check.py').write_bytes(ENTRY.read_bytes())
    (root/'pyproject.toml').write_text('[project]\nversion="0.1.0"\nlicense-files=["LICENSE", "THIRD_PARTY_NOTICES.txt"]\n')
    (root/'LICENSE').write_text('Synthetic license fixture\n')
    (root/'THIRD_PARTY_NOTICES.txt').write_text('Synthetic third-party attribution\n')
    return root,check.manifest(root,REV)


def installed_fixture(tmp_path,monkeypatch):
    root,value=source_fixture(tmp_path)
    purelib=tmp_path/'purelib'
    (purelib/'aikey').mkdir(parents=True)
    (purelib/'aikey/__init__.py').write_bytes((root/'src/aikey/__init__.py').read_bytes())
    name=Path('local_aikey-0.1.0.dist-info/licenses/LICENSE')
    (purelib/name).parent.mkdir(parents=True)
    (purelib/name).write_bytes((root/'LICENSE').read_bytes())
    notice=name.with_name('THIRD_PARTY_NOTICES.txt')
    (purelib/notice).write_bytes((root/'THIRD_PARTY_NOTICES.txt').read_bytes())
    monkeypatch.setattr(check.sysconfig,'get_path',lambda _:str(purelib))
    monkeypatch.setattr(check.importlib.util,'find_spec',lambda _:SimpleNamespace(origin=str(purelib/'aikey/__init__.py')))
    dist=SimpleNamespace(version='0.1.0',files=[name,notice],locate_file=lambda item:purelib/item)
    monkeypatch.setattr(check.importlib.metadata,'distribution',lambda _:dist)
    return value,purelib,dist


def positive(profile='aikey',phase='create',**changes):
    return {'schema':check.PHASE,'phase_passed':True,'profile':profile,'phase':phase,
        'architecture':'amd64','revision':REV,'version':'0.1.0','module_count':170,
        'saved_private_files':8,'loopback_lab_checks':20 if phase=='create' else 0,
        'real_controller_contacted':False,'real_inference_performed':False,
        'native_verification':'needs_evidence'}|changes


def negative(case):
    return {'schema':check.PHASE,'phase_passed':False,'error':{
        'wrong-source':'container_installed_source_mismatch','wrong-arch':'container_architecture_mismatch'}[case],
        'native_verification':'needs_evidence'}


def complete_records(tmp_path):
    for profile in check.PROFILES:
        for case in check.CASES:
            result=positive(profile,case) if case in ('create','reopen') else negative(case)
            value=check.record(result,0 if case in ('create','reopen') else 2,profile,case,'amd64',REV)
            check.publish(tmp_path/(profile+'-'+case+'.json'),value)
    cleanup={'schema':'aikey-container-install-cleanup/1','revision':REV,'architecture':'amd64',
             'containers':0,'volumes':0,'image_tags':0}
    check.publish(tmp_path/'cleanup.json',cleanup)


def test_actual_manifest_cli_from_unrelated_cwd_is_source_bound_and_create_only(tmp_path):
    root,value=source_fixture(tmp_path)
    output=tmp_path/'manifest.json'
    args=[sys.executable,str(ENTRY),'manifest','--source',str(root),'--revision',REV,'--output',str(output)]
    first=subprocess.run(args,cwd=tmp_path,env={},capture_output=True,text=True,timeout=10)
    assert first.returncode==0 and not first.stdout and not first.stderr
    assert check.load(output)==value and output.stat().st_mode&0o777==0o600
    saved=output.read_bytes()
    second=subprocess.run(args,cwd=tmp_path,env={},capture_output=True,text=True,timeout=10)
    assert second.returncode==2 and output.read_bytes()==saved
    assert json.loads(second.stdout)['phase_passed'] is False


def test_git_context_uses_one_verified_public_host_and_immutable_full_commit():
    assert check.git_context('example/project',REV,'1-2-arm64')=='https://github.com/example/project.git#'+REV


@pytest.mark.parametrize('value',['example/project\n$(touch canary)','example/project?x=y',
    'example/project/other','https://other.invalid/project','../project','example/.','example/project\r'])
def test_multiline_or_url_identity_cannot_reach_docker_context(value):
    with pytest.raises(check.CheckError):
        check.git_context(value,REV,'1-2-amd64')


@pytest.mark.parametrize('fault',['unknown','path','revision','hash','bool_modules'])
def test_source_metadata_rejects_structural_and_path_ambiguity(tmp_path,fault):
    _,value=source_fixture(tmp_path)
    if fault=='unknown':
        value['private_payload']='fictional-canary'
    elif fault=='path':
        value['modules']={'aikey/../../outside.py':'a'*64}
    elif fault=='revision':
        value['revision']=int('1'*40)
    elif fault=='hash':
        value['license_files']['LICENSE']='a'*64+'\n'
    else:
        value['modules']=True
    with pytest.raises(check.CheckError):
        check.checked_source(value)


def test_installed_package_bytes_license_and_version_are_verified_before_application_import(tmp_path,monkeypatch):
    value,_,_=installed_fixture(tmp_path,monkeypatch)
    assert check.installed(value)==1


@pytest.mark.parametrize('fault',['shadow','binary_origin','changed','extra','missing','symlink','version','license','probe'])
def test_install_provenance_refuses_checkout_shadow_and_inconsistent_artifacts(tmp_path,monkeypatch,fault):
    value,purelib,dist=installed_fixture(tmp_path,monkeypatch)
    module=purelib/'aikey/__init__.py'
    if fault=='shadow':
        monkeypatch.setattr(check.importlib.util,'find_spec',lambda _:SimpleNamespace(origin=str(tmp_path/'checkout/aikey/__init__.py')))
    elif fault=='binary_origin':
        monkeypatch.setattr(check.importlib.util,'find_spec',lambda _:SimpleNamespace(origin=str(purelib/'aikey/__init__.so')))
    elif fault=='changed':
        module.write_text('VALUE = 2\n')
    elif fault=='extra':
        (module.parent/'extra.py').write_text('VALUE = 3\n')
    elif fault=='missing':
        module.unlink()
    elif fault=='symlink':
        target=tmp_path/'retained.py'
        module.rename(target)
        module.symlink_to(target)
    elif fault=='version':
        dist.version='other'
    elif fault=='license':
        dist.locate_file(dist.files[0]).write_text('Different synthetic license\n')
    else:
        value['probe_sha256']='0'*64
    with pytest.raises((check.CheckError,OSError)):
        check.installed(value)


@pytest.mark.parametrize('raw',[b'{"schema":1,"schema":2}',b'{"x":NaN}',b'[]'])
def test_duplicate_nonfinite_and_nonobject_protocols_are_not_accepted(tmp_path,raw):
    path=tmp_path/'response.json'
    path.write_bytes(raw)
    with pytest.raises((ValueError,check.CheckError)):
        check.load(path)


def test_zero_stat_size_regular_files_remain_bounded_and_empty_modules_are_valid(tmp_path):
    root,value=source_fixture(tmp_path)
    (root/'src/aikey/__init__.py').write_bytes(b'')
    value=check.manifest(root,REV)
    assert value['modules']['aikey/__init__.py']==check.digest(b'')


@pytest.mark.parametrize('fault',[None,'architecture','root_uid','writable_root','network','capabilities','new_privileges'])
def test_guest_kernel_preflight_does_not_infer_isolation_from_caller_flags(tmp_path,monkeypatch,fault):
    monkeypatch.setattr(check.platform,'system',lambda:'Linux')
    monkeypatch.setattr(check.platform,'machine',lambda:'aarch64' if fault=='architecture' else 'x86_64')
    monkeypatch.setattr(check.os,'geteuid',lambda:0 if fault=='root_uid' else 10001)
    monkeypatch.setattr(check.os,'getegid',lambda:10001)
    monkeypatch.setattr(check.os,'statvfs',lambda _:SimpleNamespace(f_flag=0 if fault=='writable_root' else os.ST_RDONLY))
    original=Path.iterdir
    def interfaces(path):
        if path==Path('/sys/class/net'):
            return iter([Path('lo'),Path('eth0')] if fault=='network' else [Path('lo')])
        return original(path)
    monkeypatch.setattr(Path,'iterdir',interfaces)
    content=('CapEff:\t'+('0000000000000001' if fault=='capabilities' else '0000000000000000')+'\n'
             +'NoNewPrivs:\t'+('0' if fault=='new_privileges' else '1')+'\n').encode()
    monkeypatch.setattr(check,'read',lambda *a,**kw:content)
    if fault is None:
        check.isolated_environment('amd64')
    else:
        with pytest.raises(check.CheckError):
            check.isolated_environment('amd64')


@pytest.mark.parametrize('kind',['fifo','symlink','hardlink','oversize'])
def test_bounded_reader_does_not_block_or_follow_special_files(tmp_path,kind):
    path=tmp_path/'input'
    if kind=='fifo':
        os.mkfifo(path)
    elif kind=='symlink':
        path.symlink_to(tmp_path/'absent')
    else:
        path.write_bytes(b'{}' if kind=='hardlink' else b'a'*30)
        if kind=='hardlink':
            os.link(path,tmp_path/'retained')
    with pytest.raises((OSError,check.CheckError)):
        check.read(path,limit=10)


@pytest.mark.parametrize('case',check.CASES)
def test_phase_exit_and_body_must_agree_before_record_can_be_published(case):
    response=positive(phase=case) if case in ('create','reopen') else negative(case)
    code=0 if case in ('create','reopen') else 2
    assert check.record(response,code,'aikey',case,'amd64',REV)['exit_code']==code
    with pytest.raises(check.CheckError):
        check.record(response,2 if code==0 else 0,'aikey',case,'amd64',REV)


@pytest.mark.parametrize('fault',['role','revision','arch','bool_count','unknown','real_model','unproved_native'])
def test_schema_valid_but_contradictory_phase_never_becomes_success(fault):
    value=positive()
    key,item={'role':('profile','aiport'),'revision':('revision','a'*40),
        'arch':('architecture','arm64'),'bool_count':('module_count',True),
        'unknown':('unreviewed_private_payload','fictional-canary'),
        'real_model':('real_inference_performed',True),'unproved_native':('native_verification','verified')}[fault]
    value[key]=item
    with pytest.raises(check.CheckError):
        check.record(value,0,'aikey','create','amd64',REV)


def test_whole_result_requires_all_eight_bound_records_and_cleanup(tmp_path):
    complete_records(tmp_path)
    result=check.summary(tmp_path,REV,'amd64')
    assert result['passed'] and result['cases_finished']==8 and not result['release_approved']
    assert result['native_verification']=='needs_evidence'


@pytest.mark.parametrize('fault',['missing_case','failed_cleanup','missing_cleanup','stale_revision','wrong_role','false_zero',
    'different_version','different_modules','different_state_count'])
def test_partial_or_stale_result_cannot_claim_whole_run_success(tmp_path,fault):
    complete_records(tmp_path)
    target=tmp_path/('cleanup.json' if 'cleanup' in fault or fault=='false_zero' else 'aikey-create.json')
    if fault in ('missing_case','missing_cleanup'):
        target.unlink()
    else:
        data=check.load(target)
        if fault=='failed_cleanup':
            data['volumes']=1
        elif fault=='false_zero':
            data['containers']=False
        elif fault=='stale_revision':
            data['revision']='b'*40
        elif fault in ('different_version','different_modules','different_state_count'):
            key,value={'different_version':('version','0.2.0'),'different_modules':('module_count',171),
                       'different_state_count':('saved_private_files',9)}[fault]
            data['response'][key]=value
        else:
            data['profile']='aiport'
        target.write_text(json.dumps(data))
    with pytest.raises((OSError,check.CheckError)):
        check.summary(tmp_path,REV,'amd64')


@pytest.mark.parametrize('profile',check.PROFILES)
async def test_actual_private_profile_constructor_reopens_without_identity_change_or_network(tmp_path,profile,monkeypatch):
    root=tmp_path/'private'
    root.mkdir(mode=0o700)
    import aiohttp
    monkeypatch.setattr(aiohttp,'ClientSession',lambda *a,**kw:pytest.fail('native transport unexpectedly constructed'))
    await check.open_profile(root,profile,create=True)
    first=check.snapshot(root)
    await check.open_profile(root,profile,create=False)
    assert check.snapshot(root)==first and len(first)>=5


def test_actual_cli_unknown_errors_do_not_print_paths_or_payloads(tmp_path):
    secret_path=tmp_path/'fictional-private-canary'
    result=subprocess.run([sys.executable,str(ENTRY),'probe','--expected',str(secret_path),
        '--architecture','amd64','--profile','aikey','--phase','create'],
        cwd=tmp_path,env={},capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and not result.stderr and str(secret_path) not in result.stdout
    assert json.loads(result.stdout)['error']=='container_install_check_refused'


@pytest.mark.parametrize('fault',['missing','modified','unlisted','duplicate'])
def test_declared_third_party_notice_must_survive_actual_install(tmp_path,monkeypatch,fault):
    value,purelib,dist=installed_fixture(tmp_path,monkeypatch)
    notice=dist.files[1]
    if fault=='missing':
        dist.locate_file(notice).unlink()
        dist.files.remove(notice)
    elif fault=='modified':
        dist.locate_file(notice).write_text('Missing original attribution\n')
    elif fault=='unlisted':
        dist.files.remove(notice)
    else:
        dist.files.append(notice)
    with pytest.raises((check.CheckError,OSError)):
        check.installed(value)


@pytest.mark.parametrize('names',[[],['THIRD_PARTY_NOTICES.txt'],['LICENSE','LICENSE'],['../LICENSE'],['/LICENSE'],
    ['LICENSE*'],['LICENSE\n'],[True],'LICENSE'])
def test_declared_license_paths_are_exact_bounded_root_names(tmp_path,names):
    root,_=source_fixture(tmp_path)
    import tomllib
    original=check.tomllib.loads
    def project(raw):
        value=original(raw)
        value['project']['license-files']=names
        return value
    from unittest.mock import patch
    with patch.object(tomllib,'loads',project), pytest.raises(check.CheckError):
        check.manifest(root,REV)


def test_source_manifest_cannot_silently_drop_existing_main_license(tmp_path):
    _,value=source_fixture(tmp_path)
    value['license_files'].pop('LICENSE')
    with pytest.raises(check.CheckError):
        check.checked_source(value)
