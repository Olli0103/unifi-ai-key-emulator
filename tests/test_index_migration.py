"""Read-only index-migration status and dry-run plan (#18)."""

import hashlib
import json
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from aikey.admin_security import AdminSecurity
from aikey.control_site import ControlSite, _COOKIE
from aikey.index_rebuild import _FIXED_BLOCKERS, migration_plan
from test_control_site import fixture
from test_index_rebuild import BACKUP, NEW, OLD, Embedder, quiet, rebuild, setup, source


def backup_manifest(directory, rows, *, verified=True):
    directory.mkdir(exist_ok=True)
    (directory / "search-20260927T010000.dump").write_bytes(b"PGDMP")
    value = {"dump": "search-20260927T010000.dump", "sha256": "0" * 64,
             "counts": {"embeddings": rows, "tables": {"ramDetections": rows}}}
    if verified:
        value["verified"] = {"at": "20260927T010100", "restore_counts_match": True}
    (directory / "search-20260927T010000.json").write_text(json.dumps(value))


def test_without_a_profile_nothing_is_pinned_and_apply_is_unavailable(tmp_path):
    plan = migration_plan(tmp_path)
    assert plan["live"] == {"pinned": False, "revision": None, "source": None, "rows": None}
    assert plan["apply"]["available"] is False and plan["no_op"] is True
    assert "The live profile has no pinned encoder revision" in plan["apply"]["reasons"]


def test_the_current_index_without_a_stage_is_a_no_op(tmp_path):
    setup(tmp_path)
    backup_manifest(tmp_path / "backups", 5)
    plan = migration_plan(tmp_path, backups_dir=tmp_path / "backups", live_rows=5)
    assert plan["live"]["revision"] == OLD[:12] and plan["generations"] == [] and plan["no_op"]
    reasons = plan["apply"]["reasons"]
    assert reasons[:2] == list(_FIXED_BLOCKERS) and "No staged generation for a new encoder revision" in reasons
    assert plan["rollback"]["ready"] is True                        # the verified backup restores it
    assert [s["status"] for s in plan["steps"]][:1] == ["done"]


async def test_a_partial_stage_reports_coverage_and_why_it_cannot_apply(tmp_path):
    store = setup(tmp_path)
    await rebuild(store, tmp_path).stage(Embedder(NEW), source(), batch=2, max_batches=1)
    plan = migration_plan(tmp_path, live_rows=5)
    [generation] = plan["generations"]
    assert generation["state"] == "staging" and generation["coverage"] == {
        "embedded": 2, "no_source": 0, "live_rows": 5, "percent": 40.0, "uncovered": 3}
    reasons = plan["apply"]["reasons"]
    assert f"Generation {NEW[:12]} is staging, not verified" in reasons
    assert f"Generation {NEW[:12]} does not cover every live row" in reasons
    assert plan["no_op"] is False


async def test_a_generation_from_an_older_live_revision_is_flagged_stale(tmp_path):
    store = setup(tmp_path)
    await rebuild(store, tmp_path).stage(Embedder(NEW), source(), batch=2, max_batches=1)
    profile = json.loads((tmp_path / "search-profile.json").read_text())
    profile["revision"] = "c" * 64
    (tmp_path / "search-profile.json").write_text(json.dumps(profile))
    [generation] = migration_plan(tmp_path)["generations"]
    assert "this generation is stale" in generation["problems"][0]


def test_a_journal_targeting_the_live_revision_or_unreadable_is_a_problem(tmp_path):
    setup(tmp_path)
    same = tmp_path / "index-rebuild" / OLD[:12]
    same.mkdir(parents=True)
    (same / "journal.json").write_text(json.dumps({"state": "staged", "revision": OLD,
                                                   "from_revision": OLD, "counts": {}}))
    broken = tmp_path / "index-rebuild" / ("d" * 12)
    broken.mkdir()
    (broken / "journal.json").write_text("{not json")
    generations = {g["generation"]: g for g in migration_plan(tmp_path)["generations"]}
    assert "already the live revision" in generations[OLD[:12]]["problems"][0]
    assert generations["d" * 12]["state"] == "unreadable"


