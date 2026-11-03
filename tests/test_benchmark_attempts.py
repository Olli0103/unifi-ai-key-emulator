"""Errors/unrun slots never earn correct-negative credit or invented exposure."""
from copy import deepcopy
import json

import pytest

from aikey.benchmark import BenchmarkError
from aikey.benchmark_attempts import PLAN_SCHEMA, SCHEMA, score, targets


def fixture():
    rows = []
    for kind in ('plate','speech','retrieval','operation'):
        for i in (0,1):
            values = ({'relevant':['fictional-result'] if i == 0 else [], 'k':2} if kind == 'retrieval'
                else {'camera_days':.5, 'critical_events':1 if i == 0 else 0} if kind == 'operation'
                else {'expected':'Fictional text' if i == 0 else ''})
            rows.append({'id':kind+str(i),'kind':kind,'camera_class':'synthetic',
                'conditions':['day'],'input_sha256':str(i)*64, **values})
    runs = {k:{'model':'fixture-model','provider':'local','profile':k,'code_revision':'fixture'}
            for k in ('plate','speech','retrieval','operation')}
    return {'schema':SCHEMA,'plan':{'schema':PLAN_SCHEMA,'runs':runs,
        'models':{k:{'checkpoint_sha256':'a'*64,'runtime_sha256':'b'*64} for k in runs},
        'policy_sha256':'c'*64,'cases':rows,
        'timing':{k:{'latency':'frame_to_owned_alert_sink' if k=='operation' else 'adapter_start_to_terminal',
                     'cpu':'owned_server_cgroup'} for k in runs}},'attempts':[]}


def attempt(row, *, error=False):
    values = ({'error_code':'synthetic_error'} if error else
        {'ranked':list(row['relevant'])} if row['kind'] == 'retrieval' else
        {'false_alarms':0,'missed_critical':0,'camera_days':row['camera_days']} if row['kind'] == 'operation'
        else {'predicted':row['expected']})
    return {'id':row['id'],'input_sha256':row['input_sha256'],
        'status':'error' if error else 'valid','client_attempts':1,'latency_ms':10,
        'cpu_ms':20,'provider_charge_usd':None,'response_sha256':None if error else 'f'*64,**values}


def test_failed_negative_does_not_become_an_empty_correct_prediction():
    value = fixture()
    value['attempts'] = [attempt(row,error=row['id'].endswith('1')) for row in value['plan']['cases']]
    before = deepcopy(value)
    report = score(value)
    assert report['complete'] and value == before
    for kind in ('plate','speech','retrieval'):
        m = report['metrics'][kind]['overall']
        assert (m['planned'],m['valid'],m['errors'],m['unrun']) == (2,1,1,0)
        assert m['all_slot_correct_negative_rejection'] == 0
        assert m['all_slot_positive_hit_fraction'] == 1
        assert m['charge_unknown_slots'] == 2
    assert report['metrics']['plate']['overall']['all_slot_exact_accuracy'] == .5
    assert report['metrics']['plate']['overall']['valid_only_metrics']['negative_false_positive_rate'] is None
    op = report['metrics']['operation']['overall']
    assert op['planned_camera_days'] == 1 and op['measured_camera_days'] == .5
    assert op['unmeasured_windows'] == 1 and op['valid_only_metrics']['total_provider_charge_usd'] is None


def test_unrun_is_not_a_rejection_and_missing_timers_are_not_zero():
    value = fixture()
    value['attempts'] = [attempt(value['plan']['cases'][0])]
    value['attempts'][0].update(cpu_ms=None,latency_ms=None)
    report = score(value)
    assert not report['complete']
    p = report['metrics']['plate']['overall']
    assert p['unrun'] == 1 and p['all_slot_correct_negative_rejection'] == 0
    assert p['cpu_ms_terminal_only']['p50'] is None
    assert p['cpu_unknown_slots'] == p['latency_unknown_slots'] == p['charge_unknown_slots'] == 2
    assert report['metrics']['speech']['overall']['valid_only_metrics'] is None


def test_empty_valid_prediction_is_distinct_from_failed_transport():
    value = fixture()
    value['attempts'] = [attempt(row) for row in value['plan']['cases']]
    report = score(value)
    assert report['complete']
    assert report['metrics']['speech']['overall']['all_slot_correct_negative_rejection'] == 1
    encoded = json.dumps(report)
    assert 'Fictional text' not in encoded and 'fictional-result' not in encoded and 'plate0' not in encoded
    value['attempts'].reverse()
    value['plan']['cases'].reverse()
    assert score(value) == report


