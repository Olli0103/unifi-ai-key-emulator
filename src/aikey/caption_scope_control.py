"""Remove-only saved automatic-caption camera controls; no live processing action."""

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time

from aiohttp import ClientError, web

from .admin_inventory import inventory_view
from .camera_inventory import InventoryError
from .config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from .control_site import _page, _safe

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ERRORS = (ValueError, KeyError, OSError, InventoryError, ConfigurationStoreError, ClientError, TimeoutError)


class CaptionScopeControl:
    def __init__(self, site):
        self.site, self.grants = site, {}
        self.key = secrets.token_bytes(32)
        self.clock = time.monotonic

    def register(self, app):
        app.router.add_get("/caption-cameras", self.page)
        app.router.add_get("/api/admin/v1/caption-cameras", self.json)
        app.router.add_post("/caption-cameras/preview", self.preview)
        app.router.add_post("/caption-cameras/apply", self.apply)

    @staticmethod
    def _hidden(fields):
        return "".join(f"<input type='hidden' name='{_safe(k)}' value='{_safe(v)}'>" for k, v in fields.items())

    @staticmethod
    def _fields(fields, required, repeated=()):
        return (required <= set(fields) <= required | set(repeated)
                and all(isinstance(v, str) and len(v) <= 128 for _, v in fields.items())
                and all(len(fields.getall(k)) == 1 for k in required))

    def _expire(self):
        for token, grant in list(self.grants.items()):
            if self.clock() >= grant["expires"]:
                del self.grants[token]

    async def _choices(self, cookie, status, *, fresh):
        cameras = status["camera_ids"]
        if (not cameras or self.site.inventory_loader is None
                or any(not re.fullmatch(r"[0-9a-f]{24}", c) for c in cameras)):
            raise ValueError
        if fresh:
            async with asyncio.timeout(12):
                report = await self.site.inventory_loader()
        else:
            report = await self.site.inventory_snapshot()
        rows = inventory_view(report, {})["cameras"]
        by_id = {r["id"]: r for r in rows}
        if len(by_id) != len(rows) or len({r["mac"] for r in rows}) != len(rows):
            raise ValueError
        selected = [by_id[c] for c in cameras]
        if (len({r["name"].casefold() for r in selected}) != len(selected)
                or any(sum(other["name"].casefold() == r["name"].casefold() for other in rows) != 1
                       for r in selected)
                or any(not r["name"] or r["id"].casefold() in r["name"].casefold()
                       or r["mac"].casefold() in re.sub(r"[:.\-]", "", r["name"]).casefold()
                       for r in selected)):
            raise ValueError
        return {hmac.new(self.key, json.dumps([cookie, status["revision"], r["id"],
                r["mac"], r["name"], r["model"]], separators=(",", ":")).encode(),
                hashlib.sha256).hexdigest(): r for r in selected}

    def _refused(self):
        self.site._audit("caption_scope", "rejected", "aikey")
        return _page("Caption cameras not saved", "<h1>Caption cameras not saved</h1>"
            "<p>This request changed no settings. Reload the current policy and fresh camera names, "
            "then review a new preview. Keep at least one camera, or explicitly disable the saved "
            "automatic-caption policy.</p><p><a href='/caption-cameras'>Caption cameras</a></p>", status=409)

    async def json(self, request):
        from .admin_api import error, reply
        if not self.site.admin_api.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        try:
            status = await asyncio.to_thread(self.site.aikey.caption_scope_status)
        except (OSError, ConfigurationStoreError):
            status = None
        if not self.site.admin_api.authenticated(request):
            return error("authentication_required", 401)
        if status is None:
            return error("caption_scope_unavailable", 503)
        cameras = status.pop("camera_ids")
        return reply({"caption_scope": {**status, "explicit_camera_count": len(cameras) if cameras is not None else None},
                      "read_only": True, "observation": "saved_policy_not_running_authority"})

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        if request.query:
            raise web.HTTPBadRequest()
        body = ("<h1>Saved automatic-caption cameras</h1><p><a href='/'>Settings</a> · "
            "<a href='/processing'>Running processing status</a> · <a href='/caption-policy'>Caption cost policy</a></p>"
            "<p><a href='/caption-camera-enrollment'>Add named cameras with separate consent</a></p>"
            "<p>Remove cameras from an existing explicit list, or disable the saved continuous-caption policy. "
            "No camera can be added here. A separate AI Key restart is required: running jobs and camera "
            "permissions in Protect remain unchanged. Speech, search/indexing, face recognition and AI Port "
            "detection retain their separate policies. Existing budget usage is never cleared or refunded.</p>")
        try:
            status = await asyncio.to_thread(self.site.aikey.caption_scope_status)
        except (OSError, ConfigurationStoreError):
            status = None
        if self.site._session(request) is None:
            raise web.HTTPSeeOther("/login")
        if status is None:
            return _page("Caption cameras", body + "<p>Saved policy is unavailable.</p>")
        if not status["configured"]:
            return _page("Caption cameras", body + "<p>No continuous-caption policy is configured. "
                         "One-use scopes are not converted or changed.</p>")
        try:
            choices = await self._choices(cookie, status, fresh=False)
        except _ERRORS:
            choices = None
        if self.site._session(request) is None:
            raise web.HTTPSeeOther("/login")
        hidden = self._hidden({"csrf": self.site.security.csrf_token(cookie), "revision": status["revision"]})
        if choices:
            body += "<form method='post' action='/caption-cameras/preview'>" + hidden
            body += "<input type='hidden' name='mode' value='restrict'><fieldset><legend>Keep these saved cameras</legend>"
            for token, row in choices.items():
                body += (f"<label><input type='checkbox' name='keep_camera' value='{token}' checked>"
                         f"{_safe(row['name'])}</label>")
            body += "</fieldset><button>Preview camera restriction</button></form>"
        else:
            body += ("<p>Individual editing needs an explicit saved camera list and fresh, unambiguous "
                     "inventory names. Model-wide policies are not silently converted to a camera list.</p>")
        body += ("<form method='post' action='/caption-cameras/preview'>" + hidden
            + "<input type='hidden' name='mode' value='disable_all'>"
            "<button>Preview disabling saved automatic captions</button></form>"
            "<p>Disabling removes the saved continuous policy, including its cost override, not history, "
            "credentials or model files. There is no automatic Undo or automatic restart here.</p>")
        return _page("Caption cameras", body)

    async def preview(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (request.query or not self._fields(fields, {"csrf", "revision", "mode"}, {"keep_camera"})
                    or not _HEX.fullmatch(fields["revision"]) or fields["mode"] not in {"restrict", "disable_all"}):
                raise ValueError
            status = await asyncio.to_thread(self.site.aikey.caption_scope_status)
            if status["revision"] != fields["revision"]:
                raise ValueError
            selected = fields.getall("keep_camera", [])
            if len(selected) > 16 or len(set(selected)) != len(selected):
                raise ValueError
            choices = await self._choices(cookie, status, fresh=True) if fields["mode"] == "restrict" else {}
            if (fields["mode"] == "restrict" and (not selected or any(t not in choices for t in selected))
                    or fields["mode"] == "disable_all" and selected):
                raise ValueError
            retained = [choices[t]["id"] for t in selected]
            result = await asyncio.to_thread(self.site.aikey.preview_caption_restriction, status["revision"], retained)
        except _ERRORS:
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        self._expire()
        if len(self.grants) >= 32:
            return self._refused()
        token = secrets.token_hex(32)
        self.grants[token] = {"session": hashlib.sha256(cookie.encode()).hexdigest(),
            "revision": status["revision"], "retained": retained, "tokens": selected,
            "mode": fields["mode"], "resulting_revision": result.resulting_revision, "expires": self.clock() + 600}
        names = "".join(f"<li>{_safe(choices[t]['name'])}</li>" for t in selected)
        body = ("<h1>Review caption cameras</h1><p>Nothing has been saved.</p>"
            + ("<p>Keep only these existing saved cameras:</p><ul>" + names + "</ul>" if retained else
               "<p>Disable the entire saved continuous-caption policy. This does not disable other features.</p>")
            + "<p>The running Key and pending jobs remain unchanged until a separate restart. "
            "No model, media, controller setting, scope enrollment or budget reset is requested.</p>"
            "<form method='post' action='/caption-cameras/apply'>" + self._hidden({
                "csrf": self.site.security.csrf_token(cookie), "grant": token})
            + "<label><input type='checkbox' name='confirm' value='yes' required>"
            "Save only this reviewed restriction; it does not stop current processing.</label>"
            "<button>Confirm saved camera restriction</button></form>"
            "<p><a href='/caption-cameras'>Cancel</a></p>")
        self.site._audit("caption_scope", "previewed", "aikey")
        return _page("Review caption cameras", body)

    async def apply(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (request.query or not self._fields(fields, {"csrf", "grant", "confirm"})
                    or not _HEX.fullmatch(fields["grant"]) or fields["confirm"] != "yes"):
                raise ValueError
            self._expire()
            grant = self.grants.get(fields["grant"])
            if grant is None or not hmac.compare_digest(grant["session"], hashlib.sha256(cookie.encode()).hexdigest()):
                raise ValueError
            if grant["mode"] == "restrict":
                status = await asyncio.to_thread(self.site.aikey.caption_scope_status)
                if status["revision"] != grant["revision"]:
                    raise ValueError
                choices = await self._choices(cookie, status, fresh=True)
                if [choices[t]["id"] for t in grant["tokens"]] != grant["retained"]:
                    raise ValueError
            result = await asyncio.to_thread(self.site.aikey.preview_caption_restriction,
                                            grant["revision"], grant["retained"])
            if (result.resulting_revision != grant["resulting_revision"] or self.clock() >= grant["expires"]
                    or self.grants.get(fields["grant"]) is not grant):
                raise ValueError
        except _ERRORS:
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        del self.grants[fields["grant"]]
        return await self.site.admin_api.file_operation(self._commit, grant)

    async def _commit(self, grant):
        try:
            result = await asyncio.to_thread(self.site.aikey.restrict_captions, grant["revision"], grant["retained"])
            if result.revision != grant["resulting_revision"]:
                raise ConfigurationStoreError("Caption restriction acknowledgement uncertain")
        except (RevisionConflict, UnsafeConfigurationChange):
            return self._refused()
        except (OSError, ConfigurationStoreError):
            self.site._audit("caption_scope", "uncertain", "aikey")
            return _page("Caption restriction outcome uncertain", "<h1>Caption restriction outcome uncertain</h1>"
                "<p>The save may have succeeded. Read current saved settings before trying again. "
                "No automatic retry or reversal occurred.</p><p><a href='/caption-cameras'>Read caption cameras</a></p>", status=503)
        self.site._audit("caption_scope", "saved", "aikey")
        return _page("Caption restriction saved", "<h1>Caption restriction saved</h1>"
            "<p>The running Key and queued jobs are unchanged. A separate restart is required. "
            "Existing budget usage and other features are unchanged.</p>"
            "<p><a href='/caption-cameras'>Read saved caption cameras</a></p>")
