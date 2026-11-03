"""Preserve dated failures/denominators and redacted artifact integrity."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).parents[1]/'docs/evidence/synthetic-multimodal-cpu-20261103'


def read(name):
    return json.loads((ROOT/name).read_bytes())


def test_historical_reports_are_hash_bound_and_do_not_hide_bad_baselines():
    binding=read('binding.json')
    for name,digest in binding['report_files'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
    expected={'plate':12,'speech':8,'retrieval':10,'operation':4}
    assert binding['cases']==binding['valid']==binding['terminal']==sum(expected.values())
    for kind,count in expected.items():
        report=read(kind+'-report.json')
        metrics=report['metrics'][kind]['overall']
        assert report['complete'] and metrics['planned']==metrics['valid']==count
        assert metrics['errors']==metrics['unrun']==0
        assert metrics['total_provider_charge_usd'] is None
        assert metrics['charge_unknown_slots']==count
    speech=read('speech-report.json')['metrics']['speech']['overall']['valid_only_metrics']
    assert speech['positive_exact_accuracy']==1 and speech['false_positive_negatives']==2
    assert speech['reference_words']==43 and speech['word_errors']==3
    retrieval=read('retrieval-report.json')['metrics']['retrieval']['overall']['valid_only_metrics']
    assert retrieval['mrr_at_k']==.166667 and retrieval['positive_queries']==6
    assert retrieval['negative_queries']==4 and retrieval['negative_queries_with_results']==0
    status=read('initial-target-status.json')['targets']
    assert status['speech']['negative_rejection'] is False
    assert status['retrieval']['mrr_at_5'] is status['retrieval']['ndcg_at_5'] is False


def test_tiny_synthetic_exposure_is_not_promoted_to_field_or_native_evidence():
    binding=read('binding.json')
    metrics=read('operation-report.json')['metrics']['operation']['overall']
    assert 73<binding['operation_measured_seconds']<74
    assert metrics['measured_camera_days']<.001<metrics['planned_camera_days']
    assert metrics['latency_unknown_slots']==2
    assert metrics['valid_only_metrics']['latency_samples']==2
    assert metrics['valid_only_metrics']['critical_events']==2
    assert metrics['valid_only_metrics']['known_provider_charge_usd'] is None
    assert binding['field_exposure'] is binding['native_notification_latency'] is None
    assert binding['real_ai_key_reference'] is None
