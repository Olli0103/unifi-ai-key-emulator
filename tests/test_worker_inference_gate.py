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
