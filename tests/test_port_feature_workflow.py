"""Explicit Port role scope and retained face-space compatibility, no models loaded."""
from copy import deepcopy
import json

import pytest

from aikey.aiport_candidate import CandidateError,CandidateService
from aikey.config import atomic_private
from aikey.port_feature_models import face_space,load_face_space,publish,load
from test_aiport_config_store import CAMERAS
from test_device_camera_pool import workflow_fixture
from test_device_workflow import revision
from test_state_backup_preflight import backup_of
from aikey.state_backup_preflight import inventory,validate


def model_settings(kind):
    if kind=='face':
        return {'detector_path':'/models/fictional-detector.onnx','detector_sha256':'a'*64,
            'embedder_path':'/models/fictional-embedder.onnx','embedder_sha256':'b'*64,'embedder_input':'arcface'}
    return {'model_path':'/models/fictional-sound.onnx','model_sha256':'c'*64,
        'class_map_path':'/models/fictional-classes.json','class_map_sha256':'d'*64}


def selection(kind,**changes):
    result={'feature':kind,'mode':'configure','cameras':[CAMERAS[0]]}
    if kind!='speech':
        result['model_settings']=model_settings(kind)
    if kind in ('speech','sound'):
        result['event_limit']=12
    if kind=='sound':
        result['types']=['alrmBark']
    return dict(result,**changes)


def save(workflow,path,settings):
    preview=workflow.preview_port_feature(revision(path),settings)
    return workflow.apply(preview,acknowledgements=set(preview.required_consents))


@pytest.mark.parametrize('kind',['face','speech','sound'])
def test_initial_role_preserves_other_config_and_identity_and_policy_undo(tmp_path,kind):
    workflow,path,original=workflow_fixture(tmp_path)
    if kind=='sound':
        save(workflow,path,selection('speech'))
    before=path.read_bytes()
    identities={name:(tmp_path/name).read_bytes() for name in ('device.crt','device.key','controller-ca.pem')}
    save(workflow,path,selection(kind))
    saved=json.loads(path.read_bytes())
    assert saved['paired_streams']==original['paired_streams'] and saved['live_pool_detector']==original['live_pool_detector']
    assert all((tmp_path/name).read_bytes()==raw for name,raw in identities.items())
    undo=workflow.preview_rollback(revision(path))
    result=workflow.rollback(undo,acknowledgements=set(undo.required_consents))
    if kind=='face':
        restored=json.loads(path.read_bytes())
        reference=restored.pop('face_model_space_ref')
        assert restored==json.loads(before)
        assert result['outcome']=='prior_policy_restored_model_space_guard_retained'
        assert load_face_space(tmp_path,reference,original['mac'])
        with pytest.raises(ValueError,match='migration'):
            workflow.preview_port_feature(revision(path),selection('face',model_settings=dict(model_settings('face'),embedder_sha256='e'*64)))
    else:
        assert path.read_bytes()==before and result['outcome']=='prior_bytes_restored_and_read_back'


def test_disabling_face_keeps_guard_and_same_bytes_can_move_but_not_change(tmp_path):
    workflow,path,_=workflow_fixture(tmp_path)
    save(workflow,path,selection('face'))
    reference=json.loads(path.read_bytes())['face_model_space_ref']
    save(workflow,path,{'feature':'face','mode':'disable'})
    assert json.loads(path.read_bytes())['face_model_space_ref']==reference
    moved=dict(model_settings('face'),embedder_path='/models/moved.onnx')
    save(workflow,path,selection('face',model_settings=moved))
    assert json.loads(path.read_bytes())['face_model_space_ref']==reference
    with pytest.raises(ValueError,match='migration'):
        workflow.preview_port_feature(revision(path),selection('face',model_settings=dict(moved,embedder_input='adaface')))
    with pytest.raises(ValueError,match='migration'):
        workflow.store.preview_patch(revision(path),{'live_face':{'embedder_sha256':'e'*64}})
    save(workflow,path,{'feature':'face','mode':'disable'})
    with pytest.raises(ValueError,match='migration'):
        workflow.store.preview_patch(revision(path),{'live_face':dict(moved,embedder_sha256='e'*64,cameras=[CAMERAS[0]])})
    with pytest.raises(ValueError,match='migration'):
        workflow.store.preview_patch(revision(path),{'live_face':{'cameras':[CAMERAS[0]]}})


