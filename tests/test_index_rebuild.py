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


async def crash_after_profile_swap(tmp_path, monkeypatch):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()

    def crash_before_journal(**changes):
        assert changes["state"] == "cut_over"
        raise SystemExit("crash after profile replacement")

    monkeypatch.setattr(run, "_save", crash_before_journal)
    with pytest.raises(SystemExit):
        run.cutover(backup=BACKUP, key_quiesced=quiet)
    assert json.loads(run.live_profile.read_text())["revision"] == NEW
    assert json.loads(run.journal_path.read_text())["state"] == "verified"
    return store, run


async def test_a_crash_after_profile_swap_recovers_without_replacing_the_snapshot(tmp_path, monkeypatch):
    store, run = await crash_after_profile_swap(tmp_path, monkeypatch)
    original_snapshot = {k: list(v) for k, v in store.snapshot[run.tag].items()}

    resumed = rebuild(store, tmp_path)
    assert resumed.cutover(backup=BACKUP, key_quiesced=quiet) == {"state": "cut_over", "rows": 5}
    assert store.snapshot[run.tag] == original_snapshot
    await rebuild(store, tmp_path).rollback(key_quiesced=quiet)
    assert store.live == {o: vector(int(o.split("-")[1])) for o in OBJECTS}
    assert json.loads(run.live_profile.read_text())["revision"] == OLD


@pytest.mark.parametrize("fault", ["missing_previous", "wrong_previous", "missing_snapshot",
                                   "changed_vectors", "changed_profile", "changed_endpoint"])
async def test_profile_swap_recovery_refuses_inconsistent_evidence_without_writes(tmp_path, monkeypatch, fault):
    store, run = await crash_after_profile_swap(tmp_path, monkeypatch)
    previous = run.directory / "previous-profile.json"
    if fault == "missing_previous":
        previous.unlink()
    elif fault == "wrong_previous":
        value = json.loads(previous.read_text())
        value["revision"] = "c" * 64
        previous.write_text(json.dumps(value))
    elif fault == "missing_snapshot":
        del store.snapshot[run.tag]
    elif fault == "changed_vectors":
        store.live["obj-0"] = vector(50)
    elif fault == "changed_profile":
        value = json.loads(run.live_profile.read_text())
        value["model"] = "another-model"
        run.live_profile.write_text(json.dumps(value))
    before_files = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    before_store = json.dumps(vars(store), sort_keys=True)
    with pytest.raises(RebuildError):
        Rebuild(store, tmp_path, target_revision=NEW,
                target_source="http://127.0.0.1:9999" if fault == "changed_endpoint" else "http://127.0.0.1:8181")
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before_files
    assert json.dumps(vars(store), sort_keys=True) == before_store


@pytest.mark.parametrize("fault", ["active_key", "unverified_backup", "withdrawn_approval", "lost_snapshot"])
async def test_profile_swap_recovery_rechecks_cutover_guards(tmp_path, monkeypatch, fault):
    store, run = await crash_after_profile_swap(tmp_path, monkeypatch)
    resumed = rebuild(store, tmp_path)
    if fault == "withdrawn_approval":
        monkeypatch.setattr("aikey.index_rebuild.APPROVED_NATIVE_READBACK", None)
    elif fault == "lost_snapshot":
        del store.snapshot[run.tag]
    before_files = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    before_store = json.dumps(vars(store), sort_keys=True)
    with pytest.raises(RebuildError):
        resumed.cutover(backup={} if fault == "unverified_backup" else BACKUP,
                        key_quiesced=lambda: fault != "active_key")
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before_files
    assert json.dumps(vars(store), sort_keys=True) == before_store


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
    profile.pop('fingerprint')
    (tmp_path / "search-profile.json").write_text(json.dumps(_with_fingerprint(profile)))
    with pytest.raises(RebuildError, match="live revision changed"):
        rebuild(store, tmp_path)


def test_an_invalid_revision_or_identical_full_identity_is_refused(tmp_path):
    from aikey.encoding_identity import CLIP_ENCODING
    store = setup(tmp_path)
    path = tmp_path/'search-profile.json'
    profile = json.loads(path.read_text())
    profile.pop('fingerprint')
    profile.update(CLIP_ENCODING, encoding_required=True)
    path.write_text(json.dumps(_with_fingerprint(profile)))
    with pytest.raises(RebuildError, match="already the live"):
        Rebuild(store, tmp_path, target_revision=OLD, target_source="http://127.0.0.1:8180")
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
                                  "encoding": clip.CLIP_ENCODING, "embeddings": [vector(3)]})

    async def health(request):
        return web.json_response({"status": "ok", "revision": served["revision"],
                                  "encoding": clip.CLIP_ENCODING})
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


