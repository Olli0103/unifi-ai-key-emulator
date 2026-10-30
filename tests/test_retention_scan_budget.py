"""Large retained archives must not make an admin status read scan everything."""
import json
from pathlib import Path
import time

import pytest

from aikey import worker_archive
from aikey.data_policy import live_status


def archive(root: Path, count: int):
    bucket = root / 'worker-archive' / 'aa'
    bucket.mkdir(parents=True, mode=0o700)
    bucket.parent.chmod(0o700)
    for number in range(count):
        job = 'aa' + f'{number:062x}'
        (bucket / (job + '.json')).write_text(json.dumps({
            'schema': 1, 'jobId': job, 'fingerprint': 'b' * 64,
            'state': 'completed', 'updatedAt': time.time() - 400 * 86400,
        }))


def test_retention_large_archive_is_unknown_not_a_complete_partial_count(tmp_path, monkeypatch):
    archive(tmp_path, 2100)
    original = worker_archive._read_json
    reads = []

    def observed(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(worker_archive, '_read_json', observed)
    result = live_status(tmp_path)['worker_archive']
    assert len(reads) <= 2048
    assert result['markers'] is None and result['problems'] is None
    assert result['scan'] == 'incomplete; entry or time limit reached'
    assert len(list((tmp_path / 'worker-archive' / 'aa').iterdir())) == 2100


def test_incomplete_inventory_can_never_become_an_expiry_plan(tmp_path):
    archive(tmp_path, 4)
    report = worker_archive.inventory(tmp_path, max_entries=2)
    assert report['complete'] is False
    with pytest.raises(worker_archive.ArchiveError, match='incomplete'):
        worker_archive.plan(report, min_age_days=1,
                            controller_absent={'aa' + f'{n:062x}' for n in range(4)})
    assert worker_archive.inventory(tmp_path)['tombstones'] == 4


def test_monotonic_read_budget_and_default_full_cli_inventory(tmp_path, monkeypatch):
    archive(tmp_path, 8)
    ticks = iter([0.0, 0.0, 0.0, 0.3])
    monkeypatch.setattr(worker_archive.time, 'monotonic', lambda: next(ticks, 0.3))
    report = worker_archive.inventory(tmp_path, max_entries=2048, max_seconds=.25)
    assert report['complete'] is False and report['tombstones'] < 8
    full = worker_archive.inventory(tmp_path)
    assert full['tombstones'] == 8 and full.get('complete', True)
    assert worker_archive.plan(full, min_age_days=1)['expirable'] == []


@pytest.mark.parametrize('limit', [True, 0, -1, 1.5])
def test_invalid_entry_budget_refuses_before_archive_reads(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(worker_archive, '_read_json', lambda _: pytest.fail('unexpected read'))
    with pytest.raises(ValueError):
        worker_archive.inventory(tmp_path, max_entries=limit)


@pytest.mark.parametrize('seconds', [True, 0, -1, float('inf'), float('nan')])
def test_invalid_time_budget_refuses_before_archive_reads(tmp_path, monkeypatch, seconds):
    monkeypatch.setattr(worker_archive, '_read_json', lambda _: pytest.fail('unexpected read'))
    with pytest.raises(ValueError):
        worker_archive.inventory(tmp_path, max_seconds=seconds)


def test_reference_only_overflow_is_not_an_unreferenced_archive(tmp_path):
    jobs = tmp_path / 'worker-jobs'
    jobs.mkdir()
    for i in range(5):
        (jobs / (f'{i:064x}' + '.json')).write_text('{}')
    report = worker_archive.inventory(tmp_path, max_entries=3)
    assert report['complete'] is False
    assert all(value is None for value in report['references'].values())
    with pytest.raises(worker_archive.ArchiveError, match='incomplete'):
        worker_archive.plan(report, min_age_days=1)
    assert len(list(jobs.iterdir())) == 5


def test_bounded_scan_does_not_materialize_directories_before_checking_budget(tmp_path, monkeypatch):
    archive(tmp_path, 2)
    monkeypatch.setattr(Path, 'iterdir', lambda _: pytest.fail('unbounded materialization'))
    report = worker_archive.inventory(tmp_path, max_entries=1)
    assert report['complete'] is False and report['tombstones'] == 0