@pytest.mark.parametrize('fault',['duplicate','retry','hash','unknown','error-prediction','nan','bool','unbalanced','model','bad-field'])
def test_bad_or_unbound_attempts_and_plans_refuse(fault):
    value = fixture()
    row = value['plan']['cases'][0]
    record = attempt(row,error=fault=='error-prediction')
    value['attempts'] = [record]
    if fault == 'duplicate':
        value['attempts'].append(dict(record))
    elif fault == 'retry':
        record['client_attempts'] = 2
    elif fault == 'hash':
        record['input_sha256'] = 'd'*64
    elif fault == 'unknown':
        record['id'] = 'not-planned'
    elif fault == 'error-prediction':
        record['predicted'] = ''
    elif fault == 'nan':
        record['cpu_ms'] = float('nan')
    elif fault == 'bool':
        record['client_attempts'] = True
    elif fault == 'unbalanced':
        value['plan']['cases'][1]['expected'] = 'another positive'
    elif fault == 'model':
        value['plan']['models']['plate']['checkpoint_sha256'] = 'unknown'
    else:
        record['private_payload'] = 'Not allowed'
    with pytest.raises(BenchmarkError):
        score(value)


def test_failed_request_resource_cost_is_retained_but_unrun_cost_stays_unknown():
    value=fixture()
    rows=value['plan']['cases']
    value['attempts']=[attempt(rows[0]),attempt(rows[1],error=True)]
    value['attempts'][0]['provider_charge_usd']=.02
    value['attempts'][1]['provider_charge_usd']=.03
    result=score(value)['metrics']['plate']['overall']
    assert result['total_provider_charge_usd']==pytest.approx(.05)
    assert result['cpu_ms_total']==40 and result['valid_coverage']==.5
    value['attempts'].pop()
    result=score(value)['metrics']['plate']['overall']
    assert result['known_provider_charge_usd']==.02
    assert result['total_provider_charge_usd'] is None and result['cpu_ms_total'] is None



def test_models_are_not_corpus_identity_but_timings_and_policy_are_plan_identity():
    value=fixture()
    first=score(value)
    value['plan']['models']['plate']['checkpoint_sha256']='f'*64
    second=score(value)
    assert second['corpus_sha256']==first['corpus_sha256']
    assert second['plan_sha256']!=first['plan_sha256']
    assert first['metrics']['operation']['timing_scope']['latency']=='frame_to_owned_alert_sink'


def test_declared_unmeasured_cost_cannot_be_filled_with_zero():
    value=fixture()
    value['plan']['timing']['plate']['cpu']='unmeasured'
    row=value['plan']['cases'][0]
    value['attempts']=[attempt(row)]
    value['attempts'][0]['cpu_ms']=0
    with pytest.raises(BenchmarkError,match='unmeasured timer'):
        score(value)


def test_measured_camera_exposure_is_not_the_planned_maximum():
    value=fixture()
    rows=[r for r in value['plan']['cases'] if r['kind']=='operation']
    value['attempts']=[attempt(r) for r in rows]
    for record in value['attempts']:
        record['camera_days']=.01
    result=score(value)['metrics']['operation']['overall']
    assert result['planned_camera_days']==1 and result['measured_camera_days']==.02
    assert result['valid_only_metrics']['camera_days']==.02
    value['attempts'][0]['camera_days']=.6
    with pytest.raises(BenchmarkError,match='exposure'):
        score(value)


def test_prospective_targets_keep_bad_baselines_bad_and_unknowns_unknown():
    value=fixture()
    value['attempts']=[attempt(r) for r in value['plan']['cases']]
    value['attempts'][1]['predicted']='hallucinated negative'
    value['attempts'][2]['cpu_ms']=None
    frozen=targets(value)
    plate=frozen['targets']['plate']['numeric_thresholds']
    assert plate['minimum_all_slot_exact_accuracy']==.5
    assert plate['minimum_correct_negative_rejection']==0
    assert plate['maximum_latency_ms_p95']==11
    assert 'not waived' in frozen['claims'] and 'Fictional text' not in json.dumps(frozen)
    assert frozen['targets']['speech']['numeric_thresholds']['maximum_cpu_ms_p95'] is None
    value['attempts'][0].update(status='error',error_code='request_failed')
    value['attempts'][0].pop('predicted')
    with pytest.raises(BenchmarkError,match='complete valid'):
        targets(value)
    value['attempts'].clear()
    with pytest.raises(BenchmarkError,match='complete valid'):
        targets(value)


def test_actual_cli_keeps_errors_unknown_and_raw_labels_out(tmp_path,capsys):
    from aikey.benchmark import main
    value=fixture()
    value['attempts']=[attempt(row,error=True) for row in value['plan']['cases']]
    source=tmp_path/'fixture.json'
    source.write_text(json.dumps(value))
    assert main(['multi-attempt-score',str(source)])==0
    output=json.loads(capsys.readouterr().out)
    assert output['complete'] and all(v['overall']['valid']==0 for v in output['metrics'].values())
    assert 'Fictional text' not in json.dumps(output)
