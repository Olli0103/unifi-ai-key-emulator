"""Explicit saved plate-crop permission for already paired API Port cameras."""

import asyncio
import hmac
import re
import secrets
import time

from aiohttp import web

from .aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from .camera_inventory import InventoryError
from .camera_threshold_control import CameraThresholdControl
from .config_store import ConfigurationStoreError
from .control_site import _page, _safe

_BASE = {"csrf", "profile", "revision"}
_CONFIRM = {"draft", "expires", "signature", "resulting_revision", "confirm"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class PlateCameraControl(CameraThresholdControl):
    """Reuse validated opaque camera choices; each instance owns a distinct key."""

    def __init__(self, site):
        super().__init__(site)
        self.pending = {}

    def register(self, app):
        app.router.add_get("/plate-cameras", self.page)
        app.router.add_post("/plate-cameras/preview", self.preview)
        app.router.add_post("/plate-cameras/apply", self.apply)

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        body = ("<h1>Plate camera permissions</h1><p><a href='/'>Settings</a></p>"
                "<p>Choose which already paired API Port cameras may send detected vehicle "
                "crops to the displayed OCR route. This is a separate plate-data permission, "
                "not permission to pair a new camera. Remote processing may incur charges. "
                "No accuracy or model capability is verified here.</p>"
                "<p><strong>Saved permissions only:</strong> restart the selected AI Port "
                "separately to change running processing. This page sends no crop or model "
                "probe and does not restart a processor, change detection classes or revoke keys.</p>")
        if request.query.get("saved") == "1":
            body += "<p class='notice'>Plate permissions saved; running service unchanged.</p>"
        report = None
        if self.site.aiports and self.site.inventory_loader is not None:
            try:
                report = await self.site.inventory_snapshot()
            except (ValueError, OSError, InventoryError, TimeoutError):
                pass
        for profile, store in self.site.aiports.items():
            body += f"<section><h2>{_safe(profile)}</h2>"
            try:
                status = await asyncio.to_thread(store.plate_camera_status)
                choices = await self._choices(cookie, profile, status, report)
            except (ValueError, KeyError, OSError, InventoryError):
                body += ("<p>Editing unavailable: an API detector and fresh validated inventory "
                         "with unambiguous names for every saved camera are required.</p></section>")
                continue
            csrf = self.site.security.csrf_token(cookie)
            if csrf is None or self.site._session(request) is None:
                raise web.HTTPSeeOther("/login")
            route = status["route"]
            body += (f"<p>OCR destination: {_safe(route['base_url'])}; provider: {_safe(route['provider'])}; "
                     f"model: {_safe(route['model'])}. "
                     + ("Separate OCR assignment." if status["separate_ocr"] else "Uses the current detector route.")
                     + "</p>")
            if not status["vehicle_detection_enabled"]:
                body += ("<p class='notice'>Vehicle detection is not enabled in saved classes. "
                         "Plate permission alone does not enable vehicle detection.</p>")
            body += ("<form method='post' action='/plate-cameras/preview'>"
                     + self._hidden({"csrf": csrf, "profile": profile, "revision": status["revision"]}))
            for token, row in choices.items():
                body += (f"<label><input type='checkbox' name='{token}' value='yes'"
                         f"{' checked' if row['mac'] in status['plate_cameras'] else ''}>"
                         f"Allow plate crops: {_safe(row['name'])}</label>")
            body += ("<p>Uncheck every camera to disable saved plate processing. This also "
                     "removes a separate saved OCR assignment, if configured, but retains its "
                     "private key files. No one-click permission regrant is offered.</p>"
                     "<button>Preview plate permissions</button></form></section>")
        if not self.site.aiports:
            body += "<p>No AI Port profile is configured.</p>"
        if self.site._session(request) is None:
            raise web.HTTPSeeOther("/login")
        return _page("Plate camera permissions", body)

    async def _parse(self, cookie, fields, confirming=False):
        required = _BASE | (_CONFIRM if confirming else set())
        store = self._base(fields, required)
        status = await asyncio.to_thread(store.plate_camera_status)
        if status["revision"] != fields["revision"]:
            raise ValueError
        choices = await self._choices(cookie, fields["profile"], status)
        if (not set(fields) <= required | set(choices)
                or any(fields[k] != "yes" for k in choices if k in fields)):
            raise ValueError
        selected = [row["mac"] for token, row in choices.items() if token in fields]
        preview = await asyncio.to_thread(store.preview_plate_cameras, fields["revision"], selected)
        return store, status, choices, selected, preview

    def _signature(self, cookie, fields):
        signed = {k: fields[k] for k in fields if k not in {"csrf", "signature", "confirm"}}
        return self._mac_signature(["plate-camera-preview/1", cookie, signed])

    async def preview(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            _, status, choices, selected, preview = await self._parse(cookie, fields)
        except (ValueError, KeyError, OSError, InventoryError, ConfigurationStoreError):
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        hidden = dict(fields)
        hidden.update(draft=secrets.token_hex(16), expires=str(int(time.time()) + 600),
                      resulting_revision=preview.resulting_revision)
        hidden["signature"] = self._signature(cookie, hidden)
        self.pending = {k: v for k, v in self.pending.items() if v[2] >= time.time()}
        if len(self.pending) >= 256:
            self.pending.pop(next(iter(self.pending)))
        self.pending[hidden["signature"]] = (cookie, fields["profile"], int(hidden["expires"]),
                                              preview.resulting_revision)
        rows = ""
        for row in choices.values():
            before = "allowed" if row["mac"] in status["plate_cameras"] else "not allowed"
            after = "allowed" if row["mac"] in selected else "not allowed"
            rows += f"<tr><th>{_safe(row['name'])}</th><td>{before}</td><td>{after}</td></tr>"
        route = status["route"]
        warning = ("<p><strong>The separate saved OCR assignment will also be removed.</strong> "
                   "Its private key files are retained. No provider switch is made while plate "
                   "processing remains enabled.</p>" if not selected and status["separate_ocr"] else "")
        self.site._audit("plate_permissions", "previewed", fields["profile"])
        return _page("Confirm plate permissions", "<h1>Confirm plate permissions</h1>"
                     f"<p>Profile: {_safe(fields['profile'])}. Plate-crop destination: "
                     f"{_safe(route['base_url'])}; provider: {_safe(route['provider'])}; "
                     f"model: {_safe(route['model'])}. Detection classes, global and camera "
                     "score gates, caps, fallback, other permissions and pairings stay unchanged.</p>"
                     "<div class='table-scroll' role='region' tabindex='0' aria-label='Plate permission changes'>"
                     "<table><caption>Plate-crop permission</caption><tr><th>Camera</th>"
                     "<th>Before</th><th>After</th></tr>" + rows + "</table></div>" + warning
                     + "<form method='post' action='/plate-cameras/apply'>" + self._hidden(hidden)
                     + "<label><input type='checkbox' name='confirm' value='yes' required>"
                     "I authorize the selected cameras' plate crops to this OCR route, "
                     "understand any remote charges, and will restart this Port separately.</label>"
                     "<button>Save plate permissions</button></form>"
                     "<p><a href='/plate-cameras'>Cancel and return</a></p>")

    async def apply(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            self._base(fields, _BASE | _CONFIRM)
            if (fields["confirm"] != "yes" or not re.fullmatch(r"[0-9a-f]{32}", fields["draft"])
                    or not re.fullmatch(r"[0-9]{1,12}", fields["expires"])
                    or not time.time() <= int(fields["expires"]) <= time.time() + 600
                    or not _HEX.fullmatch(fields["signature"])
                    or not hmac.compare_digest(self._signature(cookie, fields), fields["signature"])):
                raise ValueError
            store, _, _, selected, preview = await self._parse(cookie, fields, True)
            if preview.resulting_revision != fields["resulting_revision"]:
                raise ValueError
        except (ValueError, KeyError, OSError, InventoryError, ConfigurationStoreError):
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        expected = (cookie, fields["profile"], int(fields["expires"]), preview.resulting_revision)
        if self.pending.pop(fields["signature"], None) != expected:
            return self._refused()
        # No await between consuming authority and invalidating older same-profile
        # intents. A used grant cannot be replayed after an exact-byte revocation.
        self.pending = {k: v for k, v in self.pending.items() if v[1] != fields["profile"]}
        return await self.site.admin_api.file_operation(self._commit, store, fields["profile"],
                                                       fields["revision"], selected)

    async def _commit(self, store, profile, revision, selected):
        try:
            await asyncio.to_thread(store.apply_plate_cameras, revision, selected)
        except AiPortRevisionConflict:
            return self._refused()
        except (ValueError, OSError, ConfigurationStoreError, AiPortConfigurationError):
            self.site._audit("plate_permissions", "uncertain", profile)
            return self._refused(uncertain=True)
        self.site._audit("plate_permissions", "saved", profile)
        raise web.HTTPSeeOther("/plate-cameras?saved=1")

    @staticmethod
    def _refused(*, uncertain=False):
        title = "Plate permission outcome uncertain" if uncertain else "Plate permissions not saved"
        return _page(title, f"<h1>{title}</h1><p>Reload the saved permission and revision before "
                     "retrying. Check the inventory, destination, camera selection and confirmation. "
                     "No automatic retry, rollback, permission regrant or restart occurs.</p>"
                     "<p><a href='/plate-cameras'>Plate camera permissions</a></p>",
                     status=503 if uncertain else 409)
