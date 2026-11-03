"""Private metadata/credential references only; zero ONVIF/native claims."""
import json

import pytest

from aikey.onvif_catalog import load, public
from aikey.device_workflow import DeviceWorkflowError
from aikey import device_workflow as module
from test_device_workflow import setup, revision
from test_state_backup_preflight import backup_of
from aikey.state_backup_preflight import inventory, validate


USERNAME='synthetic-camera-user'
PASSWORD='synthetic-never-issued-ONVIF-password'


def selection(**changes):
    return dict({'operation':'save','id':'a'*32,'name':'Fictional ONVIF camera','host':'192.168.50.30',
        'port':443,'tls':True,'certificate_pin':'d'*64,'username':USERNAME,'password':PASSWORD,
        'credential_slot':'b'*32},**changes)


def test_metadata_save_edit_and_exact_rollback_preserve_device_and_never_expose_credentials(tmp_path):
    workflow,path,_=setup(tmp_path,'aiport')
    before=path.read_bytes()
    static={name:(path.parent/name).read_bytes() for name in ('device.crt','device.key','identity.json')}
    preview=workflow.preview_onvif(revision(path),selection())
    assert path.read_bytes()==before and not list(path.parent.glob('onvif-*'))
    assert PASSWORD not in repr(preview) and USERNAME not in json.dumps(preview.public())
    workflow.apply(preview,acknowledgements=set(preview.required_consents))
    current=json.loads(path.read_bytes())
    assert set(current)-set(json.loads(before))=={'onvif_catalog_ref'}
    catalog=load(path.parent,current['onvif_catalog_ref'],current['mac'])
    view=public(catalog)
    assert view['configured_cameras'][0]['ingest']=='not_implemented'
    assert view['configured_cameras'][0]['processing_enabled'] is False
    assert PASSWORD not in json.dumps(view) and USERNAME not in json.dumps(view)
    assert all((path.parent/name).read_bytes()==raw for name,raw in static.items())
    assert len(list(path.parent.glob('onvif-*')))==3
    renamed=selection(name='Renamed synthetic camera',username='',password='',credential_slot=None)
    edit=workflow.preview_onvif(revision(path),renamed)
    workflow.apply(edit,acknowledgements=set(edit.required_consents))
    assert len(list(path.parent.glob('onvif-password-*')))==1
    undo=workflow.preview_rollback(revision(path))
    assert 'restore_onvif_saved_credentials' in undo.required_consents
    workflow.rollback(undo,acknowledgements=set(undo.required_consents))
    assert workflow.onvif_status()['configured_cameras'][0]['name']=='Fictional ONVIF camera'


@pytest.mark.parametrize('change',[{'host':'https://wrong.invalid'}, {'port':True}, {'port':0},
    {'tls':False,'certificate_pin':'d'*64},{'password':''},{'username':'line\nbreak'},
    {'username':'','password':'','credential_slot':None},{'certificate_pin':'not-a-pin'}])
def test_invalid_or_missing_initial_credentials_never_write(tmp_path,change):
    workflow,path,_=setup(tmp_path,'aiport')
    before=path.read_bytes()
    with pytest.raises(ValueError):
        workflow.preview_onvif(revision(path),selection(**change))
    assert path.read_bytes()==before and not list(path.parent.glob('onvif-*'))


def test_destination_retarget_cannot_silently_reuse_existing_credentials(tmp_path):
    workflow,path,_=setup(tmp_path,'aiport')
    preview=workflow.preview_onvif(revision(path),selection())
    workflow.apply(preview,acknowledgements=set(preview.required_consents))
    before=path.read_bytes()
    with pytest.raises(ValueError,match='new_credentials'):
        workflow.preview_onvif(revision(path),selection(host='another.invalid',username='',password='',credential_slot=None))
    assert path.read_bytes()==before


def test_removed_registration_retains_secret_files_and_undo_requires_them(tmp_path):
    workflow,path,_=setup(tmp_path,'aiport')
    preview=workflow.preview_onvif(revision(path),selection())
    workflow.apply(preview,acknowledgements=set(preview.required_consents))
    removed=workflow.preview_onvif(revision(path),{'operation':'remove','id':'a'*32})
    workflow.apply(removed,acknowledgements=set(removed.required_consents))
    assert not workflow.onvif_status()['configured_cameras']
    password=next(path.parent.glob('onvif-password-*'))
    assert password.read_text()==PASSWORD+'\n'
    password.unlink()  # fictional missing-resource boundary only
    assert not workflow.transaction_status()['rollback_available']


def test_catalog_and_secret_refs_are_in_the_actual_backup_consumer_contract(tmp_path):
    workflow,path,_=setup(tmp_path,'aiport')
    preview=workflow.preview_onvif(revision(path),selection())
    workflow.apply(preview,acknowledgements=set(preview.required_consents))
    profiles={workflow.profile:('aiport',path.parent)}
    assert inventory(profiles)['ready_for_backup']
    backup=backup_of(tmp_path,profiles)
    assert validate(backup,profiles)['restorable']
    target=backup/workflow.profile.replace(':','_')
    removed=next(target.glob('onvif-password-*'))
    name=removed.name
    removed.unlink()
    manifest=json.loads((backup/'manifest.json').read_bytes())
    del manifest['profiles'][workflow.profile]['files'][name]
    (backup/'manifest.json').write_text(json.dumps(manifest))
    assert not validate(backup,profiles)['restorable']


def test_credential_publication_failure_cannot_retarget_config_and_conflict_never_overwrites(tmp_path,monkeypatch):
    workflow,path,_=setup(tmp_path,'aiport')
    preview=workflow.preview_onvif(revision(path),selection())
    target=path.parent/('onvif-password-'+'b'*32)
    module.atomic_private(target,b'fictional retained conflicting bytes')
    before=path.read_bytes()
    with pytest.raises(DeviceWorkflowError,match='conflict'):
        workflow.apply(preview,acknowledgements=set(preview.required_consents))
    assert target.read_bytes()==b'fictional retained conflicting bytes' and path.read_bytes()==before
