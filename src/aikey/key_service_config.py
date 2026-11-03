"""Typed selections for existing local roles; no model/client construction."""
from copy import deepcopy

from .face_policy import recognition_policy, enhancement_policy
from .deep_mode import validate_config as deep_policy

ROLES=('face_recognition','face_enhancement','deep_understanding')


def candidate(current,selection):
    if (not isinstance(selection,dict) or not {'role','mode'}<=set(selection)
            or selection['role'] not in ROLES or selection['mode'] not in ('configure','disable')):
        raise ValueError('invalid_local_service_selection')
    role=selection['role']
    result=deepcopy(current)
    if selection['mode']=='disable':
        if set(selection)!={'role','mode'}:
            raise ValueError('invalid_local_service_selection')
        result.pop(role,None)
        return result
    fields=({'server','camera_ids','max_faces','native_face_cameras'} if role=='face_recognition'
            else {'server'} if role=='face_enhancement' else {'reid_server','rerank_server'})
    if set(selection)!={'role','mode'}|fields:
        raise ValueError('invalid_local_service_selection')
    value={key:selection[key] for key in fields}
    if role=='face_recognition':
        checked=recognition_policy(value)
        value=dict(checked,camera_ids=list(checked['camera_ids']))
    elif role=='face_enhancement':
        value=enhancement_policy(value)
    else:
        if value['rerank_server']=='':
            value.pop('rerank_server')
        value=deep_policy(value)
    result[role]=value
    return result


def assert_scope(before,after):
    old,new=deepcopy(before),deepcopy(after)
    changed=[role for role in ROLES if old.get(role)!=new.get(role)]
    if len(changed)>1:
        raise ValueError('one_local_role_per_review')
    for role in ROLES:
        old.pop(role,None)
        new.pop(role,None)
    if old!=new:
        raise ValueError('local_service_unrelated_scope_change')
