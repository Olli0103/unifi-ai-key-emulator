"""Actual child death at a loopback model boundary; no camera/provider data."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import stat
import sys

import pytest

from aikey.worker import JobProcessor, WorkerError

from test_worker import command, configuration
from test_basic_descriptions import command as caption_command

pytest_plugins = ['test_worker']


CHILD = r'''
import asyncio,json,sys
from pathlib import Path
from aikey.worker import JobProcessor,WorkerError
class Registry:
    allowed_ids=frozenset({'camera-fixture'})
    def allows(self,camera): return camera in self.allowed_ids
async def main():
    config=json.loads(Path(sys.argv[1]).read_text())
    processor=JobProcessor(config,Path(sys.argv[2]),camera_registry=Registry())
    try:
        result=await processor.handle(json.loads(Path(sys.argv[3]).read_text()))
        print(json.dumps({'result':result}),flush=True)
    except WorkerError as error:
        print(json.dumps({'refused':str(error)}),flush=True)
    finally:
        await processor.stop()
asyncio.run(main())
'''


async def child(config_path, state_path, command_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    # Synthetic worker uses only direct loopback HTTP, never inherited proxies.
    for name in tuple(env):
        if name.lower().endswith('_proxy'):
            env.pop(name)
    return await asyncio.create_subprocess_exec(
        sys.executable, '-c', CHILD, str(config_path), str(state_path), str(command_path),
        env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)


@pytest.mark.asyncio
@pytest.mark.parametrize('continuous', [False, True])
async def test_killed_model_attempt_is_not_reissued_after_process_restart(services, tmp_path, continuous):
    config = configuration(services)
    task = command()
    if continuous:
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            pytest.skip('actual FFmpeg video fixture requires ffmpeg')
        video = tmp_path / 'fixture.mp4'
        encoder = await asyncio.create_subprocess_exec(
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
            'color=c=blue:s=32x32:r=2:d=10', '-c:v', 'mpeg4', str(video))
        assert await asyncio.wait_for(encoder.wait(), 10) == 0
        services.video = video.read_bytes()
        config['worker']['continuous'] = {'unmetered': True}
        config['worker']['request_mp4_exports'] = True
        config['worker']['ffmpeg_path'] = ffmpeg
        task = caption_command()
    config_path = tmp_path / 'config.json'
    command_path = tmp_path / 'command.json'
    config_path.write_text(json.dumps(config))
    command_path.write_text(json.dumps(task))
    state = tmp_path / 'state'
    services.release.clear()
    first = await child(config_path, state, command_path)
    try:
        await asyncio.wait_for(services.entered.wait(), 10)
        assert len(services.requests) == 1 and services.callbacks == []
        first.kill()
        await asyncio.wait_for(first.communicate(), 5)
        assert first.returncode != 0
    finally:
        if first.returncode is None:
            first.kill()
            await first.communicate()
        services.release.set()
    before = {p.name: p.read_bytes() for p in (state / 'worker-jobs').glob('*.json')}
    second = await child(config_path, state, command_path)
    try:
        stdout, stderr = await asyncio.wait_for(second.communicate(), 10)
    finally:
        if second.returncode is None:
            second.kill()
            await second.communicate()
    assert second.returncode == 0, stderr.decode()
    assert len(services.requests) == 1, 'An already accepted model request was issued again'
    assert services.callbacks == []
    assert 'execution' in json.loads(stdout)['refused'].lower()
    assert before and before == {p.name: p.read_bytes() for p in (state / 'worker-jobs').glob('*.json')}


@pytest.mark.asyncio
async def test_execution_marker_directory_sync_failure_prevents_model_io(services, tmp_path, monkeypatch):
    worker = JobProcessor(configuration(services), tmp_path)
    real_sync = os.fsync
    failed = []

    def fail_first_directory_sync(fd):
        if not failed and stat.S_ISDIR(os.fstat(fd).st_mode):
            failed.append(True)
            raise OSError('synthetic directory sync refusal')
        return real_sync(fd)

    monkeypatch.setattr(os, 'fsync', fail_first_directory_sync)
    try:
        with pytest.raises(WorkerError, match='directory sync refusal'):
            await worker.handle(command())
        assert failed and services.media_requests == services.requests == services.callbacks == []
        record, = worker._history.values()
        assert record['state'] == 'failed'
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_old_execution_marker_stays_visible_and_does_not_block_other_jobs(services, tmp_path):
    config = configuration(services)
    worker = JobProcessor(config, tmp_path)
    normalized = worker._normalize(command())
    record = {'jobId': normalized[0], 'fingerprint': normalized[1],
              'state': 'execution_started', 'operation': normalized[2], 'updatedAt': 1.0}
    path = worker.state_dir / (normalized[0] + '.json')
    path.write_text(json.dumps(record))
    original = path.read_bytes()
    await worker.stop()
    restarted = JobProcessor(config, tmp_path)
    try:
        with pytest.raises(WorkerError, match='Execution outcome is uncertain'):
            await restarted.handle(command())
        changed = command()
        changed['payload']['event'] = 'different-fixture'
        with pytest.raises(WorkerError, match='different input'):
            await restarted.handle(changed)
        assert services.requests == []
        assert (await restarted.handle(command('distinct-task')))['callback'] == 'http_accepted'
        assert len(services.requests) == 1
        assert path.read_bytes() == original
        assert restarted.get_status(normalized[0])['state'] == 'execution_started'
    finally:
        await restarted.stop()
