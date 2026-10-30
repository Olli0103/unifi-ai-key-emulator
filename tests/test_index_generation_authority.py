"""Generation transitions must use current, exclusively owned durable state."""

import asyncio
import json
import os

import pytest

from aikey.index_rebuild import Rebuild, RebuildError, cancel_generation, migration_plan
from test_index_rebuild import approved  # noqa: F401
from test_index_rebuild import BACKUP, NEW, OLD, Embedder, quiet, rebuild, setup, source


async def test_old_stage_handle_cannot_rewrite_a_committed_generation(tmp_path):
    store = setup(tmp_path)
    stale = rebuild(store, tmp_path)
    owner = rebuild(store, tmp_path)
    await owner.stage(Embedder(NEW), source())
    owner.verify()
    owner.cutover(backup=BACKUP, key_quiesced=quiet)
    before = (owner.journal_path.read_bytes(), owner.live_profile.read_bytes(), dict(store.live))
    result = await stale.stage(Embedder(NEW), source())
    assert result["state"] == "cut_over"
    assert (owner.journal_path.read_bytes(), owner.live_profile.read_bytes(), store.live) == before
    # A new process can still identify and roll back the committed generation.
    assert (await rebuild(store, tmp_path).rollback(key_quiesced=quiet))["state"] == "rolled_back"


async def test_old_verified_handle_cannot_cut_over_again_after_rollback(tmp_path):
    store = setup(tmp_path)
    owner = rebuild(store, tmp_path)
    await owner.stage(Embedder(NEW), source())
    owner.verify()
    stale = rebuild(store, tmp_path)
    owner.cutover(backup=BACKUP, key_quiesced=quiet)
    await owner.rollback(key_quiesced=quiet)
    before = (owner.journal_path.read_bytes(), owner.live_profile.read_bytes(), dict(store.live))
    with pytest.raises(RebuildError, match="rolled_back"):
        stale.cutover(backup=BACKUP, key_quiesced=quiet)
    assert (owner.journal_path.read_bytes(), owner.live_profile.read_bytes(), store.live) == before


async def test_a_second_stager_is_refused_before_any_source_or_encoder_call(tmp_path):
    store = setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def held_source(object_id):
        entered.set()
        await release.wait()
        return object_id.encode()

    class UnusedEncoder:
        async def revision(self):
            calls.append("revision")
            return NEW

        async def embed(self, image):
            calls.append("embed")
            raise AssertionError("must not enter a second inference path")

    async def unused_source(object_id):
        calls.append("source")
        raise AssertionError("must not read another source")

    owner = rebuild(store, tmp_path)
    task = asyncio.create_task(owner.stage(Embedder(NEW), held_source, batch=1, max_batches=1))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        with pytest.raises(RebuildError, match="busy"):
            await rebuild(store, tmp_path).stage(UnusedEncoder(), unused_source)
        assert calls == []
    finally:
        release.set()
        await task


async def test_cancel_partial_generation_keeps_live_and_staged_vectors(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source(), batch=2, max_batches=1)
    live, profile = dict(store.live), run.live_profile.read_bytes()
    staged = store.staged_summary(run.tag)
    result = run.cancel()
    assert result["state"] == "cancelled"
    assert store.live == live and run.live_profile.read_bytes() == profile
    assert store.staged_summary(run.tag) == staged
    fresh = rebuild(store, tmp_path)
    assert fresh.cancel() == {"state": "cancelled", "already": True}
    with pytest.raises(RebuildError, match="cancelled"):
        await fresh.stage(Embedder(NEW), source())
    with pytest.raises(RebuildError, match="cancelled"):
        fresh.verify()
    with pytest.raises(RebuildError, match="cancelled"):
        fresh.cutover(backup=BACKUP, key_quiesced=quiet)
    assert json.loads(profile)["revision"] == OLD


async def test_cancel_does_not_claim_to_abort_an_in_flight_writer(tmp_path):
    store = setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_source(object_id):
        entered.set()
        await release.wait()
        return object_id.encode()

    run = rebuild(store, tmp_path)
    task = asyncio.create_task(run.stage(Embedder(NEW), held_source, batch=1, max_batches=1))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        with pytest.raises(RebuildError, match="busy"):
            rebuild(store, tmp_path).cancel()
    finally:
        release.set()
        await task
    assert rebuild(store, tmp_path).cancel()["state"] == "cancelled"


