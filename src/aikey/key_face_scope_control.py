"""Remove-only saved AI Key face camera scope; no template/model/runtime action."""

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time

from aiohttp import ClientError, web

from .admin_api import error, reply
from .admin_inventory import inventory_view
from .camera_inventory import InventoryError
from .config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from .control_site import _page, _safe

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ERRORS = (ValueError, KeyError, OSError, ConfigurationStoreError, InventoryError, ClientError, TimeoutError)
_LIMITS = ("Saved policy only: a separate AI Key restart is required. Running jobs, "
           "in-flight matches and Protect camera permissions remain unchanged. This page cannot "
           "add cameras or enable face recognition. AI Port recognition, speech, captions, search "
           "and image enhancement keep their separate policies.")
_RETENTION = ("This does not delete identity templates, footage, events, native groups, model files "
              "or server/backup copies. Private configuration history retains earlier policies. "
              "No automatic Undo, restart, model request or media fetch occurs here.")


class KeyFaceScopeControl:
    def __init__(self, api):
        self.api, self.site = api, api.site
        self.key = secrets.token_bytes(32)
        self.clock = time.monotonic
        self.grants = {}
        self.read_task = None

    def register(self, app):
        app.router.add_get("/key-face-cameras", self.page)
        app.router.add_get("/api/admin/v1/key-face-cameras", self.metadata)
        app.router.add_post("/key-face-cameras/preview", self.preview)
        app.router.add_post("/key-face-cameras/apply", self.apply)

    async def _read(self):
        return await asyncio.to_thread(self.site.aikey.face_scope_status)

    async def _status(self):
        if self.read_task is None or self.read_task.done():
            self.read_task = asyncio.create_task(self.api.file_operation(self._read))
            self.read_task.add_done_callback(self._observe)
        async with asyncio.timeout(3):
            return await asyncio.shield(self.read_task)

    @staticmethod
    def _observe(task):
        if not task.cancelled():
            task.exception()

    @staticmethod
    def _session_hash(cookie):
        return hashlib.sha256(cookie.encode()).hexdigest()

    def _expire(self):
        self.grants = {key: value for key, value in self.grants.items()
                       if self.clock() < value["deadline"]}

    def _audit(self, result):
        if self.site.audit is None:
            return True
        try:
            return self.site.audit.record("key_face_scope", result, profile="aikey")
        except (OSError, ValueError):
            return False

    @staticmethod
    def _hidden(fields):
        return "".join(f"<input type='hidden' name='{_safe(key)}' value='{_safe(value)}'>"
                       for key, value in fields.items())

    async def _fields(self, request, required, *, repeated=()):
        if request.query or request.content_type != "application/x-www-form-urlencoded":
            raise ValueError
        fields = await request.post()
        if (not required <= set(fields) <= required | set(repeated)
                or any(not isinstance(v, str) or len(v) > 128 for _, v in fields.items())
                or any(len(fields.getall(key)) != 1 for key in required)):
            raise ValueError
        return fields

    async def _choices(self, cookie, status, *, fresh):
        cameras = status["camera_ids"]
        if (not cameras or self.site.inventory_loader is None
                or any(not re.fullmatch(r"[0-9a-f]{24}", camera) for camera in cameras)):
            raise ValueError
        async with asyncio.timeout(12):
            report = await self.site.inventory_loader() if fresh else await self.site.inventory_snapshot()
        rows = inventory_view(report, {})["cameras"]
        by_id = {row["id"]: row for row in rows}
        if len(by_id) != len(rows) or len({row["mac"] for row in rows}) != len(rows):
            raise ValueError
        selected = [by_id[camera] for camera in cameras]
        def label(row):
            return row["name"].strip() if isinstance(row["name"], str) else ""
        if any(not label(row) or row["id"].casefold() in label(row).casefold()
               or row["mac"].casefold() in re.sub(r"[:.\-\s]", "", label(row)).casefold()
               or sum(label(other).casefold() == label(row).casefold()
                      for other in rows) != 1 for row in selected):
            raise ValueError
        return {hmac.new(self.key, json.dumps(["key-face-camera-choice/1", cookie,
                status["revision"], row["id"], row["mac"], row["name"], row["model"]],
                separators=(",", ":")).encode(), hashlib.sha256).hexdigest(): row for row in selected}

    @staticmethod
    def _refused(*, unavailable=False):
        return _page("Key face scope not saved", "<h1>Key face scope not saved</h1>"
                     "<p>No Key configuration change was saved by this request. Reload the saved policy, "
                     "session and fresh camera names, then review a new preview. Keep at least "
                     "one existing camera or explicitly disable the saved policy.</p>"
                     "<p><a href='/key-face-cameras'>Reload saved face cameras</a></p>",
                     status=503 if unavailable else 409)

    async def metadata(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        try:
            status = await self._status()
        except _ERRORS:
            return error("key_face_scope_unavailable", 503)
        if self.site._session(request) != cookie:
            return error("authentication_required", 401)
        return reply({"face_scope": {"configured": status["configured"], "revision": status["revision"],
                      "explicit_camera_count": len(status["camera_ids"] or ())}, "read_only": True,
                      "observation": "saved_policy_not_running_authority", "scope": "aikey_face_recognition"})

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        if request.query:
            return self._refused()
        body = ("<h1>Saved AI Key face cameras</h1><p><a href='/'>Settings</a> · "
                "<a href='/face-identities'>Local face identities</a> · "
                "<a href='/processing'>Running processing status</a></p>"
                f"<p>{_LIMITS}</p><p>{_RETENTION}</p>")
        try:
            status = await self._status()
        except _ERRORS:
            status = None
        csrf = self.site.security.csrf_token(cookie)
        if csrf is None or self.site._session(request) != cookie:
            raise web.HTTPSeeOther("/login")
        if status is None:
            return _page("Key face cameras", body + "<p>Saved policy unavailable; scope unknown.</p>", status=503)
        if not status["configured"]:
            return _page("Key face cameras", body + "<p>No AI Key face recognition policy is saved. "
                         "This does not establish the live worker's state.</p>")
        try:
            choices = await self._choices(cookie, status, fresh=False)
        except _ERRORS:
            choices = None
        if self.site._session(request) != cookie:
            raise web.HTTPSeeOther("/login")
        hidden = self._hidden({"csrf": csrf, "revision": status["revision"]})
        body += f"<p>Saved camera count: {len(status['camera_ids'])}.</p>"
        if choices:
            body += ("<form method='post' action='/key-face-cameras/preview'>" + hidden
                     + "<input type='hidden' name='mode' value='restrict'><fieldset>"
                     "<legend>Keep these existing saved cameras</legend>")
            for token, row in choices.items():
                body += (f"<label><input type='checkbox' name='keep_camera' value='{token}' checked>"
                         f"{_safe(row['name'])}</label>")
            body += "</fieldset><button>Review camera restriction</button></form>"
        else:
            body += "<p>Individual editing needs fresh, unambiguous names for every saved camera.</p>"
        body += ("<form method='post' action='/key-face-cameras/preview'>" + hidden
                 + "<input type='hidden' name='mode' value='disable_all'>"
                 "<button>Review disabling saved Key recognition</button></form>")
        return _page("Key face cameras", body)

    async def preview(self, request):
        try:
            fields = await self._fields(request, {"csrf", "revision", "mode"}, repeated={"keep_camera"})
        except ValueError:
            return self._refused()
        cookie = self.site._session(request, mutate=True, csrf=fields["csrf"])
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if not _HEX.fullmatch(fields["revision"]) or fields["mode"] not in {"restrict", "disable_all"}:
                raise ValueError
            status = await self._status()
            if status["revision"] != fields["revision"] or not status["configured"]:
                raise ValueError
            selected = fields.getall("keep_camera", [])
            if len(selected) > 8 or len(selected) != len(set(selected)):
                raise ValueError
            choices = await self._choices(cookie, status, fresh=True) if fields["mode"] == "restrict" else {}
            if ((fields["mode"] == "restrict" and (not selected or any(token not in choices for token in selected)))
                    or (fields["mode"] == "disable_all" and selected)):
                raise ValueError
            retained = [choices[token]["id"] for token in selected]
            preview = await asyncio.to_thread(self.site.aikey.preview_face_restriction, fields["revision"], retained)
        except _ERRORS:
            return self._refused()
        if self.site._session(request) != cookie:
            raise web.HTTPForbidden()
        self._expire()
        if len(self.grants) >= 32 or not self._audit("previewed"):
            return self._refused(unavailable=True)
        if self.site._session(request) != cookie:
            raise web.HTTPForbidden()
        token = secrets.token_hex(32)
        self.grants[token] = {"session": self._session_hash(cookie), "revision": fields["revision"],
            "retained": retained, "tokens": selected, "mode": fields["mode"],
            "resulting_revision": preview.resulting_revision, "deadline": self.clock() + 600}
        detail = ("<p>Keep only these saved cameras:</p><ul>"
                  + "".join(f"<li>{_safe(choices[t]['name'])}</li>" for t in selected) + "</ul>"
                  if retained else "<p>Remove the entire saved AI Key face recognition policy.</p>")
        return _page("Review Key face cameras", "<h1>Review Key face cameras</h1>"
                     "<p>Nothing has been saved. This one-use confirmation expires in ten minutes.</p>"
                     + detail + f"<p>{_LIMITS}</p><p>{_RETENTION}</p>"
                     "<form method='post' action='/key-face-cameras/apply'>"
                     + self._hidden({"csrf": fields["csrf"], "grant": token})
                     + "<label><input type='checkbox' name='confirm' value='yes' required>"
                     "Save only this reviewed restriction; running processing does not stop.</label>"
                     "<button>Save Key face restriction</button></form>"
                     "<p><a href='/key-face-cameras'>Cancel</a></p>")

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
                or not hmac.compare_digest(grant["session"], self._session_hash(cookie))):
            return self._refused()
        try:
            if grant["mode"] == "restrict":
                status = await self._status()
                if status["revision"] != grant["revision"]:
                    raise ValueError
                choices = await self._choices(cookie, status, fresh=True)
                if [choices[t]["id"] for t in grant["tokens"]] != grant["retained"]:
                    raise ValueError
            result = await asyncio.to_thread(self.site.aikey.preview_face_restriction,
                                            grant["revision"], grant["retained"])
        except _ERRORS:
            return self._refused()
        if self.site._session(request) != cookie:
            raise web.HTTPForbidden()
        if (result.resulting_revision != grant["resulting_revision"] or self.clock() >= grant["deadline"]
                or self.grants.get(token) is not grant):
            return self._refused()
        if not self._audit("admitted"):
            return self._refused(unavailable=True)
        if self.site._session(request) != cookie:
            raise web.HTTPForbidden()
        if self.clock() >= grant["deadline"] or self.grants.get(token) is not grant:
            return self._refused()
        self.grants.clear()  # No await before ownership/admission; consume siblings even for no-op.
        return await self.api.file_operation(self._commit, grant)

    async def _commit(self, grant):
        try:
            result = await asyncio.to_thread(self.site.aikey.restrict_faces, grant["revision"], grant["retained"])
        except (RevisionConflict, UnsafeConfigurationChange):
            self._audit("conflict")
            return self._refused()
        except (OSError, ConfigurationStoreError):
            return self._uncertain()
        if result.revision != grant["resulting_revision"] or not self._audit("saved"):
            return self._uncertain()
        return _page("Key face restriction saved", "<h1>Key face restriction saved</h1>"
                     f"<p>{_LIMITS}</p><p>{_RETENTION}</p>"
                     "<p><a href='/key-face-cameras'>Read saved face cameras</a></p>")

    def _uncertain(self):
        self._audit("uncertain")
        return _page("Key face restriction outcome uncertain", "<h1>Key face restriction outcome uncertain</h1>"
                     "<p>The save may already have succeeded. Reload and reconcile saved settings "
                     "before deciding on a new action. No automatic retry or reversal occurred.</p>"
                     "<p><a href='/key-face-cameras'>Read saved face cameras</a></p>", status=503)
