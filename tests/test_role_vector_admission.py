"""A different encoder cannot silently become the fixed Re-ID model."""

import json

import pytest

from aikey import deep_mode
from aikey.worker import JobProcessor
from test_deep_mode import controller as controller, Registry, worker_config, embed_request


def reply(kind):
    body = {"embedding": [0.6, 0.8] + [0.0] * 254,
            "model": deep_mode.REID_MODEL, "dim": 256}
    if isinstance(kind, int):
        body["embedding"] = [1.0] + [0.0] * (kind - 1)
    elif kind == "wrong_model":
        body["model"] = "multilingual-e5-small"
    elif kind == "missing_model":
        del body["model"]
    elif kind == "float_dim":
        body["dim"] = 256.0
    elif kind == "bool_dim":
        body["dim"] = True
    elif kind == "missing_dim":
        del body["dim"]
    elif kind == "error":
        body["error"] = "synthetic-role-unavailable"
    elif kind == "duplicate_model":
        return (json.dumps(body)[:-1] + ',"model":"other-role"}').encode()
    elif kind == "duplicate_dim":
        return (json.dumps(body)[:-1] + ',"dim":256}').encode()
    elif kind == "zero":
        body["embedding"] = [0.0] * 256
    elif kind == "nonfinite":
        body["embedding"][0] = float("nan")
    return body


@pytest.mark.parametrize("kind", [1, 128, 257, 384, 512, "wrong_model", "missing_model",
                                  "float_dim", "bool_dim", "missing_dim", "error",
                                  "duplicate_model", "duplicate_dim", "zero", "nonfinite"])
async def test_wrong_role_metadata_or_vectors_never_reach_the_reid_index(controller, tmp_path, kind):
    controller.reid_reply = reply(kind)
    worker = JobProcessor(worker_config(controller), tmp_path, camera_registry=Registry())
    try:
        result = await worker.handle(embed_request())
        assert result["result"] == {"embedded": 0, "failed": 2}
    finally:
        await worker.stop()
    [(_, callback)] = controller.callbacks
    assert callback["embeddings"] == []
    assert callback["failed"] == [{"objectId": "obj1", "reason": "reid_failed"},
                                  {"objectId": "obj2", "reason": "reid_failed"}]


async def test_the_fixed_model_with_256_values_keeps_the_512_value_storage_contract(controller, tmp_path):
    controller.reid_reply = reply("valid")
    worker = JobProcessor(worker_config(controller), tmp_path, camera_registry=Registry())
    try:
        assert (await worker.handle(embed_request()))["result"] == {"embedded": 2, "failed": 0}
    finally:
        await worker.stop()
    [(_, callback)] = controller.callbacks
    assert len(callback["embeddings"]) == 2
    for entry in callback["embeddings"]:
        assert entry["dim"] == 512 and entry["model"] == deep_mode.REID_MODEL
        assert len(entry["reidEmbed"]) == 512
        assert entry["reidEmbed"][256:] == [0.0] * 256
