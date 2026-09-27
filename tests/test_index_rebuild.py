"""Staged, resumable search-index rebuild (#18): resume, mixing, rollback, retries."""

import json

import pytest

from aikey.embedding_profile import _with_fingerprint
from aikey.index_rebuild import MemoryStore, MixedRevisionError, Rebuild, RebuildError

OLD, NEW = "a" * 64, "b" * 64
OBJECTS = [f"obj-{i}" for i in range(5)]


def vector(seed):
    values = [0.0] * 768
    values[seed % 768] = 1.0
    return values


class Embedder:
    def __init__(self, revision, offset=100):
        self.served, self.offset, self.calls = revision, offset, []

    async def revision(self):
        return self.served

    async def embed(self, image):
        self.calls.append(image)
        return vector(self.offset + int(image.decode().split("-")[1]))


def source(missing=(), fail_on=None):
    async def read(object_id):
        if object_id == fail_on:
            raise OSError("interrupted")
        return None if object_id in missing else object_id.encode()
    return read


def setup(tmp_path, objects=OBJECTS):
    identity = {"backend": "local-clip-onnx", "dimensions": 768, "model": "clip-ViT-L-14",
                "profile": "clip-basic-v1", "revision": OLD, "source": "http://127.0.0.1:8180"}
    (tmp_path / "search-profile.json").write_text(json.dumps(_with_fingerprint(identity)))
    return MemoryStore({o: vector(int(o.split("-")[1])) for o in objects})


def rebuild(store, tmp_path):
    return Rebuild(store, tmp_path, target_revision=NEW, target_source="http://127.0.0.1:8181")


BACKUP = {"verified": True, "counts": {"embeddings": 5, "tables": {"ramDetections": 5}}}


def quiet():
    return True


@pytest.fixture(autouse=True)
def approved(monkeypatch):
    """Engine tests run as if both #18 gates were approved; the gate has its own tests."""
    monkeypatch.setattr("aikey.index_rebuild.APPROVED_IMAGE_SOURCE", "synthetic-test-source")
    monkeypatch.setattr("aikey.index_rebuild.APPROVED_NATIVE_READBACK", "synthetic-test-readback")


async def test_an_interrupted_stage_resumes_without_re_embedding(tmp_path):
    store = setup(tmp_path)
    embedder = Embedder(NEW)
    first = await rebuild(store, tmp_path).stage(embedder, source(), batch=2, max_batches=1)
    assert first == {"embedded": 2, "no_source": 0, "state": "staging"}
    with pytest.raises(OSError):                               # a crash inside the next batch
        await rebuild(store, tmp_path).stage(embedder, source(fail_on="obj-3"), batch=2)
    resumed = rebuild(store, tmp_path)                         # a new process reads the journal
    result = await resumed.stage(embedder, source(), batch=2)
    assert result == {"embedded": 5, "no_source": 0, "state": "staged"}
    assert sorted(embedder.calls).count(b"obj-0") == 1          # finished rows were not redone
    assert store.live == {o: vector(int(o.split("-")[1])) for o in OBJECTS}   # live untouched
    staged_profile = json.loads((tmp_path / "index-rebuild" / NEW[:12] / "search-profile.json").read_text())
    live_profile = json.loads((tmp_path / "search-profile.json").read_text())
    assert staged_profile["revision"] == NEW and live_profile["revision"] == OLD


async def test_staging_refuses_an_encoder_of_another_revision(tmp_path):
    store = setup(tmp_path)
    with pytest.raises(MixedRevisionError):
        await rebuild(store, tmp_path).stage(Embedder(OLD), source())
    assert store.staged_summary(NEW[:12])["embedded"] == 0


async def test_an_encoder_swap_mid_run_stops_before_the_next_batch(tmp_path):
    store = setup(tmp_path)
    embedder = Embedder(NEW)
    run = rebuild(store, tmp_path)
    await run.stage(embedder, source(), batch=2, max_batches=1)
    embedder.served = "c" * 64
    with pytest.raises(MixedRevisionError):
        await run.stage(embedder, source(), batch=2)
    assert store.staged_summary(NEW[:12])["embedded"] == 2


