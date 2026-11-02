"""Separate renewed audio consent for additions to an existing saved speech role."""

import asyncio
import hashlib
import hmac
import re
import secrets

from aiohttp import ClientError, web

from .admin_inventory import inventory_view
from .camera_inventory import InventoryError
from .config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from .control_site import _page, _safe
from .speech_enrollment import SpeechEnrollment

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ERRORS = (ValueError, KeyError, OSError, InventoryError, ConfigurationStoreError, ClientError, TimeoutError)
_BASE = {"csrf", "revision"}
_CONFIRM = {"csrf", "grant", "confirm", "acknowledge_audio", "acknowledge_cost"}


class SpeechCameraEnrollment(SpeechEnrollment):
    """Reuse session-bound camera token, grant expiry and escaped hidden fields only."""

    def register(self, app):
        app.router.add_get("/speech-camera-enrollment", self.page)
        app.router.add_get("/api/admin/v1/speech-camera-enrollment", self.json)
        app.router.add_post("/speech-camera-enrollment/preview", self.preview)
        app.router.add_post("/speech-camera-enrollment/apply", self.apply)

    @staticmethod
    def _fields(fields, required, repeated=()):
        return (required <= set(fields) <= required | set(repeated)
                and all(isinstance(v, str) and len(v) <= 128 for _, v in fields.items())
                and all(len(fields.getall(k)) == 1 for k in required))

    @staticmethod
    def _eligible(status):
        return (status["configured"] and 0 < len(status["camera_ids"]) < 32
                and all(re.fullmatch(r"[0-9a-f]{24}", c) for c in status["camera_ids"]))

    async def _choices(self, cookie, status, *, cached=False):
        if not self._eligible(status) or self.site.inventory_loader is None:
            raise ValueError
        async with asyncio.timeout(12):
            report = (await self.site.inventory_snapshot() if cached else await self.site.inventory_loader())
        view = inventory_view(report, {})
        if view["protect_version"] != status["protect_version"]:
            raise ValueError
        rows = view["cameras"]
        if len({r["id"] for r in rows}) != len(rows) or len({r["mac"] for r in rows}) != len(rows):
            raise ValueError
        choices = [r for r in rows if r["id"] not in status["camera_ids"]
                   and re.fullmatch(r"[0-9a-f]{24}", r["id"])
                   and r["state"] == "CONNECTED" and "alrmSpeak" in r["audio_detect_types"]]
        choices = [r for r in choices if r["name"]
                   and sum(v["name"].casefold() == r["name"].casefold() for v in rows) == 1
                   and r["id"].casefold() not in r["name"].casefold()
                   and r["mac"].casefold() not in re.sub(r"[:.\-]", "", r["name"]).casefold()]
        return {self._token(cookie, status["revision"], r): r for r in choices}

    def _refused(self):
        self.site._audit("speech_camera_add", "rejected", "aikey")
        return _page("Speech cameras not added", "<h1>Speech cameras not added</h1>"
            "<p>This request changed no settings. Reload fresh named cameras and review a new preview. "
            "An existing explicit speech role with room for additions is required.</p>"
            "<p><a href='/speech-camera-enrollment'>Add speech cameras</a></p>", status=409)

    async def json(self, request):
        from .admin_api import error, reply
        if not self.site.admin_api.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        try:
            status = await asyncio.to_thread(self.site.aikey.speech_camera_enrollment_status)
        except _ERRORS:
            status = None
        if not self.site.admin_api.authenticated(request):
            return error("authentication_required", 401)
        if status is None:
            return error("speech_camera_enrollment_unavailable", 503)
        return reply({"revision": status["revision"], "configured": status["configured"],
                      "saved_camera_count": len(status["camera_ids"]),
                      "has_room": bool(self._eligible(status)), "saved_only": True})

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        if request.query:
            raise web.HTTPBadRequest()
        body = ("<h1>Add speech cameras</h1><p><a href='/'>Settings</a> · "
            "<a href='/speech-permissions'>Remove saved audio permissions</a></p>"
            "<p>Add named microphones to an existing explicit speech-to-text camera list. "
            "Nothing is selected by default. The existing provider, credential, model, language "
            "and audio-export limits are preserved. This page does not enable microphones or change "
            "Protect permissions, fetch recordings, test a model or restart a service. "
            "A separate AI Key restart is required; saved consent is not running authority.</p>")
        try:
            status = await asyncio.to_thread(self.site.aikey.speech_camera_enrollment_status)
            choices = await self._choices(cookie, status, cached=True)
        except _ERRORS:
            choices = {}
        if self.site._session(request) is None:
            raise web.HTTPSeeOther("/login")
        if not choices:
            return _page("Add speech cameras", body + "<p>No additions available: this needs an "
                "existing explicit speech list with room below 32 cameras and fresh connected "
                "cameras with unambiguous names and reported speech-event support. "
                "<a href='/speech-enrollment'>Initial speech setup</a> remains separate.</p>")
        body += (f"<p>Keep all {len(status['camera_ids'])} existing saved cameras. "
            "Reported support is not native delivery or speech-quality proof.</p>"
            "<form method='post' action='/speech-camera-enrollment/preview'>" + self._hidden(
                {"csrf": self.site.security.csrf_token(cookie), "revision": status["revision"]}) +
            "<fieldset><legend>Additional speech cameras</legend>")
        for token, row in choices.items():
            body += (f"<label><input type='checkbox' name='add_camera' value='{token}'>"
                     f"{_safe(row['name'])} ({_safe(row['model'])})</label>")
        body += "</fieldset><button>Preview speech additions</button></form>"
        return _page("Add speech cameras", body)

    async def preview(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (request.query or not self._fields(fields, _BASE, {"add_camera"})
                    or not _HEX.fullmatch(fields["revision"])):
                raise ValueError
            selected = fields.getall("add_camera", [])
            if not 1 <= len(selected) <= 32 or len(set(selected)) != len(selected):
                raise ValueError
            status = await asyncio.to_thread(self.site.aikey.speech_camera_enrollment_status)
            if self.site._session(request) is None:
                raise web.HTTPForbidden()
            if status["revision"] != fields["revision"]:
                raise ValueError
            choices = await self._choices(cookie, status)
            if self.site._session(request) is None:
                raise web.HTTPForbidden()
            additions = sorted(choices[t]["id"] for t in selected)
            result = await asyncio.to_thread(self.site.aikey.preview_speech_camera_additions,
                                            status["revision"], additions)
        except _ERRORS:
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        self._expire()
        if len(self.grants) >= 32:
            return self._refused()
        token = secrets.token_hex(32)
        self.grants[token] = {"session": hashlib.sha256(cookie.encode()).hexdigest(),
            "revision": status["revision"], "additions": additions, "tokens": selected,
            "resulting_revision": result.resulting_revision, "expires": self.clock() + 600}
        names = "".join(f"<li>{_safe(choices[t]['name'])}</li>" for t in selected)
        self.site._audit("speech_camera_add", "previewed", "aikey")
        return _page("Review speech additions", "<h1>Review speech additions</h1>"
            f"<p>Nothing saved. Keep all {len(status['camera_ids'])} existing cameras and add only:</p>"
            f"<ul>{names}</ul><p>Audio destination: {_safe(status['base_url'])}. "
            f"Provider: {_safe(status['provider'])}. Model: {_safe(status['model'])}.</p>"
            "<p>These microphones gain saved speech-to-text permission for this existing provider. "
            "OpenAI sends audio to OpenAI; a local API is not a privacy or billing guarantee. "
            "Other features, credentials, audio-export limits and current processing stay unchanged. "
            "A separate Key restart is required. This one-use preview expires in ten minutes.</p>"
            "<form method='post' action='/speech-camera-enrollment/apply'>" + self._hidden(
                {"csrf": self.site.security.csrf_token(cookie), "grant": token}) +
            "<label><input type='checkbox' name='acknowledge_audio' value='yes' required>"
            "I authorize audio from these additional cameras to the displayed speech provider.</label>"
            "<label><input type='checkbox' name='acknowledge_cost' value='yes' required>"
            "I accept possible speech charges independently of the caption budget.</label>"
            "<label><input type='checkbox' name='confirm' value='yes' required>"
            "Save only these reviewed additions; running processing remains unchanged.</label>"
            "<button>Confirm speech additions</button></form>"
            "<p><a href='/speech-camera-enrollment'>Cancel</a></p>")

    async def apply(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (request.query or not self._fields(fields, _CONFIRM)
                    or not _HEX.fullmatch(fields["grant"])
                    or any(fields[k] != "yes" for k in ("confirm", "acknowledge_audio", "acknowledge_cost"))):
                raise ValueError
            self._expire()
            grant = self.grants.get(fields["grant"])
            if grant is None or not hmac.compare_digest(grant["session"], hashlib.sha256(cookie.encode()).hexdigest()):
                raise ValueError
            status = await asyncio.to_thread(self.site.aikey.speech_camera_enrollment_status)
            if self.site._session(request) is None:
                raise web.HTTPForbidden()
            if status["revision"] != grant["revision"]:
                raise ValueError
            choices = await self._choices(cookie, status)
            if self.site._session(request) is None:
                raise web.HTTPForbidden()
            if sorted(choices[t]["id"] for t in grant["tokens"]) != grant["additions"]:
                raise ValueError
            result = await asyncio.to_thread(self.site.aikey.preview_speech_camera_additions,
                                            grant["revision"], grant["additions"])
            if (result.resulting_revision != grant["resulting_revision"]
                    or self.clock() >= grant["expires"] or self.grants.get(fields["grant"]) is not grant):
                raise ValueError
        except _ERRORS:
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        del self.grants[fields["grant"]]
        return await self.site.admin_api.file_operation(self._commit, grant)

    async def _commit(self, grant):
        try:
            result = await asyncio.to_thread(self.site.aikey.add_speech_cameras,
                                            grant["revision"], grant["additions"])
            if result.revision != grant["resulting_revision"]:
                raise ConfigurationStoreError("Speech camera acknowledgement uncertain")
        except (RevisionConflict, UnsafeConfigurationChange):
            return self._refused()
        except (OSError, ConfigurationStoreError):
            self.site._audit("speech_camera_add", "uncertain", "aikey")
            return _page("Speech camera outcome uncertain", "<h1>Speech camera outcome uncertain</h1>"
                "<p>The save may have succeeded. Reload saved permissions before trying again. "
                "No automatic retry, rollback, key cleanup or live activation occurred.</p>"
                "<p><a href='/speech-permissions'>Read saved permissions</a></p>", status=503)
        self.site._audit("speech_camera_add", "saved", "aikey")
        raise web.HTTPSeeOther("/speech-permissions")
