"""The Key's optional inference gate: player summaries before automatic captions. Synthetic only."""

import asyncio

import pytest

from aikey.worker import JobProcessor, WorkerError, _PriorityGate


async def test_a_waiting_summary_takes_the_next_slot_ahead_of_earlier_captions():
    gate, order = _PriorityGate(1), []
    await gate.acquire(1)                                  # a caption is being inferred

    async def job(name, priority):
        await gate.acquire(priority)
        order.append(name)
        await asyncio.sleep(0)
        gate.release()
    captions = [asyncio.create_task(job(f"caption-{n}", 1)) for n in range(3)]
    await asyncio.sleep(0)
    summary = asyncio.create_task(job("summary", 0))
    await asyncio.sleep(0)
    assert gate.waiting() == 4 and gate.waiting(0) == 1
    gate.release()
    await asyncio.gather(*captions, summary)
    assert order == ["summary", "caption-0", "caption-1", "caption-2"]
    assert gate.active == 0 and gate.waiting() == 0


async def test_a_cancelled_waiter_never_leaks_or_blocks_a_slot():
    gate = _PriorityGate(1)
    await gate.acquire(1)
    abandoned = asyncio.create_task(gate.acquire(0))
    await asyncio.sleep(0)
    abandoned.cancel()
    await asyncio.gather(abandoned, return_exceptions=True)
    later = asyncio.create_task(gate.acquire(1))
    await asyncio.sleep(0)
    gate.release()
    await asyncio.wait_for(later, 1)
    gate.release()
    assert gate.active == 0


def config(tmp_path, **worker):
    return {"runtime": {"mode": "lab"}, "controller_origins": ["http://127.0.0.1:9"],
            "device": {"mac": "02:00:00:00:00:99"},
            "inference": {"base_url": "http://127.0.0.1:9/v1", "model": "synthetic-vision"},
            "worker": {"max_queue": 4, "timeout_s": 10, "max_concurrency": 6, **worker}}


@pytest.mark.parametrize("value", [0, 7, "1", 1.0])
def test_the_gate_is_optional_and_bounded_by_the_worker_concurrency(tmp_path, value):
    assert JobProcessor(config(tmp_path), tmp_path)._inference_gate is None
    assert JobProcessor(config(tmp_path, inference_concurrency=1), tmp_path)._inference_gate.capacity == 1
    with pytest.raises(WorkerError, match="inference_concurrency"):
        JobProcessor(config(tmp_path, inference_concurrency=value), tmp_path)


async def test_the_worker_sends_at_most_the_gated_number_of_requests_summary_first(tmp_path):
    worker = JobProcessor(config(tmp_path, inference_concurrency=1), tmp_path)
    sent, in_flight, peak = [], [0], [0]

    class Response:
        status = 200

        def __init__(self, label):
            self.label = label

        async def __aenter__(self):
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
            sent.append(self.label)
            await asyncio.sleep(0.02)
            return self

        async def __aexit__(self, *exc):
            in_flight[0] -= 1

    class Session:
        def post(self, url, json, headers, allow_redirects):
            return Response(json["label"])

        async def close(self):
            pass

    worker._inference_session = Session()
    worker.provider.build_request = lambda images, prompt: ("u", {}, {"label": images[0]})

    async def body(response, limit):
        return b"{}"
    worker._read_response = body
    worker.provider.parse_response = lambda raw: "a description"
    first = asyncio.create_task(worker._infer(["caption-a"], priority=1))
    await asyncio.sleep(0.005)
    rest = [asyncio.create_task(worker._infer([f"caption-{n}"], priority=1)) for n in "bc"]
    await asyncio.sleep(0)
    summary = asyncio.create_task(worker._infer(["summary"], priority=0))
    await asyncio.gather(first, *rest, summary)
    assert peak[0] == 1 and sent == ["caption-a", "summary", "caption-b", "caption-c"]
    await worker.stop()


def test_short_local_jobs_leave_the_queue_before_captions_and_summaries_first():
    # A job's deadline includes its queue wait; on 30 Sep a 60 s face job
    # timed out behind a 20-job caption backlog.
    from aikey.worker import _QUEUE_PRIORITY
    queue = asyncio.PriorityQueue()
    arrivals = ["recognizeKeyFrames"] * 3 + ["recognizeFaces", "speechToText", "indexImages",
                                             "on_demand", "indexKeyFrames", "reverify"]
    for sequence, operation in enumerate(arrivals):
        queue.put_nowait((_QUEUE_PRIORITY.get(operation, 1), sequence, operation))
    order = [queue.get_nowait()[2] for _ in arrivals]
    assert order == ["on_demand", "recognizeFaces", "speechToText", "indexKeyFrames", "reverify",
                     "recognizeKeyFrames", "recognizeKeyFrames", "recognizeKeyFrames", "indexImages"]
    assert _QUEUE_PRIORITY.get("unknown-operation", 1) == 1          # unknown work waits with captions


