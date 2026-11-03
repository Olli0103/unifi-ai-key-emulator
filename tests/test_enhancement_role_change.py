"""A fictional replacement enhancement service cannot rewrite an earlier result."""

from copy import deepcopy
import hashlib
from io import BytesIO

from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image
import pytest

from aikey.worker import JobProcessor, WorkerError
from test_face_enhancement import CAMERA, IMAGE, OBJECT, config, jpeg, task
from test_face_enhancement import controller as controller


@pytest.mark.parametrize("status,body,outcome", [
    (200, jpeg((256, 256)), "uploaded"),
    (200, jpeg((256, 256), "PNG"), "declined"),
    (503, b"", "failed"),
])
async def test_replacement_role_only_processes_new_objects_and_keeps_old_result(
        controller, tmp_path, status, body, outcome):
    original = controller.original
    options = config(controller)
    first = JobProcessor(options, tmp_path)
    try:
        await first.handle(task())
    finally:
        await first.stop()
    [old_upload] = deepcopy(controller.uploads)
    old_id = hashlib.sha256(f"enhanceImage:{OBJECT}".encode()).hexdigest()
    old_path = tmp_path / "worker-jobs" / f"{old_id}.json"
    old_record = old_path.read_bytes()
    old_inference = deepcopy(options["inference"])
    replacement_calls = []

    async def replacement(request):
        replacement_calls.append(request.path)
        await request.read()
        return web.Response(status=status, body=body or None, content_type="image/jpeg")

    app = web.Application()
    app.router.add_post("/v1/enhance", replacement)
    async with TestServer(app, host="127.0.0.1") as server:
        changed = deepcopy(options)
        changed["face_enhancement"] = {"server": str(server.make_url("")).rstrip("/")}
        worker = JobProcessor(changed, tmp_path)
        try:
            # Loading a changed role neither contacts it nor regenerates old work.
            assert replacement_calls == [] and controller.uploads == [old_upload]
            result = await worker.handle(task())
            assert result["callback"] == "http_accepted"
            assert replacement_calls == [] and controller.enhance_requests == 1
            assert old_path.read_bytes() == old_record

            new_object = "object-fixture-after-model-change"
            new_task = task(smartDetectObject=new_object,
                reqUrl=f"/internal/aiprocessors/image/{IMAGE}?type=face&camera={CAMERA}"
                       f"&smartDetectObject={new_object}")
            if outcome == "failed":
                with pytest.raises(WorkerError, match="HTTP 503"):
                    await worker.handle(new_task)
            else:
                result = await worker.handle(new_task)
                assert result["result"]["enhanced"] is (outcome == "uploaded")
            counts = worker.status()["enhance"]
            new_id = hashlib.sha256(f"enhanceImage:{new_object}".encode()).hexdigest()
            record = worker.get_status(new_id)
            assert record["state"] == ("failed" if outcome == "failed" else "completed")
            assert counts["requests"] == 1
            assert counts["uploaded"] == int(outcome == "uploaded")
            assert counts["declined"] == int(outcome == "declined")
            assert counts["rejected_output"] == int(outcome == "declined")
        finally:
            await worker.stop()

    assert replacement_calls == ["/v1/enhance"] and controller.enhance_requests == 1
    assert controller.original == original and old_path.read_bytes() == old_record
    assert controller.uploads[0] == old_upload and changed["inference"] == old_inference
    if outcome == "failed":
        assert controller.uploads == [old_upload]
    else:
        assert controller.uploads[1]["smartDetectObject"] == new_object.encode()
        derivative = controller.uploads[1]["file"]
        if outcome == "uploaded":
            with Image.open(BytesIO(derivative)) as image:
                assert image.format == "JPEG" and image.size == (256, 256)
        else:
            assert derivative == b""  # Native "no modification", not an unsafe replacement.
