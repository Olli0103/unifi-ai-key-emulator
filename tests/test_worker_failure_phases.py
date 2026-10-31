"""Failure phase is an observed boundary, never permission, root cause or replay."""
import asyncio
import json

import pytest

from aikey import worker_failure_notes as notes
from aikey.worker import (JobProcessor, WorkerError, _FAILURE_JOB, _failure_boundary,
                          _FailureObservation)
from test_worker import configuration, command
from test_worker_failure_notes import record, JOB, FP

pytest_plugins = ["test_worker"]


@pytest.mark.parametrize('phase', sorted(notes.PHASES))
def test_phase_schema_and_legacy_filename_namespace(tmp_path, phase):
    store = notes.FailureNotes(tmp_path)
    assert record(store, phase=phase) == 'written'
    value = store.lookup(JOB, FP)['record']
    assert value['schema'] == notes.PHASE_SCHEMA and value['phase'] == phase
    assert len(json.dumps(value).encode()) < 2048
    assert next(store.root.glob('*.json')).name == notes._name(JOB, FP)
    assert record(store) == 'already_recorded'
    assert store.lookup(JOB, FP)['record'] == value


def test_legacy_first_note_is_not_replaced_or_given_an_invented_phase(tmp_path):
    store = notes.FailureNotes(tmp_path)
    assert record(store) == 'written'
    target = next(store.root.glob('*.json'))
    before = target.read_bytes()
    assert record(store, phase='inference_request') == 'already_recorded'
    assert target.read_bytes() == before
    assert store.lookup(JOB, FP)['record']['schema'] == notes.SCHEMA
    assert 'phase' not in store.lookup(JOB, FP)['record']
    assert len(list(store.root.glob('*.json'))) == 1


@pytest.mark.parametrize('phase', ['private URL', '', [], False, 1])
def test_unbounded_or_wrong_type_phase_refused_before_io(tmp_path, phase):
    assert record(notes.FailureNotes(tmp_path), phase=phase) == 'unavailable'
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'wrong_schema', 'wrong_phase'])
def test_versioned_record_refuses_ambiguous_fields(tmp_path, mutation):
    store = notes.FailureNotes(tmp_path)
    assert record(store, phase='callback') == 'written'
    target = next(store.root.glob('*.json'))
    value = json.loads(target.read_bytes())
    if mutation == 'missing':
        del value['phase']
    elif mutation == 'extra':
        value['private_raw_error'] = 'PRIVATE CANARY'
    elif mutation == 'wrong_schema':
        value['schema'] = notes.SCHEMA
    else:
        value['phase'] = 'PRIVATE CANARY'
    raw = json.dumps(value).encode()
    target.write_bytes(raw)
    assert store.lookup(JOB, FP)['status'] == 'unavailable'
    assert record(store, phase='execution') == 'unavailable'
    assert target.read_bytes() == raw


async def test_task_and_worker_isolation_first_exception_and_cancellation():
    owner, other = object(), object()
    a, b = _FailureObservation(owner), _FailureObservation(owner)
    entered, release = asyncio.Event(), asyncio.Event()

    async def first():
        token = _FAILURE_JOB.set(a)
        try:
            with pytest.raises(TimeoutError) as caught:
                async with asyncio.timeout(.03):
                    with _failure_boundary(owner, 'execution'):
                        with _failure_boundary(owner, 'inference_request'):
                            entered.set()
                            await release.wait()
            original_phase = a.for_exception(caught.value)
            with pytest.raises(OSError):
                with _failure_boundary(owner, 'callback'):
                    raise OSError('PRIVATE CANARY')
            assert original_phase == 'inference_request'
            assert a.for_exception(caught.value) is None
        finally:
            _FAILURE_JOB.reset(token)

    async def second():
        await entered.wait()
        token = _FAILURE_JOB.set(b)
        try:
            with pytest.raises(OSError):
                with _failure_boundary(other, 'media_fetch'):
                    raise OSError('PRIVATE CANARY')
            assert b.phase is None
            with pytest.raises(OSError):
                with _failure_boundary(owner, 'inference_wait'):
                    raise OSError('PRIVATE CANARY')
        finally:
            _FAILURE_JOB.reset(token)
    await asyncio.gather(first(), second())
    assert (a.phase, b.phase) == ('callback', 'inference_wait')
    assert _FAILURE_JOB.get() is None


async def test_expired_waiting_job_is_not_reported_as_model_timeout(services, tmp_path):
    services.release.clear()
    processor = JobProcessor(configuration(services), tmp_path)
    first = asyncio.create_task(processor.handle(command('first')))
    second = None
    try:
        await asyncio.wait_for(services.entered.wait(), 1)
        request = command('waiting')
        request['timeoutMs'] = 50
        second = asyncio.create_task(processor.handle(request))
        async with asyncio.timeout(1):
            while len(processor._pending) < 2:
                await asyncio.sleep(.001)
        await asyncio.sleep(.08)
        services.release.set()
        await first
        with pytest.raises(WorkerError, match='timed out'):
            await second
        failures = [v for v in processor._history.values() if v['state'] == 'failed']
        assert len(failures) == 1
        value = failures[0]
        note = processor._failure_notes.lookup(value['jobId'], value['fingerprint'])['record']
        assert (note['category'], note['phase']) == ('timeout', 'queue_expired')
        assert len(services.requests) == len(services.media_requests) == 1
        assert len(services.callbacks) == 1
        assert _FAILURE_JOB.get() is None
    finally:
        services.release.set()
        await processor.stop()
        await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)


