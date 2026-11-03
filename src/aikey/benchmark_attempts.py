"""All planned multimodal slots, including failed and unrun measurements.

Uses the maintained canonical metrics only on validated successful observations.
Failures are never converted into empty predictions/correct negative rejection.
Supplied measurements and digests are bindings, not independent attestation.
"""
from __future__ import annotations

import hashlib
import json
import math

from .benchmark import BenchmarkError, _run_identity, _text
from . import benchmark_multi as multi

SCHEMA = 'aikey-multimodal-attempts/1'
PLAN_SCHEMA = 'aikey-multimodal-plan/1'
REPORT_SCHEMA = 'aikey-multimodal-attempt-report/1'
TRUTH = {'plate': {'expected'}, 'speech': {'expected'}, 'retrieval': {'relevant', 'k'},
         'operation': {'camera_days', 'critical_events'}}
OBSERVATION = {'plate': {'predicted'}, 'speech': {'predicted'}, 'retrieval': {'ranked'},
               'operation': {'false_alarms', 'missed_critical', 'camera_days'}}
BASE = {'id', 'input_sha256', 'status', 'client_attempts', 'latency_ms', 'cpu_ms',
        'provider_charge_usd', 'response_sha256'}


def _sha(value):
    return isinstance(value, str) and multi.SHA.fullmatch(value) is not None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def plan(value):
    if (not isinstance(value, dict) or set(value) != {
            'schema', 'runs', 'models', 'policy_sha256', 'cases', 'timing'}
            or value['schema'] != PLAN_SCHEMA or not _sha(value['policy_sha256'])
            or not isinstance(value['runs'], dict) or not value['runs']
            or set(value['runs']) - set(TRUTH)
            or not isinstance(value['models'], dict) or set(value['models']) != set(value['runs'])
            or not isinstance(value['cases'], list) or not 1 <= len(value['cases']) <= 10000):
        raise BenchmarkError('Invalid frozen multimodal plan')
    value = json.loads(canonical(value))
    runs = {k: _run_identity(v) for k, v in value['runs'].items()}
    if not isinstance(value['timing'], dict) or set(value['timing']) != set(runs):
        raise BenchmarkError('Every run requires an explicit timing scope')
    for kind, scope in value['timing'].items():
        if (not isinstance(scope, dict) or set(scope) != {'latency', 'cpu'}
                or scope['latency'] not in {'adapter_start_to_terminal', 'frame_to_owned_alert_sink',
                                          'native_event_to_notification', 'unmeasured'}
                or scope['cpu'] not in {'owned_server_cgroup', 'owned_process_group', 'unmeasured'}
                or kind != 'operation' and scope['latency'] in {
                    'frame_to_owned_alert_sink', 'native_event_to_notification'}):
            raise BenchmarkError('Invalid or misleading timing scope')
    for model in value['models'].values():
        if (not isinstance(model, dict) or set(model) != {'checkpoint_sha256', 'runtime_sha256'}
                or any(not _sha(v) for v in model.values())):
            raise BenchmarkError('Exact checkpoint and runtime digests are required')
    cases, seen = [], set()
    for row in value['cases']:
        if (not isinstance(row, dict) or not isinstance(row.get('kind'), str)
                or row['kind'] not in runs or set(row) != multi.COMMON | TRUTH[row['kind']]):
            raise BenchmarkError('Invalid frozen multimodal truth fields')
        _text(row['id'], 'planned slot')
        if row['id'] in seen:
            raise BenchmarkError('Duplicate planned slot')
        seen.add(row['id'])
        cases.append(dict(row))
    if set(runs) != {r['kind'] for r in cases}:
        raise BenchmarkError('Every declared model needs planned inputs')
    checked = []
    for kind, run in runs.items():
        # Structural validation only, never emitted/scored as a model observation.
        stubs = []
        for row in cases:
            if row['kind'] != kind:
                continue
            stub = dict(row)
            stub.update({'ranked': []} if kind == 'retrieval' else
                        {'false_alarms': 0, 'missed_critical': 0, 'latency_ms': None,
                         'provider_charge_usd': None} if kind == 'operation' else {'predicted': ''})
            stubs.append(stub)
        _, normalized = multi.validate({'schema': multi.CASES_SCHEMA, 'run': run, 'cases': stubs})
        checked.extend({k: row[k] for k in multi.COMMON | TRUTH[kind]} for row in normalized)
    return dict(value, runs=runs, cases=sorted(checked, key=lambda c: c['id']))


def _percentiles(values):
    ordered = sorted(values)
    return {name: ordered[math.ceil(q*len(ordered))-1] if ordered else None
            for name, q in (('p50', .5), ('p95', .95), ('p99', .99))}


