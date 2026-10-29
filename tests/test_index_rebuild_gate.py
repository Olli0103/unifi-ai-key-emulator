"""The rebuild library refuses unapproved staging and cutover before any write (#18)."""

import hashlib
from pathlib import Path

import pytest

from aikey import index_rebuild
from aikey.index_rebuild import _FIXED_BLOCKERS, RebuildError
from test_index_rebuild import BACKUP, NEW, Embedder, quiet, rebuild, setup, source, vector


def _digest(root: Path) -> str:
    value = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        value.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return value.hexdigest()


def test_both_gates_ship_closed():
    assert index_rebuild.APPROVED_IMAGE_SOURCE is None
    assert index_rebuild.APPROVED_NATIVE_READBACK is None


async def test_staging_without_an_approved_source_writes_nothing(tmp_path):
    store = setup(tmp_path)
    before = _digest(tmp_path)
    embedder = Embedder(NEW)
    with pytest.raises(RebuildError, match="No approved stored-object image source"):
        await rebuild(store, tmp_path).stage(embedder, source())
    assert store.stage == {} and embedder.calls == []               # no table, no encoder call
    assert _digest(tmp_path) == before                               # no journal, no staged profile


async def _verified(tmp_path, monkeypatch):
    monkeypatch.setattr(index_rebuild, "APPROVED_IMAGE_SOURCE", "synthetic-test-source")
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()
    return store, run


async def test_a_verified_generation_cannot_cut_over_without_native_readback(tmp_path, monkeypatch):
    store, run = await _verified(tmp_path, monkeypatch)
    before = _digest(tmp_path)
    with pytest.raises(RebuildError, match="No approved native Protect post-cutover readback"):
        run.cutover(backup=BACKUP, key_quiesced=quiet)
    assert store.live["obj-1"] == vector(1) and store.snapshot == {}
    assert run.state == "verified" and _digest(tmp_path) == before


async def test_a_cutover_is_refused_when_the_source_approval_is_withdrawn(tmp_path, monkeypatch):
    store, run = await _verified(tmp_path, monkeypatch)
    monkeypatch.setattr(index_rebuild, "APPROVED_IMAGE_SOURCE", None)
    monkeypatch.setattr(index_rebuild, "APPROVED_NATIVE_READBACK", "synthetic-test-readback")
    with pytest.raises(RebuildError, match=_FIXED_BLOCKERS[0].split(":")[0]):
        run.cutover(backup=BACKUP, key_quiesced=quiet)
    assert store.live["obj-1"] == vector(1)


async def test_rollback_stays_available_after_approvals_are_withdrawn(tmp_path, monkeypatch):
    store, run = await _verified(tmp_path, monkeypatch)
    monkeypatch.setattr(index_rebuild, "APPROVED_NATIVE_READBACK", "synthetic-test-readback")
    run.cutover(backup=BACKUP, key_quiesced=quiet)
    assert store.live["obj-1"] != vector(1)
    monkeypatch.setattr(index_rebuild, "APPROVED_IMAGE_SOURCE", None)
    monkeypatch.setattr(index_rebuild, "APPROVED_NATIVE_READBACK", None)
    assert (await run.rollback(key_quiesced=quiet))["state"] == "rolled_back"
    assert store.live["obj-1"] == vector(1)
