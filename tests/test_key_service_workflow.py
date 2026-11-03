"""Typed local-role changes preserve templates/identities and search guards."""
import json

import pytest

from aikey.config import atomic_private
from aikey.faces import FaceStore
from aikey.config_store import UnsafeConfigurationChange
from test_device_workflow import setup,revision


def selection(role):
    fields={'face_recognition':{'server':'http://127.0.0.1:8181','camera_ids':['fictional-camera-a'],
        'max_faces':8,'native_face_cameras':'skip'},'face_enhancement':{'server':'http://127.0.0.1:8182'},
        'deep_understanding':{'reid_server':'http://127.0.0.1:8183','rerank_server':'http://127.0.0.1:8184'}}
    return dict(fields[role],role=role,mode='configure')


@pytest.mark.parametrize('role',['face_recognition','face_enhancement','deep_understanding'])
def test_initial_change_retained_template_exact_undo_and_unrelated_role_bytes(tmp_path,role):
    workflow,path,_=setup(tmp_path,'aikey')
    FaceStore(path.parent).enroll('Fictional Synthetic',[1.0]+[0.0]*127,embedding_space='f'*64)
    template=path.parent/'faces'/'identities.json'
    retained=template.read_bytes()
    identity={name:(path.parent/name).read_bytes() for name in ('device.key','device.crt','identity.json')}
    before=path.read_bytes()
    preview=workflow.preview_key_service(revision(path),selection(role))
    assert path.read_bytes()==before and preview.changed_fields==(role,)
    workflow.apply(preview,acknowledgements=set(preview.required_consents))
    current=json.loads(path.read_bytes())
    assert current.pop(role) and current==json.loads(before)
    undo=workflow.preview_rollback(revision(path))
    assert undo.changed_fields==(role,) and 'restore_saved_local_feature_scope' in undo.required_consents
    workflow.rollback(undo,acknowledgements=set(undo.required_consents))
    assert path.read_bytes()==before and template.read_bytes()==retained
    assert all((path.parent/name).read_bytes()==raw for name,raw in identity.items())


def test_server_replacement_and_disable_have_current_exact_undo(tmp_path):
    workflow,path,_=setup(tmp_path,'aikey')
    preview=workflow.preview_key_service(revision(path),selection('face_recognition'))
    workflow.apply(preview,acknowledgements=set(preview.required_consents))
    before=path.read_bytes()
    preview=workflow.preview_key_service(revision(path),dict(selection('face_recognition'),server='http://127.0.0.1:8185'))
    workflow.apply(preview,acknowledgements=set(preview.required_consents))
    undo=workflow.preview_rollback(revision(path))
    assert undo.changed_fields==('face_recognition',)
    workflow.rollback(undo,acknowledgements=set(undo.required_consents))
    assert path.read_bytes()==before
    preview=workflow.preview_key_service(revision(path),{'role':'face_recognition','mode':'disable'})
    assert preview.required_consents==('disable_saved_local_feature',)
    workflow.apply(preview,acknowledgements=set(preview.required_consents))
    assert 'face_recognition' not in json.loads(path.read_bytes())


@pytest.mark.parametrize('role,changes',[('face_recognition',{'max_faces':True}),('face_recognition',{'camera_ids':[]}),
    ('face_recognition',{'server':'https://public.invalid'}),('face_enhancement',{'server':'http://127.0.0.1:8182/path'}),
    ('deep_understanding',{'reid_server':'http://user:password@127.0.0.1'}),('deep_understanding',{'model':'new-incompatible-vector-space'})])
def test_invalid_roles_or_destinations_do_not_write(tmp_path,role,changes):
    workflow,path,_=setup(tmp_path,'aikey')
    before=path.read_bytes()
    with pytest.raises(ValueError):
        workflow.preview_key_service(revision(path),dict(selection(role),**changes))
    assert path.read_bytes()==before and not workflow.journal.exists()


def test_existing_search_profile_guard_cannot_be_bypassed_by_local_feature_setup(tmp_path,monkeypatch):
    workflow,path,_=setup(tmp_path,'aikey')
    before=path.read_bytes()
    seen=[]
    def index_guard(candidate,current):
        seen.append(True)
        assert candidate['embeddings']==current['embeddings']
        raise UnsafeConfigurationChange('synthetic retained incompatible index state')
    monkeypatch.setattr(workflow.store,'_assert_search_profile',index_guard)
    with pytest.raises(UnsafeConfigurationChange):
        workflow.preview_key_service(revision(path),selection('deep_understanding'))
    assert seen==[True] and path.read_bytes()==before


def test_later_unrelated_provider_change_cannot_be_stomped_by_old_local_role_undo(tmp_path):
    workflow,path,_=setup(tmp_path,'aikey')
    preview=workflow.preview_key_service(revision(path),selection('face_enhancement'))
    workflow.apply(preview,acknowledgements=set(preview.required_consents))
    changed=json.loads(path.read_bytes())
    changed['inference']['model']='unrelated-later-model'
    atomic_private(path,json.dumps(changed))
    assert not workflow.transaction_status()['rollback_available']
