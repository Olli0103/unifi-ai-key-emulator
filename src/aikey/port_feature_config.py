"""Pure explicit Port face/speech/sound selections; no model or media access."""
from copy import deepcopy

from .aiport_ingest import normalize_mac
from .port_feature_models import checked,SCHEMA

ROOTS={'face':{'live_face'},'speech':{'live_speech_cameras','live_speech_max_events_per_hour'},'sound':{'live_sound'}}


def candidate(current,selection):
    if (not isinstance(selection,dict) or not {'feature','mode'}<=set(selection)
            or selection['feature'] not in ROOTS or selection['mode'] not in ('configure','disable')):
        raise ValueError('invalid_port_feature_selection')
    feature=selection['feature']
    result=deepcopy(current)
    if selection['mode']=='disable':
        if set(selection)!={'feature','mode'}:
            raise ValueError('invalid_port_feature_selection')
        for field in ROOTS[feature]:
            result.pop(field,None)
        return result
    expected={'feature','mode','cameras'}|({'event_limit'} if feature=='speech' else {'model_settings'})
    if feature=='sound':
        expected|={'types','event_limit'}
    cameras=selection.get('cameras')
    if (set(selection)!=expected or not isinstance(cameras,list) or not 1<=len(cameras)<=5
            or any(not isinstance(value,str) or normalize_mac(value)!=value for value in cameras)
            or len(set(cameras))!=len(cameras)
            or not set(cameras)<={normalize_mac(row['camera_mac']) for row in current.get('paired_streams',[])}):
        raise ValueError('invalid_port_feature_camera_scope')
    if feature=='speech':
        limit=selection['event_limit']
        if type(limit) is not int or not 1<=limit<=3600:
            raise ValueError('invalid_speech_policy_limit')
        result.update(live_speech_cameras=list(cameras),live_speech_max_events_per_hour=limit)
    else:
        binding=checked({'schema':SCHEMA,'name':'server-bound selected model set','kind':feature,
            'settings':selection['model_settings'],'publication_check':'saved_configuration_only'})
        policy=dict(binding['settings'],cameras=list(cameras))
        if feature=='sound':
            from .aiport_sounds import SOUND_TYPES
            types,limit=selection['types'],selection['event_limit']
            if (not isinstance(types,list) or not types or any(not isinstance(value,str) for value in types)
                    or len(set(types))!=len(types) or not set(types)<=set(SOUND_TYPES)
                    or type(limit) is not int or not 1<=limit<=120):
                raise ValueError('invalid_sound_policy')
            policy.update(types=list(types),max_events_per_hour=limit)
        else:
            policy['payload']='full'
        result['live_'+feature]=policy
    return result


def assert_scope(before,after):
    changed={field for field in set(before)|set(after) if before.get(field)!=after.get(field)}
    if changed and not any(changed<=(roots|({'face_model_space_ref'} if feature=='face' else set()))
                           for feature,roots in ROOTS.items()):
        raise ValueError('one_port_feature_and_no_unrelated_changes')
    if before.get('face_model_space_ref') and before['face_model_space_ref']!=after.get('face_model_space_ref'):
        raise ValueError('face_model_space_migration_required')
