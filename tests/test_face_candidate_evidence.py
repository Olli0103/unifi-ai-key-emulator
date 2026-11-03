"""Public artifact consistency, not independent authentication of NAS receipts."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'docs' / 'evidence' / 'face262-candidate-20261103'


def test_candidate_summary_is_manifest_bound_and_not_a_release():
    manifest = json.loads((ROOT / 'manifest.json').read_bytes())
    assert manifest['schema'] == 1
    assert manifest['not_a_release_or_native_feature_acceptance'] is True
    assert set(manifest['files']) == {'summary.json'}
    for name, digest in manifest['files'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    summary = json.loads((ROOT / 'summary.json').read_bytes())
    assert summary['candidate_source'] == manifest['source']
    assert set(summary) == {'schema', 'observation_date', 'scope', 'candidate_source', 'records'}
    assert summary['scope'] == 'existing_installation_candidate_not_deployed'


def test_all_candidate_stages_preserve_the_not_installed_boundary():
    records = json.loads((ROOT / 'summary.json').read_bytes())['records']
    assert set(records) == {'native-build-result', 'native-qualify-result',
        'native-admission-result', 'native-activation-prefix-result',
        'native-activation-apply-result', 'terminal-reconciliation'}
    build, qualify = records['native-build-result'], records['native-qualify-result']
    assert build['deployed'] is qualify['deployed'] is False
    assert build['image'] == qualify['source_image'] == records['native-activation-apply-result']['new_image']
    assert build['module_count'] == qualify['module_count'] == 135
    assert build['original_build_actor_returned_success'] is False
    assert build['post_build_verification_recovered_from_optional_cmd_shape'] is True
    assert build['image_rebuilt'] is False
    assert qualify['real_image_requests'] == qualify['model_inference_calls'] == qualify['biometric_record_reads'] == 0
    assert qualify['real_profile_mounts'] == 0
    assert qualify['owned_candidate_container_absent'] is True


def test_no_effect_terminal_is_not_reported_as_admission_or_idle_proof():
    records = json.loads((ROOT / 'summary.json').read_bytes())['records']
    admission = records['native-admission-result']
    prefix = records['native-activation-prefix-result']
    apply = records['native-activation-apply-result']
    terminal = records['terminal-reconciliation']
    assert admission['status'] == 'no_window_consumer_waiting_terminal'
    assert admission['maintenance_intent_written'] is False and admission['service_actions'] == 0
    assert prefix['writer_lock_attempted'] is False and prefix['service_actions'] == 0
    assert apply['status'] == terminal['terminal_status'] == 'not_started_consumer_waiting_terminal'
    assert apply['lock_polls'] == admission['lock_polls'] == 1
    assert apply['image_replacements'] == terminal['actual_service_replacements'] == 0
    assert apply['maintenance_intent_written'] is False
    assert terminal['all_ten_original_processes_exact'] is True
    assert terminal['old_face_image_still_running'] is True
    assert terminal['durable_intent_idle_no_new_transaction'] is True
    assert terminal['replacement_and_rollback_actuator_receipts_absent'] is True
    assert terminal['no_active_update_owner'] is True