def _slice(rows, attempts, kind):
    terminal = [attempts[r['id']] for r in rows if r['id'] in attempts]
    successful, exact, hits, rejections = [], 0, 0, 0
    positive = sum(bool(r.get('expected', r.get('relevant', []))) for r in rows)
    for row in rows:
        observed = attempts.get(row['id'])
        if observed is None or observed['status'] != 'valid':
            continue
        result = dict(row, **{k: observed[k] for k in OBSERVATION[kind]})
        if kind == 'operation':
            result.update(latency_ms=observed['latency_ms'],
                          provider_charge_usd=observed['provider_charge_usd'])
        successful.append(result)
        if kind in ('plate', 'speech'):
            exact += result['expected'] == result['predicted']
            hits += bool(result['expected']) and result['expected'] == result['predicted']
            rejections += not result['expected'] and not result['predicted']
        elif kind == 'retrieval':
            hits += bool(result['relevant']) and bool(set(result['ranked'][:result['k']]) & set(result['relevant']))
            rejections += not result['relevant'] and not result['ranked']
    valid = len(successful)
    result = {'planned': len(rows), 'terminal': len(terminal), 'valid': valid,
              'errors': len(terminal)-valid, 'unrun': len(rows)-len(terminal),
              'valid_coverage': multi.ratio(valid, len(rows)),
              'valid_only_metrics': multi.metrics(successful, kind) if valid else None,
              'latency_ms_terminal_only': _percentiles([a['latency_ms'] for a in terminal if a['latency_ms'] is not None]),
              'cpu_ms_terminal_only': _percentiles([a['cpu_ms'] for a in terminal if a['cpu_ms'] is not None]),
              'latency_unknown_slots': len(rows)-sum(a['latency_ms'] is not None for a in terminal),
              'cpu_unknown_slots': len(rows)-sum(a['cpu_ms'] is not None for a in terminal),
              'charge_unknown_slots': len(rows)-sum(a['provider_charge_usd'] is not None for a in terminal)}
    known_cpu = [a['cpu_ms'] for a in terminal if a['cpu_ms'] is not None]
    known_charges = [a['provider_charge_usd'] for a in terminal if a['provider_charge_usd'] is not None]
    result.update(cpu_ms_measured_sum=sum(known_cpu) if known_cpu else None,
                  cpu_ms_total=sum(known_cpu) if len(known_cpu) == len(rows) else None,
                  known_provider_charge_usd=sum(known_charges) if known_charges else None,
                  total_provider_charge_usd=sum(known_charges) if len(known_charges) == len(rows) else None)
    if kind != 'operation':
        result.update(planned_positives=positive, planned_negatives=len(rows)-positive,
                      all_slot_positive_hit_fraction=multi.ratio(hits, positive),
                      all_slot_correct_negative_rejection=multi.ratio(rejections, len(rows)-positive))
        if kind in ('plate', 'speech'):
            result['all_slot_exact_accuracy'] = multi.ratio(exact, len(rows))
    else:
        result.update(planned_camera_days=sum(r['camera_days'] for r in rows),
                      measured_camera_days=sum(r['camera_days'] for r in successful),
                      unmeasured_windows=len(rows)-valid)
    return result


