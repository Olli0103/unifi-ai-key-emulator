"""Copy/manifest/readback/Undo consumer contract, with synthetic identities only."""
import hashlib
import json
import shutil

import pytest

from aikey.config import atomic_private
from aikey.config_store import ConfigurationStore
from aikey.aiport_config_store import AiPortConfigurationStore
from aikey.device_workflow import DeviceWorkflowStore, DeviceOutcomeUncertain
from aikey.controller_trust import DIRECTORY
from aikey.state_backup_preflight import inventory, validate
from aikey import device_workflow as module
from test_device_workflow import setup, apply, revision
from test_state_backup_preflight import backup_of


def fixture(tmp_path, kind):
    live_root=tmp_path/'live'
    live_root.mkdir(mode=0o700)
    workflow,path,selection=setup(live_root,kind)
    if kind=='aikey':
        current=json.loads(path.read_bytes())
        atomic_private(path.parent/'device-state.json',json.dumps({'mac':current['device']['mac'],'adopted':True}))
    return workflow,path,selection,{workflow.profile:(kind,path.parent)}


@pytest.mark.parametrize('kind',['aikey','aiport'])
def test_complete_copied_state_validates_and_restored_consumer_can_exact_undo(tmp_path,kind):
    workflow,path,selection,profiles=fixture(tmp_path,kind)
    before=path.read_bytes()
    apply(workflow,path,selection)
    assert inventory(profiles)['ready_for_backup']
    backup=backup_of(tmp_path,profiles)
    assert validate(backup,profiles)['restorable']
    copy=tmp_path/'restored'
    shutil.copytree(backup/workflow.profile.replace(':','_'),copy)
    restored_path=copy/'config.json'
    store=ConfigurationStore(restored_path) if kind=='aikey' else AiPortConfigurationStore(restored_path)
    restored=DeviceWorkflowStore(workflow.profile,store,copy)
    undo=restored.preview_rollback(revision(restored_path))
    restored.rollback(undo,acknowledgements=set(undo.required_consents))
    assert restored_path.read_bytes()==before
    assert path.read_bytes()!=before  # no source/production rollback


@pytest.mark.parametrize('damage',['retained_object','archive','journal'])
def test_omitted_retained_material_cannot_be_hidden_by_a_recomputed_complete_manifest(tmp_path,damage):
    workflow,path,selection,profiles=fixture(tmp_path,'aiport')
    apply(workflow,path,selection)
    first_object=next((path.parent/DIRECTORY).iterdir())
    # Add another valid digest-bound retained object that is not the current
    # selection: it must survive a backup even when current TLS still works.
    raw=first_object.read_bytes()+b'\n'
    historical=path.parent/DIRECTORY/('controller-trust.'+hashlib.sha256(raw).hexdigest()+'.json')
    atomic_private(historical,raw)
    backup=backup_of(tmp_path,profiles)
    target=backup/workflow.profile.replace(':','_')
    if damage=='retained_object':
        removed=target/DIRECTORY/historical.name
    elif damage=='archive':
        removed=next((target/'.aiport-config-history').iterdir())
    else:
        removed=target/workflow.journal.name
    name=removed.relative_to(target).as_posix()
    removed.unlink()
    manifest=json.loads((backup/'manifest.json').read_bytes())
    del manifest['profiles'][workflow.profile]['files'][name]
    (backup/'manifest.json').write_text(json.dumps(manifest))
    assert not validate(backup,profiles)['restorable']


def test_pending_live_intent_blocks_backup_and_restore_even_with_valid_old_copy(tmp_path,monkeypatch):
    workflow,path,selection,profiles=fixture(tmp_path,'aiport')
    backup=backup_of(tmp_path,profiles)
    original=module.atomic_private
    def lost_ack(target,raw):
        original(target,raw)
        if target==workflow.journal:
            raise OSError('synthetic uncertain intent publication')
    monkeypatch.setattr(module,'atomic_private',lost_ack)
    with pytest.raises(DeviceOutcomeUncertain):
        apply(workflow,path,selection)
    assert not inventory(profiles)['ready_for_backup']
    result=validate(backup,profiles)
    assert not result['restorable']
    assert 'live_profile_unreadable' in result['profiles'][workflow.profile]['problems']
