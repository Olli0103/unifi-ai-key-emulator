"""Accepted-job exclusion on the existing opt-in inode; synthetic HTTP only."""

import asyncio
import time

import pytest

from aikey.model_work_fence import ModelWorkFence, initialize
from aikey.worker import JobProcessor, WorkerError
from test_worker import command, configuration

pytest_plugins = ['test_worker']


async def settled(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline
        await asyncio.sleep(.001)


async def test_exclusive_model_writer_prevents_new_job_acceptance_not_only_execution(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    writer = observer.try_exclusive()
    worker = JobProcessor(configuration(services), tmp_path)
    task = asyncio.create_task(worker.submit(command('fictional-maintenance-wait')))
    try:
        await settled(lambda: task.done() or worker.status()['model_work_fence']['waiters'] == 1)
        assert not task.done(), 'New work must not be acknowledged behind the maintenance writer'
        assert worker.status()['pending'] == worker.status()['queued'] == worker.status()['active'] == 0
        assert services.media_requests == services.requests == services.callbacks == []
        writer.close()
        writer = None
        assert (await asyncio.wait_for(task, 2))['accepted'] is True
        await worker.wait_for_idle()
        assert len(services.requests) == len(services.callbacks) == 1
    finally:
        if writer is not None:
            writer.close()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.stop()
        observer.close()


async def test_accepted_but_unconsumed_job_keeps_writer_excluded_until_stop(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    worker = JobProcessor(configuration(services), tmp_path)
    park = asyncio.Event()
    async def parked():
        await park.wait()
    worker._consume = parked
    unexpected = None
    try:
        assert (await worker.submit(command('fictional-queued-lease')))['accepted'] is True
        assert worker.status()['pending'] == worker.status()['queued'] == 1
        unexpected = observer.try_exclusive()
        assert unexpected is None, 'An accepted queued job must retain its own lease'
        assert worker.status()['model_work_fence']['leases'] == 1
        assert services.media_requests == services.requests == services.callbacks == []
        await worker.stop()
        assert not worker._admission_leases
        replacement = observer.try_exclusive()
        assert replacement is not None
        replacement.close()
    finally:
        if unexpected is not None:
            unexpected.close()
        await worker.stop()
        observer.close()


async def test_cancelled_waiting_admission_uses_no_queue_budget_or_journal_slot(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    writer = observer.try_exclusive()
    worker = JobProcessor(configuration(services), tmp_path)
    task = asyncio.create_task(worker.submit(command('fictional-cancel-admission')))
    try:
        await settled(lambda: worker.status()['model_work_fence']['waiters'] == 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert worker.status()['pending'] == worker.status()['queued'] == 0
        assert worker.status()['model_work_fence']['waiters'] == worker.status()['model_work_fence']['leases'] == 0
        assert not worker._admission_leases and not worker._history
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        writer.close()
        await worker.stop()
        observer.close()


async def test_admission_wait_deadline_is_not_acknowledged_or_retried(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    writer = observer.try_exclusive()
    worker = JobProcessor(configuration(services), tmp_path)
    value = command('fictional-admission-deadline')
    value['timeoutMs'] = 30
    try:
        with pytest.raises(WorkerError, match='^Worker admission timed out$'):
            await worker.submit(value)
        assert worker.status()['pending'] == worker.status()['queued'] == 0
        assert not worker._admission_leases and not worker._history
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        writer.close()
        await worker.stop()
        observer.close()


async def test_waiting_consumes_original_budget_instead_of_starting_new_job_budget(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    writer = observer.try_exclusive()
    worker = JobProcessor(configuration(services), tmp_path)
    park = asyncio.Event()
    async def parked():
        await park.wait()
    worker._consume = parked
    value = command('fictional-original-budget')
    value['timeoutMs'] = 1000
    started = time.monotonic()
    task = asyncio.create_task(worker.submit(value))
    try:
        await settled(lambda: worker.status()['model_work_fence']['waiters'] == 1)
        await asyncio.sleep(.08)
        writer.close()
        writer = None
        admitted = await task
        job = worker._pending[admitted['jobId']]
        assert started < job.deadline <= started + 1.05
        assert job.deadline - job.admitted < .98
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        if writer is not None:
            writer.close()
        await worker.stop()
        observer.close()


async def test_duplicate_of_accepted_job_does_not_allocate_another_lease(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    worker = JobProcessor(configuration(services), tmp_path)
    park = asyncio.Event()
    async def parked():
        await park.wait()
    worker._consume = parked
    try:
        first = await worker.submit(command('fictional-duplicate'))
        second = await worker.submit(command('fictional-duplicate'))
        assert first['duplicate'] is False and second['duplicate'] is True
        assert worker.status()['model_work_fence']['leases'] == len(worker._admission_leases) == 1
        assert worker.status()['pending'] == worker.status()['queued'] == 1
    finally:
        await worker.stop()
        observer.close()


async def test_two_waiting_same_job_admissions_recheck_dedup_after_writer_release(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    writer = observer.try_exclusive()
    worker = JobProcessor(configuration(services), tmp_path)
    park = asyncio.Event()
    async def parked():
        await park.wait()
    worker._consume = parked
    tasks = [asyncio.create_task(worker.submit(command('fictional-concurrent'))) for _ in range(2)]
    try:
        await settled(lambda: worker.status()['model_work_fence']['waiters'] == 2)
        writer.close()
        writer = None
        result = await asyncio.gather(*tasks)
        assert sorted(r['duplicate'] for r in result) == [False, True]
        assert worker.status()['pending'] == worker.status()['queued'] == 1
        assert worker.status()['model_work_fence']['leases'] == len(worker._admission_leases) == 1
    finally:
        if writer is not None:
            writer.close()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await worker.stop()
        observer.close()


@pytest.mark.parametrize('fault', ['queue', 'reservation', 'draining'])
async def test_rejection_after_exclusion_does_not_leak_a_reader(services, tmp_path, monkeypatch, fault):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    worker = JobProcessor(configuration(services), tmp_path)
    if fault == 'queue':
        monkeypatch.setattr(worker._queue, 'full', lambda: True)
    elif fault == 'reservation':
        def refused(_):
            raise WorkerError('Test scope permit is already consumed')
        monkeypatch.setattr(worker, '_reserve_test_scope', refused)
    else:
        worker._draining = True
    try:
        with pytest.raises(WorkerError):
            await worker.submit(command('fictional-rejected-' + fault))
        assert not worker._admission_leases and not worker._pending
        assert worker.status()['model_work_fence']['leases'] == 0
        writer = observer.try_exclusive()
        assert writer is not None
        writer.close()
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        await worker.stop()
        observer.close()


async def test_completed_cached_result_remains_available_during_writer_without_new_work(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    worker = JobProcessor(configuration(services), tmp_path)
    writer = None
    try:
        await worker.handle(command('fictional-completed-cache'))
        await worker.wait_for_idle()
        writer = observer.try_exclusive()
        assert writer is not None
        result = await asyncio.wait_for(worker.submit(command('fictional-completed-cache')), .5)
        assert result['accepted'] is True and result['duplicate'] is True
        assert worker.status()['pending'] == 0 and not worker._admission_leases
        assert len(services.requests) == len(services.callbacks) == 1
    finally:
        if writer is not None:
            writer.close()
        await worker.stop()
        observer.close()


async def test_actual_job_completion_releases_reader_before_next_writer(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    worker = JobProcessor(configuration(services), tmp_path)
    services.release.clear()
    try:
        await worker.submit(command('fictional-active-reader'))
        await asyncio.wait_for(services.entered.wait(), 2)
        assert observer.try_exclusive() is None
        assert worker.status()['model_work_fence']['leases'] == 1
        services.release.set()
        await worker.wait_for_idle()
        assert not worker._admission_leases and worker.status()['model_work_fence']['leases'] == 0
        writer = observer.try_exclusive()
        assert writer is not None
        writer.close()
    finally:
        services.release.set()
        await worker.stop()
        observer.close()


@pytest.mark.parametrize('fault', ['retarget', 'malformed'])
async def test_queued_reader_still_revalidates_marker_before_execution(services, tmp_path, fault):
    from aikey.model_work_fence import FILENAME
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    worker = JobProcessor(configuration(services), tmp_path)
    park = asyncio.Event()
    async def parked():
        await park.wait()
    worker._consume = parked
    try:
        await worker.submit(command('fictional-retargeted-queue'))
        *_, job = worker._queue.get_nowait()
        worker._queue.task_done()
        marker = tmp_path / FILENAME
        if fault == 'retarget':
            marker.rename(tmp_path / 'fictional-retained-marker')
            initialize(tmp_path, '1' * 32)
        else:
            marker.write_bytes(b'{')
        await worker._run_job(job)
        with pytest.raises(WorkerError):
            await job.future
        assert not worker._admission_leases and not worker._pending
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        await worker.stop()
        observer.close()


async def test_waiting_caption_stop_closes_accepted_lease_without_execution(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    worker = JobProcessor(configuration(services), tmp_path)
    park = asyncio.Event()
    async def parked():
        await park.wait()
    worker._consume = parked
    try:
        await worker.submit(command('fictional-caption-wait'))
        entry = worker._queue.get_nowait()
        worker._queue.task_done()
        worker._caption_waiting.append(entry)
        assert observer.try_exclusive() is None
        await worker.stop()
        writer = observer.try_exclusive()
        assert writer is not None
        writer.close()
        assert not worker._admission_leases and not worker._pending
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        await worker.stop()
        observer.close()


async def test_absent_marker_keeps_legacy_no_file_or_physical_lease(services, tmp_path):
    from aikey.model_work_fence import FILENAME
    worker = JobProcessor(configuration(services), tmp_path)
    try:
        await worker.handle(command('fictional-no-marker'))
        assert not (tmp_path / FILENAME).exists()
        assert worker.status()['model_work_fence']['enabled'] is False
        assert worker.status()['model_work_fence']['leases'] == 0 and not worker._admission_leases
    finally:
        await worker.stop()


async def test_worker_stop_settles_unaccepted_waiter_without_reservation(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    writer = observer.try_exclusive()
    worker = JobProcessor(configuration(services), tmp_path)
    task = asyncio.create_task(worker.submit(command('fictional-stop-admission-wait')))
    try:
        await settled(lambda: worker.status()['model_work_fence']['waiters'] == 1)
        await worker.stop()
        with pytest.raises(WorkerError, match='^Worker maintenance admission is unavailable$'):
            await asyncio.wait_for(task, 1)
        assert not worker._pending and not worker._admission_leases
        assert worker.status()['model_work_fence']['waiters'] == 0
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        writer.close()
        await asyncio.gather(task, return_exceptions=True)
        await worker.stop()
        observer.close()


async def test_waiting_admission_cannot_enter_after_stop_has_flushed_queue(services, tmp_path, monkeypatch):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    writer = observer.try_exclusive()
    worker = JobProcessor(configuration(services), tmp_path)
    task = asyncio.create_task(worker.submit(command('fictional-stop-cleanup-race')))
    close_entered, close_release = asyncio.Event(), asyncio.Event()
    stop_task = None
    try:
        await settled(lambda: worker.status()['model_work_fence']['waiters'] == 1)
        original_close = worker._session.close
        async def delayed_close():
            close_entered.set()
            await close_release.wait()
            await original_close()
        monkeypatch.setattr(worker._session, 'close', delayed_close)
        stop_task = asyncio.create_task(worker.stop())
        await asyncio.wait_for(close_entered.wait(), 2)
        writer.close()
        writer = None
        with pytest.raises(WorkerError, match='^Worker has stopped$'):
            await asyncio.wait_for(task, 2)
        assert not worker._pending and not worker._admission_leases
        assert worker.status()['queued'] == worker.status()['model_work_fence']['leases'] == 0
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        if writer is not None:
            writer.close()
        close_release.set()
        if stop_task is not None:
            await stop_task
        await asyncio.gather(task, return_exceptions=True)
        await worker.stop()
        observer.close()


async def test_marker_retarget_during_admission_wait_has_fixed_distinct_reason(services, tmp_path):
    from aikey.model_work_fence import FILENAME
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    writer = observer.try_exclusive()
    worker = JobProcessor(configuration(services), tmp_path)
    task = asyncio.create_task(worker.submit(command('fictional-retarget-before-admission')))
    try:
        await settled(lambda: worker.status()['model_work_fence']['waiters'] == 1)
        (tmp_path / FILENAME).rename(tmp_path / 'fictional-original-inode')
        initialize(tmp_path, '1' * 32)
        with pytest.raises(WorkerError, match='^Worker maintenance admission binding changed$'):
            await task
        assert not worker._pending and not worker._admission_leases
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        writer.close()
        await asyncio.gather(task, return_exceptions=True)
        await worker.stop()
        observer.close()


@pytest.mark.parametrize('kind', ['fence', 'os'])
async def test_unavailable_admission_never_exports_exception_payload_or_accepts(services, tmp_path, monkeypatch, kind):
    from aikey.model_work_fence import ModelWorkFenceError
    initialize(tmp_path, '0' * 32)
    worker = JobProcessor(configuration(services), tmp_path)
    async def broken(**kwargs):
        raise (ModelWorkFenceError if kind == 'fence' else OSError)('FICTIONAL-PRIVATE-CANARY')
    monkeypatch.setattr(worker._model_work_fence, 'acquire', broken)
    try:
        with pytest.raises(WorkerError) as failure:
            await worker.submit(command('fictional-broken-admission'))
        assert str(failure.value) == 'Worker maintenance admission is unavailable'
        assert 'CANARY' not in str(failure.value)
        assert not worker._pending and not worker._admission_leases
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        await worker.stop()


async def test_two_waiting_jobs_recheck_queue_capacity_and_release_losing_lease(services, tmp_path):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    writer = observer.try_exclusive()
    config = configuration(services)
    config['worker']['max_queue'] = 1
    worker = JobProcessor(config, tmp_path)
    park = asyncio.Event()
    async def parked():
        await park.wait()
    worker._consume = parked
    tasks = [asyncio.create_task(worker.submit(command('fictional-capacity-' + str(i)))) for i in range(2)]
    try:
        await settled(lambda: worker.status()['model_work_fence']['waiters'] == 2)
        writer.close()
        writer = None
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(isinstance(v, dict) and v['accepted'] for v in results) == 1
        assert sum(isinstance(v, WorkerError) and str(v) == 'Worker queue is full' for v in results) == 1
        assert worker.status()['pending'] == worker.status()['queued'] == 1
        assert worker.status()['model_work_fence']['leases'] == len(worker._admission_leases) == 1
        assert services.media_requests == services.requests == services.callbacks == []
    finally:
        if writer is not None:
            writer.close()
        await asyncio.gather(*tasks, return_exceptions=True)
        await worker.stop()
        observer.close()


@pytest.mark.parametrize('message,reason', [
    ('Worker admission timed out', 'admission_expired'),
    ('Worker maintenance admission is unavailable', 'maintenance_unavailable'),
    ('Worker maintenance admission binding changed', 'maintenance_binding_changed'),
])
def test_native_rejection_classifiers_keep_fixed_admission_reasons(message, reason):
    from aikey.device import (_WORKER_REJECTION_REASONS, _REQUEST_AI_REJECTION_REASONS,
                               _speech_rejection_reason)
    assert _WORKER_REJECTION_REASONS[message] == _REQUEST_AI_REJECTION_REASONS[message] == reason
    assert _speech_rejection_reason(message) == reason
