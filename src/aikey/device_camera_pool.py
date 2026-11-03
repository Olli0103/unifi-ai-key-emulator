"""Pure local relay-allowlist edits, distinct from native Protect pairing.

No camera setting, credential, native zone or adopted identity is invented.
New cameras inherit only the explicitly reviewed object detector; face, plate,
speech and sound enrollment is never expanded by this operation.
"""
from copy import deepcopy

from .aiport_ingest import normalize_mac, private_source_ip


def candidate(current, selection):
    if (not isinstance(selection, dict) or not {'cameras', 'decoder'} <= set(selection) <= {'cameras', 'decoder', 'detector'}
            or not isinstance(selection['cameras'], list) or len(selection['cameras']) > 5
            or any(not isinstance(value, str) or normalize_mac(value) != value for value in selection['cameras'])
            or len(set(selection['cameras'])) != len(selection['cameras'])):
        raise ValueError('invalid_camera_pool_selection')
    # A decoder path is supplied by the host-owned binding, never an HTTP field.
    from .aiport_candidate import _offline_decoder_path
    decoder = _offline_decoder_path(selection['decoder'])
    relay = private_source_ip(current['controller_ip'])
    if 'paired_stream' in current or any(key.startswith('diagnostic_') for key in current):
        raise ValueError('diagnostic_profile_not_camera_pool')
    old = current.get('paired_streams', [])
    detector = current.get('live_pool_detector')
    if 'detector' in selection:
        if detector is not None or not isinstance(selection['detector'], dict) or not selection['cameras']:
            raise ValueError('camera_pool_bootstrap_only')
        detector = deepcopy(selection['detector'])
    selected = set(selection['cameras'])
    prior = {normalize_mac(row['camera_mac']): row for row in old}
    if not selected:
        # Disable all local capture and its dependent feature configuration.
        # The review/Undo separately discloses the removed permission surface;
        # keys/models/trust/identities are retained, not erased or rotated.
        result = deepcopy(current)
        for name in ('paired_streams', 'live_pool_detector', 'live_speech_cameras',
                     'live_speech_max_events_per_hour', 'live_face', 'live_sound'):
            result.pop(name, None)
        return result
    if not isinstance(detector, dict):
        raise ValueError('camera_pool_detector_setup_required')
    result = deepcopy(current)
    result['live_pool_detector'] = deepcopy(detector)
    for name in ('live_speech_cameras',):
        if name in result:
            result[name] = [value for value in result[name] if normalize_mac(value) in selected]
            if not result[name]:
                result.pop(name)
                result.pop('live_speech_max_events_per_hour', None)
    for name in ('live_face', 'live_sound'):
        if name in result:
            result[name]['cameras'] = [value for value in result[name]['cameras'] if normalize_mac(value) in selected]
            if not result[name]['cameras']:
                result.pop(name)
    policy = result['live_pool_detector']
    if 'plate_cameras' in policy:
        policy['plate_cameras'] = [value for value in policy['plate_cameras'] if normalize_mac(value) in selected]
        if not policy['plate_cameras']:
            policy.pop('plate_cameras')
            policy.pop('ocr_provider_config', None)
    if 'camera_thresholds' in policy:
        policy['camera_thresholds'] = {key: value for key, value in policy['camera_thresholds'].items()
                                       if normalize_mac(key) in selected}
        if not policy['camera_thresholds']:
            policy.pop('camera_thresholds')
    result['paired_streams'] = [deepcopy(prior[mac]) if mac in prior else {
        'camera_mac': mac, 'source_ip': relay, 'ffmpeg_path': decoder} for mac in selection['cameras']]
    return result


def assert_scope(before, after):
    """Re-derive the only permitted pool delta, including permission removals.

For Undo, the original candidate transition is checked in its forward
direction. A restored historical scope still needs separate explicit consent.
"""
    pool = after.get('paired_streams', [])
    previous = {normalize_mac(row['camera_mac']) for row in before.get('paired_streams', [])}
    added = [row for row in pool if normalize_mac(row['camera_mac']) not in previous]
    decoder = ((added or pool or before.get('paired_streams', []))[0]['ffmpeg_path'])
    selection = {'cameras': [normalize_mac(row['camera_mac']) for row in pool], 'decoder': decoder}
    if 'live_pool_detector' not in before and pool:
        selection['detector'] = after['live_pool_detector']
    if candidate(before, selection) != after:
        raise ValueError('camera_pool_unrelated_or_permission_expansion')