def test_continuous_mode_archives_terminal_records_after_an_hour_not_a_day():
    # Nine continuous cameras made over 1000 jobs a day and filled the
    # 1024-entry ledger with day-old completed records (30 Sep).
    from aikey.worker import rollover_due
    now = 1_000_000.0
    record = lambda state, age, op="recognizeKeyFrames": {"state": state, "updatedAt": now - age, "operation": op}  # noqa: E731
    assert not rollover_due(record("completed", 3500), now, continuous=True)
    assert rollover_due(record("completed", 3700), now, continuous=True)
    assert rollover_due(record("failed", 3700), now, continuous=True)
    assert not rollover_due(record("completed", 3700), now, continuous=False)      # a day outside continuous
    assert rollover_due(record("completed", 24 * 3600 + 1), now, continuous=False)
    assert not rollover_due(record("failed", 3 * 24 * 3600), now, continuous=False)  # a week for retries
    assert not rollover_due(record("callback_uncertain", 10 ** 6), now, continuous=True)
    assert rollover_due(record("completed", 61, "indexImages"), now, continuous=False)


def scheduled_worker(tmp_path, **worker_options):
    """A real JobProcessor whose captions block as if the model gate were stuck."""
    worker = JobProcessor(config(tmp_path, max_concurrency=3, max_queue=16, **worker_options), tmp_path)
    gate_open, done, started = asyncio.Event(), [], []

    names = {}

    def normalize(command):
        import hashlib
        op, name = command["operation"], command["name"]
        job_id = hashlib.sha256(name.encode()).hexdigest()
        names[job_id] = name
        return (job_id, "fp-" + name, op, {"camera": "c"}, "http://127.0.0.1:9/cb", "legacy", [], 120)

    async def execute(job):
        started.append(names[job.job_id])
        if job.operation in ("recognizeKeyFrames", "describe"):
            await gate_open.wait()                          # waiting for the local model
        done.append(names[job.job_id])
        return {"status": "processed"}
    worker._normalize, worker._execute = normalize, execute
    worker.started = started
    return worker, gate_open, done


async def admit(worker, operation, name):
    job, _ = await worker._admit({"operation": operation, "name": name})
    return job


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


async def test_a_blocked_caption_backlog_leaves_workers_for_summaries_faces_and_speech(tmp_path):
    worker, gate_open, done = scheduled_worker(tmp_path, inference_concurrency=1)
    try:
        captions = [await admit(worker, "recognizeKeyFrames", f"caption-{n}") for n in range(6)]
        await settle()
        urgent = [await admit(worker, op, op) for op in
                  ("on_demand", "recognizeFaces", "speechToText", "indexKeyFrames", "reverify")]
        await asyncio.wait_for(asyncio.gather(*(job.future for job in urgent)), 2)
        assert set(done) == {"on_demand", "recognizeFaces", "speechToText", "indexKeyFrames", "reverify"}
        gate = worker.status()["inference_gate"]
        assert (gate["caption_lane"], gate["captions_active"], gate["captions_waiting"]) == (2, 2, 4)
        gate_open.set()
        await asyncio.wait_for(asyncio.gather(*(job.future for job in captions)), 2)
        # Waiting captions start oldest first.
        assert [d for d in worker.started if d.startswith("caption")] == [f"caption-{n}" for n in range(6)]
        gate = worker.status()["inference_gate"]
        assert gate["captions_active"] == 0 and gate["captions_waiting"] == 0
    finally:
        await worker.stop()


async def test_without_the_gate_the_old_behaviour_starves_short_jobs(tmp_path):
    # The control: no inference_concurrency, no lane; three blocked captions
    # take all three workers and a face job cannot start.
    worker, gate_open, done = scheduled_worker(tmp_path)
    try:
        for n in range(3):
            await admit(worker, "recognizeKeyFrames", f"caption-{n}")
        await settle()
        face = await admit(worker, "recognizeFaces", "face")
        await settle()
        assert not face.future.done() and done == []
        gate_open.set()
        await asyncio.wait_for(face.future, 2)
    finally:
        await worker.stop()


async def test_waiting_captions_count_against_the_queue_and_stop_releases_every_slot(tmp_path):
    worker, gate_open, done = scheduled_worker(tmp_path, inference_concurrency=1)
    worker._queue = asyncio.PriorityQueue(maxsize=4)
    captions = []
    for n in range(6):
        captions.append(await admit(worker, "recognizeKeyFrames", f"caption-{n}"))
        await settle()
    assert worker.status()["inference_gate"]["captions_waiting"] == 4
    with pytest.raises(WorkerError, match="queue is full"):
        await admit(worker, "recognizeKeyFrames", "one-too-many")
    await worker.stop()
    for job in captions:
        assert job.future.done() and job.future.exception() is not None
    gate = worker.status()["inference_gate"]
    assert (gate["captions_active"], gate["captions_waiting"]) == (0, 0)
    assert worker._pending == {} and done == []


def test_a_single_worker_or_no_gate_has_no_lane(tmp_path):
    assert JobProcessor(config(tmp_path), tmp_path)._caption_lane is None
    assert JobProcessor(config(tmp_path, max_concurrency=1, inference_concurrency=1),
                        tmp_path)._caption_lane is None
    assert JobProcessor(config(tmp_path, max_concurrency=6, inference_concurrency=1),
                        tmp_path)._caption_lane == 2
    assert JobProcessor(config(tmp_path, max_concurrency=3, inference_concurrency=3),
                        tmp_path)._caption_lane == 2          # always one worker left
