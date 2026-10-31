"""Separate initial automatic-caption consent; saved policy, never activation."""

import asyncio
import hashlib
import hmac
import json
import re
import secrets

from aiohttp import web

from .admin_inventory import inventory_view
from .caption_scope_control import CaptionScopeControl, _ERRORS, _HEX
from .config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from .control_site import _page, _safe

_NOTICE = (
    "<p><strong>Saved settings only.</strong> Nothing is selected by default. A separate AI Key "
    "restart and eligible native events are required. This page never starts a service, enables "
    "camera recording or microphones, changes Protect permissions, fetches media or tests a model.</p>"
    "<p>All selected cameras share the existing global <strong>12 caption reservations per rolling "
    "hour</strong>. This is not twelve per camera, a price cap or a total-provider bill cap. "
    "Failed or uncertain admitted work may consume reservations. Existing usage is never reset or refunded. "
    "There is no unmetered override in this setup. Other features retain separate permissions and costs.</p>"
)


class CaptionSetupControl(CaptionScopeControl):
    def register(self, app):
        app.router.add_get("/caption-setup", self.page)
        app.router.add_post("/caption-setup/preview", self.preview)
        app.router.add_post("/caption-setup/apply", self.apply)

    async def _status(self):
        binding = self.site.caption_setup_binding
        status = await asyncio.to_thread(self.site.aikey.caption_setup_status, binding)
        if not status["available"] or binding is not self.site.caption_setup_binding:
            raise ValueError
        return status, binding

    async def _choices(self, cookie, status, *, fresh):
        if self.site.inventory_loader is None:
            raise ValueError
        loader = self.site.inventory_loader
        async with asyncio.timeout(12):
            report = await loader() if fresh else await self.site.inventory_snapshot()
        if loader is not self.site.inventory_loader:
            raise ValueError
        view = inventory_view(report, {})
        if view["protect_version"] != status["protect_version"]:
            raise ValueError
        rows = view["cameras"]
        if len({r["id"] for r in rows}) != len(rows) or len({r["mac"] for r in rows}) != len(rows):
            raise ValueError
        selected = [
            r
            for r in rows
            if r["state"] == "CONNECTED"
            and r["processing_class"] == "smart_event_candidate"
            and r["name"]
            and sum(v["name"].casefold() == r["name"].casefold() for v in rows) == 1
            and r["id"].casefold() not in r["name"].casefold()
            and r["mac"].casefold() not in re.sub(r"[:.\-]", "", r["name"]).casefold()
        ]
        return {
            hmac.new(
                self.key,
                json.dumps(
                    [
                        "caption-setup/1",
                        cookie,
                        status["revision"],
                        r["id"],
                        r["mac"],
                        r["name"],
                        r["model"],
                    ],
                    separators=(",", ":"),
                ).encode(),
                hashlib.sha256,
            ).hexdigest(): r
            for r in selected
        }

    def _refused(self):
        self.site._audit("caption_setup", "rejected", "aikey")
        return _page(
            "Caption setup not saved",
            "<h1>Caption setup not saved</h1>"
            "<p>This request changed no settings. Reload initial setup and review fresh named cameras. "
            "Existing policies must use their separate controls.</p>"
            "<p><a href='/caption-setup'>Caption setup</a></p>",
            status=409,
        )

    @staticmethod
    def _route(status):
        return (
            f"<p>Existing caption provider: {_safe(status['provider'])}; "
            f"model: {_safe(status['model'])}; destination: {_safe(status['destination'])}. "
            "Selected camera images/video may be sent to this configured service after separate "
            "activation. A local endpoint does not prove free inference or local-only upstream routing. "
            "<a href='/'>Review provider settings</a> before accepting.</p>"
        )

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        if request.query:
            raise web.HTTPBadRequest()
        body = "<h1>Set up automatic captions</h1><p><a href='/'>Settings</a></p>" + _NOTICE
        try:
            status, _ = await self._status()
            if self.site._session(request) is None:
                raise web.HTTPSeeOther("/login")
            choices = await self._choices(cookie, status, fresh=False)
        except _ERRORS:
            choices = {}
        if self.site._session(request) is None:
            raise web.HTTPSeeOther("/login")
        if not choices:
            return _page(
                "Automatic caption setup",
                body
                + "<p>Initial setup is unavailable. It requires no existing continuous or one-use policy, "
                "already configured callbacks/MP4 exports, a supported saved Protect version, host-enabled "
                "same-controller pinned inventory references and fresh unambiguous connected smart-event "
                "cameras. No existing policy or prerequisite is converted automatically. "
                "Metadata eligibility is not native event-delivery or quality evidence.</p>"
                "<p><a href='/caption-cameras'>Existing caption cameras</a></p>",
            )
        body += (
            self._route(status)
            + "<form method='post' action='/caption-setup/preview'>"
            + self._hidden(
                {"csrf": self.site.security.csrf_token(cookie), "revision": status["revision"]}
            )
        )
        body += "<fieldset><legend>Explicit initial caption cameras</legend>"
        for token, row in choices.items():
            body += (
                f"<label><input type='checkbox' name='camera' value='{token}'>"
                f"{_safe(row['name'])} ({_safe(row['model'])})</label>"
            )
        return _page(
            "Automatic caption setup",
            body + "</fieldset><button>Preview caption setup</button></form>",
        )

    async def preview(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (
                request.query
                or not self._fields(fields, {"csrf", "revision"}, {"camera"})
                or not _HEX.fullmatch(fields["revision"])
            ):
                raise ValueError
            tokens = fields.getall("camera", [])
            if not 1 <= len(tokens) <= 16 or len(set(tokens)) != len(tokens):
                raise ValueError
            status, binding = await self._status()
            if self.site._session(request) is None:
                raise web.HTTPForbidden()
            if status["revision"] != fields["revision"]:
                raise ValueError
            loader = self.site.inventory_loader
            choices = await self._choices(cookie, status, fresh=True)
            picked = sorted((choices[t] for t in tokens), key=lambda r: r["id"])
            cameras = [{"id": r["id"], "model": r["model"]} for r in picked]
            result = await asyncio.to_thread(
                self.site.aikey.preview_caption_setup, status["revision"], binding, cameras
            )
            if (
                binding is not self.site.caption_setup_binding
                or loader is not self.site.inventory_loader
            ):
                raise ValueError
        except _ERRORS:
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        self._expire()
        if len(self.grants) >= 32:
            return self._refused()
        token = secrets.token_hex(32)
        self.grants[token] = {
            "session": hashlib.sha256(cookie.encode()).hexdigest(),
            "revision": status["revision"],
            "binding": binding,
            "loader": loader,
            "cameras": cameras,
            "tokens": tokens,
            "resulting_revision": result.resulting_revision,
            "expires": self.clock() + 600,
        }
        names = "".join(f"<li>{_safe(r['name'])} ({_safe(r['model'])})</li>" for r in picked)
        self.site._audit("caption_setup", "previewed", "aikey")
        return _page(
            "Review initial captions",
            "<h1>Review initial captions</h1><p>Nothing saved. "
            "Allow only these cameras; same-family cameras added later are not included:</p>"
            f"<ul>{names}</ul>"
            + _NOTICE
            + self._route(status)
            + "<form method='post' action='/caption-setup/apply'>"
            + self._hidden({"csrf": self.site.security.csrf_token(cookie), "grant": token})
            + "<label><input type='checkbox' name='acknowledge_processing' value='yes' required>"
            "I allow automatic image/video caption processing of these cameras by the reviewed "
            "provider under the shared rolling reservation limit and accept possible costs.</label>"
            "<label><input type='checkbox' name='confirm' value='yes' required>Save this initial policy only; "
            "no running service or native permission is changed.</label>"
            "<button>Confirm initial caption setup</button></form><p><a href='/caption-setup'>Cancel</a></p>",
        )

    async def apply(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (
                request.query
                or not self._fields(fields, {"csrf", "grant", "acknowledge_processing", "confirm"})
                or not _HEX.fullmatch(fields["grant"])
                or fields["confirm"] != "yes"
                or fields["acknowledge_processing"] != "yes"
            ):
                raise ValueError
            self._expire()
            grant = self.grants.get(fields["grant"])
            if grant is None or not hmac.compare_digest(
                grant["session"], hashlib.sha256(cookie.encode()).hexdigest()
            ):
                raise ValueError
            status, binding = await self._status()
            if self.site._session(request) is None:
                raise web.HTTPForbidden()
            if (
                status["revision"] != grant["revision"]
                or binding is not grant["binding"]
                or self.site.inventory_loader is not grant["loader"]
            ):
                raise ValueError
            choices = await self._choices(cookie, status, fresh=True)
            cameras = sorted(
                ({"id": choices[t]["id"], "model": choices[t]["model"]} for t in grant["tokens"]),
                key=lambda r: r["id"],
            )
            if cameras != grant["cameras"]:
                raise ValueError
            result = await asyncio.to_thread(
                self.site.aikey.preview_caption_setup, grant["revision"], binding, cameras
            )
            if (
                result.resulting_revision != grant["resulting_revision"]
                or self.clock() >= grant["expires"]
                or self.grants.get(fields["grant"]) is not grant
                or binding is not self.site.caption_setup_binding
                or self.site.inventory_loader is not grant["loader"]
            ):
                raise ValueError
        except _ERRORS:
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        del self.grants[fields["grant"]]
        return await self.site.admin_api.file_operation(self._commit, grant)

    async def _commit(self, grant):
        try:
            result = await asyncio.to_thread(
                self.site.aikey.apply_caption_setup,
                grant["revision"],
                grant["binding"],
                grant["cameras"],
            )
            if result.revision != grant["resulting_revision"]:
                raise ConfigurationStoreError("Caption setup acknowledgement uncertain")
        except (RevisionConflict, UnsafeConfigurationChange):
            return self._refused()
        except (ConfigurationStoreError, OSError, ValueError):
            self.site._audit("caption_setup", "uncertain", "aikey")
            return _page(
                "Caption setup outcome uncertain",
                "<h1>Caption setup outcome uncertain</h1>"
                "<p>The save may have succeeded. Reload saved settings before trying again. "
                "No automatic retry or reversal occurred.</p><p><a href='/caption-cameras'>"
                "Read saved caption state</a></p>",
                status=503,
            )
        self.site._audit("caption_setup", "saved", "aikey")
        return _page(
            "Initial captions saved",
            "<h1>Initial captions saved</h1>"
            "<p>The explicit saved caption policy was added. Other settings and existing budget usage "
            "are unchanged. A separate Key restart is required; no inference was invoked.</p>"
            "<p><a href='/caption-cameras'>Read saved caption cameras</a></p>",
        )
