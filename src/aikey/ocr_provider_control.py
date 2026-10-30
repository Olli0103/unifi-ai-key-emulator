"""Separate OCR setup for already opted-in Port cameras; no inference or scope grant."""

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from aiohttp import web

from .aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from .control_site import _page, _private_directory, _safe


_BASE = {"csrf", "profile", "revision", "provider", "model", "base_url", "max_output_tokens"}
_FLAGS = {"allow_remote", "allow_insecure_http", "replace_key"}
_CONFIRM = {"draft", "expires", "signature", "resulting_revision", "confirm"}
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PROVIDERS = (("openai", "OpenAI"), ("anthropic", "Claude / Anthropic"),
              ("ollama", "Ollama"), ("openai-compatible", "Compatible API"))


class OcrProviderControl:
    def __init__(self, site):
        self.site = site
        # Confirmation authority is server-private and intentionally lost on restart.
        self.signing_key = secrets.token_bytes(32)

    def register(self, app):
        app.router.add_get("/ocr-provider", self.page)
        app.router.add_post("/ocr-provider/preview", self.preview)
        app.router.add_post("/ocr-provider/apply", self.apply)

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        rows = []
        for profile, store in self.site.aiports.items():
            try:
                status = await asyncio.to_thread(store.ocr_provider_status)
            except (AiPortConfigurationError, OSError, ValueError):
                rows.append(f"<section><h2>{_safe(profile)}</h2><p>Saved OCR settings unavailable.</p></section>")
                continue
            csrf = self.site.security.csrf_token(cookie)
            if self.site._session(request) is None or csrf is None:
                raise web.HTTPSeeOther("/login")
            if not status["eligible"]:
                rows.append(f"<section><h2>{_safe(profile)}</h2><p>Separate OCR setup requires "
                            "an existing API detector and existing plate-camera permission. "
                            "This page cannot add permission or switch the detector.</p></section>")
                continue
            selection = status["selection"]
            options = "".join(f"<option value='{value}'"
                              f"{' selected' if value == selection.get('provider') else ''}>{label}</option>"
                              for value, label in _PROVIDERS)
            form = (f"<section><h2>{_safe(profile)} · plate OCR</h2>"
                    f"<p>{status['plate_camera_count']} already permitted plate camera(s). "
                    f"Saved assignment: {_safe(status['assignment'])}. "
                    f"Separate OCR key configured: {'yes' if status['key_configured'] else 'no'}.</p>"
                    "<form method='post' action='/ocr-provider/preview'>"
                    f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                    f"<input type='hidden' name='profile' value='{_safe(profile)}'>"
                    f"<input type='hidden' name='revision' value='{_safe(status['revision'])}'>"
                    f"<label>OCR provider<select name='provider'>{options}</select></label>"
                    f"<label>OCR model ID<input name='model' required maxlength='128' value='{_safe(selection.get('model'))}'></label>"
                    f"<label>OCR API base URL<input name='base_url' required maxlength='512' value='{_safe(selection.get('base_url'))}'></label>"
                    f"<label>OCR maximum output tokens<input name='max_output_tokens' type='number' min='1' max='512' value='{_safe(selection.get('max_output_tokens', 256))}'></label>")
            for name, label in (("allow_remote", "Allow remote OCR API"),
                                ("allow_insecure_http", "Allow unencrypted remote OCR HTTP")):
                form += (f"<label><input type='checkbox' name='{name}' value='yes'"
                         f"{' checked' if selection.get(name) else ''}>{label}</label>")
            form += ("<label><input type='checkbox' name='replace_key' value='yes'>"
                     "Supply a new separate OCR key on the confirmation page</label>"
                     "<p>The detection key is never copied. An existing explicit OCR key is "
                     "kept only for the exact same provider and API base URL. A changed "
                     "destination drops it; OpenAI/Anthropic require a separate OCR key.</p>"
                     "<button>Preview OCR provider</button></form>"
                     + self.site._undo_form(profile, profile, store.provider_rollback_status(), csrf)
                     + "</section>")
            rows.append(form)
        return _page("Plate OCR provider", "<h1>Plate OCR provider</h1><p><a href='/'>Settings</a></p>"
                     "<p>Set a separate route for already permitted plate crops. No images, model "
                     "probes or network tests are sent by viewing, previewing or saving here. "
                     "Camera scope, detection, fallback, thresholds and request caps stay unchanged.</p>"
                     "<p><strong>Saved settings only:</strong> restart the selected AI Port separately "
                     "to activate a change. Model capability and plate accuracy are not verified here. "
                     "OCR shares the detector's configured request allowance; if unset, there "
                     "is no request cap. Remote processing can incur charges.</p>"
                     + ("<p class='notice'>OCR provider saved; running service unchanged.</p>"
                        if request.query.get("saved") == "1" else "")
                     + "<div class='grid'>" + ("".join(rows) or "<p>No AI Port profile is configured.</p>") + "</div>")

    def _parse(self, fields, *, confirming):
        required = _BASE | (_CONFIRM if confirming else set())
        allowed = required | _FLAGS | ({"api_key"} if confirming else set())
        if (not required <= set(fields) <= allowed
                or any(len(fields.getall(k)) != 1 or not isinstance(fields[k], str) for k in fields)
                or any(fields[k] != "yes" for k in _FLAGS if k in fields)
                or fields["profile"] not in self.site.aiports
                or not _HEX64.fullmatch(fields["revision"])
                or not re.fullmatch(r"[0-9]{1,3}", fields["max_output_tokens"])):
            raise ValueError
        if confirming and fields["confirm"] != "yes":
            raise ValueError
        draft = fields["draft"] if confirming else secrets.token_hex(16)
        if not _HEX32.fullmatch(draft):
            raise ValueError
        store = self.site.aiports[fields["profile"]]
        selection = {"provider": fields["provider"], "model": fields["model"].strip(),
                     "base_url": fields["base_url"].strip(),
                     "max_output_tokens": int(fields["max_output_tokens"]),
                     "allow_remote": "allow_remote" in fields,
                     "allow_insecure_http": "allow_insecure_http" in fields}
        if "replace_key" in fields:
            selection["api_key_file"] = str(store.runtime_state_dir / ("ocr-key-" + draft))
        return store, selection, draft

    def _signature(self, cookie, fields, selection, draft, expires, resulting):
        value = ["ocr-provider-preview/1", cookie, fields["profile"], fields["revision"],
                 selection, draft, expires, resulting]
        return hmac.new(self.signing_key, json.dumps(value, sort_keys=True,
                        separators=(",", ":")).encode(), hashlib.sha256).hexdigest()

    async def preview(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            store, selection, draft = self._parse(fields, confirming=False)
            preview = await asyncio.to_thread(store.preview_ocr_provider, fields["revision"], selection)
        except (ValueError, OSError, AiPortConfigurationError):
            self.site._audit("ocr_provider", "preview_refused")
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        expires = int(time.time()) + 600
        signature = self._signature(cookie, fields, selection, draft, expires, preview.resulting_revision)
        hidden = {k: fields[k] for k in fields}
        hidden.update(draft=draft, expires=str(expires), signature=signature,
                      resulting_revision=preview.resulting_revision)
        inputs = "".join(f"<input type='hidden' name='{_safe(k)}' value='{_safe(v)}'>" for k, v in hidden.items())
        self.site._audit("ocr_provider", "previewed", fields["profile"])
        return _page("Confirm OCR provider", "<h1>Confirm OCR provider</h1>"
                     f"<p>Profile: {_safe(fields['profile'])}. Only the separate OCR assignment changes. "
                     f"Destination: {_safe(selection['base_url'])}. Model: {_safe(selection['model'])}.</p>"
                     "<p>Detection provider/key, plate-camera permissions, thresholds, fallback and "
                     "request caps remain unchanged. No model capability or accuracy test has run. "
                     "Current processing continues until a separate restart.</p>"
                     "<form method='post' action='/ocr-provider/apply'>" + inputs
                     + ("<label>New separate OCR API key<input name='api_key' type='password' "
                        "required autocomplete='new-password' maxlength='4095'></label>"
                        if "replace_key" in fields else "")
                     + "<label><input type='checkbox' name='confirm' value='yes' required>"
                     "I approve this OCR destination for the already permitted plate crops; "
                     "I understand any remote charges and the separate restart requirement.</label>"
                     "<button>Save separate OCR provider</button></form>"
                     "<p><a href='/ocr-provider'>Cancel and return</a></p>")

    async def apply(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            store, selection, draft = self._parse(fields, confirming=True)
            if not re.fullmatch(r"[0-9]{1,12}", fields["expires"]):
                raise ValueError
            expires = int(fields["expires"])
            if not time.time() <= expires <= time.time() + 600:
                raise ValueError
            expected = self._signature(cookie, fields, selection, draft, expires, fields["resulting_revision"])
            if not _HEX64.fullmatch(fields["signature"]) or not hmac.compare_digest(expected, fields["signature"]):
                raise ValueError
            key = fields.get("api_key", "")
            if ("replace_key" in fields and (not 8 <= len(key) <= 4095
                    or len((key + "\n").encode("utf-8")) > 4096
                    or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in key))
                    or "replace_key" not in fields and key):
                raise ValueError
            preview = await asyncio.to_thread(store.preview_ocr_provider, fields["revision"], selection)
            if preview.resulting_revision != fields["resulting_revision"]:
                raise ValueError
        except (ValueError, OSError, AiPortConfigurationError):
            self.site._audit("ocr_provider", "apply_refused")
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        # Reuse the existing admin adapter's admitted-write ownership/drain.
        return await self.site.admin_api.file_operation(self._commit, store, fields["profile"],
                    fields["revision"], selection, draft, key, preview.resulting_revision)

    async def _commit(self, store, profile, revision, selection, draft, key, resulting):
        def write():
            created = None
            started = committed = False
            try:
                if key:
                    _private_directory(store.path.parent)
                    path = store.path.parent / ("ocr-key-" + draft)
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    created = path
                    os.fchmod(descriptor, 0o600)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write((key + "\n").encode())
                        stream.flush()
                        os.fsync(stream.fileno())
                    directory = os.open(store.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                started = True
                saved = store.apply_ocr_provider(revision, selection)
                committed = True
                if saved.revision != resulting:
                    raise ValueError
            except (ValueError, OSError, AiPortConfigurationError) as exc:
                certain = self.site._failed_save_key(created, committed, started, exc)
                return "refused" if certain or isinstance(exc, AiPortRevisionConflict) else "uncertain"
            return "saved"
        result = await asyncio.to_thread(write)
        self.site._audit("ocr_provider", result, profile)
        if result != "saved":
            return self._refused(uncertain=result == "uncertain")
        raise web.HTTPSeeOther("/ocr-provider?saved=1")

    @staticmethod
    def _refused(*, uncertain=False):
        title = "OCR save outcome uncertain" if uncertain else "OCR settings not saved"
        return _page(title, f"<h1>{title}</h1><p>Reload saved settings and reconcile the "
                     "current revision before retrying. Check existing plate permission, provider, "
                     "separate key and confirmation. No automatic retry, restart or key cleanup "
                     "for an uncertain commit occurs.</p><p><a href='/ocr-provider'>OCR settings</a></p>",
                     status=503 if uncertain else 409)