async def test_cancelling_a_live_generation_requires_explicit_rollback(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()
    run.cutover(backup=BACKUP, key_quiesced=quiet)
    before = (run.live_profile.read_bytes(), dict(store.live))
    with pytest.raises(RebuildError, match="cut_over"):
        rebuild(store, tmp_path).cancel()
    assert (run.live_profile.read_bytes(), store.live) == before


async def test_task_cancellation_releases_the_state_writer(tmp_path):
    store = setup(tmp_path)
    entered = asyncio.Event()

    async def blocked(_):
        entered.set()
        await asyncio.Future()

    task = asyncio.create_task(rebuild(store, tmp_path).stage(Embedder(NEW), blocked))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await rebuild(store, tmp_path).stage(Embedder(NEW), source()))["state"] == "staged"


async def test_a_different_target_generation_cannot_bypass_the_active_writer(tmp_path):
    store = setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(object_id):
        entered.set()
        await release.wait()
        return object_id.encode()

    task = asyncio.create_task(rebuild(store, tmp_path).stage(Embedder(NEW), blocked))
    await asyncio.wait_for(entered.wait(), 2)
    other = Rebuild(store, tmp_path, target_revision="c" * 64, target_source="http://127.0.0.1:8182")
    try:
        with pytest.raises(RebuildError, match="busy"):
            await other.stage(Embedder("c" * 64), source())
        assert not other.directory.exists()
    finally:
        release.set()
        await task


@pytest.mark.parametrize("fault", ["symlink", "hardlink", "public", "fifo"])
async def test_an_unsafe_writer_lock_is_refused_before_any_work(tmp_path, fault):
    store = setup(tmp_path)
    path = tmp_path / ".index-rebuild.lock"
    canary = tmp_path / "canary"
    canary.write_text("outside-state-canary")
    canary.chmod(0o600)
    if fault == "symlink":
        path.symlink_to(canary)
    elif fault == "hardlink":
        os.link(canary, path)
    elif fault == "public":
        path.write_text("")
        path.chmod(0o644)
    else:
        os.mkfifo(path, 0o600)
    embedder = Embedder(NEW)
    with pytest.raises(RebuildError):
        await rebuild(store, tmp_path).stage(embedder, source())
    assert embedder.calls == [] and not (tmp_path / "index-rebuild").exists()
    assert canary.read_text() == "outside-state-canary"


@pytest.mark.parametrize("raw", ['{"state":"staging","state":"verified"}', '{"n":NaN}',
                                '{"n":Infinity}', '{"n":1e999}', '[]', 'x' * 65537])
def test_ambiguous_or_oversized_index_records_are_refused(tmp_path, raw):
    path = tmp_path / "journal.json"
    path.write_text(raw)
    with pytest.raises(RebuildError):
        Rebuild._read(path)


@pytest.mark.parametrize("kind", ["fifo", "symlink", "directory"])
def test_index_record_special_files_are_bounded_and_not_followed(tmp_path, kind):
    path = tmp_path / "journal.json"
    if kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "directory":
        path.mkdir()
    else:
        target = tmp_path / "private-canary.json"
        target.write_text('{"private":"must-not-be-read"}')
        path.symlink_to(target)
    with pytest.raises(RebuildError):
        Rebuild._read(path)


@pytest.mark.parametrize("generation,journal,profile", [("../escape", "a" * 64, "b" * 64),
    ("c" * 12, None, "b" * 64), ("c" * 12, "a" * 64, None),
    ("c" * 12, "short", "b" * 64), (True, "a" * 64, "b" * 64)])
def test_invalid_cancel_identity_is_rejected_before_lock_or_directory_creation(
        tmp_path, generation, journal, profile):
    with pytest.raises(RebuildError):
        cancel_generation(tmp_path, generation, expected_journal=journal, expected_profile=profile)
    assert list(tmp_path.iterdir()) == []