def score(document):
    try:
        if (not isinstance(document, dict) or set(document) != {'schema', 'plan', 'attempts'}
                or document['schema'] != SCHEMA or not isinstance(document['attempts'], list)):
            raise BenchmarkError('Invalid multimodal attempts document')
        frozen = plan(document['plan'])
        if len(document['attempts']) > len(frozen['cases']):
            raise BenchmarkError('Too many terminal attempts')
        by_id = {r['id']: r for r in frozen['cases']}
        records = {}
        for record in document['attempts']:
            if not isinstance(record, dict) or not isinstance(record.get('id'), str) or record['id'] not in by_id:
                raise BenchmarkError('An attempt has no frozen source slot')
            row = by_id[record['id']]
            status, kind = record.get('status'), row['kind']
            expected = BASE | (OBSERVATION[kind] if status == 'valid' else {'error_code'})
            if (status not in ('valid', 'error') or set(record) != expected
                    or record['id'] in records or type(record['client_attempts']) is not int
                    or record['client_attempts'] != 1 or record['input_sha256'] != row['input_sha256']
                    or record['response_sha256'] is not None and not _sha(record['response_sha256'])
                    or status == 'valid' and record['response_sha256'] is None):
                raise BenchmarkError('Ambiguous, retried or source-mismatched terminal attempt')
            for field in ('latency_ms', 'cpu_ms', 'provider_charge_usd'):
                multi.number(record[field], nullable=True)
            scope = frozen['timing'][kind]
            if (scope['latency'] == 'unmeasured' and record['latency_ms'] is not None
                    or scope['cpu'] == 'unmeasured' and record['cpu_ms'] is not None):
                raise BenchmarkError('An unmeasured timer cannot contain an invented value')
            checked = dict(record)
            if status == 'valid' and kind == 'operation':
                multi.number(record['camera_days'], positive=True)
                if record['camera_days'] > row['camera_days']:
                    raise BenchmarkError('Measured exposure exceeds the frozen maximum')
            if status == 'error':
                _text(record['error_code'], 'fixed error code')
            records[record['id']] = checked
        # One structural pass per kind, not one full-plan pass per observation.
        # Placeholders for failed/unrun slots never become successful records.
        for kind, run in frozen['runs'].items():
            stubs = []
            for row in frozen['cases']:
                if row['kind'] != kind:
                    continue
                record = records.get(row['id'])
                if record is not None and record['status'] == 'valid':
                    values = {k: record[k] for k in OBSERVATION[kind]}
                    if kind == 'operation':
                        values.update(latency_ms=record['latency_ms'], provider_charge_usd=record['provider_charge_usd'])
                else:
                    values = {'ranked': []} if kind == 'retrieval' else {'predicted': ''}
                    if kind == 'operation':
                        values = dict(false_alarms=0, missed_critical=0, latency_ms=None, provider_charge_usd=None)
                stubs.append(dict(row, **values))
            _, validated = multi.validate({'schema': multi.CASES_SCHEMA, 'run': run, 'cases': stubs})
            for row in validated:
                record = records.get(row['id'])
                if record is not None and record['status'] == 'valid':
                    record.update({k: row[k] for k in OBSERVATION[kind]})
        result = {}
        for kind in sorted(frozen['runs']):
            rows = [r for r in frozen['cases'] if r['kind'] == kind]
            def section(selected):
                return _slice(selected, records, kind)
            classes, conditions = {}, {}
            for row in rows:
                classes.setdefault(row['camera_class'], []).append(row)
                for tag in row['conditions']:
                    conditions.setdefault(tag, []).append(row)
            result[kind] = {'run': frozen['runs'][kind], 'model': frozen['models'][kind],
                'timing_scope': frozen['timing'][kind],
                'overall': section(rows),
                'by_camera_class': {name: section(classes[name]) for name in sorted(classes)},
                'by_condition': {tag: section(conditions[tag]) for tag in sorted(conditions)}}
        return {'schema': REPORT_SCHEMA, 'plan_sha256': hashlib.sha256(canonical(frozen)).hexdigest(),
                'corpus_sha256': hashlib.sha256(canonical(frozen['cases'])).hexdigest(),
                'complete': len(records) == len(by_id), 'metrics': result,
                'claims': 'Supplied measurements, not independent attestation or native acceptance. '
                          'Failed/unrun slots are not correct negatives. Valid-only error/rank metrics '
                          'exclude invalid attempts; all-slot coverage and success stay explicit. '
                          'CPU time is not provider-reported monetary charge; unknown is not zero.'}
    except BenchmarkError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        raise BenchmarkError('Invalid multimodal attempt data') from None


def targets(document):
    """Freeze numeric no-regression thresholds AFTER baseline, BEFORE tuning.

    A bad baseline remains bad. These thresholds are not an initial engineering
    target PASS or production promotion, and never manufacture unknown costs.
    """
    report=score(document)
    if not report['complete'] or any(
            section['overall']['valid_coverage']!=1 for section in report['metrics'].values()):
        raise BenchmarkError('A complete valid baseline is required to freeze regression targets')
    result={}
    for kind,section in report['metrics'].items():
        overall=section['overall']
        metrics=overall['valid_only_metrics']
        rules={'minimum_valid_coverage':1}
        if kind!='operation':
            rules.update(minimum_positive_hit_fraction=overall['all_slot_positive_hit_fraction'],
                         minimum_correct_negative_rejection=overall['all_slot_correct_negative_rejection'])
        if kind in {'plate','speech'}:
            rules.update(minimum_all_slot_exact_accuracy=overall['all_slot_exact_accuracy'],
                         maximum_cer=metrics['cer'])
            if kind=='speech':
                rules['maximum_wer']=metrics['wer']
        elif kind=='retrieval':
            rules.update({'minimum_'+k:metrics[k] for k in ('recall_at_k','mrr_at_k','ndcg_at_k')})
        else:
            rules.update(maximum_missed_critical=metrics['missed_critical'],
                         maximum_false_alarms_per_camera_day=metrics['false_alarms_per_camera_day'])
        for key in ('latency_ms','cpu_ms'):
            p95=overall[key+'_terminal_only']['p95']
            unknown=overall[('latency' if key=='latency_ms' else 'cpu')+'_unknown_slots']
            rules['maximum_'+key+'_p95']=None if p95 is None or unknown else math.ceil(p95*1.1)
        result[kind]={'run':section['run'],'model':section['model'],
                      'timing_scope':section['timing_scope'],'numeric_thresholds':rules}
    return {'schema':'aikey-multimodal-regression-targets/1',
            'corpus_sha256':report['corpus_sha256'],
            'baseline_plan_sha256':report['plan_sha256'],
            'baseline_attempts_sha256':hashlib.sha256(canonical(document)).hexdigest(),
            'targets':result,'claims':'Prospective identical-corpus no-regression thresholds only; '
            'freeze before tuning. Failed initial quality targets are not waived. '
            'Timing needs the same externally verified resource/host envelope. '
            'Unknown thresholds remain needs_evidence, never zero. No field or hardware parity claim.'}
