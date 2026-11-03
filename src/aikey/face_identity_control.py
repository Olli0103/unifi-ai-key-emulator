"""Private local AI Key identity metadata and explicit, revision-bound deletion.

No enrollment, model request, face image, vector export or native Protect group
management. The fixed state root belongs to the existing control-site profile.
"""

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time

from aiohttp import web

from .admin_api import error, reply
from .control_site import _page, _safe
from .faces import FaceStore, FaceStoreError, FaceStoreOutcomeUncertain, FaceStoreRevisionConflict

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SCOPE = ("This is the local AI Key SFace identity store, not AI Port 512-dimensional "
          "recognition or native Protect face groups. No photos or embedding vectors are shown. "
          "Enrollment still uses the existing local face enrollment tool.")
_DELETION = ("Deletion removes every saved local template for this one identity. It does not "
             "erase original footage, historical Protect events, native face groups, backups "
             "or enrollment-server copies. An in-flight match that already read the templates "
             "may still finish. No permission, model, processor or service is changed or restarted.")


class FaceIdentityControl:
    def __init__(self, api):
        self.api = api
        self.site = api.site
        self.store = FaceStore(self.site.aikey_state_dir)
        self.signing_key = secrets.token_bytes(32)
        self.grants = {}
        self.clock = time.monotonic
        self.read_task = None

    def register(self, app):
        app.router.add_get("/face-identities", self.page)
        app.router.add_get("/api/admin/v1/face-identities", self.metadata)
        app.router.add_post("/face-identities/preview", self.preview)
        app.router.add_post("/face-identities/apply", self.apply)

    async def _read(self):
        return await asyncio.to_thread(self.store.snapshot)

    async def _snapshot(self):
        # One retained read at a time, including after HTTP timeout/disconnect.
        # AdminAPI owns the underlying operation and drains it on app cleanup.
        if self.read_task is None or self.read_task.done():
            self.read_task = asyncio.create_task(self.api.file_operation(self._read))
            self.read_task.add_done_callback(self._observed)
        async with asyncio.timeout(3):
            return await asyncio.shield(self.read_task)

    @staticmethod
    def _observed(task):
        if not task.cancelled():
            task.exception()

    @staticmethod
    def _session_digest(cookie):
        return hashlib.sha256(cookie.encode()).hexdigest()

    def _choice(self, cookie, revision, name):
        value = json.dumps(["local-key-face-choice/1", cookie, revision, name],
                           separators=(",", ":")).encode()
        return hmac.new(self.signing_key, value, hashlib.sha256).hexdigest()

    def _expire(self):
        self.grants = {k: v for k, v in self.grants.items() if self.clock() < v["deadline"]}

    def _audit(self, result):
        if self.site.audit is None:
            return True
        try:
            return self.site.audit.record("face_identity_delete", result, profile="aikey")
        except (OSError, ValueError):
            return False

    @staticmethod
    def _hidden(fields):
        return "".join(f"<input type='hidden' name='{_safe(k)}' value='{_safe(v)}'>"
                       for k, v in fields.items())

    @staticmethod
    def _refused(*, unavailable=False):
        return _page("Identity action not completed", "<h1>Identity action not completed</h1>"
                     "<p>No deletion was admitted by this request. The saved record, session, "
                     "confirmation or private audit is unavailable or changed. Reload the list "
                     "and review a new preview.</p><p><a href='/face-identities'>Reload identities</a></p>",
                     status=503 if unavailable else 409)

    async def _fields(self, request, required):
        if request.query or request.content_type != "application/x-www-form-urlencoded":
            raise ValueError
        fields = await request.post()
        if (set(fields) != required or any(len(fields.getall(k)) != 1
                or not isinstance(fields[k], str) for k in fields)):
            raise ValueError
        return fields

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        if request.query:
            return self._refused()
        try:
            snapshot = await self._snapshot()
        except (FaceStoreError, OSError, TimeoutError):
            snapshot = None
        csrf = self.site.security.csrf_token(cookie)
        if csrf is None or self.site._session(request) != cookie:
            raise web.HTTPSeeOther("/login")
        body = ("<h1>Local face identities</h1><p><a href='/'>Settings</a></p>"
                f"<p>{_SCOPE}</p><p>{_DELETION}</p>")
        if snapshot is None:
            body += ("<p class='notice'>Identity store unavailable. No identity count, "
                     "live worker state or deletion is asserted.</p>")
        elif not snapshot["identities"]:
            body += ("<p>No local named templates found at the configured AI Key state root. "
                     "This does not establish whether a live worker is enabled.</p>")
        else:
            body += "<section aria-label='Saved local identities'>"
            for row in snapshot["identities"]:
                fields = {"csrf": csrf, "revision": snapshot["revision"],
                          "identity": self._choice(cookie, snapshot["revision"], row["name"])}
                body += (f"<section class='face-identity'><h2>{_safe(row['name'])}</h2>"
                         f"<p>Saved templates: {row['templates']}</p>"
                         "<form method='post' action='/face-identities/preview'>"
                         + self._hidden(fields) + "<button>Review deletion</button></form></section>")
            body += "</section>"
        return _page("Local face identities", body, status=503 if snapshot is None else 200)

    async def metadata(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        try:
            snapshot = await self._snapshot()
        except (FaceStoreError, OSError, TimeoutError):
            return error("face_identity_store_unavailable", 503)
        if self.site._session(request) != cookie:
            return error("authentication_required", 401)
        return reply({"scope": "local_aikey_sface128", "revision": snapshot["revision"],
                      "identities": [{"name": r["name"], "templates": r["templates"]}
                                     for r in snapshot["identities"]], "live_worker_state": "not_asserted"})

    async def preview(self, request):
        try:
            fields = await self._fields(request, {"csrf", "revision", "identity"})
        except ValueError:
            return self._refused()
        cookie = self.site._session(request, mutate=True, csrf=fields["csrf"])
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if not _HEX.fullmatch(fields["revision"]) or not _HEX.fullmatch(fields["identity"]):
                raise ValueError
            snapshot = await self._snapshot()
            if snapshot["revision"] != fields["revision"]:
                raise ValueError
            row = next((r for r in snapshot["identities"] if hmac.compare_digest(
                self._choice(cookie, snapshot["revision"], r["name"]), fields["identity"])), None)
            if row is None:
                raise ValueError
        except (ValueError, OSError, TimeoutError):
            return self._refused()
        if self.site._session(request) != cookie:
            raise web.HTTPForbidden()
        self._expire()
        if len(self.grants) >= 32 or not self._audit("previewed"):
            return self._refused(unavailable=True)
        if self.site._session(request) != cookie:
            raise web.HTTPForbidden()
        token = secrets.token_hex(32)
        self.grants[token] = {"session": self._session_digest(cookie), "name": row["name"],
                              "revision": snapshot["revision"], "templates": row["templates"],
                              "deadline": self.clock() + 600}
        return _page("Confirm local identity deletion", "<h1>Confirm local identity deletion</h1>"
                     f"<p class='face-confirmation'>Identity: <strong>{_safe(row['name'])}</strong>. "
                     f"Saved templates to remove: {row['templates']}.</p><p>{_DELETION}</p>"
                     "<p>This one-use confirmation expires after ten minutes. "
                     "No automatic undo copy is created.</p>"
                     "<form method='post' action='/face-identities/apply'>"
                     + self._hidden({"csrf": fields["csrf"], "grant": token})
                     + "<label><input type='checkbox' name='confirm' value='yes' required>"
                     "Delete all local templates for this identity.</label>"
                     "<button>Delete local identity</button></form>"
                     "<p><a href='/face-identities'>Cancel and return</a></p>")

    async def apply(self, request):
        try:
            fields = await self._fields(request, {"csrf", "grant", "confirm"})
        except ValueError:
            return self._refused()
        cookie = self.site._session(request, mutate=True, csrf=fields["csrf"])
        if cookie is None:
            raise web.HTTPForbidden()
        self._expire()
        token = fields["grant"]
        grant = self.grants.get(token)
        if (fields["confirm"] != "yes" or not _HEX.fullmatch(token) or grant is None
                or not hmac.compare_digest(grant["session"], self._session_digest(cookie))):
            return self._refused()
        try:
            snapshot = await self._snapshot()
        except (FaceStoreError, OSError, TimeoutError):
            return self._refused(unavailable=True)
        if self.site._session(request) != cookie:
            raise web.HTTPForbidden()
        if (self.clock() >= grant["deadline"] or self.grants.get(token) is not grant
                or snapshot["revision"] != grant["revision"]
                or not any(r["name"] == grant["name"] and r["templates"] == grant["templates"]
                           for r in snapshot["identities"])):
            return self._refused()
        if not self._audit("admitted"):
            return self._refused(unavailable=True)
        if self.site._session(request) != cookie:
            raise web.HTTPForbidden()
        if self.clock() >= grant["deadline"] or self.grants.get(token) is not grant:
            return self._refused()
        # No await between final authority checks, all sibling consumption and
        # admission. An exact-byte restoration must not revive old previews.
        self.grants.clear()
        return await self.api.file_operation(self._commit, grant)

    async def _commit(self, grant):
        try:
            removed = await asyncio.to_thread(self.store.delete, grant["name"],
                                             expected_revision=grant["revision"])
        except FaceStoreRevisionConflict:
            self._audit("conflict")
            return self._refused()
        except (FaceStoreOutcomeUncertain, OSError):
            return self._uncertain()
        except FaceStoreError:
            self._audit("refused")
            return self._refused()
        if not removed:
            return self._uncertain()
        if not self._audit("saved"):
            return self._uncertain()
        return web.Response(status=303, headers={"Location": "/face-identities", "Cache-Control": "no-store"})

    def _uncertain(self):
        self._audit("uncertain")
        return _page("Deletion outcome uncertain", "<h1>Deletion outcome uncertain</h1>"
                     "<p>The deletion may already have been stored. Do not automatically retry "
                     "or restore old data. Reload the identity list and reconcile the saved "
                     "record before deciding on a new action.</p>"
                     "<p><a href='/face-identities'>Reload identities</a></p>", status=503)