async def test_verify_rejects_vectors_of_another_revision(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    store.upsert(NEW[:12], "c" * 64, [("obj-0", "embedded", vector(1))])
    with pytest.raises(MixedRevisionError):
        run.verify()


async def test_missing_sources_block_cutover_because_rows_cannot_be_cleared(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    assert (await run.stage(Embedder(NEW), source(missing={"obj-2"})))["no_source"] == 1
    with pytest.raises(RebuildError, match="Not every live row"):
        run.verify()


async def test_cutover_needs_a_quiesced_key_and_a_matching_verified_backup(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()
    with pytest.raises(RebuildError, match="Stop the AI Key"):
        run.cutover(backup=BACKUP, key_quiesced=lambda: False)
    with pytest.raises(RebuildError, match="verified backup"):
        run.cutover(backup={"verified": True, "counts": {"embeddings": 4, "tables": {"ramDetections": 4}}},
                    key_quiesced=quiet)
    assert store.live["obj-1"] == vector(1)


async def test_a_failed_cutover_transaction_leaves_everything_unchanged(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()
    store.fail_cutover = True
    with pytest.raises(RebuildError, match="Simulated"):
        run.cutover(backup=BACKUP, key_quiesced=quiet)
    assert store.live["obj-1"] == vector(1) and run.state == "verified"
    assert json.loads((tmp_path / "search-profile.json").read_text())["revision"] == OLD


async def test_a_failed_profile_swap_rolls_the_vectors_back(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()

    def broken(path, value):
        raise OSError("disk full")
    with pytest.raises(RebuildError, match="restored"):
        run.cutover(backup=BACKUP, key_quiesced=quiet, write_profile=broken)
    assert store.live["obj-1"] == vector(1) and run.state == "verified"
    assert json.loads((tmp_path / "search-profile.json").read_text())["revision"] == OLD
    # The retry after the fault succeeds from the same verified generation.
    assert run.cutover(backup=BACKUP, key_quiesced=quiet)["state"] == "cut_over"
    assert store.live["obj-1"] == vector(101)


async def test_cutover_and_rollback_are_idempotent_and_restore_the_previous_index(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()
    assert run.cutover(backup=BACKUP, key_quiesced=quiet) == {"state": "cut_over", "rows": 5}
    assert run.cutover(backup=BACKUP, key_quiesced=quiet)["already"] is True
    assert json.loads((tmp_path / "search-profile.json").read_text())["revision"] == NEW
    reopened = rebuild(store, tmp_path)                        # after a restart, from the journal
    assert (await reopened.rollback(key_quiesced=quiet))["state"] == "rolled_back"
    assert (await reopened.rollback(key_quiesced=quiet))["already"] is True
    assert store.live == {o: vector(int(o.split("-")[1])) for o in OBJECTS}
    assert json.loads((tmp_path / "search-profile.json").read_text())["revision"] == OLD


async def test_a_crash_after_the_swap_committed_keeps_the_original_snapshot(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()
    store.cutover(NEW[:12], NEW)          # the transaction committed; the journal never heard of it
    result = rebuild(store, tmp_path).cutover(backup=BACKUP, key_quiesced=quiet)
    assert result["state"] == "cut_over"
    await rebuild(store, tmp_path).rollback(key_quiesced=quiet)
    assert store.live["obj-4"] == vector(4)                    # the old vectors, not the staged ones


async def test_rollback_re_embeds_rows_indexed_after_cutover_or_refuses(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()
    run.cutover(backup=BACKUP, key_quiesced=quiet)
    store.live["obj-9"] = vector(109)                          # indexed with the new encoder
    with pytest.raises(RebuildError, match="indexed after cutover"):
        await run.rollback(key_quiesced=quiet)
    with pytest.raises(MixedRevisionError):
        await run.rollback(key_quiesced=quiet, previous_embedder=Embedder(NEW), source=source())
    result = await run.rollback(key_quiesced=quiet, previous_embedder=Embedder(OLD, offset=0), source=source())
    assert result == {"state": "rolled_back", "rows": 6, "reembedded": 1}
    assert store.live["obj-9"] == vector(9)


async def test_a_generation_refuses_a_live_revision_that_moved_on(tmp_path):
    store = setup(tmp_path)
    await rebuild(store, tmp_path).stage(Embedder(NEW), source(), batch=1, max_batches=1)
    profile = json.loads((tmp_path / "search-profile.json").read_text())
    profile["revision"] = "c" * 64
    (tmp_path / "search-profile.json").write_text(json.dumps(profile))
    with pytest.raises(RebuildError, match="live revision changed"):
        rebuild(store, tmp_path)


def test_an_unpinned_or_identical_revision_is_refused(tmp_path):
    store = setup(tmp_path)
    with pytest.raises(RebuildError, match="already the live"):
        Rebuild(store, tmp_path, target_revision=OLD, target_source="x")
    with pytest.raises(RebuildError, match="64-digit"):
        Rebuild(store, tmp_path, target_revision="short", target_source="x")


async def test_the_clip_embedder_is_pinned_to_the_target_revision(tmp_path):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from aikey import clip
    from aikey.index_rebuild import ClipEmbedder, main, status
    served = {"revision": NEW}

    async def image(request):
        await request.read()
        return web.json_response({"model": clip.MODEL, "dim": 768, "revision": served["revision"],
                                  "embeddings": [vector(3)]})

    async def health(request):
        return web.json_response({"status": "ok", "revision": served["revision"]})
    app = web.Application()
    app.router.add_post("/v1/image", image)
    app.router.add_get("/healthz", health)
    async with TestServer(app) as server:
        embedder = ClipEmbedder(f"http://127.0.0.1:{server.port}", NEW)
        try:
            assert await embedder.revision() == NEW
            assert (await embedder.embed(b"\xff\xd8\xff" + b"0" * 32))[3] == 1.0
            served["revision"] = OLD
            with pytest.raises(clip.ClipError):
                await embedder.embed(b"\xff\xd8\xff" + b"0" * 32)
        finally:
            await embedder.close()
    store = setup(tmp_path)
    await rebuild(store, tmp_path).stage(Embedder(NEW), source(), batch=2, max_batches=1)
    [entry] = status(tmp_path)
    assert entry["state"] == "staging" and entry["to"] == NEW[:12] and entry["counts"]["embedded"] == 2
    assert main(["status", "--state-dir", str(tmp_path)]) == 0
