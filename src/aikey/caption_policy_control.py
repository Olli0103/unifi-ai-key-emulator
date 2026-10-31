"""Explicit saved caption cost policy; no runtime, journal or provider operation."""

import asyncio
import hashlib
import hmac
import re
import secrets
import time

from aiohttp import web

from .config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from .control_site import _page, _safe

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MODES = {"metered": "Metered: 12 new caption attempts / rolling hour globally",
          "unmetered": "Explicit local unmetered captions: bypass the caption limit"}


class CaptionPolicyControl:
    def __init__(self, site):
        self.site = site
        self.grants = {}
        self.clock = time.monotonic

    def register(self, app):
        app.router.add_get("/caption-policy", self.page)
        app.router.add_get("/api/admin/v1/caption-policy", self.json)
        app.router.add_post("/caption-policy/preview", self.preview)
        app.router.add_post("/caption-policy/apply", self.apply)

    @staticmethod
    def _hidden(fields):
        return "".join(f"<input type='hidden' name='{_safe(k)}' value='{_safe(v)}'>"
                       for k, v in fields.items())

    @staticmethod
    def _fields(fields, required):
        return (set(fields) == required and all(isinstance(v, str) and len(fields.getall(k)) == 1
                for k, v in fields.items()))

    def _expire(self):
        now = self.clock()
        for token, grant in list(self.grants.items()):
            if now >= grant["expires"]:
                del self.grants[token]

    def _refused(self):
        self.site._audit("caption_policy", "rejected", "aikey")
        return _page("Caption policy not saved", "<h1>Caption policy not saved</h1>"
            "<p>Reload the current saved policy and review a fresh preview. This request changed no settings.</p>"
            "<p><a href='/caption-policy'>Caption cost policy</a></p>", status=409)

    async def json(self, request):
        from .admin_api import error, reply
        if not self.site.admin_api.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        try:
            status = await asyncio.to_thread(self.site.aikey.caption_policy_status)
        except (OSError, ConfigurationStoreError):
            status = None
        if not self.site.admin_api.authenticated(request):
            return error("authentication_required", 401)
        if status is None:
            return error("caption_policy_unavailable", 503)
        return reply({"caption_policy": status, "read_only": True,
                      "observation": "saved_policy_not_running_authority"})

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        if request.query:
            raise web.HTTPBadRequest()
        body = ("<h1>Caption cost policy</h1><p><a href='/'>Settings</a> · "
            "<a href='/processing'>Running processing status</a> · "
            "<a href='/caption-preflight'>Caption preflight</a></p>"
            "<p>These are saved settings for an existing continuous-caption camera policy. "
            "The running AI Key is unchanged until a separate processor restart. This form "
            "cannot create a camera policy, add cameras, change a provider or start processing.</p>")
        try:
            status = await asyncio.to_thread(self.site.aikey.caption_policy_status)
        except (OSError, ConfigurationStoreError):
            status = None
        if self.site._session(request) is None:
            raise web.HTTPSeeOther("/login")
        if status is None:
            return _page("Caption cost policy", body + "<p>Saved policy is unavailable. Reload settings.</p>")
        if not status["configured"]:
            return _page("Caption cost policy", body + "<p>No continuous camera policy is configured. "
                "No policy switch is available here; existing one-use scopes are not converted.</p>")
        body += (f"<p>Current saved policy: <strong>{_safe(_MODES[status['mode']])}</strong>.</p>"
            "<p>Metered captions share 12 new reservations per rolling hour across cameras. "
            "Failed or uncertain reserved attempts are not refunded. An explicit local unmetered "
            "override bypasses that limit; a local route is not a guarantee of free inference. "
            "Speech, Port detection and local base detection retain their separate policies. "
            "Changing this policy never clears or refunds existing budget usage.</p>"
            "<form method='post' action='/caption-policy/preview'>" + self._hidden({
                "csrf": self.site.security.csrf_token(cookie), "revision": status["revision"]}) +
            "<label for='caption-policy-mode'>Proposed saved policy</label>"
            "<select id='caption-policy-mode' name='mode' required>"
            "<option value='' selected disabled>Choose a policy</option>"
            f"<option value='metered'>{_safe(_MODES['metered'])}</option>")
        if status["unmetered_available"]:
            body += f"<option value='unmetered'>{_safe(_MODES['unmetered'])}</option>"
        body += "</select><button>Preview caption policy</button></form>"
        if not status["unmetered_available"]:
            body += "<p>The current caption provider/model configuration does not permit the local unmetered override.</p>"
        return _page("Caption cost policy", body)

    async def preview(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (request.query or not self._fields(fields, {"csrf", "revision", "mode"})
                    or not _HEX.fullmatch(fields["revision"])
                    or fields["mode"] not in _MODES):
                raise ValueError
            result = await asyncio.to_thread(self.site.aikey.preview_caption_policy,
                                            fields["revision"], fields["mode"])
        except (ValueError, OSError, ConfigurationStoreError):
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        self._expire()
        if len(self.grants) >= 32:
            return self._refused()
        token = secrets.token_hex(32)
        self.grants[token] = {"session": hashlib.sha256(cookie.encode()).hexdigest(),
            "revision": fields["revision"], "mode": fields["mode"],
            "resulting_revision": result.resulting_revision, "expires": self.clock() + 600}
        self.site._audit("caption_policy", "previewed", "aikey")
        body = ("<h1>Review caption policy</h1><p>Nothing has been saved.</p>"
            f"<p>Proposed saved policy: <strong>{_safe(_MODES[fields['mode']])}</strong>.</p>"
            "<p>Only the saved caption cost policy changes. Existing camera "
            "scope, providers, secrets, device identity, search indexes, speech and Port settings "
            "stay unchanged. Existing budget usage is not reset or refunded. This is not "
            "a reservation or billing estimate. A separate AI Key restart is required; this page "
            "does not restart it or send a model, media or controller request.</p>"
            "<form method='post' action='/caption-policy/apply'>" + self._hidden({
                "csrf": self.site.security.csrf_token(cookie), "grant": token}) +
            "<label><input type='checkbox' name='confirm' value='yes' required>"
            "Save this reviewed policy only; the running processor remains unchanged.</label>")
        if fields["mode"] == "unmetered":
            body += ("<label><input type='checkbox' name='acknowledge_bypass' value='yes' required>"
                "I explicitly accept bypassing the global caption limit. Local inference is not proven free.</label>")
        body += "<button>Confirm saved caption policy</button></form><p><a href='/caption-policy'>Cancel</a></p>"
        return _page("Review caption policy", body)

    async def apply(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (request.query or not (
                    self._fields(fields, {"csrf", "grant", "confirm"})
                    or self._fields(fields, {"csrf", "grant", "confirm", "acknowledge_bypass"}))
                    or not _HEX.fullmatch(fields["grant"])):
                raise ValueError
            self._expire()
            grant = self.grants.get(fields.get("grant"))
            if grant is None or not hmac.compare_digest(grant["session"], hashlib.sha256(cookie.encode()).hexdigest()):
                raise ValueError
            required = {"csrf", "grant", "confirm"}
            if grant["mode"] == "unmetered":
                required.add("acknowledge_bypass")
            if (request.query or not self._fields(fields, required) or fields["confirm"] != "yes"
                    or grant["mode"] == "unmetered" and fields["acknowledge_bypass"] != "yes"):
                raise ValueError
            result = await asyncio.to_thread(self.site.aikey.preview_caption_policy,
                                            grant["revision"], grant["mode"])
            if (result.resulting_revision != grant["resulting_revision"] or self.clock() >= grant["expires"]
                    or self.grants.get(fields["grant"]) is not grant):
                raise ValueError
        except (ValueError, OSError, ConfigurationStoreError):
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        del self.grants[fields["grant"]]
        return await self.site.admin_api.file_operation(self._commit, grant)

    async def _commit(self, grant):
        try:
            result = await asyncio.to_thread(self.site.aikey.apply_caption_policy,
                                            grant["revision"], grant["mode"])
            if result.revision != grant["resulting_revision"]:
                raise ConfigurationStoreError("Caption policy acknowledgement is uncertain")
        except (RevisionConflict, UnsafeConfigurationChange):
            return self._refused()
        except (OSError, ConfigurationStoreError):
            self.site._audit("caption_policy", "uncertain", "aikey")
            return _page("Caption policy outcome uncertain", "<h1>Caption policy outcome uncertain</h1>"
                "<p>Reload saved settings and check which policy is stored before trying again. "
                "An interrupted save may have succeeded. No automatic retry or reversal was attempted.</p>"
                "<p><a href='/caption-policy'>Read saved policy</a></p>", status=503)
        self.site._audit("caption_policy", "saved", "aikey")
        return _page("Caption policy saved", "<h1>Caption policy saved</h1>"
            "<p>The running AI Key is unchanged. A separate processor restart is required. "
            "Existing budget usage is unchanged. To change this choice again, review and "
            "confirm another policy.</p>"
            "<p><a href='/caption-policy'>Read saved policy</a> · <a href='/processing'>Processing status</a></p>")