async def test_cancelled_generation_is_not_an_active_plan_or_a_live_profile_change(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source(), batch=1, max_batches=1)
    run.cancel()
    [generation] = migration_plan(tmp_path, live_rows=5)["generations"]
    assert generation["state"] == "cancelled" and not generation["cancel"]["available"]
    assert "No staged generation for a new encoder revision" in migration_plan(tmp_path)["apply"]["reasons"]
    assert json.loads(run.live_profile.read_text())["revision"] == OLD


@pytest.mark.parametrize("swapped", ["root", "generation"])
@pytest.mark.parametrize("when", ["after_read", "during_commit"])
async def test_cancel_retains_directory_authority_across_path_replacement(
        tmp_path, monkeypatch, swapped, when):
    import aikey.index_rebuild as engine

    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source(), batch=2, max_batches=1)
    [view] = migration_plan(tmp_path)["generations"]
    choice = view["cancel"]
    canary = tmp_path / "outside"
    canary.mkdir(mode=0o700)
    (canary / "journal.json").write_text('{"private":"outside-canary"}')
    target = run.directory if swapped == "generation" else tmp_path / "index-rebuild"
    retained = target.with_name(target.name + "-retained")
    moved = []

    def replace_path():
        if not moved:
            target.rename(retained)
            target.symlink_to(canary, target_is_directory=True)
            moved.append(True)

    if when == "after_read":
        original = Rebuild._record

        def read_then_replace(path, **kwargs):
            value = original(path, **kwargs)
            if path == "search-profile.json" and kwargs.get("dir_fd") is not None:
                replace_path()
            return value

        monkeypatch.setattr(Rebuild, "_record", staticmethod(read_then_replace))
        failure = RebuildError
    else:
        original = engine._atomic_cancel_json

        def replace_then_commit(directory_fd, value):
            replace_path()
            original(directory_fd, value)

        monkeypatch.setattr(engine, "_atomic_cancel_json", replace_then_commit)
        failure = engine.RebuildOutcomeUncertain
    before = dict(store.live), run.live_profile.read_bytes()
    with pytest.raises(failure):
        cancel_generation(tmp_path, run.tag, expected_journal=choice["journal_revision"],
                          expected_profile=choice["profile_revision"])
    assert moved and (store.live, run.live_profile.read_bytes()) == before
    assert (canary / "journal.json").read_text() == '{"private":"outside-canary"}'
    assert sorted(p.name for p in canary.iterdir()) == ["journal.json"]
    actual = retained if swapped == "generation" else retained / run.tag
    assert json.loads((actual / "journal.json").read_text())["state"] == (
        "cancelled" if when == "during_commit" else "staging")


@pytest.mark.parametrize("counts", [[], None, "", False, {"embedded": True}, {"no_source": -1}])
async def test_invalid_generation_counts_do_not_become_cancel_choices(tmp_path, counts):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source(), batch=1, max_batches=1)
    value = json.loads(run.journal_path.read_text())
    value["counts"] = counts
    run.journal_path.write_text(json.dumps(value))
    [view] = migration_plan(tmp_path)["generations"]
    assert view["state"] == "unreadable" and not view.get("cancel", {}).get("available")


@pytest.mark.parametrize("schema,counts", [(True, {"embedded": 1, "no_source": 0}),
    (2, {"embedded": 1, "no_source": 0}), (1, {}), (1, {"embedded": True, "no_source": 0})])
async def test_manual_cancel_cannot_bypass_invalid_journal_identity(tmp_path, schema, counts):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source(), batch=1, max_batches=1)
    value = json.loads(run.journal_path.read_text())
    value.update(schema=schema, counts=counts)
    run.journal_path.write_text(json.dumps(value))
    _, journal_revision = Rebuild._record(run.journal_path)
    _, profile_revision = Rebuild._record(run.live_profile)
    before = run.journal_path.read_bytes(), run.live_profile.read_bytes(), dict(store.live)
    with pytest.raises(RebuildError, match="identity"):
        cancel_generation(tmp_path, run.tag, expected_journal=journal_revision,
                          expected_profile=profile_revision)
    assert (run.journal_path.read_bytes(), run.live_profile.read_bytes(), store.live) == before
