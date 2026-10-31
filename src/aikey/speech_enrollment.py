"""Explicit initial camera audio consent; saved settings, not worker activation."""

import asyncio
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import time

from aiohttp import ClientError, web

from .admin_inventory import inventory_view
from .camera_inventory import InventoryError
from .config import atomic_private
from .config_store import ConfigurationStoreError
from .control_site import _page, _private_directory, _safe

_FIELDS = {"csrf", "revision", "provider", "model", "base_url", "language", "camera"}
_OPTIONAL = {"local_network"}
_GRANT_SECONDS = 600
_GRANT_CAPACITY = 32
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class SpeechEnrollment:
    def __init__(self, site):
        self.site = site
        self.signing_key = secrets.token_bytes(32)
        self.grants = {}
        self.clock = time.monotonic

    def register(self, app):
        app.router.add_get("/speech-enrollment", self.page)
        app.router.add_post("/speech-enrollment/preview", self.preview)
        app.router.add_post("/speech-enrollment/apply", self.apply)

    def _token(self, cookie, revision, row):
        payload = ["speech-enrollment-camera/1", cookie, revision,
                   {k: row[k] for k in ("id", "mac", "name", "model", "state", "audio_detect_types")}]
        return hmac.new(self.signing_key, json.dumps(payload, sort_keys=True,
            separators=(",", ":")).encode(), hashlib.sha256).hexdigest()

    async def _choices(self, cookie, revision, *, cached=False):
        if self.site.inventory_loader is None:
            raise ValueError
        async with asyncio.timeout(12):
            report = (await self.site.inventory_snapshot() if cached else await self.site.inventory_loader())
        rows = inventory_view(report, {})["cameras"]
        # A duplicate/ambiguous name or identity must not authorize the wrong microphone.
        if len({r["mac"] for r in rows}) != len(rows):
            raise ValueError
        eligible = [r for r in rows if r["state"] == "CONNECTED"
                    and "alrmSpeak" in r["audio_detect_types"]]
        if (any(not r["name"] or not re.fullmatch(r"[0-9a-f]{24}", r["id"]) for r in eligible)
                or len({r["name"].casefold() for r in eligible}) != len(eligible)):
            raise ValueError
        return {self._token(cookie, revision, r): r for r in eligible}

    async def _snapshot(self):
        snapshot = await asyncio.to_thread(self.site.aikey.snapshot)
        if snapshot.configuration.get("speech_to_text") is not None:
            raise ValueError
        return snapshot

    @staticmethod
    def _hidden(fields):
        return "".join(f"<input type='hidden' name='{_safe(k)}' value='{_safe(v)}'>" for k, v in fields.items())

    @staticmethod
    def _refused():
        return _page("Speech setup not saved", "<h1>Speech setup not saved</h1>"
            "<p>Reload setup and review the current cameras, provider and settings. "
            "No setup was admitted. Existing speech settings use their separate controls.</p>"
            "<p><a href='/speech-enrollment'>Speech setup</a> · <a href='/'>Settings</a></p>", status=409)

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        if request.query:
            raise web.HTTPBadRequest()
        body = ("<h1>Set up speech transcription</h1><p><a href='/'>Settings</a></p>"
            "<p>This initial setup saves an explicit camera allowlist and a separate speech provider. "
            "Nothing is selected by default. Audio processing starts only after a separate AI Key "
            "restart and eligible native speech events. This page does not enable camera microphones, "
            "change Protect settings, fetch recordings or test a model.</p>")
        try:
            snapshot = await asyncio.to_thread(self.site.aikey.snapshot)
            configured = snapshot.configuration.get("speech_to_text") is not None
            choices = None if configured else await self._choices(cookie, snapshot.revision, cached=True)
        except (ValueError, OSError, ConfigurationStoreError, InventoryError, ClientError, TimeoutError):
            choices, configured = None, None
        if self.site._session(request) is None:
            raise web.HTTPSeeOther("/login")
        if configured:
            return _page("Speech transcription setup", body +
                "<p>Speech is already configured. This initial setup cannot replace it or expand "
                "its camera permissions.</p><p><a href='/'>Existing speech provider settings</a> · "
                "<a href='/speech-permissions'>Existing saved audio permissions</a></p>")
        if not choices:
            return _page("Speech transcription setup", body +
                "<p>Setup unavailable: no connected cameras "
                "with unambiguous names and reported speech-event support could be read.</p>"
                "<p><a href='/speech-permissions'>Existing saved audio permissions</a></p>")
        body += ("<p>Listed cameras report speech-event support in Protect; this is not a guarantee "
            "of native event delivery. Choose only cameras whose audio you intend to transcribe. "
            "Existing worker audio limits, video policy, pairing and other feature permissions remain unchanged.</p>"
            "<form method='post' action='/speech-enrollment/preview'>" + self._hidden(
                {"csrf": self.site.security.csrf_token(cookie), "revision": snapshot.revision}))
        for token, row in choices.items():
            body += (f"<label><input type='checkbox' name='camera' value='{token}'>"
                     f"Allow audio from {_safe(row['name'])}</label>")
        body += ("<label for='enrollment-provider'>Speech provider</label>"
                 "<select id='enrollment-provider' name='provider'><option value='openai-compatible'>"
                 "Local compatible API</option><option value='openai'>OpenAI</option></select>"
                 "<label>Speech API base URL<input name='base_url' type='url' required maxlength='512' "
                 "placeholder='http://127.0.0.1:8178/v1'></label>"
                 "<label>Speech model<input name='model' required maxlength='128'></label>"
                 "<label>Language (optional two-letter code)<input name='language' maxlength='2'></label>"
                 "<label><input type='checkbox' name='local_network' value='yes'>"
                 "Allow a private-network server; HTTP sends audio unencrypted.</label>"
                 "<p>OpenAI requires https://api.openai.com/v1. Compatible servers must be loopback "
                 "or an explicitly allowed private numeric address. Enter a new speech credential "
                 "only on the confirmation page; no vision credential is reused.</p>"
                 "<button>Preview speech setup</button></form>")
        return _page("Speech transcription setup", body)

    def _expire(self):
        now = self.clock()
        for token, grant in list(self.grants.items()):
            if grant["expires"] <= now:
                del self.grants[token]

    async def preview(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (request.query or not _FIELDS <= set(fields) or set(fields) - _FIELDS - _OPTIONAL
                    or any(len(fields.getall(k)) != 1 for k in fields if k != "camera")
                    or any(not isinstance(v, str) for _, v in fields.items())
                    or not _HEX.fullmatch(fields["revision"])
                    or "local_network" in fields and fields["local_network"] != "yes"):
                raise ValueError
            snapshot = await self._snapshot()
            if snapshot.revision != fields["revision"]:
                raise ValueError
            choices = await self._choices(cookie, snapshot.revision)
            selected = fields.getall("camera")
            if not 1 <= len(selected) <= 32 or len(set(selected)) != len(selected) or not set(selected) <= set(choices):
                raise ValueError
            selection = {"provider": fields["provider"], "model": fields["model"].strip(),
                         "base_url": fields["base_url"].strip(), "local_network": "local_network" in fields}
            if selection["provider"] == "openai" and selection["base_url"].rstrip("/") != "https://api.openai.com/v1":
                raise ValueError
            if fields["language"].strip():
                selection["language"] = fields["language"].strip()
            cameras = [choices[t]["id"] for t in selected]
            # Validate without creating a key or storing its value in preview authority.
            validation = dict(selection, api_key_file=str(Path(snapshot.configuration["runtime"]["state_dir"])
                                                        / "speech-enrollment-preview"))
            await asyncio.to_thread(self.site.aikey.preview_speech_enrollment,
                                    snapshot.revision, validation, cameras)
            self._expire()
            if len(self.grants) >= _GRANT_CAPACITY:
                raise ValueError
        except (ValueError, KeyError, OSError, ConfigurationStoreError, InventoryError, ClientError, TimeoutError):
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        token = secrets.token_hex(32)
        self.grants[token] = {"session": hashlib.sha256(cookie.encode()).hexdigest(), "revision": snapshot.revision,
            "selection": deepcopy(selection), "tokens": list(selected), "cameras": cameras,
            "expires": self.clock() + _GRANT_SECONDS,
            "runtime_state": snapshot.configuration["runtime"]["state_dir"]}
        labels = "".join("<li>" + _safe(choices[t]["name"]) + "</li>" for t in selected)
        remote = selection["provider"] == "openai"
        self.site._audit("speech_enroll", "previewed", "aikey")
        return _page("Confirm speech transcription", "<h1>Confirm speech transcription</h1>"
            + f"<p>Destination: {_safe(selection['base_url'])}. Model: {_safe(selection['model'])}. "
            + ("Audio will be sent to OpenAI." if remote else "Audio will be sent to the selected local API.")
            + "</p><ul>" + labels + "</ul>"
            "<p>Only these cameras gain a saved speech-to-text permission. Existing audio-export "
            "limits remain unchanged. No audio or model test has been sent. This preview expires "
            "in ten minutes and is usable once in this signed-in session.</p>"
            "<form method='post' action='/speech-enrollment/apply'>" + self._hidden(
                {"csrf": self.site.security.csrf_token(cookie), "grant": token})
            + "<label>New separate speech API key<input name='api_key' type='password' autocomplete='new-password' "
            + ("required " if remote else "") + "maxlength='4096'></label>"
            "<label><input type='checkbox' name='confirm' value='yes' required>"
            "I authorize audio from these cameras to this provider and understand that a separate "
            "AI Key restart is required.</label>"
            "<label><input type='checkbox' name='acknowledge_cost' value='yes' required>"
            "I understand speech can incur charges independently of the caption budget; "
            "a local API is not a billing guarantee.</label>"
            "<button>Save initial speech permissions</button></form>"
            "<p><a href='/speech-enrollment'>Cancel and return</a></p>")

    async def apply(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (request.query or set(fields) != {"csrf", "grant", "api_key", "confirm", "acknowledge_cost"}
                    or any(len(fields.getall(k)) != 1 or not isinstance(fields[k], str) for k in fields)
                    or fields["confirm"] != "yes" or fields["acknowledge_cost"] != "yes"):
                raise ValueError
            self._expire()
            grant = self.grants.get(fields["grant"])
            if grant is None or not hmac.compare_digest(grant["session"], hashlib.sha256(cookie.encode()).hexdigest()):
                raise ValueError
            choices = await self._choices(cookie, grant["revision"])
            if not set(grant["tokens"]) <= set(choices):
                raise ValueError
            key = fields["api_key"]
            if (key and (not 8 <= len(key) <= 4096 or any(c.isspace() for c in key))
                    or not key and grant["selection"]["provider"] == "openai"):
                raise ValueError
            validation = dict(grant["selection"])
            if key:
                validation["api_key_file"] = str(Path(grant["runtime_state"]) / "speech-enrollment-preview")
            await asyncio.to_thread(self.site.aikey.preview_speech_enrollment,
                                    grant["revision"], validation, grant["cameras"])
            # Another confirmation, logout, expiry or settings change during either await
            # cannot obtain authority from this earlier read. The store rechecks OCC at commit.
            if (self.grants.get(fields["grant"]) is not grant or self.clock() >= grant["expires"]):
                raise ValueError
        except (ValueError, KeyError, OSError, ConfigurationStoreError, InventoryError, ClientError, TimeoutError):
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        del self.grants[fields["grant"]]
        return await self.site.admin_api.file_operation(self._commit, grant, key)

    async def _commit(self, grant, key):
        created = None
        apply_started = committed = False
        try:
            selection = dict(grant["selection"])
            if key:
                name = "speech-key-" + secrets.token_hex(16)
                created = self.site.aikey.path.parent / name
                selection["api_key_file"] = str(Path(grant["runtime_state"]) / name)
            await asyncio.to_thread(self.site.aikey.preview_speech_enrollment,
                                    grant["revision"], selection, grant["cameras"])
            if created is not None:
                _private_directory(created.parent)
                atomic_private(created, key + "\n")
            apply_started = True
            await asyncio.to_thread(self.site.aikey.enroll_speech,
                                    grant["revision"], selection, grant["cameras"])
            committed = True
        except (ValueError, OSError, ConfigurationStoreError) as exc:
            certain = self.site._failed_save_key(created, committed, apply_started, exc)
            self.site._audit("speech_enroll", "rejected" if certain else "uncertain", "aikey")
            return _page("Speech setup not saved" if certain else "Speech setup outcome uncertain",
                "<h1>" + ("Speech setup not saved" if certain else "Speech setup outcome uncertain") + "</h1>"
                "<p>Reload saved settings before trying again. An interrupted save may have committed; "
                "there is no automatic retry, rollback or key cleanup after an uncertain write.</p>"
                "<p><a href='/'>Settings</a></p>", status=409 if certain else 503)
        self.site._audit("speech_enroll", "saved", "aikey")
        raise web.HTTPSeeOther("/?saved=1")
