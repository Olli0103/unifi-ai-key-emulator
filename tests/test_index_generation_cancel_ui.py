"""A cancellation is a bounded state transition, never an index/model operation."""

import asyncio
from contextlib import asynccontextmanager
from html.parser import HTMLParser
import json

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey.admin_security import AdminSecurity
from aikey.control_site import ControlSite
from aikey.index_rebuild import migration_plan
from test_control_site import fixture
from test_index_rebuild import approved  # noqa: F401
from test_index_rebuild import NEW, Embedder, rebuild, setup, source


class Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "input" and attributes.get("name"):
            self.fields[attributes["name"]] = attributes.get("value", "")


@asynccontextmanager
async def site_fixture(tmp_path):
    key, port = fixture(tmp_path)
    search = tmp_path / "search"
    search.mkdir(mode=0o700)
    store = setup(search)
    await rebuild(store, search).stage(Embedder(NEW), source(), batch=2, max_batches=1)
    site = ControlSite(key, port, signing_key=b"s" * 32, port=8765,
                       password_record=AdminSecurity.create_password_record("synthetic-admin-passphrase"),
                       search_state_dir=search, audit_log=tmp_path / "audit.jsonl")
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"s" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        await client.post("/login", data={"password": "synthetic-admin-passphrase"},
                          headers={"Origin": site.origin}, allow_redirects=False)
        page = await (await client.get("/index-migration")).text()
        inputs = Inputs()
        inputs.feed(page)
        yield site, client, store, inputs.fields
    finally:
        await client.close()


async def test_confirmed_cancel_has_no_database_or_model_authority(tmp_path, monkeypatch):
    async with site_fixture(tmp_path) as (site, client, store, fields):
        run = rebuild(store, site.search_state_dir)
        before = (dict(store.live), run.live_profile.read_bytes(), store.staged_summary(run.tag))
        # Cancellation must stay available with staging/cutover gates still closed.
        monkeypatch.setattr("aikey.index_rebuild.APPROVED_IMAGE_SOURCE", None)
        monkeypatch.setattr("aikey.index_rebuild.APPROVED_NATIVE_READBACK", None)
        response = await client.post("/index-migration/cancel", data=fields,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303 and response.headers["Location"] == "/index-migration"
        assert (store.live, run.live_profile.read_bytes(), store.staged_summary(run.tag)) == before
        plan = migration_plan(site.search_state_dir)
        assert plan["generations"][0]["state"] == "cancelled"
        assert not plan["generations"][0]["cancel"]["available"]
        page = await (await client.get("/index-migration")).text()
        assert "cancelled" in page and "action='/index-migration/cancel'" not in page
        audit = [json.loads(line) for line in site.audit.path.read_text().splitlines()]
        assert any(x["action"] == "index_cancel" and x["result"] == "cancelled" for x in audit)
        assert all(set(x) <= {"at", "action", "result", "profile"} for x in audit)


@pytest.mark.parametrize("fault,status", [("csrf", 403), ("origin", 403), ("anonymous", 403),
                                          ("confirm", 400), ("extra", 400), ("duplicate", 400),
                                          ("generation", 409), ("revision", 409)])
async def test_refused_cancel_preserves_the_exact_generation(tmp_path, fault, status):
    async with site_fixture(tmp_path) as (site, client, store, fields):
        run = rebuild(store, site.search_state_dir)
        before = (run.journal_path.read_bytes(), run.live_profile.read_bytes(), dict(store.live))
        headers = {"Origin": site.origin}
        fields = dict(fields)
        if fault == "csrf":
            fields["csrf"] = "wrong"
        elif fault == "origin":
            headers["Origin"] = "https://untrusted.invalid"
        elif fault == "anonymous":
            client.session.cookie_jar.clear()
        elif fault == "confirm":
            del fields["confirm"]
        elif fault == "extra":
            fields["path"] = "outside-state"
        elif fault == "duplicate":
            fields = list(fields.items()) + [("generation", NEW[:12])]
        elif fault == "generation":
            fields["generation"] = "../outside"
        elif fault == "revision":
            fields["journal_revision"] = "0" * 64
        response = await client.post("/index-migration/cancel", data=fields, headers=headers,
                                     allow_redirects=False)
        assert response.status == status
        assert (run.journal_path.read_bytes(), run.live_profile.read_bytes(), store.live) == before


@pytest.mark.parametrize("changed", ["journal", "profile"])
async def test_a_rendered_cancel_cannot_overwrite_unseen_progress_or_profile(tmp_path, changed):
    async with site_fixture(tmp_path) as (site, client, store, fields):
        run = rebuild(store, site.search_state_dir)
        if changed == "journal":
            await run.stage(Embedder(NEW), source(), batch=1, max_batches=1)
        else:
            profile = json.loads(run.live_profile.read_text())
            profile["source"] = "http://127.0.0.1:8280"
            run.live_profile.write_text(json.dumps(profile))
        before = (run.journal_path.read_bytes(), run.live_profile.read_bytes(), dict(store.live))
        response = await client.post("/index-migration/cancel", data=fields,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 409
        assert (run.journal_path.read_bytes(), run.live_profile.read_bytes(), store.live) == before


async def test_cancel_refuses_busy_writer_without_blocking_http_or_stopping_it(tmp_path):
    async with site_fixture(tmp_path) as (site, client, store, fields):
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_source(object_id):
            entered.set()
            await release.wait()
            return object_id.encode()

        run = rebuild(store, site.search_state_dir)
        task = asyncio.create_task(run.stage(Embedder(NEW), held_source, batch=1, max_batches=1))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            response = await asyncio.wait_for(client.post("/index-migration/cancel", data=fields,
                headers={"Origin": site.origin}, allow_redirects=False), 2)
            assert response.status == 409 and not task.done()
            assert json.loads(run.journal_path.read_text())["state"] == "staging"
        finally:
            release.set()
            await task


async def test_post_commit_failure_is_uncertain_and_does_not_retry_or_restore(tmp_path, monkeypatch):
    async with site_fixture(tmp_path) as (site, client, store, fields):
        import aikey.index_rebuild as engine

        original, calls = engine._atomic_cancel_json, []

        def fail_after_commit(directory_fd, value):
            original(directory_fd, value)
            if value.get("state") == "cancelled":
                calls.append("committed")
                raise OSError("synthetic-fsync-failure")

        monkeypatch.setattr(engine, "_atomic_cancel_json", fail_after_commit)
        profile = (site.search_state_dir / "search-profile.json").read_bytes()
        before = dict(store.live)
        response = await client.post("/index-migration/cancel", data=fields,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 409 and "outcome uncertain" in (await response.text()).lower()
        assert calls == ["committed"] and store.live == before
        assert (site.search_state_dir / "search-profile.json").read_bytes() == profile
        assert migration_plan(site.search_state_dir)["generations"][0]["state"] == "cancelled"
