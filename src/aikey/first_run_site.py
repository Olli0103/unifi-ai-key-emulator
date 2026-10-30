"""Authenticated new-profile preparation; no device startup or camera access."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
from types import SimpleNamespace

from aiohttp import web

from .cli import _inference_options
from .config import ConfigError, atomic_private, readiness
from .config_store import ConfigurationStoreError
from .control_site import ControlSite, _page, _safe
from .first_run import prepare, trust_target
from .tls import import_controller_certificate


class FirstRunSite(ControlSite):
    def __init__(self, directory: Path, **kwargs):
        requested = Path(directory).expanduser().absolute()
        self.directory = requested.parent.resolve(strict=True) / requested.name
        if os.path.lexists(self.directory):
            raise ConfigError("First-run directory already exists; use the existing control site")
        super().__init__(self.directory / "config.json", **kwargs)
        self.prepared = False
        self._setup_lock = asyncio.Lock()
        self._operations = set()

    def app(self):
        app = web.Application(client_max_size=16384)
        app.router.add_get("/", self.index)
        app.router.add_get("/login", self.login_page)
        app.router.add_post("/login", self.login)
        app.router.add_post("/logout", self.logout)
        app.router.add_post("/create", self.create)
        app.router.add_post("/trust", self.trust)
        app.router.add_get("/healthz", self.health)
        app.on_cleanup.append(self._drain_operations)
        return app

    async def _file_action(self, operation, *args):
        # Cancelling an HTTP waiter cannot cancel a thread already writing
        # private files. Retain it, block new operations, and drain on shutdown.
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        self._operations.add(task)
        def settled(completed):
            self._operations.discard(completed)
            if not completed.cancelled():
                completed.exception()  # Never leave a late private exception unobserved.
        task.add_done_callback(settled)
        return await asyncio.shield(task)

    async def _drain_operations(self, _):
        while self._operations:
            await asyncio.gather(*tuple(self._operations), return_exceptions=True)

    def _error(self, title, detail, status):
        response = _page(title, f"<h1>{_safe(title)}</h1><p class='error'>{_safe(detail)}</p>"
                         "<p><a href='/'>First-run status</a></p>")
        response.set_status(status)
        return response

    async def _fields(self, request, required, optional=(), *, action, certificate=False):
        fields = await request.post()
        cookie = self._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            self._audit(action, "forbidden", "aikey")
            raise web.HTTPForbidden()
        if (not set(required) <= set(fields) or set(fields) - set(required) - set(optional)
                or any(len(fields.getall(k)) != 1 or not isinstance(fields[k], str)
                       or len(fields[k]) > (8192 if certificate and k == "certificate" else 4096)
                       for k in fields)):
            self._audit(action, "invalid_request", "aikey")
            raise web.HTTPBadRequest(text="Use the current first-run form")
        return fields, cookie

    async def index(self, request):
        cookie = self._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        csrf = self.security.csrf_token(cookie)
        if not self.prepared:
            if self._operations:
                return self._error("Preparation in progress", "A private-file operation is still running. No new preparation is admitted; shutdown waits for it.", 409)
            if os.path.lexists(self.directory):
                return self._error("Retained setup state", "The directory exists. Inspect the retained private state; this wizard never repairs or reinitializes it.", 409)
            return _page("First-run AI Key preparation", self._form(csrf))
        try:
            raw, config = await self._file_action(self.aikey._read, self.aikey.path)
            report = await self._file_action(readiness, config)
        except (ConfigurationStoreError, OSError, ValueError):
            return self._error("Prepared state unavailable", "Inspect the retained private configuration. No automatic repair, deletion or restart occurs.", 409)
        revision = hashlib.sha256(raw).hexdigest()
        rows = "".join(f"<tr><th scope='row'>{_safe(k.replace('_', ' '))}</th><td>{'Present/valid' if v else 'Missing/not valid'}</td></tr>"
                       for k, v in report["checks"].items())
        body = ("<h1>Fresh profile prepared</h1><p>No device was started or adopted. "
                "No controller credentials, camera media or provider request were sent by preparation.</p>"
                f"<p>Vision destination: {_safe(config['inference']['base_url'])}; model: {_safe(config['inference']['model'])}. "
                "Model availability, modality, native compatibility and quality are not tested.</p>"
                "<h2>Local file checks, not native acceptance</h2><table>" + rows + "</table>"
                "<h2>Import independently verified controller trust</h2><p>Paste one public PEM certificate "
                "obtained and verified separately. This wizard never contacts its endpoint. "
                "Forms expire, are single-use and share a 30-second cooldown. Existing trust is never replaced.</p>")
        for channel in ("control", "search"):
            controller = config["controller"]
            key = "search_ca_file" if channel == "search" else "ca_file"
            if key not in controller:
                continue
            if os.path.lexists(controller[key]):
                body += f"<p>{_safe(channel.title())} trust path exists; no replacement action.</p>"
                continue
            grant = self._probe_grants.issue("aikey", "trust-" + channel, revision, csrf)
            if grant is None:
                body += "<p>Trust forms are temporarily full. Reload later.</p>"
                continue
            hidden = {"csrf": csrf, "revision": revision, "channel": channel, "grant": grant}
            port = controller["search_port" if channel == "search" else "control_port"]
            body += ("<form method='post' action='/trust'>"
                     + "".join(f"<input type='hidden' name='{k}' value='{_safe(v)}'>" for k, v in hidden.items())
                     + f"<h3>{_safe(channel.title())}: {_safe(controller['host'])}, port {port}</h3>"
                     "<label>One public PEM certificate<textarea name='certificate' required maxlength='8192' rows='8' spellcheck='false'></textarea></label>"
                     "<p>Never paste a private key. Chains, private keys and extra content are refused.</p>"
                     "<label>Independently verified SHA-256 fingerprint<input name='fingerprint' required maxlength='95'></label>"
                     "<label><input type='checkbox' name='acknowledge_import' value='yes' required> "
                     "Install this public certificate only if its independently verified fingerprint matches.</label>"
                     "<button>Import this certificate</button></form>")
        body += ("<h2>Next steps remain explicit</h2><p>Provision/check any required provider credential. "
                 "Stop this preparation server before running the normal control site with the new configuration. "
                 "Review camera scope, media destination, provider retention and cost before an explicit device "
                 "run/adoption. Search/database, discovery, continuous cameras, audio, named recognition and "
                 "migrations remain disabled. This wizard has no start, adopt, reset or inference action.</p>"
                 f"<form method='post' action='/logout'><input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                 "<button>Sign out</button></form>")
        return _page("Fresh profile prepared", body)

    @staticmethod
    def _form(csrf):
        return ("<h1>First-run AI Key preparation</h1><p>Creates only the new directory chosen by the "
                "server operator. Existing or partial installations are never reused. This is not AI Port "
                "provisioning, adoption or a native-result test.</p><form method='post' action='/create'>"
                f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                "<section><h2>Controller and listener</h2>"
                "<label>Controller hostname or IP<input name='controller' required maxlength='253'></label>"
                "<label>Advertised device IP<input name='device_ip' required maxlength='45'></label>"
                "<label>Listener IP (blank uses advertised IP)<input name='bind' maxlength='45'></label>"
                "<label>HTTPS device port<input name='https_port' type='number' min='1' max='65535' value='8080' required></label>"
                "<label>Operator-reported Protect version<input name='protect_version' required maxlength='40'></label>"
                "<label>Control profile<select name='control_profile'><option value='ucp4'>UCP4</option>"
                "<option value='device-service'>Device service (requires pin)</option></select></label>"
                "<label>Verified control SHA-256 pin (optional unless required by profile)<input name='controller_fingerprint' maxlength='95'></label>"
                "<label>Separate verified search SHA-256 pin (optional)<input name='search_fingerprint' maxlength='95'></label>"
                "<label><input type='checkbox' name='pin_only_hostname' value='yes'> Use the explicit control pin "
                "instead of hostname matching (certificate verification remains required)</label></section>"
                "<section><h2>Vision model and destination</h2><label>Provider<select name='provider' required>"
                "<option value=''>Choose a provider</option><option value='ollama'>Ollama</option>"
                "<option value='openai-compatible'>OpenAI-compatible</option><option value='openai'>OpenAI</option>"
                "<option value='anthropic'>Anthropic</option></select></label>"
                "<label>Installed/available vision model ID<input name='model' required maxlength='128'></label>"
                "<label>API base URL (blank uses official/provider default)<input name='base_url' maxlength='512'></label>"
                "<label>New vision API key, if required<input name='api_key' type='password' autocomplete='new-password' maxlength='4096'></label>"
                "<p>The key is saved only in the new private profile and never returned. No provider is called. "
                "Later inference sends selected frames to the chosen endpoint. A private proxy can still "
                "forward remotely; retention and charges are not verified here.</p>"
                "<label><input type='checkbox' name='allow_remote' value='yes'> Allow this non-loopback API</label>"
                "<label><input type='checkbox' name='allow_insecure_http' value='yes'> Allow unencrypted remote HTTP</label>"
                "<label>FFmpeg absolute path in eventual runtime (optional)<input name='ffmpeg_path' maxlength='1024'></label>"
                "<label><input type='checkbox' name='request_mp4_exports' value='yes'> Request supported MP4 exports</label>"
                "<label><input type='checkbox' name='acknowledge_destination' value='yes' required> "
                "I reviewed this model/destination and its future media-sharing, retention and cost. "
                "Create local files only; do not contact a controller or model.</label>"
                "<button>Prepare a new private profile</button></section></form>")

    async def create(self, request):
        required = {"csrf", "controller", "device_ip", "bind", "https_port", "protect_version",
                    "control_profile", "controller_fingerprint", "search_fingerprint", "provider", "model",
                    "base_url", "api_key", "ffmpeg_path", "acknowledge_destination"}
        optional = {"pin_only_hostname", "allow_remote", "allow_insecure_http", "request_mp4_exports"}
        fields, _ = await self._fields(request, required, optional, action="first_run_create")
        if self._setup_lock.locked() or self._operations:
            raise web.HTTPConflict(text="A setup operation is already running")
        async with self._setup_lock:
            if self.prepared or os.path.lexists(self.directory):
                self._audit("first_run_create", "refused", "aikey")
                raise web.HTTPConflict(text="Existing or partial setup is never reused")
            try:
                if (fields["acknowledge_destination"] != "yes"
                        or any(fields[k] != "yes" for k in optional if k in fields)
                        or not re.fullmatch(r"[0-9]{1,5}", fields["https_port"])):
                    raise ConfigError("Invalid form")
                key = fields["api_key"].strip()
                if key and (not key.isascii() or not key.isprintable() or any(c.isspace() for c in key)):
                    raise ConfigError("Invalid key")
                key_path = self.directory / "state" / ("vision-key-" + secrets.token_hex(8)) if key else None
                options = SimpleNamespace(directory=self.directory, controller=fields["controller"],
                    device_ip=fields["device_ip"], bind=fields["bind"] or None, https_port=int(fields["https_port"]),
                    protect_version=fields["protect_version"], control_profile=fields["control_profile"],
                    controller_fingerprint=fields["controller_fingerprint"] or None,
                    search_fingerprint=fields["search_fingerprint"] or None,
                    pin_only_hostname="pin_only_hostname" in fields, name=fields["provider"], model=fields["model"],
                    base_url=fields["base_url"] or None, api_key_file=key_path, allow_remote="allow_remote" in fields,
                    allow_insecure_http="allow_insecure_http" in fields,
                    ffmpeg_path=Path(fields["ffmpeg_path"]) if fields["ffmpeg_path"] else None,
                    request_mp4_exports="request_mp4_exports" in fields)
                if options.name not in {"openai", "anthropic", "ollama", "openai-compatible"}:
                    raise ConfigError("Invalid provider")
                inference = _inference_options(options)
                await self._file_action(prepare, options, inference)
                if key_path is not None:
                    await self._file_action(atomic_private, key_path, key)
                self.prepared = True
            except asyncio.CancelledError:
                self._audit("first_run_create", "interrupted_partial", "aikey")
                raise
            except (ValueError, OSError, RuntimeError):
                self._audit("first_run_create", "refused_or_partial", "aikey")
                return self._error("Preparation not verified", "Check the fields and private server state. Any created state is retained; no automatic retry, deletion or repair occurs.", 400)
            self._audit("first_run_create", "prepared", "aikey")
            raise web.HTTPSeeOther("/")

    async def trust(self, request):
        fields, _ = await self._fields(request, {"csrf", "revision", "channel", "fingerprint", "certificate", "grant", "acknowledge_import"}, action="first_run_trust", certificate=True)
        if not self.prepared or fields["channel"] not in {"control", "search"} or fields["acknowledge_import"] != "yes":
            raise web.HTTPBadRequest(text="Use the current trust form")
        if self._operations or self._setup_lock.locked():
            raise web.HTTPConflict(text="A private-file operation is still running")
        try:
            raw, config = await self._file_action(self.aikey._read, self.aikey.path)
            revision = hashlib.sha256(raw).hexdigest()
            if not re.fullmatch(r"[0-9a-f]{64}", fields["revision"]) or not hmac.compare_digest(revision, fields["revision"]):
                raise ConfigError("Stale configuration")
            _, _, output = trust_target(config, fields["channel"], fields["fingerprint"])
            self._probe_grants.begin(fields["grant"], "aikey", "trust-" + fields["channel"], revision, fields["csrf"])
        except (ValueError, OSError, ConfigurationStoreError):
            self._audit("first_run_trust", "preflight_refused", "aikey")
            return self._error("Trust request refused", "The form, revision, channel, pin or cooldown is not current. No network request was made.", 409)
        try:
            await self._file_action(import_controller_certificate, fields["certificate"].encode("ascii"), fields["fingerprint"], output)
        except asyncio.CancelledError:
            self._audit("first_run_trust", "interrupted_partial", "aikey")
            raise
        except (ValueError, OSError, RuntimeError):
            self._audit("first_run_trust", "unverified", "aikey")
            return self._error("Trust import not verified", "Inspect the retained state and independently verified fingerprint. Existing trust is never replaced; no automatic retry occurred.", 400)
        finally:
            self._probe_grants.finish()
        self._audit("first_run_trust", "imported", "aikey")
        raise web.HTTPSeeOther("/")
