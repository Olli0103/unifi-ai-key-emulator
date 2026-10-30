"""Camera-local score gate controls for existing API Port streams; no inference."""

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time

from aiohttp import web

from .admin_inventory import inventory_view
from .aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from .camera_inventory import InventoryError
from .config_store import ConfigurationStoreError
from .control_site import _page, _safe

_BASE = {"csrf", "profile", "revision"}
_CONFIRM = {"expires", "signature", "resulting_revision", "confirm"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class CameraThresholdControl:
    def __init__(self, site):
        self.site = site
        self.signing_key = secrets.token_bytes(32)

    def register(self, app):
        app.router.add_get("/camera-thresholds", self.page)
        app.router.add_post("/camera-thresholds/preview", self.preview)
        app.router.add_post("/camera-thresholds/apply", self.apply)
        app.router.add_post("/camera-thresholds/undo", self.undo)

    def _mac_signature(self, value):
        return hmac.new(self.signing_key, json.dumps(value, sort_keys=True,
                        separators=(",", ":")).encode(), hashlib.sha256).hexdigest()

    async def _choices(self, cookie, profile, status):
        if not status["eligible"] or self.site.inventory_loader is None:
            raise ValueError
        report = await self.site.inventory_loader()
        rows = inventory_view(report, {})["cameras"]
        # Fail closed on ambiguous IDs/MACs/names rather than naming the wrong camera.
        if len({r["mac"] for r in rows}) != len(rows):
            raise ValueError
        labels = {r["mac"]: r for r in rows}
        selected = [labels[c] for c in status["cameras"]]
        if (not selected or any(not r["name"] or r["mac"].casefold() in
                re.sub(r"[:.\-]", "", r["name"]).casefold() for r in selected)
                or len({r["name"].casefold() for r in selected}) != len(selected)):
            raise ValueError
        return {"gate_" + self._mac_signature(["camera-threshold-choice/1", cookie, profile,
                status["revision"], r["id"], r["mac"], r["name"], r["model"]]): r
                for r in selected}

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        body = ("<h1>Camera score thresholds</h1><p><a href='/'>Settings</a></p>"
                "<p>Adjust existing API detection cameras only. Leave a camera field empty to "
                "inherit the global threshold. Values must be greater than 0 and at most 1. "
                "A lower gate accepts weaker scores and can add false detections; a higher "
                "gate can miss real detections. This is not a model accuracy test.</p>"
                "<p><strong>Saved settings only:</strong> restart the selected AI Port separately "
                "to activate a change. No provider request, restart, camera enrollment, zone, "
                "permission, provider, global threshold or request allowance changes here.</p>")
        if request.query.get("saved") == "1":
            body += "<p class='notice'>Camera thresholds saved; running service unchanged.</p>"
        if request.query.get("undone") == "1":
            body += "<p class='notice'>Prior camera thresholds restored; running service unchanged.</p>"
        for profile, store in self.site.aiports.items():
            body += f"<section><h2>{_safe(profile)}</h2>"
            try:
                status = await asyncio.to_thread(store.camera_threshold_status)
                choices = await self._choices(cookie, profile, status)
            except (ValueError, KeyError, OSError, InventoryError):
                choices = None
            csrf = self.site.security.csrf_token(cookie)
            if csrf is None or self.site._session(request) is None:
                raise web.HTTPSeeOther("/login")
            if choices is None:
                body += ("<p>Editing unavailable: this needs an API detector and a fresh validated "
                         "inventory with unambiguous names for every saved camera.</p>")
            else:
                body += (f"<p>Global threshold: {_safe(status['global_threshold'])}.</p>"
                         "<form method='post' action='/camera-thresholds/preview'>"
                         + self._hidden({"csrf": csrf, "profile": profile, "revision": status["revision"]}))
                for token, row in choices.items():
                    value = status["thresholds"].get(row["mac"], "")
                    effective = status["thresholds"].get(row["mac"], status["global_threshold"])
                    body += (f"<label>{_safe(row['name'])} score threshold"
                             f"<input name='{token}' type='number' min='0' max='1' step='any' "
                             f"value='{value}' placeholder='Inherit global'></label>"
                             f"<p>Current effective value: {_safe(effective)}.</p>")
                body += "<button>Preview camera thresholds</button></form>"
            undo = await asyncio.to_thread(store.camera_threshold_undo_status)
            if undo["available"]:
                body += ("<form method='post' action='/camera-thresholds/undo'>"
                         + self._hidden({"csrf": csrf, "profile": profile, "revision": undo["revision"]})
                         + "<label><input type='checkbox' name='confirm' value='yes' required>"
                         "Restore the exact prior camera gates in saved settings only.</label>"
                         "<button>Undo camera thresholds</button></form>")
            body += "</section>"
        if not self.site.aiports:
            body += "<p>No AI Port profile is configured.</p>"
        if self.site._session(request) is None:
            raise web.HTTPSeeOther("/login")
        return _page("Camera score thresholds", body)

    @staticmethod
    def _hidden(fields):
        return "".join(f"<input type='hidden' name='{_safe(k)}' value='{_safe(v)}'>"
                       for k, v in fields.items())

    def _base(self, fields, required):
        if (not required <= set(fields)
                or any(len(fields.getall(k)) != 1 or not isinstance(fields[k], str) for k in fields)
                or fields["profile"] not in self.site.aiports
                or not _HEX.fullmatch(fields["revision"])):
            raise ValueError
        return self.site.aiports[fields["profile"]]

    async def _parse(self, cookie, fields, confirming=False):
        required = _BASE | (_CONFIRM if confirming else set())
        store = self._base(fields, required)
        status = await asyncio.to_thread(store.camera_threshold_status)
        if status["revision"] != fields["revision"]:
            raise ValueError
        choices = await self._choices(cookie, fields["profile"], status)
        if set(fields) != required | set(choices):
            raise ValueError
        thresholds = {}
        for token, row in choices.items():
            value = fields[token].strip()
            if value:
                if len(value) > 32:
                    raise ValueError
                number = float(value)
                if not 0 < number <= 1:
                    raise ValueError
                thresholds[row["mac"]] = number
        preview = await asyncio.to_thread(store.preview_camera_thresholds, fields["revision"], thresholds)
        return store, status, choices, thresholds, preview

    def _signature(self, cookie, fields):
        # CSRF and acknowledgement are not confirmation authority.
        signed = {k: fields[k] for k in fields if k not in {"csrf", "signature", "confirm"}}
        return self._mac_signature(["camera-threshold-preview/1", cookie, signed])

    async def preview(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            _, status, choices, thresholds, preview = await self._parse(cookie, fields)
        except (ValueError, KeyError, OSError, InventoryError):
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        hidden = dict(fields)
        hidden.update(expires=str(int(time.time()) + 600), resulting_revision=preview.resulting_revision)
        hidden["signature"] = self._signature(cookie, hidden)
        rows = ""
        for row in choices.values():
            old = status["thresholds"].get(row["mac"], status["global_threshold"])
            new = thresholds.get(row["mac"], status["global_threshold"])
            mode = "override" if row["mac"] in thresholds else "inherit global"
            rows += f"<tr><th>{_safe(row['name'])}</th><td>{old}</td><td>{new} ({mode})</td></tr>"
        self.site._audit("camera_threshold", "previewed", fields["profile"])
        return _page("Confirm camera thresholds", "<h1>Confirm camera thresholds</h1>"
                     f"<p>Profile: {_safe(fields['profile'])}. Global threshold stays "
                     f"{_safe(status['global_threshold'])}. Other settings remain unchanged.</p>"
                     "<table><caption>Effective detection gates</caption><tr><th>Camera</th>"
                     "<th>Before</th><th>After</th></tr>" + rows + "</table>"
                     "<form method='post' action='/camera-thresholds/apply'>" + self._hidden(hidden)
                     + "<label><input type='checkbox' name='confirm' value='yes' required>"
                     "I approve these camera score gates in saved settings and understand "
                     "the separate restart requirement.</label><button>Save camera thresholds</button>"
                     "</form><p><a href='/camera-thresholds'>Cancel and return</a></p>")

    async def apply(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            self._base(fields, _BASE | _CONFIRM)
            if (fields["confirm"] != "yes" or not re.fullmatch(r"[0-9]{1,12}", fields["expires"])
                    or not time.time() <= int(fields["expires"]) <= time.time() + 600
                    or not _HEX.fullmatch(fields["signature"])
                    or not hmac.compare_digest(self._signature(cookie, fields), fields["signature"])):
                raise ValueError
            store, _, _, thresholds, preview = await self._parse(cookie, fields, True)
            if preview.resulting_revision != fields["resulting_revision"]:
                raise ValueError
        except (ValueError, KeyError, OSError, InventoryError):
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        return await self.site.admin_api.file_operation(self._commit, store, fields["profile"],
                        fields["revision"], thresholds, False)

    async def undo(self, request):
        fields = await request.post()
        if self.site._session(request, mutate=True, csrf=fields.get("csrf")) is None:
            raise web.HTTPForbidden()
        try:
            store = self._base(fields, _BASE | {"confirm"})
            if set(fields) != _BASE | {"confirm"} or fields["confirm"] != "yes":
                raise ValueError
        except ValueError:
            return self._refused()
        return await self.site.admin_api.file_operation(self._commit, store, fields["profile"],
                        fields["revision"], {}, True)

    async def _commit(self, store, profile, revision, thresholds, undo):
        try:
            if undo:
                await asyncio.to_thread(store.undo_camera_thresholds, revision)
            else:
                await asyncio.to_thread(store.apply_camera_thresholds, revision, thresholds)
        except AiPortRevisionConflict:
            return self._refused()
        except (OSError, AiPortConfigurationError, ConfigurationStoreError, ValueError):
            self.site._audit("camera_threshold", "uncertain", profile)
            return self._refused(uncertain=True)
        self.site._audit("camera_threshold", "undone" if undo else "saved", profile)
        raise web.HTTPSeeOther("/camera-thresholds?" + ("undone=1" if undo else "saved=1"))

    @staticmethod
    def _refused(*, uncertain=False):
        title = "Camera threshold outcome uncertain" if uncertain else "Camera thresholds not saved"
        return _page(title, f"<h1>{title}</h1><p>Reload saved settings and reconcile the "
                     "current revision before retrying. Check the inventory, score values and "
                     "confirmation. No automatic retry, rollback or restart occurs.</p>"
                     "<p><a href='/camera-thresholds'>Camera thresholds</a></p>",
                     status=503 if uncertain else 409)