async def test_rollback_restores_the_verbatim_previous_profile_not_a_reserialization(tmp_path):
    store = setup(tmp_path)
    path = tmp_path / 'search-profile.json'
    original = b' \n' + path.read_bytes() + b'\n\n'
    path.write_bytes(original)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()
    run.cutover(backup=BACKUP, key_quiesced=quiet)
    assert (run.directory / 'previous-profile.json').read_bytes() == original
    await run.rollback(key_quiesced=quiet)
    assert path.read_bytes() == original


async def test_failed_profile_switch_restores_verbatim_live_profile(tmp_path):
    store = setup(tmp_path)
    path = tmp_path / 'search-profile.json'
    original = b' \n' + path.read_bytes() + b'\n\n'
    path.write_bytes(original)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()
    def fail(*args):
        raise OSError('synthetic write interruption')
    with pytest.raises(RebuildError, match='previous vectors and profile were restored'):
        run.cutover(backup=BACKUP, key_quiesced=quiet, write_profile=fail)
    assert path.read_bytes() == original
    assert store.live == {o: vector(int(o.split('-')[1])) for o in OBJECTS}


async def test_same_weights_with_new_encoding_contract_can_cut_over_and_restore_exact_old_profile(tmp_path):
    from aikey.encoding_identity import CLIP_ENCODING
    store = setup(tmp_path)
    profile_path = tmp_path / 'search-profile.json'
    old = json.loads(profile_path.read_text())
    old.pop('fingerprint')
    old.update(CLIP_ENCODING, preprocessing='fictional-prior-preprocessing-v0')
    profile_path.write_text(json.dumps(_with_fingerprint(old)))
    before = profile_path.read_bytes(), dict(store.live)
    def generation():
        return Rebuild(store, tmp_path, target_revision=OLD, target_source=old['source'],
                       generation='123456abcdef')
    run = generation()
    await run.stage(Embedder(OLD), source())
    run.verify()
    run.cutover(backup=BACKUP, key_quiesced=quiet)
    assert generation().state == 'cut_over'
    assert json.loads(profile_path.read_text())['preprocessing'] == CLIP_ENCODING['preprocessing']
    await generation().rollback(key_quiesced=quiet)
    assert (profile_path.read_bytes(), store.live) == before


def test_tampered_profile_fingerprint_refuses_without_any_stage(tmp_path):
    store = setup(tmp_path)
    path = tmp_path / 'search-profile.json'
    profile = json.loads(path.read_text())
    profile['fingerprint'] = '0'*64
    path.write_text(json.dumps(profile))
    with pytest.raises(RebuildError, match='fingerprint is invalid'):
        rebuild(store, tmp_path)
    assert not (tmp_path / 'index-rebuild').exists()


async def test_first_failed_quality_attempt_freezes_targets_before_model_work(tmp_path):
    from aikey.index_readback import NativeReadbackPlan, ReadbackError
    from test_index_readback import specification
    store = setup(tmp_path)
    run = rebuild(store,tmp_path)
    await run.stage(Embedder(NEW),source())
    run.verify()
    run._save(source_archive_sha256='c'*64,source_identity=store.source_identity())
    store.staged_identity = lambda tag: 'd'*64
    class FailedTarget(Embedder):
        async def embed_text(self,text):
            self.calls.append('quality')
            raise RuntimeError('Fictional failed target query')
    encoder = FailedTarget(NEW)
    plan = NativeReadbackPlan(specification())
    before = dict(store.live),run.live_profile.read_bytes()
    with pytest.raises(RuntimeError,match='Fictional failed target query'):
        await run.qualify(encoder,plan)
    assert json.loads((run.directory/'readback-plan.json').read_text()) == plan.value
    assert rebuild(store,tmp_path).journal['quality']['passed'] is False
    changed = plan.value
    changed['queries'][0]['text'] = 'A changed fictional target'
    with pytest.raises(ReadbackError,match='targets are frozen'):
        await run.qualify(encoder,NativeReadbackPlan(changed))
    assert encoder.calls == ['quality']
    assert (store.live,run.live_profile.read_bytes()) == before
