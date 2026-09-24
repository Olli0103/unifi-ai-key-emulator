"""Loopback administration site for AI Key and AI Port provider settings."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import html
import json
import os
from pathlib import Path
import secrets
import signal
import stat

from aiohttp import web

from .admin_security import AdminSecurity
from .aiport_config_store import (
    AiPortConfigurationError, AiPortConfigurationStore, AiPortRevisionConflict,
)
from .config import atomic_private, load_config
from .config_store import (
    ConfigurationStore, ConfigurationStoreError, RevisionConflict,
)


_COOKIE = "aikey_admin_local"
_STYLE = """
:root { font-family: system-ui, sans-serif; color: #192430; background: #f4f7fb; }
body { max-width: 1100px; margin: 2rem auto; padding: 0 1.2rem; }
h1 { margin-bottom: .25rem; } p { line-height: 1.45; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 1rem; }
section { background: white; border: 1px solid #d9e2eb; border-radius: 12px;
          padding: 1.3rem; box-shadow: 0 2px 8px #1a35450a; }
label { display: block; font-weight: 600; margin-top: 1rem; }
input, select { box-sizing: border-box; width: 100%; margin-top: .35rem; padding: .6rem;
                font: inherit; border: 1px solid #9aaebe; border-radius: 6px; }
input[type=checkbox] { width: auto; margin-right: .4rem; }
.checks label { display: inline-block; margin-right: .9rem; font-weight: 400; }
button { margin-top: 1.2rem; border: 0; border-radius: 6px; padding: .7rem 1rem;
         background: #075bd8; color: white; font: inherit; cursor: pointer; }
.muted { color: #4f6173; } .error { color: #a1231b; } .notice { color: #195c31; }
.row { display: flex; gap: .8rem; align-items: center; justify-content: space-between; }
"""


def _safe(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _page(title: str, body: str) -> web.Response:
    markup = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
              "<meta name='viewport' content='width=device-width,initial-scale=1'>"
              f"<title>{_safe(title)}</title><style>{_STYLE}</style></head>"
              f"<body>{body}</body></html>")
    return web.Response(text=markup, content_type="text/html", headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                   "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    })


def _private_bytes(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or not 0 < info.st_size <= limit):
            raise ValueError("Invalid private administrator file")
        return source.read(limit + 1)


def _private_directory(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077):
        raise ValueError("Provider state directory is not private")


class ControlSite:
    def __init__(self, aikey_config: Path, aiport_config: Path, *,
                 signing_key: bytes, password_record: str, port: int,
                 aiport_runtime_state_dir: Path | None = None):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Invalid control-site port")
        self.origin = f"http://127.0.0.1:{port}"
        self.security = AdminSecurity(signing_key, self.origin, allow_loopback_http=True)
        self.password_record = password_record
        self.aikey = ConfigurationStore(aikey_config)
        self.aiport = AiPortConfigurationStore(
            aiport_config, runtime_state_dir=aiport_runtime_state_dir)

    def app(self) -> web.Application:
        app = web.Application(client_max_size=8192)
        app.router.add_get("/", self.index)
        app.router.add_get("/login", self.login_page)
        app.router.add_post("/login", self.login)
        app.router.add_post("/provider", self.save_provider)
        app.router.add_post("/logout", self.logout)
        app.router.add_get("/healthz", self.health)
        return app

    async def health(self, _: web.Request) -> web.Response:
        return web.json_response({"service": "control-site"})

    def _same_host(self, request: web.Request) -> bool:
        return request.host == self.origin.removeprefix("http://")

    def _session(self, request: web.Request, *, mutate: bool = False,
                 csrf: str | None = None) -> str | None:
        if not self._same_host(request):
            return None
        cookie = request.cookies.get(_COOKIE, "")
        origin = request.headers.get("Origin", "") if mutate else self.origin
        decision = self.security.authorize(
            "POST" if mutate else "GET", origin, cookie, csrf)
        return cookie if decision.allowed else None

    async def login_page(self, request: web.Request) -> web.Response:
        if not self._same_host(request):
            raise web.HTTPForbidden()
        return _page("Sign in", "<h1>Local AI processor</h1><section>"
                     "<h2>Administrator sign in</h2><form method='post' action='/login'>"
                     "<label for='password'>Password</label>"
                     "<input id='password' name='password' type='password' required "
                     "autocomplete='current-password'><button>Sign in</button></form></section>")

    async def login(self, request: web.Request) -> web.Response:
        if not self._same_host(request):
            raise web.HTTPForbidden()
        fields = await request.post()
        password = fields.get("password", "")
        decision = self.security.login(request.remote or "local", password,
                                       self.password_record,
                                       request.headers.get("Origin", ""))
        if not decision.allowed or decision.credentials is None:
            return _page("Sign in failed", "<h1>Sign in failed</h1>"
                         "<p class='error'>Check the password and try again.</p>"
                         "<p><a href='/login'>Back to sign in</a></p>")
        response = web.HTTPSeeOther("/")
        response.set_cookie(_COOKIE, decision.credentials.cookie, httponly=True,
                            secure=False, samesite="Strict", path="/",
                            max_age=self.security.session_ttl)
        raise response

    async def index(self, request: web.Request) -> web.Response:
        cookie = self._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        csrf = self.security.csrf_token(cookie)
        assert csrf is not None
        try:
            key = self.aikey.snapshot()
            port = self.aiport.snapshot()
        except (ConfigurationStoreError, AiPortConfigurationError):
            return _page("Configuration unavailable", "<h1>Configuration unavailable</h1>"
                         "<p class='error'>Check both private config files.</p>")
        inference = key.configuration["inference"]
        key_configured = bool(inference.get("api_key_file", {}).get("configured"))
        notice = "<p class='notice'>Settings saved. Restart the affected service to apply them.</p>" \
            if request.query.get("saved") == "1" else ""
        body = ("<div class='row'><div><h1>Local AI processor</h1>"
                "<p class='muted'>Provider and model settings for both Protect profiles.</p>"
                "</div><form method='post' action='/logout'>"
                f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                "<button>Sign out</button></form></div>" + notice +
                "<p>Changes are saved with a revision check. They take effect after a service "
                "restart. Pairings and device identities stay in place.</p><div class='grid'>")
        body += self._provider_form("AI Key", "aikey", key.revision, inference,
                                    key_configured, csrf)
        if port.camera_count >= 2:
            current = {"provider": port.provider or "openai", "model": port.model or "gpt-6-luna",
                       "base_url": port.base_url or "https://api.openai.com/v1",
                       "allow_remote": port.allow_remote if port.provider else True,
                       "allow_insecure_http": port.allow_insecure_http,
                       "max_output_tokens": port.max_output_tokens or 256,
                       "threshold": port.threshold if port.threshold is not None else .8,
                       "smart_types": port.smart_types or ("person",),
                       "max_events_per_hour": port.max_events_per_hour or 12,
                       "max_requests_per_hour": port.max_requests_per_hour or 24}
            body += self._provider_form("AI Port", "aiport", port.revision, current,
                                        port.key_configured, csrf,
                                        camera_count=port.camera_count,
                                        backend=port.backend)
        else:
            body += ("<section><h2>AI Port</h2><p>No paired camera pool is configured. "
                     "Provider settings become available after pairing at least two cameras."
                     "</p></section>")
        return _page("Local AI processor", body + "</div>")

    @staticmethod
    def _provider_form(title: str, profile: str, revision: str, current: dict,
                       key_configured: bool, csrf: str, *, camera_count: int = 0,
                       backend: str | None = None) -> str:
        provider = current.get("provider", "openai")
        options = "".join(
            f"<option value='{name}'{' selected' if name == provider else ''}>{label}</option>"
            for name, label in (("openai", "OpenAI"), ("anthropic", "Claude / Anthropic"),
                                ("ollama", "Ollama"),
                                ("openai-compatible", "Compatible API")))
        detail = (f"<p>{camera_count} paired cameras. "
                  f"Configured detector: {_safe(backend or 'off')}.</p>"
                  if camera_count else "")
        if profile == "aiport" and backend != "vision_api":
            detail += ("<p class='muted'>Saving provider settings switches AI Port "
                       "to API detection after its next restart. The current local "
                       "detector keeps running until then.</p>")
        output_limit = 32768 if profile == "aikey" else 512
        form = (f"<section><h2>{_safe(title)}</h2>{detail}<p class='muted'>"
                f"Key reference configured: {'yes' if key_configured else 'no'}. "
                "A new key is write-only and replaces the previous reference.</p>"
                "<form method='post' action='/provider'>"
                f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                f"<input type='hidden' name='profile' value='{profile}'>"
                f"<input type='hidden' name='revision' value='{_safe(revision)}'>"
                f"<label>Provider<select name='provider'>{options}</select></label>"
                f"<label>Model ID<input name='model' required maxlength='128' "
                f"value='{_safe(current.get('model'))}'></label>"
                f"<label>API base URL<input name='base_url' required maxlength='512' "
                f"value='{_safe(current.get('base_url'))}'></label>"
                "<label>New API key, if needed<input name='api_key' type='password' "
                "autocomplete='new-password'></label>"
                f"<label>Maximum output tokens<input name='max_output_tokens' type='number' "
                f"min='1' max='{output_limit}' "
                f"value='{_safe(current.get('max_output_tokens', 256))}'></label>"
                "<div class='checks'><label><input name='allow_remote' type='checkbox' "
                + ("checked" if current.get("allow_remote") else "") + ">Allow remote API</label>"
                "<label><input name='allow_insecure_http' type='checkbox' "
                + ("checked" if current.get("allow_insecure_http") else "") +
                ">Allow unencrypted remote HTTP</label></div>")
        if profile == "aiport":
            form += (f"<label>Score threshold<input name='threshold' type='number' "
                     f"min='0.01' max='1' step='0.01' value='{_safe(current['threshold'])}'></label>"
                     "<div class='checks'><p>Detection classes</p>" + "".join(
                         f"<label><input name='smart_types' type='checkbox' value='{kind}'"
                         f"{' checked' if kind in current['smart_types'] else ''}>{kind.title()}</label>"
                         for kind in ("person", "vehicle", "animal")) + "</div>"
                     f"<label>Maximum events per camera per hour<input name='max_events_per_hour' "
                     f"type='number' min='1' max='3600' value='{_safe(current['max_events_per_hour'])}'></label>"
                     f"<label>Maximum API requests per camera per hour<input "
                     f"name='max_requests_per_hour' type='number' min='2' max='3600' "
                     f"value='{_safe(current['max_requests_per_hour'])}'></label>")
        return form + "<button>Save provider settings</button></form></section>"

    async def save_provider(self, request: web.Request) -> web.Response:
        fields = await request.post()
        cookie = self._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        profile = fields.get("profile")
        if profile not in {"aikey", "aiport"}:
            raise web.HTTPBadRequest(text="Unknown profile")
        store = self.aikey if profile == "aikey" else self.aiport
        created_key: Path | None = None
        committed = False
        try:
            provider = fields["provider"]
            base_url = fields["base_url"].strip()
            selection: dict = {
                "provider": provider, "model": fields["model"].strip(),
                "base_url": base_url, "allow_remote": "allow_remote" in fields,
                "allow_insecure_http": "allow_insecure_http" in fields,
                "max_output_tokens": int(fields["max_output_tokens"]),
            }
            if profile == "aiport":
                selection.update({
                    "threshold": float(fields["threshold"]),
                    "smart_types": fields.getall("smart_types", []),
                    "max_events_per_hour": int(fields["max_events_per_hour"]),
                    "max_requests_per_hour": int(fields["max_requests_per_hour"]),
                })
            key_value = fields.get("api_key", "")
            if key_value:
                if (not isinstance(key_value, str) or not 8 <= len(key_value) <= 4096
                        or any(ch.isspace() for ch in key_value)):
                    raise ValueError
                host_state = (self.aikey.path.parent if profile == "aikey"
                              else self.aiport.path.parent)
                runtime_state = (Path(load_config(self.aikey.path)["runtime"]["state_dir"])
                                 if profile == "aikey" else self.aiport.runtime_state_dir)
                _private_directory(host_state)
                key_name = f"provider-key-{secrets.token_hex(8)}"
                created_key = host_state / key_name
                selection["api_key_file"] = str(runtime_state / key_name)
            if provider in {"openai", "anthropic"} and created_key is None:
                current = store.snapshot()
                if profile == "aikey":
                    saved = current.configuration["inference"]
                    configured = bool(saved.get("api_key_file", {}).get("configured"))
                    same = saved.get("provider") == provider and saved.get("base_url") == base_url
                else:
                    configured = current.key_configured
                    same = current.provider == provider and current.base_url == base_url
                if not configured or not same:
                    raise ValueError
            preview = (store.preview_inference(fields["revision"], selection)
                       if profile == "aikey" else store.preview(fields["revision"], selection))
            if created_key is not None:
                atomic_private(created_key, key_value + "\n")
            result = (store.apply_inference(fields["revision"], selection)
                      if profile == "aikey" else store.apply(fields["revision"], selection))
            committed = True
            if result.revision != preview.resulting_revision:
                raise ValueError
        except (KeyError, ValueError, TypeError, OSError, ConfigurationStoreError,
                AiPortConfigurationError, RevisionConflict, AiPortRevisionConflict):
            if created_key is not None and not committed:
                created_key.unlink(missing_ok=True)
            return _page("Settings not saved", "<h1>Settings not saved</h1>"
                         "<p class='error'>Check the provider, model, key and request limits. "
                         "The configuration may also have changed in another session.</p>"
                         "<p><a href='/'>Back to settings</a></p>")
        raise web.HTTPSeeOther("/?saved=1")

    async def logout(self, request: web.Request) -> web.Response:
        fields = await request.post()
        if self._session(request, mutate=True, csrf=fields.get("csrf")) is None:
            raise web.HTTPForbidden()
        response = web.HTTPSeeOther("/login")
        response.del_cookie(_COOKIE, path="/")
        raise response


def _cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local-only AI processor control site")
    commands = parser.add_subparsers(dest="command", required=True)
    initial = commands.add_parser("init", help="Set an administrator password locally")
    initial.add_argument("--state-dir", required=True, type=Path)
    initial.add_argument("--generate", action="store_true",
                         help="Generate a private one-time bootstrap password file")
    run = commands.add_parser("run", help="Serve provider settings on loopback")
    run.add_argument("--state-dir", required=True, type=Path)
    run.add_argument("--aikey-config", required=True, type=Path)
    run.add_argument("--aiport-config", required=True, type=Path)
    run.add_argument("--aiport-runtime-state-dir", type=Path,
                     help="AI Port state path inside its processor container")
    run.add_argument("--port", type=int, default=8765)
    return parser


async def _run(site: ControlSite, port: int) -> None:
    runner = web.AppRunner(site.app(), access_log=None)
    await runner.setup()
    listener = web.TCPSite(runner, "127.0.0.1", port)
    await listener.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()


def main(argv: list[str] | None = None) -> int:
    args = _cli().parse_args(argv)
    state = args.state_dir.expanduser().absolute()
    try:
        if args.command == "init":
            state.mkdir(mode=0o700, parents=True, exist_ok=True)
            _private_directory(state)
            record_path = state / "admin-password-record"
            key_path = state / "admin-signing-key"
            bootstrap_path = state / "admin-bootstrap-password"
            if record_path.exists() or key_path.exists() or bootstrap_path.exists():
                raise ValueError("Administrator already initialized")
            if args.generate:
                password = secrets.token_urlsafe(32)
            else:
                password = getpass.getpass("New administrator password: ")
                repeated = getpass.getpass("Repeat administrator password: ")
                if password != repeated:
                    raise ValueError("Passwords do not match")
            record = AdminSecurity.create_password_record(password)
            atomic_private(key_path, secrets.token_bytes(32))
            atomic_private(record_path, record + "\n")
            if args.generate:
                atomic_private(bootstrap_path, password + "\n")
            print(json.dumps({"initialized": True, "state_dir": str(state),
                              "bootstrap_password_file": str(bootstrap_path)
                              if args.generate else None}))
            return 0
        _private_directory(state)
        signing_key = _private_bytes(state / "admin-signing-key", 64)
        record = _private_bytes(state / "admin-password-record", 4096).decode().strip()
        site = ControlSite(args.aikey_config, args.aiport_config,
                           signing_key=signing_key, password_record=record, port=args.port,
                           aiport_runtime_state_dir=args.aiport_runtime_state_dir)
        site.aikey.snapshot()
        site.aiport.snapshot()
        print(json.dumps({"control_site": site.origin, "loopback_only": True}))
        asyncio.run(_run(site, args.port))
        return 0
    except (OSError, ValueError, ConfigurationStoreError, AiPortConfigurationError) as exc:
        print(f"Control site unavailable: {type(exc).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