async def test_a_verified_generation_with_a_verified_backup_still_cannot_cut_over(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()
    backup_manifest(tmp_path / "backups", 5)
    plan = migration_plan(tmp_path, backups_dir=tmp_path / "backups", live_rows=5)
    assert plan["apply"] == {"available": False, "reasons": [
        *_FIXED_BLOCKERS, "The AI Key must be stopped for a cutover; this page never stops it"]}
    statuses = {s["step"]: s["status"] for s in plan["steps"]}
    assert statuses["Verify revision, dimensions and coverage"] == "done"
    assert statuses["Stop the AI Key and cut over in one transaction"] == "blocked"


def test_an_unverified_or_stale_backup_blocks_rollback_readiness(tmp_path):
    setup(tmp_path)
    backup_manifest(tmp_path / "backups", 5, verified=False)
    plan = migration_plan(tmp_path, backups_dir=tmp_path / "backups", live_rows=5)
    assert plan["rollback"]["ready"] is False and "no recorded scratch-restore" in plan["rollback"]["reason"]
    backup_manifest(tmp_path / "backups", 4)
    plan = migration_plan(tmp_path, backups_dir=tmp_path / "backups", live_rows=5)
    assert "holds 4 rows; the live index holds 5" in plan["backup"]["reason"]


async def test_a_cut_over_generation_reports_rollback_readiness(tmp_path):
    store = setup(tmp_path)
    run = rebuild(store, tmp_path)
    await run.stage(Embedder(NEW), source())
    run.verify()
    run.cutover(backup=BACKUP, key_quiesced=quiet)
    plan = migration_plan(tmp_path, live_rows=5)
    assert plan["live"]["revision"] == NEW[:12] and plan["rollback"]["ready"] is True
    assert plan["generations"][0]["problems"] == []


def _digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return digest.hexdigest()


async def test_the_control_site_page_is_read_only(tmp_path):
    key_config, port_config = fixture(tmp_path)
    search = tmp_path / "search"
    search.mkdir()
    store = setup(search)
    await rebuild(store, search).stage(Embedder(NEW), source(), batch=2, max_batches=1)
    backup_manifest(tmp_path / "backups", 5)

    async def rows():
        return 5
    password = "synthetic-admin-passphrase"
    site = ControlSite(key_config, port_config, signing_key=b"s" * 32,
                       password_record=AdminSecurity.create_password_record(password), port=8765,
                       search_state_dir=search, search_backups_dir=tmp_path / "backups",
                       search_live_rows=rows)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"s" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    before = _digest(search) + _digest(tmp_path / "backups")
    try:
        assert (await client.get("/index-migration", allow_redirects=False)).status == 303
        await client.post("/login", data={"password": password},
                          headers={"Origin": site.origin}, allow_redirects=False)
        assert _COOKIE in client.session.cookie_jar.filter_cookies(URL(site.origin))
        index = await (await client.get("/")).text()
        assert "/index-migration" in index
        page = await (await client.get("/index-migration")).text()
        assert OLD[:12] in page and NEW[:12] in page and "40.0% of 5" in page
        assert "Apply is unavailable" in page and "No approved stored-object image source" in page
        assert "<form" not in page.split("Search index migration</h1>")[1] and "<button" not in page
        assert (await client.post("/index-migration", headers={"Origin": site.origin})).status == 405
        assert _digest(search) + _digest(tmp_path / "backups") == before
    finally:
        await client.close()
        await server.close()


async def test_the_page_is_absent_unless_configured(tmp_path):
    key_config, port_config = fixture(tmp_path)
    site = ControlSite(key_config, port_config, signing_key=b"s" * 32,
                       password_record=AdminSecurity.create_password_record("p" * 20), port=8765)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"s" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        await client.post("/login", data={"password": "p" * 20}, headers={"Origin": site.origin},
                          allow_redirects=False)
        assert (await client.get("/index-migration")).status == 404
    finally:
        await client.close()
        await server.close()

