"""Explicit saved-scope additions, never initial setup or live camera enrollment."""

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


class CaptionCameraEnrollment(CaptionScopeControl):
    """Reuse only grant storage, fixed form validation and expiry primitives."""

    def register(self, app):
        app.router.add_get("/caption-camera-enrollment", self.page)
        app.router.add_get("/api/admin/v1/caption-camera-enrollment", self.json)
        app.router.add_post("/caption-camera-enrollment/preview", self.preview)
        app.router.add_post("/caption-camera-enrollment/apply", self.apply)

    @staticmethod
    def _explicit(status):
        cameras = status["camera_ids"]
        return (
            status["configured"]
            and cameras
            and len(cameras) < 16
            and all(re.fullmatch(r"[0-9a-f]{24}", c) for c in cameras)
        )

    async def _choices(self, cookie, status, *, fresh):
        if not self._explicit(status) or self.site.inventory_loader is None:
            raise ValueError
        if fresh:
            async with asyncio.timeout(12):
                report = await self.site.inventory_loader()
        else:
            report = await self.site.inventory_snapshot()
        view = inventory_view(report, {})
        if view["protect_version"] != status["protect_version"]:
            raise ValueError
        rows = view["cameras"]
        if len({r["id"] for r in rows}) != len(rows) or len({r["mac"] for r in rows}) != len(rows):
            raise ValueError
        selected = [
            r
            for r in rows
            if r["id"] not in status["camera_ids"]
            and r["model"] in status["camera_models"]
            and r["state"] == "CONNECTED"
            and r["processing_class"] == "smart_event_candidate"
        ]
        # Never ask a person to approve an opaque identifier or an ambiguous name.
        selected = [
            r
            for r in selected
            if r["name"]
            and sum(v["name"].casefold() == r["name"].casefold() for v in rows) == 1
            and r["id"].casefold() not in r["name"].casefold()
            and r["mac"].casefold() not in re.sub(r"[:.\-]", "", r["name"]).casefold()
        ]
        return {
            hmac.new(
                self.key,
                json.dumps(
                    [cookie, status["revision"], r["id"], r["mac"], r["name"], r["model"]],
                    separators=(",", ":"),
                ).encode(),
                hashlib.sha256,
            ).hexdigest(): r
            for r in selected
        }

    def _refused(self):
        self.site._audit("caption_enrollment", "rejected", "aikey")
        return _page(
            "Caption cameras not added",
            "<h1>Caption cameras not added</h1>"
            "<p>This request changed no settings. Reload fresh named candidates and review a new preview. "
            "An existing explicit camera list and already allowed model family are required.</p>"
            "<p><a href='/caption-camera-enrollment'>Add caption cameras</a></p>",
            status=409,
        )

    async def json(self, request):
        from .admin_api import error, reply

        if not self.site.admin_api.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        try:
            status = await asyncio.to_thread(self.site.aikey.caption_enrollment_status)
        except _ERRORS:
            status = None
        if not self.site.admin_api.authenticated(request):
            return error("authentication_required", 401)
        if status is None:
            return error("caption_enrollment_unavailable", 503)
        return reply(
            {
                "caption_enrollment": {
                    "revision": status["revision"],
                    "configured": status["configured"],
                    "explicit_camera_count": len(status["camera_ids"])
                    if status["camera_ids"] is not None
                    else None,
                    "existing_cost_mode": ("unmetered" if status["unmetered"] else "metered")
                    if status["configured"]
                    else None,
                    "explicit_list_has_room": bool(self._explicit(status)),
                },
                "read_only": True,
                "observation": "saved_policy_not_running_authority",
            }
        )

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        if request.query:
            raise web.HTTPBadRequest()
        body = (
            "<h1>Add caption cameras</h1><p><a href='/'>Settings</a> · "
            "<a href='/caption-cameras'>Remove caption cameras</a> · "
            "<a href='/caption-policy'>Caption cost policy</a></p>"
            "<p>Add named cameras to an existing explicit automatic-caption list. This is not initial "
            "continuous setup: allowed model families and cost mode cannot be changed here. "
            "A separate Key restart is needed; running jobs and native Protect permissions stay unchanged. "
            "Speech, faces, search and AI Port detection keep their separate policies. "
            "No model or media test, budget reset or refund occurs.</p>"
        )
        try:
            status = await asyncio.to_thread(self.site.aikey.caption_enrollment_status)
        except _ERRORS:
            status = None
        if self.site._session(request) is None:
            raise web.HTTPSeeOther("/login")
        if status is None or not self._explicit(status):
            return _page(
                "Add caption cameras",
                body + "<p>Adding cameras needs an existing explicit list "
                "with fewer than sixteen cameras. Missing or model-wide policies are not converted.</p>",
            )
        try:
            choices = await self._choices(cookie, status, fresh=False)
        except _ERRORS:
            choices = {}
        if self.site._session(request) is None:
            raise web.HTTPSeeOther("/login")
        if not choices:
            return _page(
                "Add caption cameras",
                body + "<p>No fresh unambiguous connected smart-event "
                "candidate in an already allowed model family is available. Metadata eligibility is "
                "not proof of native event delivery or model quality.</p>",
            )
        body += (
            f"<p>Keep all {len(status['camera_ids'])} existing saved cameras. Select only additions:</p>"
            "<form method='post' action='/caption-camera-enrollment/preview'>"
            + self._hidden(
                {"csrf": self.site.security.csrf_token(cookie), "revision": status["revision"]}
            )
            + "<fieldset><legend>Additional caption cameras</legend>"
        )
        for token, row in choices.items():
            body += (
                f"<label><input type='checkbox' name='add_camera' value='{token}'>"
                f"{_safe(row['name'])} ({_safe(row['model'])})</label>"
            )
        body += "</fieldset><button>Preview caption additions</button></form>"
        return _page("Add caption cameras", body)

    async def preview(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (
                request.query
                or not self._fields(fields, {"csrf", "revision"}, {"add_camera"})
                or not _HEX.fullmatch(fields["revision"])
            ):
                raise ValueError
            selected = fields.getall("add_camera", [])
            if not 1 <= len(selected) <= 16 or len(set(selected)) != len(selected):
                raise ValueError
            status = await asyncio.to_thread(self.site.aikey.caption_enrollment_status)
            if self.site._session(request) is None:
                raise web.HTTPForbidden()
            if status["revision"] != fields["revision"]:
                raise ValueError
            choices = await self._choices(cookie, status, fresh=True)
            if self.site._session(request) is None:
                raise web.HTTPForbidden()
            picked = sorted((choices[t] for t in selected), key=lambda r: r["id"])
            additions = [{"id": r["id"], "model": r["model"]} for r in picked]
            result = await asyncio.to_thread(
                self.site.aikey.preview_caption_enrollment, status["revision"], additions
            )
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
            "additions": additions,
            "tokens": selected,
            "unmetered": status["unmetered"],
            "resulting_revision": result.resulting_revision,
            "expires": self.clock() + 600,
        }
        names = "".join(f"<li>{_safe(r['name'])} ({_safe(r['model'])})</li>" for r in picked)
        mode = (
            "Existing local-unmetered override: no rolling caption reservation cap. "
            "A local endpoint does not prove free inference."
            if status["unmetered"]
            else "These cameras share the existing rolling caption reservation limit. "
            "It is not a total-provider bill or price cap; past usage is retained."
        )
        body = (
            f"<h1>Review caption additions</h1><p>Nothing saved. Keep all {len(status['camera_ids'])} "
            f"existing saved cameras and add only:</p><ul>{names}</ul>"
            f"<p>{mode}</p><p>No other feature or native permission changes. A separate Key restart "
            "is required; no inference or media test occurs.</p>"
            "<form method='post' action='/caption-camera-enrollment/apply'>"
            + self._hidden({"csrf": self.site.security.csrf_token(cookie), "grant": token})
            + "<label><input type='checkbox' name='acknowledge_processing' value='yes' required>"
            "Allow automatic processing of these cameras under the reviewed existing cost policy.</label>"
        )
        if status["unmetered"]:
            body += (
                "<label><input type='checkbox' name='acknowledge_bypass' value='yes' required>"
                "Use the existing local-unmetered override for these additional cameras without "
                "the rolling caption reservation cap.</label>"
            )
        body += (
            "<label><input type='checkbox' name='confirm' value='yes' required>"
            "Save only these reviewed camera additions.</label><button>Confirm caption additions</button></form>"
            "<p><a href='/caption-camera-enrollment'>Cancel</a></p>"
        )
        self.site._audit("caption_enrollment", "previewed", "aikey")
        return _page("Review caption additions", body)

    async def apply(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (
                request.query
                or not self._fields(
                    fields,
                    {"csrf", "grant", "confirm", "acknowledge_processing"},
                    {"acknowledge_bypass"},
                )
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
            if (
                grant["unmetered"]
                and fields.getall("acknowledge_bypass", []) != ["yes"]
                or not grant["unmetered"]
                and "acknowledge_bypass" in fields
            ):
                raise ValueError
            status = await asyncio.to_thread(self.site.aikey.caption_enrollment_status)
            if self.site._session(request) is None:
                raise web.HTTPForbidden()
            if status["revision"] != grant["revision"]:
                raise ValueError
            choices = await self._choices(cookie, status, fresh=True)
            if self.site._session(request) is None:
                raise web.HTTPForbidden()
            additions = sorted(
                ({"id": choices[t]["id"], "model": choices[t]["model"]} for t in grant["tokens"]),
                key=lambda r: r["id"],
            )
            if additions != grant["additions"]:
                raise ValueError
            result = await asyncio.to_thread(
                self.site.aikey.preview_caption_enrollment, grant["revision"], additions
            )
            if (
                result.resulting_revision != grant["resulting_revision"]
                or self.clock() >= grant["expires"]
                or self.grants.get(fields["grant"]) is not grant
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
                self.site.aikey.enroll_caption_cameras, grant["revision"], grant["additions"]
            )
            if result.revision != grant["resulting_revision"]:
                raise ConfigurationStoreError("Caption enrollment acknowledgement uncertain")
        except (RevisionConflict, UnsafeConfigurationChange):
            return self._refused()
        except (ConfigurationStoreError, OSError, ValueError):
            self.site._audit("caption_enrollment", "uncertain", "aikey")
            return _page(
                "Caption additions outcome uncertain",
                "<h1>Caption additions outcome uncertain</h1>"
                "<p>The save may have succeeded. Read saved settings before trying again. "
                "No automatic retry or reversal occurred.</p>"
                "<p><a href='/caption-camera-enrollment'>Read saved caption state</a></p>",
                status=503,
            )
        self.site._audit("caption_enrollment", "saved", "aikey")
        return _page(
            "Caption additions saved",
            "<h1>Caption additions saved</h1>"
            "<p>Only the saved explicit caption list changed. Other features, budget usage and running "
            "jobs are unchanged; a separate Key restart is needed. No inference was invoked.</p>"
            "<p><a href='/caption-cameras'>Read saved caption cameras</a></p>",
        )
