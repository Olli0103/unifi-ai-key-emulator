"""PgStore against a real pgvector database (#18). Opt-in: set AIKEY_TEST_PG_DSN to a
scratch database; the test creates and drops its own tables. Never point it at a live index."""

import json
import os

import pytest

from test_index_rebuild import BACKUP, NEW, OBJECTS, Embedder, quiet, setup, source, vector

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("AIKEY_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="AIKEY_TEST_PG_DSN is not set")

from aikey.index_rebuild import PgStore, Rebuild, RebuildError  # noqa: E402


def connect():
    return psycopg.connect(DSN, autocommit=True)


@pytest.fixture
def store(tmp_path):
    with connect() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("DROP SCHEMA IF EXISTS aikey_rebuild CASCADE")
        conn.execute('DROP TABLE IF EXISTS public."ramDetections"')
        conn.execute('CREATE TABLE public."ramDetections" (id varchar PRIMARY KEY, '
                     '"smartDetectObjectId" varchar NOT NULL UNIQUE, embedding vector NOT NULL)')
        for i, object_id in enumerate(OBJECTS):
            conn.execute('INSERT INTO public."ramDetections" VALUES (%s, %s, %s::vector)',
                         (f"row-{i}", object_id, json.dumps(vector(i))))
    setup(tmp_path)
    yield PgStore(connect)
    with connect() as conn:
        conn.execute("DROP SCHEMA IF EXISTS aikey_rebuild CASCADE")
        conn.execute('DROP TABLE IF EXISTS public."ramDetections"')


def live(object_id):
    with connect() as conn:
        (text,) = conn.execute('SELECT embedding::text FROM public."ramDetections" '
                               'WHERE "smartDetectObjectId" = %s', (object_id,)).fetchone()
    return json.loads(text)


async def test_the_sql_store_stages_resumes_cuts_over_and_rolls_back(store, tmp_path):
    run = Rebuild(store, tmp_path, target_revision=NEW, target_source="http://127.0.0.1:8181")
    embedder = Embedder(NEW)
    await run.stage(embedder, source(), batch=2, max_batches=1)
    with pytest.raises(OSError):
        await run.stage(embedder, source(fail_on="obj-3"), batch=2)
    assert (await run.stage(embedder, source(), batch=2))["embedded"] == 5
    assert run.verify()["uncovered"] == 0
    assert live("obj-1") == vector(1)                                  # staging never touched live rows

    store_fail = PgStore(connect)
    original = store_fail.cutover
    with connect() as conn:                                            # a row appears after verify
        conn.execute('INSERT INTO public."ramDetections" VALUES (%s, %s, %s::vector)',
                     ("row-x", "obj-x", json.dumps(vector(50))))
    backup = {"verified": True, "counts": {"embeddings": 6, "tables": {"ramDetections": 6}}}
    with pytest.raises(RebuildError, match="Coverage changed"):
        Rebuild(store_fail, tmp_path, target_revision=NEW, target_source="http://127.0.0.1:8181").cutover(
            backup=backup, key_quiesced=quiet)
    assert live("obj-1") == vector(1) and original                     # transaction rolled back
    with connect() as conn:
        conn.execute('DELETE FROM public."ramDetections" WHERE id = %s', ("row-x",))

    assert run.cutover(backup=BACKUP, key_quiesced=quiet)["rows"] == 5
    assert live("obj-1") == vector(101)
    assert (await run.rollback(key_quiesced=quiet))["rows"] == 5
    assert live("obj-1") == vector(1) and live("obj-4") == vector(4)


async def test_the_sql_store_recognizes_a_committed_swap_after_a_crash(store, tmp_path):
    run = Rebuild(store, tmp_path, target_revision=NEW, target_source="http://127.0.0.1:8181")
    await run.stage(Embedder(NEW), source())
    run.verify()
    store.cutover(NEW[:12], NEW)                                        # committed, journal unaware
    assert store.swapped(NEW[:12]) is True
    Rebuild(store, tmp_path, target_revision=NEW, target_source="x").cutover(backup=BACKUP, key_quiesced=quiet)
    await Rebuild(store, tmp_path, target_revision=NEW, target_source="x").rollback(key_quiesced=quiet)
    assert live("obj-2") == vector(2)