async def test_timeout_waiting_for_gate_keeps_active_inference_untouched(services, tmp_path):
    services.release.clear()
    config = configuration(services)
    config['worker'].update(max_concurrency=3, inference_concurrency=1)
    processor = JobProcessor(config, tmp_path)
    first = asyncio.create_task(processor.handle(command('first')))
    second = None
    try:
        await asyncio.wait_for(services.entered.wait(), 1)
        request = command('gate-waiter')
        request['timeoutMs'] = 100
        second = asyncio.create_task(processor.handle(request))
        with pytest.raises(WorkerError, match='timed out'):
            await second
        value = next(v for v in processor._history.values() if v['state'] == 'failed')
        note = processor._failure_notes.lookup(value['jobId'], value['fingerprint'])['record']
        assert (note['category'], note['phase']) == ('timeout', 'inference_wait')
        assert processor._inference_gate.active == 1
        assert processor._inference_gate.waiting() == 0
        assert len(services.requests) == 1 and not services.callbacks
        services.release.set()
        await first
        assert processor._inference_gate.active == 0
        assert len(services.callbacks) == 1
    finally:
        services.release.set()
        await processor.stop()
        await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)


def test_recovered_exception_does_not_label_unrelated_failure():
    owner = object()
    observation = _FailureObservation(owner)
    token = _FAILURE_JOB.set(observation)
    try:
        with pytest.raises(ValueError):
            with _failure_boundary(owner, 'media_decode'):
                raise ValueError('recovered synthetic decoder error')
        unrelated = TimeoutError('unclassified later timeout')
        assert observation.for_exception(unrelated) is None
        with pytest.raises(TimeoutError) as caught:
            with _failure_boundary(owner, 'execution'):
                with _failure_boundary(owner, 'inference_wait'):
                    raise unrelated
        assert observation.for_exception(caught.value) == 'inference_wait'
    finally:
        _FAILURE_JOB.reset(token)


def test_recovered_implicit_context_is_not_a_cause():
    owner = object()
    observation = _FailureObservation(owner)
    token = _FAILURE_JOB.set(observation)
    try:
        try:
            with _failure_boundary(owner, 'media_decode'):
                raise ValueError('recovered decoder failure')
        except ValueError:
            with pytest.raises(TimeoutError) as caught:
                with _failure_boundary(owner, 'inference_wait'):
                    raise TimeoutError('new wait error')
        assert observation.for_exception(caught.value) == 'inference_wait'
    finally:
        _FAILURE_JOB.reset(token)


async def test_fence_timeout_never_claims_a_provider_request(services, tmp_path, monkeypatch):
    processor = JobProcessor(configuration(services), tmp_path)
    block = asyncio.Event()
    async def acquire(*, deadline):
        await block.wait()
    monkeypatch.setattr(processor._model_work_fence, 'acquire', acquire)
    request = command()
    request['timeoutMs'] = 50
    try:
        with pytest.raises(WorkerError, match='timed out'):
            await processor.handle(request)
        value = next(iter(processor._history.values()))
        note = processor._failure_notes.lookup(value['jobId'], value['fingerprint'])['record']
        assert (note['category'], note['phase']) == ('timeout', 'model_work_fence')
        assert services.media_requests == services.requests == services.callbacks == []
        assert _FAILURE_JOB.get() is None
    finally:
        await processor.stop()


async def test_truncated_response_has_response_phase_and_no_callback(services, tmp_path):
    processor = JobProcessor(configuration(services), tmp_path)
    services.finish_reason = 'length'
    try:
        with pytest.raises(WorkerError, match='complete, nonempty'):
            await processor.handle(command())
        value = next(iter(processor._history.values()))
        note = processor._failure_notes.lookup(value['jobId'], value['fingerprint'])['record']
        assert note['phase'] == 'inference_response'
        assert len(services.requests) == 1 and not services.callbacks
    finally:
        await processor.stop()


@pytest.mark.parametrize('callback_status', [200, 503])
async def test_on_demand_error_callback_keeps_original_decode_phase(
        services, tmp_path, forbidden_video_decoder, callback_status):
    config = configuration(services)
    config['worker']['ffmpeg_path'] = forbidden_video_decoder
    services.callback_status = callback_status
    request = {'targetUri': ':7968/on_demand_inference', 'resUrl': '/internal/camera-upload/failure',
               'payload': {'cameraId': 'c', 'eventId': 'e', 'timestamp': 1000,
                           'videoUrl': '/internal/aiprocessors/video/export?start=1000'}}
    processor = JobProcessor(config, tmp_path)
    try:
        with pytest.raises(WorkerError, match='UBV'):
            await processor.handle(request)
        value = next(iter(processor._history.values()))
        note = processor._failure_notes.lookup(value['jobId'], value['fingerprint'])['record']
        assert note['phase'] == 'media_decode'
        assert value['state'] == ('completed' if callback_status == 200 else 'callback_uncertain')
        assert len(services.callbacks) == 1 and not services.requests
    finally:
        await processor.stop()