def test_legacy_face_configuration_cannot_silently_switch_vector_space(tmp_path):
    workflow,path,original=workflow_fixture(tmp_path)
    original['live_face']=dict(model_settings('face'),cameras=[CAMERAS[0]],payload='full')
    atomic_private(path,json.dumps(original))
    with pytest.raises(ValueError,match='migration'):
        workflow.preview_port_feature(revision(path),selection('face',model_settings=dict(model_settings('face'),detector_sha256='e'*64)))
    save(workflow,path,selection('face'))
    assert json.loads(path.read_bytes())['face_model_space_ref']


def test_sound_never_grants_parent_audio_or_removes_dependent_scope(tmp_path):
    workflow,path,_=workflow_fixture(tmp_path)
    before=path.read_bytes()
    with pytest.raises(ValueError):
        workflow.preview_port_feature(revision(path),selection('sound'))
    assert path.read_bytes()==before
    save(workflow,path,selection('speech'))
    save(workflow,path,selection('sound'))
    before=path.read_bytes()
    for setting in ({'feature':'speech','mode':'disable'},selection('speech',cameras=[CAMERAS[1]])):
        with pytest.raises(ValueError):
            workflow.preview_port_feature(revision(path),setting)
        assert path.read_bytes()==before
    save(workflow,path,{'feature':'sound','mode':'disable'})
    save(workflow,path,{'feature':'speech','mode':'disable'})
    assert 'live_speech_cameras' not in json.loads(path.read_bytes())


@pytest.mark.parametrize('damage',['missing','wrong_mac','changed_encoder','generic_reset'])
def test_selected_face_space_fails_closed_and_generic_policy_cannot_reset(tmp_path,damage):
    workflow,path,original=workflow_fixture(tmp_path)
    save(workflow,path,selection('face'))
    config=json.loads(path.read_bytes())
    reference=config['face_model_space_ref']
    if damage=='generic_reset':
        changed=deepcopy(config)
        changed.pop('face_model_space_ref')
        with pytest.raises(ValueError):
            workflow.store._assert_policy_only(config,changed)
        return
    if damage=='missing':
        (tmp_path/reference).unlink()
    elif damage=='wrong_mac':
        reference,raw=face_space(config['live_face'],'020000000099')
        atomic_private(tmp_path/reference,raw)
        config['face_model_space_ref']=reference
        atomic_private(path,json.dumps(config))
    else:
        config['live_face']['embedder_sha256']='e'*64
        atomic_private(path,json.dumps(config))
    with pytest.raises((CandidateError,OSError,ValueError)):
        CandidateService.from_configuration_file(path)
    assert not inventory({workflow.profile:('aiport',tmp_path)})['ready_for_backup']


def test_face_guard_backup_consumer_cannot_omit_resource_even_with_recomputed_manifest(tmp_path):
    live=tmp_path/'live'
    live.mkdir(mode=0o700)
    workflow,path,_=workflow_fixture(live)
    atomic_private(live/'identity.json',json.dumps({'mac':json.loads(path.read_bytes())['mac']}))
    save(workflow,path,selection('face'))
    profiles={workflow.profile:('aiport',live)}
    backup=backup_of(tmp_path,profiles)
    assert validate(backup,profiles)['restorable']
    name=json.loads(path.read_bytes())['face_model_space_ref']
    target=backup/workflow.profile.replace(':','_')
    (target/name).unlink()
    manifest=json.loads((backup/'manifest.json').read_bytes())
    del manifest['profiles'][workflow.profile]['files'][name]
    (backup/'manifest.json').write_text(json.dumps(manifest))
    assert not validate(backup,profiles)['restorable']


@pytest.mark.parametrize('kind',['face','sound'])
def test_host_publication_hashes_owned_bytes_without_loading_and_never_overwrites(tmp_path,kind):
    tmp_path.chmod(0o700)
    fields=('detector','embedder') if kind=='face' else ('model','class_map')
    files={}
    for field in fields:
        source=tmp_path/field
        source.write_bytes(b'fictional fixture, deliberately not a model')
        source.chmod(0o600)
        files[field]=(source,'/models/'+field)
    output=tmp_path/'binding.json'
    binding=publish(output,kind=kind,name='Fictional test set',files=files)
    assert load(output)==binding and output.stat().st_mode&0o777==0o600
    assert binding['publication_check']=='owned_file_sha256_only'
    before=output.read_bytes()
    with pytest.raises(FileExistsError):
        publish(output,kind=kind,name='Replacement',files=files)
    assert output.read_bytes()==before
    source=files[fields[0]][0]
    source.chmod(0o666)
    with pytest.raises(ValueError):
        publish(tmp_path/'unsafe.json',kind=kind,name='Unsafe',files=files)
    assert not (tmp_path/'unsafe.json').exists()
