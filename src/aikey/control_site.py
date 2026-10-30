"""Authenticated local/direct-HTTPS administration for AI Key and AI Port."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timezone
import getpass
import hashlib
import hmac
import html
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import time
from urllib.parse import urlsplit

from aiohttp import ClientError, web

from .admin_audit import AuditLog
from .caption_preflight import preflight as caption_preflight
from .state_backup_preflight import inventory as backup_inventory
from .admin_security import AdminSecurity, SESSION_COOKIE_NAME
from .admin_listener import configure_listener
from .aiport_config_store import (
    AiPortConfigurationError, AiPortConfigurationStore, AiPortRevisionConflict,
)
from .aiport_deployment import AiPortPlanError, plan_ai_ports
from .aiport_rollout import (
    PlanChanged, RolloutError, RolloutOutcomeUncertain, apply_if_current, load_rollout_config,
    compute as compute_rollout, public as public_rollout,
)
from .camera_inventory import InventoryError, fetch_inventory
from .config import atomic_private, load_config
from .data_policy import ROWS, live_status
from .config_store import (
    ConfigurationStore, ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange,
)
from .index_rebuild import RebuildError, RebuildOutcomeUncertain, cancel_generation, migration_plan
from .model_catalog import CatalogError, catalog_provider, fetch_catalog
from .providers import DEFAULT_BASE_URLS
from .provider_probe import ProbeError, ProbeGrants, probe_speech, probe_vision


_COOKIE = "aikey_admin_local"
_G3_G5_MODEL = re.compile(r"UVC G[345](?:\s|\Z)")
_INSTANCE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_MUTATION_LIMIT = 60
_MUTATION_WINDOW = 60.0
_STYLE = """
:root { font-family: system-ui, sans-serif; color: #192430; background: #f4f7fb; }
body { max-width: 1100px; margin: 2rem auto; padding: 0 1.2rem; }
h1 { margin-bottom: .25rem; } p { line-height: 1.45; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr)); gap: 1rem; }
.grid > * { min-width: 0; }
section { background: white; border: 1px solid #d9e2eb; border-radius: 12px;
          padding: 1.3rem; box-shadow: 0 2px 8px #1a35450a; }
label { display: block; font-weight: 600; margin-top: 1rem; }
input, select, textarea { box-sizing: border-box; width: 100%; margin-top: .35rem; padding: .6rem;
                font: inherit; border: 1px solid #65788b; border-radius: 6px; }
input[type=checkbox] { width: auto; margin-right: .4rem; }
.checks label { display: inline-block; margin-right: .9rem; font-weight: 400; }
button { margin-top: 1.2rem; border: 0; border-radius: 6px; padding: .7rem 1rem;
         background: #075bd8; color: white; font: inherit; cursor: pointer; }
.muted { color: #4f6173; } .error { color: #a1231b; } .notice { color: #195c31; }
.row { display: flex; flex-wrap: wrap; gap: .8rem; align-items: center; justify-content: space-between; }
table { width: 100%; border-collapse: collapse; background: white; }
th, td { padding: .65rem; text-align: left; border-bottom: 1px solid #d9e2eb; }
.scroll, .table-scroll { max-width: 100%; overflow-x: auto; }
:focus-visible { outline: 3px solid #075bd8; outline-offset: 3px; }
.skip-link { position: absolute; top: .5rem; left: .5rem; transform: translateY(-200%);
             background: white; color: #075bd8; padding: .6rem; z-index: 1; }
.skip-link:focus { transform: none; }
.maintenance fieldset { min-width: 0; margin: 1rem 0; border: 1px solid #65788b; }
.maintenance code { overflow-wrap: anywhere; }
"""


def _safe(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _page(title: str, body: str, *, landmark: bool = True, status: int = 200) -> web.Response:
    if landmark:
        body = ("<a class='skip-link' href='#main'>Skip to main content</a>"
                f"<main id='main' tabindex='-1'>{body}</main>")
    markup = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
              "<meta name='viewport' content='width=device-width,initial-scale=1'>"
              f"<title>{_safe(title)}</title><style>{_STYLE}</style></head>"
              f"<body>{body}</body></html>")
    return web.Response(text=markup, status=status, content_type="text/html", headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                   "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        # Chrome otherwise sends Origin: null for native form POSTs.
        # Keep same-origin form authentication while sending no referrer cross-origin.
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
        "Cache-Control": "no-store",
    })


def _private_bytes(path: Path, limit: int) -> bytes:
    # A FIFO blocks in open() before fstat can reject its type. Non-blocking
    # opening lets the same descriptor/type/owner/mode checks refuse it safely.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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
    def __init__(self, aikey_config: Path, aiport_config: Path | None = None, *,
                 signing_key: bytes, password_record: str, port: int,
                 aiport_runtime_state_dir: Path | None = None,
                 aiport_instances: dict[str, Path] | None = None,
                 inventory_loader: Callable[[], Awaitable[dict]] | None = None,
                 rollout_path: Path | None = None,
                 search_state_dir: Path | None = None,
                 search_backups_dir: Path | None = None,
                 search_live_rows: Callable[[], Awaitable[int]] | None = None,
                 aikey_state_dir: Path | None = None,
                 audit_log: Path | None = None,
                 runtime_health_urls: dict[str, str] | None = None,
                 maintenance_bridge: Path | None = None,
                 bind: str = "127.0.0.1", origin: str | None = None,
                 tls_certificate: Path | None = None, tls_private_key: Path | None = None):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Invalid control-site port")
        proposed = origin if origin is not None else f"http://127.0.0.1:{port}"
        self.security = AdminSecurity(signing_key, proposed, allow_loopback_http=True)
        self.origin = self.security.allowed_origin
        self.listener = configure_listener(port, bind, self.origin, tls_certificate, tls_private_key)
        self.password_record = password_record
        self.aikey = ConfigurationStore(aikey_config)
        self.aiport = (AiPortConfigurationStore(
            aiport_config, runtime_state_dir=aiport_runtime_state_dir)
            if aiport_config is not None else None)
        self.aiports = {"aiport": self.aiport} if self.aiport is not None else {}
        paths = {store.path for store in self.aiports.values()}
        for name, path in (aiport_instances or {}).items():
            if (not isinstance(name, str) or not _INSTANCE_NAME.fullmatch(name)
                    or name == "default"):
                raise ValueError("Invalid AI Port instance name")
            store = AiPortConfigurationStore(
                path, runtime_state_dir=aiport_runtime_state_dir)
            if store.path in paths:
                raise ValueError("Duplicate AI Port configuration")
            paths.add(store.path)
            self.aiports[f"aiport:{name}"] = store
        self.inventory_loader = inventory_loader
        self._inventory_clock = time.monotonic
        self._inventory_source = inventory_loader
        self._inventory_snapshot = None
        self._inventory_failure_until = 0.0
        self._inventory_snapshot_lock = asyncio.Lock()
        self.rollout_path = rollout_path
        self.search_state_dir = search_state_dir
        self.search_backups_dir = search_backups_dir
        self.search_live_rows = search_live_rows
        # The Key's host state sits next to its config unless given (#12).
        self.aikey_state_dir = aikey_state_dir or self.aikey.path.parent
        from .admin_runtime import RuntimeObserver
        self.runtime_observer = RuntimeObserver(self._backup_profiles(), runtime_health_urls or {})
        self.audit = AuditLog(audit_log) if audit_log is not None else None
        from .admin_maintenance import Bridge
        self.maintenance = Bridge(maintenance_bridge) if maintenance_bridge is not None else None
        self._catalog_lock = asyncio.Lock()
        self._catalog_next_at = 0.0
        self._probe_grants = ProbeGrants()
        # One bounded runtime-wide window, not per cookie/IP: a fresh login or
        # forged forwarding header cannot buy more file/provider operations.
        self._mutation_times: deque[float] = deque(maxlen=_MUTATION_LIMIT)
        self._mutation_clock = time.monotonic
        self._mutation_limited_logged = False

    def app(self) -> web.Application:
        app = web.Application(client_max_size=8192)
        app.router.add_get("/", self.index)
        app.router.add_get("/cameras", self.cameras)
        app.router.add_get("/aiport-rollout", self.rollout_page)
        app.router.add_post("/aiport-rollout", self.rollout_apply)
        # Apply remains unavailable. Cancellation only retires an idle staged generation.
        app.router.add_get("/index-migration", self.index_migration)
        app.router.add_post("/index-migration/cancel", self.cancel_index_migration)
        app.router.add_get("/data-retention", self.data_retention)
        app.router.add_get("/audit", self.audit_page)
        app.router.add_get("/caption-preflight", self.caption_preflight_page)
        app.router.add_get("/state-backup-preflight", self.state_backup_page)
        app.router.add_get("/model-registry", self.model_registry_page)
        app.router.add_get("/login", self.login_page)
        app.router.add_post("/login", self.login)
        app.router.add_post("/provider", self.save_provider)
        app.router.add_post("/provider/rollback", self.rollback_provider)
        app.router.add_post("/role-model", self.save_role_model)
        app.router.add_post("/role-model-preview", self.preview_role_model)
        app.router.add_post("/speech-provider", self.save_speech_provider)
        app.router.add_get("/speech-permissions", self.speech_permissions)
        app.router.add_post("/speech-permissions", self.restrict_speech_permissions)
        app.router.add_get("/aiport-permissions", self.aiport_permissions)
        app.router.add_post("/aiport-permissions", self.restrict_aiport_permissions)
        app.router.add_post("/model-catalog", self.model_catalog)
        app.router.add_post("/provider-probe", self.provider_probe)
        app.router.add_post("/logout", self.logout)
        app.router.add_get("/healthz", self.health)
        from .admin_api import AdminAPI
        self.admin_api = AdminAPI(self)
        self.admin_api.register(app)
        return app

    async def health(self, _: web.Request) -> web.Response:
        return web.json_response({"service": "control-site"})

    async def model_registry_page(self, request: web.Request) -> web.Response:
        """Saved role contracts only, never a capability/active-runtime probe."""
        if self._session(request) is None:
            raise web.HTTPSeeOther("/login")
        rows = []
        stores = [("AI Key", self.aikey)] + [
            ("this AI Port" if profile == "aiport" else "AI Port " + profile.partition(":")[2], store)
            for profile, store in self.aiports.items()]
        policies = {"role_model_preview": "Model preview and revision-checked save",
                    "index_migration": "Separate index migration required",
                    "separate_ocr_configuration": "Configure a separate OCR assignment first",
                    "separate_feature_flow": "Separate feature/model procedure; not editable here"}
        for title, store in stores:
            try:
                report = await asyncio.to_thread(store.model_registry)
            except (ConfigurationStoreError, AiPortConfigurationError, OSError, ValueError):
                rows.append(f"<tr><th scope='row'>{_safe(title)}</th>"
                            "<td colspan='7'>Saved role data unavailable. Nothing was changed.</td></tr>")
                continue
            for entry in report["roles"]:
                shape = entry["dimensions"]
                if entry["source_dimensions"] is not None:
                    shape = f"{entry['source_dimensions']} source → {shape} stored"
                contract = entry["expected_model"] + "; " + entry["output"]
                if shape is not None:
                    contract += f" ({shape})"
                status = "Configured in saved settings" if entry["configured"] else "Not configured"
                if entry["assignment"] == "legacy_shared_detection" and entry["configured"]:
                    status += "; shares the detection assignment"
                selected = entry["model"] or ("Server-managed; not reported" if entry["configured"] else "—")
                cells = (title, self._ROLE_LABELS.get(entry["role"], entry["role"]), status,
                         selected, entry["backend"] or "—", contract, entry["selection_id"] or "—",
                         policies[entry["change_policy"]])
                rows.append("<tr>" + "".join(f"<td>{_safe(cell)}</td>" for cell in cells) + "</tr>")
        return _page("Saved model role registry", "<h1 id='role-registry'>Saved model role registry</h1>"
                     "<p><a href='/'>Settings</a></p><p>Read-only, schema version 1. Saved assignments "
                     "are not active-runtime status. This read sends no provider request, reads no "
                     "referenced model or key file, and changes no settings.</p>"
                     "<p>Selection IDs version the declared role, model, backend, source and contract. "
                     "They do not verify server-managed weights, modality, quality, or vector "
                     "compatibility. Capability and weights are not checked by this read. Catalog "
                     "availability and a synthetic API test are separate, limited evidence.</p>"
                     "<div class='scroll' role='region' aria-labelledby='role-registry' tabindex='0'>"
                     "<table><tr><th scope='col'>Profile</th><th scope='col'>Role</th>"
                     "<th scope='col'>Saved binding</th><th scope='col'>Saved model</th>"
                     "<th scope='col'>Provider/backend</th>"
                     "<th scope='col'>Role contract</th><th scope='col'>Selection ID</th>"
                     "<th scope='col'>Change boundary</th></tr>" + "".join(rows) + "</table></div>")

    async def _rollout_plan(self) -> tuple[dict, dict]:
        report = await self.inventory_loader()
        rollout = await asyncio.to_thread(load_rollout_config, self.rollout_path)
        plan, _ = await asyncio.to_thread(compute_rollout, rollout, report)
        return rollout, plan

    async def data_retention(self, request: web.Request) -> web.Response:
        """Read-only data-flow and retention table (#5): categories and counts only."""
        if self._session(request) is None:
            raise web.HTTPSeeOther("/login")
        status = await asyncio.to_thread(live_status, self.aikey_state_dir, self.search_backups_dir)
        labels = {"manual": "Manual only (not scheduled)", "none": "No expiry",
                  "per_job": "Removed after each job", "not_stored": "Not stored locally",
                  "on_admission": "Rollover on startup/admission (not a timer)",
                  "size_rotation": "Size rotation (not time expiry)",
                  "process": "Volatile process buffers"}

        def counts(value):
            if value is None:
                return "not measured or not configured"
            return ", ".join(f"{_safe(key.replace('_', ' '))}: "
                             f"{_safe(item if item is not None else 'unavailable')}"
                             for key, item in value.items())
        rows = "".join(
            "<tr>" + "".join(f"<td>{_safe(cell)}</td>" for cell in (
                row["category"], row["local"], row["protect"] or "No Protect copy",
                row["destination"], row["default"],
                labels[row["expiry"]] + " · " + row["detail"],
                "Controller decides Protect's copy (needs_evidence)" if row["controller_decides"]
                else "Local only")) + f"<td>{counts(status[row['key']])}</td>"
            f"<td>{_safe(row['owner_decision'])}</td></tr>" for row in ROWS)
        body = ("<h1>Data flow and retention</h1><p><a href='/'>Settings</a></p>"
                "<p class='muted'>Read-only. Shows data categories, how each expires today and "
                "counts; never names, text, embeddings or paths. Nothing is deleted from this "
                "page, and no retention period is chosen here.</p>"
                "<p>Provider settings show the configured destination before a role is changed. "
                "Local/private endpoints do not prove local processing or provider deletion. "
                "A provider's retention terms depend on its account and service; they are not "
                "verified here. File permissions are not encryption. Protect, model servers, "
                "host logs, snapshots and backups require separate deletion/expiry checks.</p>"
                "<div class='scroll'><table><tr><th>Data</th><th>Local copy</th><th>Protect copy</th>"
                "<th>Other destinations</th><th>Product defaults (not current activation)</th>"
                "<th>Current expiry</th><th>Retention owner</th><th>Live count</th>"
                "<th>Operator decision</th></tr>" + rows + "</table></div>")
        return _page("Data flow and retention", body)

    def _audit(self, action: str, result: str, profile: str | None = None) -> None:
        if self.audit is not None:
            self.audit.record(action, result, profile=profile)

    async def audit_page(self, request: web.Request) -> web.Response:
        """Read-only view of recent administrative actions (#13)."""
        if self._session(request) is None:
            raise web.HTTPSeeOther("/login")
        if self.audit is None:
            return _page("Audit log", "<h1>Audit log</h1><p><a href='/'>Settings</a></p>"
                         "<p class='muted'>No audit log is configured for this control site.</p>")
        rows = "".join(
            "<tr>" + "".join(f"<td>{_safe(cell)}</td>" for cell in (
                time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(entry["at"])),
                entry["action"].replace("_", " "), entry.get("profile", "—"), entry["result"]))
            + "</tr>" for entry in self.audit.recent(50)) or "<tr><td colspan='4'>No entries</td></tr>"
        failures = (f"<p class='error'>{self.audit.failures} audit entries could not be written "
                    "since this site started.</p>" if self.audit.failures else "")
        return _page("Audit log", "<h1>Audit log</h1><p><a href='/'>Settings</a></p>"
                     "<p class='muted'>Newest first. Records the action, profile and result only; "
                     "never passwords, keys, paths, settings or network addresses.</p>" + failures
                     + "<table><tr><th>Time</th><th>Action</th><th>Profile</th><th>Result</th></tr>"
                     + rows + "</table>")

    async def caption_preflight_page(self, request: web.Request) -> web.Response:
        """Read-only continuous-caption preflight (#12): reason codes and counts only."""
        if self._session(request) is None:
            raise web.HTTPSeeOther("/login")

        def run():
            path = self.aikey.path
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
                raise ValueError("config")
            return caption_preflight(self.aikey_state_dir, json.loads(path.read_text()),
                                     check_health=False)
        try:
            report = await asyncio.to_thread(run)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return _page("Caption preflight", "<h1>Caption preflight</h1><p><a href='/'>Settings</a></p>"
                         "<p class='error'>The preflight could not read the AI Key state "
                         "(reason: state_unreadable). Nothing was changed.</p>")
        scope, permits = report["scope"], report["permits"]
        budget, ledger = report["budget"], report["ledger"]

        def rows(pairs):
            return "".join(f"<tr><th>{_safe(k)}</th><td>{_safe(v)}</td></tr>" for k, v in pairs)
        blockers = "".join(f"<tr><td><code>{_safe(code)}</code></td><td>{_safe(text)}</td></tr>"
                           for code, text in zip(report["blocker_codes"], report["blockers"]))
        body = ("<h1>Continuous caption preflight</h1><p><a href='/'>Settings</a> · "
                "<a href='/caption-preflight'>Refresh</a></p>"
                "<p class='muted'>Read-only. Reason codes and counts only; no camera, event or job "
                "identifiers, media, captions or credentials. Nothing is enabled or changed here.</p>"
                f"<p><strong>Ready: {'yes' if report['ready'] else 'no'}</strong></p>"
                "<h2>Blockers</h2><table><tr><th>Code</th><th>Detail</th></tr>"
                + (blockers or "<tr><td colspan='2'>None</td></tr>") + "</table>"
                "<h2>Scope</h2><table>" + rows([
                    ("Continuous mode configured", "yes" if scope["continuous_configured"] else "no"),
                    ("One-use scopes", scope["one_use_scopes"]),
                    ("Camera model policies", scope["camera_models_policy"]),
                    ("One-use permits (consumed / total)", f"{permits['consumed']} / {permits['total']}")])
                + "</table><h2>Global caption budget</h2><table>" + rows([
                    ("Budget journal present", "yes" if budget.get("journal") else "no"),
                    ("Captions in the last hour", budget.get("last_hour", "unavailable")),
                    ("Remaining this hour", budget.get("remaining", "unavailable"))])
                + "</table><h2>Job journal</h2><table>" + rows([
                    ("Entries / cap", f"{ledger['entries']} / {ledger['cap']} ({ledger['percent']}%)"),
                    ("Due for rollover", ledger["due_for_rollover"]),
                    ("Uncertain callbacks awaiting review", ledger["uncertain_callbacks"]),
                    ("Days to full at the uncertain rate",
                     ledger["days_to_full_at_uncertain_rate"] if ledger["days_to_full_at_uncertain_rate"]
                     is not None else "not growing")])
                + "</table><p class='muted'>Key health is not read by the control site; run "
                  "<code>python -m aikey.caption_preflight --health</code> for it.</p>")
        return _page("Caption preflight", body)

    def _backup_profiles(self) -> dict:
        profiles = {"aikey": ("aikey", self.aikey_state_dir)}
        for profile, store in self.aiports.items():
            profiles[profile] = ("aiport", store.path.parent)
        return profiles

    async def state_backup_page(self, request: web.Request) -> web.Response:
        """Read-only backup/restore preflight (#13): counts and reason codes only."""
        if self._session(request) is None:
            raise web.HTTPSeeOther("/login")
        report = await asyncio.to_thread(backup_inventory, self._backup_profiles())
        rows = []
        for label, item in report["profiles"].items():
            if not item.get("readable"):
                counts = "unreadable"
            else:
                counts = (f"required {item['required_present']}/{item['required']} · secrets "
                          f"{item['secrets_present']}/{item['secret_references']} · journals "
                          f"{item['journals_present']} · sensitive {item['sensitive_present']}")
            codes = ", ".join(f"<code>{_safe(code)}</code>" for code in item["problems"]) or "none"
            rows.append(f"<tr><td>{_safe(label)}</td><td>{_safe(item['kind'])}</td>"
                        f"<td>{counts}</td><td>{codes}</td></tr>")
        body = ("<h1>Backup and restore preflight</h1><p><a href='/'>Settings</a></p>"
                "<p class='muted'>Read-only. Lists whether each profile's config, adopted identity, "
                "TLS key, controller trust, referenced secrets and journals are in place for a safe "
                "backup. Counts and reason codes only; nothing is exported, restored or changed.</p>"
                f"<p><strong>Ready for backup: {'yes' if report['ready_for_backup'] else 'no'}</strong></p>"
                "<table><tr><th>Profile</th><th>Kind</th><th>Present</th><th>Problems</th></tr>"
                + "".join(rows) + "</table>"
                "<p class='muted'>A candidate backup is checked with "
                "<code>aikey.state_backup_preflight.validate</code> for identity, stale revision, "
                "missing secrets, partial archive and cross-slot contamination.</p>")
        return _page("Backup preflight", body)

    async def index_migration(self, request: web.Request) -> web.Response:
        cookie = self._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        if self.search_state_dir is None:
            raise web.HTTPNotFound()
        live_rows = None
        if self.search_live_rows is not None:
            try:
                live_rows = await self.search_live_rows()
            except Exception:            # counts are optional; the plan says so
                live_rows = None
        plan = await asyncio.to_thread(migration_plan, self.search_state_dir,
                                       backups_dir=self.search_backups_dir, live_rows=live_rows)
        if self._session(request) is None:
            raise web.HTTPSeeOther("/login")
        csrf = self.security.csrf_token(cookie)
        live, backup, rollback = plan["live"], plan["backup"], plan["rollback"]
        rows = []
        for g in plan["generations"]:
            choice = g.get("cancel", {})
            form = "—"
            if choice.get("available") and csrf is not None:
                form = ("<form method='post' action='/index-migration/cancel'>"
                        f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                        f"<input type='hidden' name='generation' value='{_safe(g['generation'])}'>"
                        f"<input type='hidden' name='journal_revision' value='{_safe(choice['journal_revision'])}'>"
                        f"<input type='hidden' name='profile_revision' value='{_safe(choice['profile_revision'])}'>"
                        "<label><input type='checkbox' name='confirm' value='cancel' required>"
                        f"Cancel staged migration {_safe(g['generation'])}; keep the active index and staged data</label>"
                        "<button type='submit'>Cancel staged migration</button></form>")
            rows.append("<tr>" + "".join(f"<td>{_safe(cell)}</td>" for cell in (
                g["generation"], g.get("state"), g.get("from", "—"), g.get("to", "—"),
                "—" if "coverage" not in g else
                f"{g['coverage']['embedded']} embedded, {g['coverage']['no_source']} without source"
                + (f", {g['coverage']['percent']}% of {g['coverage']['live_rows']}"
                   if g["coverage"].get("percent") is not None else ""),
                "; ".join(g["problems"]) or "—")) + "<td>" + form + "</td></tr>")
        rows = "".join(rows) or "<tr><td colspan='7'>No staged generation</td></tr>"
        steps = "".join(f"<li><strong>{_safe(s['status'])}</strong> · {_safe(s['step'])}"
                        + (f"<br><span class='muted'>{_safe(s['detail'])}</span>" if s["detail"] else "")
                        + "</li>" for s in plan["steps"])
        reasons = "".join(f"<li>{_safe(reason)}</li>" for reason in plan["apply"]["reasons"])
        body = ("<h1>Search index migration</h1><p><a href='/'>Settings</a> · "
                "<a href='/index-migration'>Refresh</a></p>"
                "<p class='muted'>Viewing this plan changes nothing. No index is staged, cut over, "
                "rolled back or restarted here. The separate cancellation form only retires an "
                "idle staged migration. A running writer must finish or be stopped separately.</p>"
                "<section><h2>Live index</h2><p>Encoder revision "
                f"<strong>{_safe(live['revision'] or 'not pinned')}</strong> · rows "
                f"{_safe(live['rows'] if live['rows'] is not None else 'unavailable')}</p></section>"
                "<h2>Generations</h2><div class='table-scroll' role='region' "
                "aria-label='Index migration generations' tabindex='0'><table><tr>"
                "<th scope='col'>Generation</th><th scope='col'>State</th><th scope='col'>From</th>"
                "<th scope='col'>To</th><th scope='col'>Coverage</th><th scope='col'>Problems</th>"
                "<th scope='col'>Cancel staging</th></tr>" + rows + "</table></div>"
                "<h2>Backup and rollback</h2><ul>"
                f"<li>Latest backup: {_safe(backup['latest'] or 'none')}"
                + (f" · {_safe(backup.get('rows'))} rows · scratch restore recorded {'yes' if backup['verified'] else 'no'}"
                   if backup["latest"] else "") + "</li>"
                "<li>Recorded verification is historical. This page does not rehash or restore "
                "the dump; verify it again before using it for recovery.</li>"
                f"<li>Rollback ready: {'yes' if rollback['ready'] else 'no'}"
                + (f" — {_safe(rollback['reason'])}" if rollback["reason"] else "")
                + f"<br><span class='muted'>{_safe(rollback['note'])}</span></li></ul>"
                "<h2>Dry-run plan</h2><ol>" + steps + "</ol>"
                "<h2>Apply is unavailable</h2><ul>" + reasons + "</ul>"
                + ("<p>No migration exists: this plan is a no-op.</p>" if plan["no_op"] else ""))
        return _page("Search index migration", body)

    async def cancel_index_migration(self, request: web.Request) -> web.Response:
        fields = await request.post()
        if self._session(request, mutate=True, csrf=fields.get("csrf")) is None:
            self._audit("index_cancel", "forbidden")
            raise web.HTTPForbidden()
        if self.search_state_dir is None:
            raise web.HTTPNotFound()
        expected = {"csrf", "generation", "journal_revision", "profile_revision", "confirm"}
        if (set(fields) != expected or any(len(fields.getall(k)) != 1 for k in expected)
                or fields["confirm"] != "cancel"):
            self._audit("index_cancel", "invalid")
            raise web.HTTPBadRequest(text="Invalid migration cancellation")
        try:
            await asyncio.to_thread(cancel_generation, self.search_state_dir, fields["generation"],
                                    expected_journal=fields["journal_revision"],
                                    expected_profile=fields["profile_revision"])
        except RebuildOutcomeUncertain:
            self._audit("index_cancel", "uncertain")
            return _page("Cancellation outcome uncertain", "<h1>Cancellation outcome uncertain</h1>"
                         "<p>Reload and reconcile the saved generation before retrying. "
                         "No automatic retry, index rollback or data deletion was started.</p>", status=409)
        except (RebuildError, OSError, ValueError):
            self._audit("index_cancel", "refused")
            return _page("Migration changed or busy", "<h1>Migration changed or busy</h1>"
                         "<p>Refresh the plan. Active, changed or completed migrations cannot be "
                         "cancelled from this view.</p>", status=409)
        self._audit("index_cancel", "cancelled")
        raise web.HTTPSeeOther("/index-migration")

    async def rollout_page(self, request: web.Request) -> web.Response:
        cookie = self._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        if self.inventory_loader is None or self.rollout_path is None:
            raise web.HTTPNotFound()
        csrf = self.security.csrf_token(cookie)
        try:
            _, plan = await self._rollout_plan()
        except RolloutOutcomeUncertain:
            return _page("Rollout outcome uncertain", "<h1>Rollout outcome uncertain</h1>"
                         "<p class='error'>Some local files may have changed. Reload and reconcile "
                         "the saved files and private recovery record before retrying. "
                         "No automatic retry was started.</p>")
        except (InventoryError, RolloutError, OSError, ValueError):
            return _page("AI Port rollout unavailable", "<h1>AI Port rollout unavailable</h1>"
                         "<p class='error'>Check Protect connectivity, the rollout file and "
                         "the slot state directories.</p><p><a href='/'>Settings</a></p>")
        view = public_rollout(plan)
        rows = "".join(
            "<tr>" + "".join(f"<td>{_safe(cell)}</td>" for cell in (
                label, slot["target"], slot["load"],
                "yes" if slot.get("observed") else "no — unchanged",
                ", ".join(slot["keep"]) or "—", ", ".join(slot["add"]) or "—",
                ", ".join(slot["remove"]) or "—",
                ", ".join(slot["awaiting_pairing"]) or "—")) + "</tr>"
            for label, slot in sorted(view["slots"].items()))
        rows += "".join(
            "<tr>" + "".join(f"<td>{_safe(cell)}</td>" for cell in (
                slot["label"] + " (new)", slot["target"], slot["load"], "not deployed",
                "—", ", ".join(slot["add"]), "—", "—")) + "</tr>"
            for slot in view["new_slots"])
        local = [a for a in view["actions"] if a["automated"]]
        manual = [a for a in view["actions"] if not a["automated"]]

        def items(actions: list[dict]) -> str:
            def detail(action: dict) -> str:
                if action["kind"] != "compose_add_service":
                    return ""
                # The exact service appended to the private Compose copy.
                return ("<br><span class='muted'>" + _safe(
                    f"service {action['service']} · {action['ipv4_address']} · "
                    f"{action['mac_address']} · state {action['bind_source']}")
                    + "</span><pre>" + _safe(action["block"]) + "</pre>")
            return "".join("<li>" + _safe(" · ".join(str(a[k]) for k in (
                "kind", "slot", "camera", "target") if a.get(k))) + detail(a) + "</li>"
                for a in actions) or "<li>None</li>"
        basis = "".join(
            f"<li>{_safe(slot['label'])}: capacity {_safe(slot['capacity_basis'])}</li>"
            for slot in view["new_slots"] if "capacity_basis" in slot)
        form = ("<form method='post' action='/aiport-rollout'>"
                f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                f"<input type='hidden' name='revision' value='{_safe(view['revision'])}'>"
                "<button>Apply local changes</button></form>" if local else
                "<p>No local change is needed: this plan is a no-op.</p>")
        body = ("<h1>AI Port rollout</h1><p><a href='/'>Settings</a> · "
                "<a href='/aiport-rollout'>Refresh</a></p>"
                "<p class='muted'>Derived from Protect's eligible cameras and each slot's "
                "pinned health. Paired cameras and slot identities never move. Nothing is "
                "paired, adopted, uploaded or restarted from here.</p>"
                "<table><tr><th>Slot</th><th>Target</th><th>Load</th><th>Observed</th>"
                "<th>Keep</th><th>Add</th><th>Remove</th><th>Awaiting pairing</th></tr>"
                + rows + "</table>" + (f"<ul>{basis}</ul>" if basis else "")
                + "<h2>Applied by this page (local files only)</h2><ul>"
                + items(local) + "</ul><h2>Requires you</h2><ul>" + items(manual) + "</ul>"
                + form + f"<p class='muted'>Plan revision {_safe(view['revision'][:12])}</p>")
        return _page("AI Port rollout", body)

    async def rollout_apply(self, request: web.Request) -> web.Response:
        fields = await request.post()
        if self._session(request, mutate=True, csrf=fields.get("csrf")) is None:
            self._audit("rollout_apply", "forbidden")
            raise web.HTTPForbidden()
        if self.inventory_loader is None or self.rollout_path is None:
            raise web.HTTPNotFound()
        try:
            report = await self.inventory_loader()
            # The revision is compared under the rollout lock, after re-reading
            # the slot files, so two tabs cannot both apply one plan (#13).
            await asyncio.to_thread(apply_if_current, self.rollout_path, report,
                                    fields.get("revision"))
        except PlanChanged:
            self._audit("rollout_apply", "plan_changed")
            return _page("Plan changed", "<h1>The plan changed</h1><p>Review the "
                         "refreshed plan before applying.</p>"
                         "<p><a href='/aiport-rollout'>Review</a></p>")
        except RolloutOutcomeUncertain:
            self._audit("rollout_apply", "uncertain")
            return _page("Rollout outcome uncertain", "<h1>Rollout outcome uncertain</h1>"
                         "<p class='error'>Some local files may have changed. Reload and reconcile "
                         "the saved files and private recovery record before retrying. "
                         "No automatic retry was started.</p>")
        except (InventoryError, RolloutError, OSError, ValueError):
            self._audit("rollout_apply", "refused")
            return _page("Rollout not applied", "<h1>Rollout not applied</h1>"
                         "<p class='error'>No pairing was changed. Check the slot files.</p>")
        self._audit("rollout_apply", "applied")
        raise web.HTTPSeeOther("/aiport-rollout")

    async def cameras(self, request: web.Request) -> web.Response:
        if self._session(request) is None:
            raise web.HTTPSeeOther("/login")
        if self.inventory_loader is None:
            raise web.HTTPNotFound()
        try:
            report = await self.inventory_loader()
            configured = {}
            for profile, store in self.aiports.items():
                title = ("this AI Port" if profile == "aiport" else
                         "AI Port " + profile.partition(":")[2])
                for mac in store.configured_camera_macs():
                    configured.setdefault(mac, []).append(title)
            if (not isinstance(report, dict)
                    or report.get("schema") != "aikey-camera-preflight/1"
                    or not isinstance(report.get("cameras"), list)):
                raise InventoryError("Invalid camera inventory")
        except (InventoryError, AiPortConfigurationError):
            return _page("Camera inventory unavailable", "<h1>Camera inventory unavailable</h1>"
                         "<p class='error'>Check Protect connectivity and the private trust files.</p>"
                         "<p><a href='/'>Back to settings</a></p>")
        try:
            capacity = plan_ai_ports(report, camera_scope="legacy-and-g3-g5")
        except AiPortPlanError:
            capacity_note = ("<p>AI Port capacity: needs_evidence. Check the camera IDs, "
                             "source types and recording limits before assigning slots.</p>")
        else:
            capacity_note = (f"<p>AI Port capacity plan: "
                             f"{capacity['ai_port_instances_required']} instance(s) for "
                             f"{capacity['selected_camera_count']} connected target camera(s). "
                             "This plans capacity only; create each identity and pair its cameras "
                             "in Protect.</p>")
        rows = []
        target_count = 0
        configured_count = 0
        for camera in sorted(report["cameras"], key=lambda item: (
                (item.get("name") or "").casefold(), item.get("id") or "")):
            state = camera.get("state")
            model = camera.get("model") or "Unknown"
            legacy = camera.get("processing_class") == "legacy_ingress_needed"
            g3_g5 = isinstance(model, str) and _G3_G5_MODEL.match(model) is not None
            target = state == "CONNECTED" and (legacy or g3_g5)
            assigned = configured.get(camera.get("mac"), ())
            allowed = bool(assigned)
            target_count += int(target)
            configured_count += int(allowed)
            scope = ("Offline" if state != "CONNECTED" else
                     "Legacy camera target" if legacy else
                     "G3–G5 target" if g3_g5 else "Outside AI Port target scope")
            cells = (camera.get("name") or "Unnamed", model, state or "Unknown", scope,
                     "Configured for " + ", ".join(assigned) if allowed
                     else ("Not configured on this AI Port" if len(self.aiports) == 1
                           else "Not configured on any AI Port"),
                     ", ".join(camera.get("smart_detect_types") or ()) or "None")
            rows.append("<tr>" + "".join(f"<td>{_safe(cell)}</td>" for cell in cells)
                        + "</tr>")
        fetched = datetime.fromtimestamp(report["fetched_at"], timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC")
        body = ("<h1>Protect cameras</h1><p><a href='/'>Settings</a> · "
                "<a href='/cameras'>Refresh</a></p>"
                f"<p>Protect {_safe(report['protect_version'])} · Fetched {_safe(fetched)}</p>"
                f"<p>{len(rows)} cameras · {target_count} connected legacy / G3–G5 targets · "
                f"{configured_count} configured across {len(self.aiports)} AI Port "
                "instance(s).</p>"
                + capacity_note +
                "<p class='muted'>Protect pairing and stream health are separate. "
                "Configured means the camera is in a local instance allowlist. "
                "Protect-reported smart types may reflect AI Port processing.</p>"
                "<div class='scroll'><table><thead><tr><th>Camera</th><th>Model</th>"
                "<th>State</th><th>AI Port scope</th><th>Instance</th>"
                "<th>Protect-reported smart types</th></tr></thead><tbody>"
                + "".join(rows) + "</tbody></table></div>")
        return _page("Protect cameras", body)

    def _same_host(self, request: web.Request) -> bool:
        origin = urlsplit(self.origin)
        return request.host == origin.netloc and request.scheme == origin.scheme

    @property
    def cookie_name(self) -> str:
        return SESSION_COOKIE_NAME if self.origin.startswith("https://") else _COOKIE

    def _session(self, request: web.Request, *, mutate: bool = False,
                 csrf: str | None = None) -> str | None:
        if not self._same_host(request):
            return None
        cookie = request.cookies.get(self.cookie_name, "")
        origin = request.headers.get("Origin", "") if mutate else self.origin
        decision = self.security.authorize(
            "POST" if mutate else "GET", origin, cookie, csrf)
        if decision.allowed and mutate and request.path != "/logout":
            now = self._mutation_clock()
            while self._mutation_times and self._mutation_times[0] <= now - _MUTATION_WINDOW:
                self._mutation_times.popleft()
            if len(self._mutation_times) >= _MUTATION_LIMIT:
                if not self._mutation_limited_logged:
                    self._audit("mutation", "rate_limited")
                    self._mutation_limited_logged = True
                retry = max(1, math.ceil(self._mutation_times[0] + _MUTATION_WINDOW - now))
                page = _page("Too many changes", "<h1>Too many changes</h1>"
                             "<p>No configuration change or provider request was started by "
                             "this request. Wait before trying again. You can still view "
                             "settings and sign out.</p>")
                page.headers["Retry-After"] = str(retry)
                raise web.HTTPTooManyRequests(text=page.text, headers=page.headers)
            self._mutation_times.append(now)
            self._mutation_limited_logged = False
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
            self._audit("login", decision.reason if decision.reason else "refused")
            detail = ("The active session limit is reached. Sign out another session or wait for expiry."
                      if decision.reason == "session_capacity" else "Check the password and try again.")
            return _page("Sign in failed", "<h1>Sign in failed</h1>"
                         f"<p class='error'>{detail}</p>"
                         "<p><a href='/login'>Back to sign in</a></p>")
        self._audit("login", "signed_in")
        response = web.HTTPSeeOther("/")
        response.set_cookie(self.cookie_name, decision.credentials.cookie, httponly=True,
                            secure=self.origin.startswith("https://"), samesite="Strict", path="/",
                            max_age=self.security.session_ttl)
        raise response

    async def index(self, request: web.Request) -> web.Response:
        cookie = self._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        csrf = self.security.csrf_token(cookie)
        if csrf is None:
            raise web.HTTPSeeOther("/login")
        try:
            key = self.aikey.snapshot()
            ports = {profile: store.snapshot()
                     for profile, store in self.aiports.items()}
        except (ConfigurationStoreError, AiPortConfigurationError):
            return _page("Configuration unavailable", "<h1>Configuration unavailable</h1>"
                         "<p class='error'>Check the private profile config files.</p>")
        inference = key.configuration["inference"]
        key_configured = bool(inference.get("api_key_file", {}).get("configured"))
        notice = ("<p class='notice'>Settings saved. Restart the affected service to apply them.</p>"
                  if request.query.get("saved") == "1" else
                  "<p class='notice'>The previous provider settings were restored in the selected "
                  "profile. Restart the affected service to apply them. The site changes only "
                  "the configuration files mounted or supplied to it.</p>"
                  if request.query.get("rolledback") == "1" else "")
        undo_form = self._undo_form("aikey", "AI Key", self.aikey.provider_rollback_status(), csrf)
        body = ("<div class='row'><div><h1>Local AI processor</h1>"
                "<p class='muted'>Provider and model settings for configured Protect profiles.</p>"
                "</div><form method='post' action='/logout'>"
                f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                "<button>Sign out</button></form></div>" + notice +
                "<p>Changes are saved with a revision check. They take effect after a service "
                "restart. Pairings and device identities stay in place.</p>"
                + ("<p><a href='/cameras'>View current Protect cameras</a></p>"
                   if self.inventory_loader is not None else "")
                + ("<p><a href='/index-migration'>Search index migration (read-only)</a></p>"
                   if self.search_state_dir is not None else "")
                + "<p><a href='/data-retention'>Data flow and retention (read-only)</a> · "
                  "<a href='/audit'>Audit log</a> · "
                  "<a href='/caption-preflight'>Caption preflight</a> · "
                  "<a href='/state-backup-preflight'>Backup preflight</a> · "
                  "<a href='/maintenance'>Maintenance operations</a> · "
                  "<a href='/model-registry'>Saved model role registry</a></p>"
                + ("<p><a href='/aiport-permissions'>Remove saved AI Port face, sound or speech permissions</a></p>"
                   if ports else "")
                + ("<p><a href='/plate-cameras'>Plate camera permissions</a></p>" if ports else "")
                + ("<p><a href='/camera-thresholds'>Camera score thresholds</a></p>" if ports else "")
                + ("<p><a href='/ocr-provider'>Configure separate plate OCR provider</a></p>" if ports else "")
                + undo_form + self._role_models_section(csrf) + self._speech_form(key, csrf)
                + "<div class='grid'>")
        body += self._provider_form("AI Key", "aikey", key.revision, inference,
                                    key_configured, csrf)
        for profile, port in ports.items():
            title = ("AI Port" if profile == "aiport" else
                     "AI Port " + profile.partition(":")[2])
            if port.camera_count >= 1:
                current = {"provider": port.provider or "openai",
                           "model": port.model or "gpt-6-luna",
                           "base_url": port.base_url or "https://api.openai.com/v1",
                           "allow_remote": port.allow_remote if port.provider else True,
                           "allow_insecure_http": port.allow_insecure_http,
                           "max_output_tokens": port.max_output_tokens or 256,
                           "threshold": port.threshold if port.threshold is not None else .8,
                           "smart_types": port.smart_types or ("person",),
                           "max_requests_per_hour": port.max_requests_per_hour}
                body += self._provider_form(title, profile, port.revision, current,
                                            port.key_configured, csrf,
                                            camera_count=port.camera_count,
                                            backend=port.backend)
                body += self._undo_form(profile, title, self.aiports[profile].provider_rollback_status(),
                                        csrf)
            else:
                body += (f"<section><h2>{_safe(title)}</h2><p>No camera pool is configured. "
                         "Provider settings become available after configuring a camera stream."
                         "</p></section>")
        if not ports:
            body += ("<section><h2>AI Ports</h2><p>No AI Port configuration is attached. "
                     "AI Key settings are available independently. Add an existing AI Port "
                     "configuration when starting the site to manage that profile; this page "
                     "does not create, adopt or pair a device.</p></section>")
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
        detail = (f"<p>{camera_count} configured camera slot{'s' if camera_count != 1 else ''}. "
                  f"Configured detector: {_safe(backend or 'off')}.</p>"
                  if camera_count else "")
        if profile.startswith("aiport") and backend != "vision_api":
            detail += ("<p class='muted'>Saving provider settings switches AI Port "
                       "to API detection after its next restart. The current local "
                       "detector keeps running until then.</p>")
        if profile.startswith("aiport"):
            detail += ("<p class='muted'>Any saved per-camera score gates and local "
                       "score-server routing are kept; this form does not edit them.</p>")
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
        if profile.startswith("aiport"):
            form += (f"<label>Score threshold<input name='threshold' type='number' "
                     f"min='0.01' max='1' step='0.01' value='{_safe(current['threshold'])}'></label>"
                     "<div class='checks'><p>Detection classes</p>" + "".join(
                         f"<label><input name='smart_types' type='checkbox' value='{kind}'"
                         f"{' checked' if kind in current['smart_types'] else ''}>{kind.title()}</label>"
                         for kind in ("person", "vehicle", "animal", "package")) + "</div>"
                     "<label>Optional API request cost cap per camera per hour "
                     "(empty = no cap)<input name='max_requests_per_hour' type='number' "
                     "min='2' max='3600' value='"
                     + ("" if current.get("max_requests_per_hour") is None
                        else _safe(current["max_requests_per_hour"])) + "'></label>")
        return form + "<button>Save provider settings</button></form></section>"

    async def save_provider(self, request: web.Request) -> web.Response:
        fields = await request.post()
        cookie = self._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            self._audit("provider_save", "forbidden")
            raise web.HTTPForbidden()
        profile = fields.get("profile")
        if profile != "aikey" and profile not in self.aiports:
            self._audit("provider_save", "unknown_profile")
            raise web.HTTPBadRequest(text="Unknown profile")
        is_port = profile != "aikey"
        store = self.aiports[profile] if is_port else self.aikey
        created_key: Path | None = None
        committed = False
        apply_started = False
        try:
            provider = fields["provider"]
            base_url = fields["base_url"].strip()
            selection: dict = {
                "provider": provider, "model": fields["model"].strip(),
                "base_url": base_url, "allow_remote": "allow_remote" in fields,
                "allow_insecure_http": "allow_insecure_http" in fields,
                "max_output_tokens": int(fields["max_output_tokens"]),
            }
            if is_port:
                selection.update({
                    "threshold": float(fields["threshold"]),
                    "smart_types": fields.getall("smart_types", []),
                    "max_requests_per_hour": (
                        int(fields["max_requests_per_hour"])
                        if fields.get("max_requests_per_hour", "").strip() else None),
                })
            key_value = fields.get("api_key", "")
            if key_value:
                if (not isinstance(key_value, str) or not 8 <= len(key_value) <= 4096
                        or any(ch.isspace() for ch in key_value)):
                    raise ValueError
                host_state = (self.aikey.path.parent if profile == "aikey"
                              else store.path.parent)
                runtime_state = (Path(load_config(self.aikey.path)["runtime"]["state_dir"])
                                 if profile == "aikey" else store.runtime_state_dir)
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
            apply_started = True
            result = (store.apply_inference(fields["revision"], selection)
                      if profile == "aikey" else store.apply(fields["revision"], selection))
            committed = True
            if result.revision != preview.resulting_revision:
                raise ValueError
        except (KeyError, ValueError, TypeError, OSError, ConfigurationStoreError,
                AiPortConfigurationError, RevisionConflict, AiPortRevisionConflict) as exc:
            certain_rejection = self._failed_save_key(created_key, committed, apply_started, exc)
            # Once replacement was attempted, the config or a later archived revision
            # may already reference this key. A failed acknowledgement is not rollback.
            title = "Settings not saved" if certain_rejection else "Save outcome uncertain"
            self._audit("provider_save", "rejected" if certain_rejection else "uncertain", profile)
            return _page(title, f"<h1>{title}</h1>"
                         "<p class='error'>Check the provider, model, key and request limits. "
                         "Reload settings before retrying; an interrupted save may already be active.</p>"
                         "<p><a href='/'>Back to settings</a></p>")
        self._audit("provider_save", "saved", profile)
        raise web.HTTPSeeOther("/?saved=1")

    @staticmethod
    def _failed_save_key(created_key: Path | None, committed: bool,
                         apply_started: bool, error: Exception) -> bool:
        """Delete only a key proved unused by this attempt, never an uncertain one.

        A concurrent later save may archive a just-committed key before its
        failed acknowledgement is handled. Reading live config alone cannot
        establish that the key is no longer needed for exact rollback.
        """
        certain_rejection = not apply_started or isinstance(error, (RevisionConflict, AiPortRevisionConflict))
        if created_key is not None and not committed and certain_rejection:
            try:
                created_key.unlink(missing_ok=True)
            except OSError:
                pass
        return certain_rejection

    @staticmethod
    def _undo_form(profile: str, title: str, status: dict, csrf: str) -> str:
        """One revision-checked undo button; carries no path, key or setting."""
        if not status.get("available"):
            return ""
        return ("<form method='post' action='/provider/rollback'>"
                f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                f"<input type='hidden' name='profile' value='{_safe(profile)}'>"
                f"<input type='hidden' name='revision' value='{_safe(status['revision'])}'>"
                f"<button>Roll back the last {_safe(title)} provider change</button></form>")

    _ROLE_LABELS = {"caption": "Captions (vision)", "speech": "Speech to text",
                    "ocr": "AI Port plate text (OCR)",
                    "reverification": "Local reverification", "recognition": "Face recognition",
                    "reidentification": "Person Re-ID", "reranking": "Search reranking",
                    "enhancement": "Image enhancement",
                    "detection": "AI Port detection", "search_image_text": "Search image/text encoder",
                    "text_embedding": "Text embeddings"}
    _ROLE_REASONS = {"index_migration_required": "Pinned by the search index; change it through the "
                     "index migration", "role_not_configured": "Not configured",
                     "vision_provider_not_configured": "No vision provider configured"}

    @staticmethod
    def _speech_form(snapshot, csrf: str) -> str:
        current = snapshot.configuration.get("speech_to_text")
        if not isinstance(current, dict):
            return ("<section><h2>Speech provider</h2><p>Not configured. Per-camera audio "
                    "permission must be configured before choosing a speech provider.</p></section>")
        options = "".join(f"<option value='{name}'"
                          f"{' selected' if current.get('provider') == name else ''}>{label}</option>"
                          for name, label in (("openai-compatible", "Local compatible API"),
                                               ("openai", "OpenAI")))
        configured = bool(current.get("api_key_file", {}).get("configured"))
        return ("<section><h2>Speech provider</h2>"
                "<p><a href='/speech-permissions'>Remove saved audio permissions</a></p>"
                "<p>Only already permitted cameras are affected. "
                "Their audio is sent to the API base URL below. Choosing OpenAI sends audio "
                "to OpenAI and may incur charges; it does not use the caption budget. "
                "Saving does not test the provider or enable more cameras.</p>"
                f"<p>Speech key reference configured: {'yes' if configured else 'no'}.</p>"
                "<form method='post' action='/speech-provider'>"
                f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                f"<input type='hidden' name='revision' value='{_safe(snapshot.revision)}'>"
                f"<label>Speech provider<select name='provider'>{options}</select></label>"
                f"<label>Speech model ID<input name='model' required maxlength='128' "
                f"value='{_safe(current.get('model'))}'></label>"
                f"<label>Speech API base URL<input name='base_url' required maxlength='512' "
                f"value='{_safe(current.get('base_url'))}'></label>"
                "<label>New speech API key<input name='api_key' type='password' "
                "autocomplete='new-password'></label>"
                f"<label>Language (optional two-letter code)<input name='language' maxlength='2' "
                f"value='{_safe(current.get('language'))}'></label>"
                "<label><input name='local_network' type='checkbox' "
                + ("checked" if current.get("local_network") else "") +
                ">Allow a private-network speech server (HTTP is unencrypted)</label>"
                "<label><input name='allow_cloud_audio' type='checkbox'>I allow audio from "
                "the already permitted cameras to be sent to OpenAI if selected</label>"
                "<button>Save speech provider</button></form></section>")

    def _speech_camera_tokens(self, cookie: str, snapshot) -> dict[str, str] | None:
        # Opaque selection labels, NOT independent authorization. Session,
        # origin, CSRF, revision and the server's current allowlist remain required.
        speech = snapshot.configuration.get("speech_to_text") or {}
        csrf = self.security.csrf_token(cookie)
        if csrf is None:
            return None
        key = csrf.encode()
        return {hmac.new(key, f"speech-camera:{snapshot.revision}:{camera}".encode(),
                         hashlib.sha256).hexdigest(): camera
                for camera in speech.get("camera_ids", [])}

    async def speech_permissions(self, request: web.Request) -> web.Response:
        cookie = self._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        snapshot = await asyncio.to_thread(self.aikey.snapshot)
        current = snapshot.configuration.get("speech_to_text")
        warning = ("<p class='notice'><strong>Saved settings only.</strong> Audio may still be "
                   "processed by the running worker until you restart the AI Key. This page "
                   "does not stop live work, delete recordings or transcripts, remove backups "
                   "or revoke provider keys.</p>")
        body = "<h1>Remove saved audio permissions</h1><p><a href='/'>Settings</a></p>" + warning
        if not isinstance(current, dict):
            return _page("Saved audio permissions", body +
                         "<p>Speech is not configured in the saved settings. "
                         "Running-worker status is not verified here.</p>")
        labels = {}
        if self.inventory_loader is not None:
            try:
                report = await self.inventory_loader()
                if (isinstance(report, dict) and report.get("schema") == "aikey-camera-preflight/1"
                        and isinstance(report.get("cameras"), list) and len(report["cameras"]) <= 256):
                    for camera in report["cameras"]:
                        if not isinstance(camera, dict):
                            continue
                        identifier, name = camera.get("id"), camera.get("name")
                        if (isinstance(identifier, str) and isinstance(name, str) and 0 < len(name) <= 128
                                and identifier not in name):
                            labels[identifier] = name
            except Exception:
                pass  # An inventory outage must never block disabling saved audio.
        tokens = self._speech_camera_tokens(cookie, snapshot)
        csrf = self.security.csrf_token(cookie)
        if tokens is None or csrf is None:
            raise web.HTTPSeeOther("/login")
        destination = current.get("base_url") or (
            "https://api.openai.com/v1" if current.get("provider") == "openai" else "not configured")
        hidden = (f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                  f"<input type='hidden' name='revision' value='{_safe(snapshot.revision)}'>")
        confirmation = ("<label><input type='checkbox' name='confirm' required>"
                        "I understand this changes saved permissions only; I must restart "
                        "the AI Key to change running audio processing.</label>")
        body += (f"<p>{len(tokens)} cameras currently permitted in saved settings. "
                 f"Destination: {_safe(destination)}. No audio is sent by this page.</p>")
        if all(camera in labels for camera in tokens.values()):
            checks = "".join(f"<label><input type='checkbox' name='keep_camera' value='{token}' checked>"
                             f"Keep permission: {_safe(labels[camera])}</label>"
                             for token, camera in tokens.items())
            body += ("<form method='post' action='/speech-permissions'>" + hidden +
                     "<input type='hidden' name='mode' value='restrict'>"
                     "<p>Uncheck cameras to remove their saved audio permission. "
                     "Unchecking every camera disables the saved speech role.</p>" + checks +
                     confirmation + "<button>Save reduced permissions</button></form>")
        else:
            body += ("<p class='muted'>Camera names are unavailable. Restore camera inventory "
                     "to select individual cameras, or disable all saved audio permissions below.</p>")
        body += ("<form method='post' action='/speech-permissions'>" + hidden +
                 "<input type='hidden' name='mode' value='disable_all'>" + confirmation +
                 "<button>Disable all saved speech permissions</button></form>"
                 "<p>These controls cannot add cameras, change other features or replace provider "
                 "settings. Private key files and configuration history remain for recovery.</p>")
        return _page("Saved audio permissions", body)

    async def restrict_speech_permissions(self, request: web.Request) -> web.Response:
        fields = await request.post()
        cookie = self._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            self._audit("speech_permissions", "forbidden", "aikey")
            raise web.HTTPForbidden()
        apply_started = False
        try:
            if (set(fields) - {"csrf", "revision", "mode", "confirm", "keep_camera"}
                    or any(len(fields.getall(key)) != 1 for key in ("csrf", "revision", "mode", "confirm"))
                    or fields.get("confirm") != "on" or fields.get("mode") not in {"restrict", "disable_all"}):
                raise ValueError
            snapshot = await asyncio.to_thread(self.aikey.snapshot)
            if snapshot.revision != fields["revision"]:
                raise RevisionConflict("Saved configuration changed")
            tokens = self._speech_camera_tokens(cookie, snapshot)
            if tokens is None:
                self._audit("speech_permissions", "forbidden", "aikey")
                raise web.HTTPForbidden()
            selected = fields.getall("keep_camera", [])
            if (len(selected) > 32 or any(not isinstance(token, str) or token not in tokens for token in selected)
                    or len(set(selected)) != len(selected)
                    or fields["mode"] == "disable_all" and selected):
                raise ValueError
            retained = [tokens[token] for token in selected]
            preview = await asyncio.to_thread(self.aikey.preview_speech_restriction,
                                              fields["revision"], retained)
            apply_started = True
            result = await asyncio.to_thread(self.aikey.restrict_speech, fields["revision"], retained)
            if result.revision != preview.resulting_revision:
                raise ConfigurationStoreError("Saved revision could not be verified")
        except (KeyError, ValueError, TypeError, OSError, ConfigurationStoreError) as exc:
            certain = not apply_started or isinstance(exc, (RevisionConflict, UnsafeConfigurationChange))
            self._audit("speech_permissions", "rejected" if certain else "uncertain", "aikey")
            title = "Audio permissions not saved" if certain else "Audio permission save outcome uncertain"
            return _page(title, f"<h1>{title}</h1><p class='error'>Reload the current settings "
                         "before retrying. Check the selection, confirmation and configuration revision. "
                         "The running worker has not been stopped.</p>"
                         "<p><a href='/speech-permissions'>Saved audio permissions</a></p>")
        self._audit("speech_permissions", "saved", "aikey")
        raise web.HTTPSeeOther("/speech-permissions?saved=1")

    async def inventory_snapshot(self):
        """One validated two-second GET observation; never used for write admission."""
        from .admin_inventory import inventory_view
        async with self._inventory_snapshot_lock:
            loader = self.inventory_loader
            if loader is not self._inventory_source:
                self._inventory_source = loader
                self._inventory_snapshot = None
                self._inventory_failure_until = 0.0
            if loader is None:
                raise InventoryError("Inventory is not configured")
            now = self._inventory_clock()
            if now < self._inventory_failure_until:
                raise InventoryError("Inventory observation is unavailable")
            try:
                if self._inventory_snapshot is not None and self._inventory_snapshot[0] > now:
                    report = deepcopy(self._inventory_snapshot[1])
                    inventory_view(report, {})  # Recheck version/completeness/age, even on a cache hit.
                    return report
                self._inventory_snapshot = None  # Never fall back to expired good data.
                async with asyncio.timeout(12):
                    report = deepcopy(await loader())
                if loader is not self.inventory_loader:
                    raise InventoryError("Inventory source changed")
                inventory_view(report, {})
            except (InventoryError, OSError, ValueError, ClientError, TimeoutError):
                self._inventory_snapshot = None
                self._inventory_failure_until = self._inventory_clock() + 2
                raise  # Cancellation is not a cached failure and always releases the lock.
            self._inventory_snapshot = (self._inventory_clock() + 2, report)
            return deepcopy(report)

    def _port_permission_tokens(self, cookie: str, profile: str, feature: str,
                                revision: str, cameras: tuple[str, ...]) -> dict[str, str] | None:
        csrf = self.security.csrf_token(cookie)
        if csrf is None:
            return None
        return {hmac.new(csrf.encode(),
                         f"port-permission:{profile}:{feature}:{revision}:{camera}".encode(),
                         hashlib.sha256).hexdigest(): camera for camera in cameras}

    async def aiport_permissions(self, request: web.Request) -> web.Response:
        cookie = self._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        labels = {}
        if self.aiports and self.inventory_loader is not None:
            try:
                report = await self.inventory_loader()
                if (isinstance(report, dict) and report.get("schema") == "aikey-camera-preflight/1"
                        and isinstance(report.get("cameras"), list) and len(report["cameras"]) <= 256):
                    for camera in report["cameras"]:
                        if not isinstance(camera, dict):
                            continue
                        mac, name = camera.get("mac"), camera.get("name")
                        if (isinstance(mac, str) and isinstance(name, str) and 0 < len(name) <= 128
                                and mac.casefold() not in re.sub(r"[:.\-]", "", name).casefold()):
                            labels[mac] = name
            except Exception:
                pass  # Missing names must not prevent disabling a saved role.
        body = ("<h1>Remove saved AI Port recognition and audio permissions</h1><p><a href='/'>Settings</a></p>"
                "<p><strong>Saved settings only.</strong> Face, sound or speech-presence processing may continue "
                "until you restart the selected AI Port. No processor is stopped here. "
                "This page cannot add cameras, change models, delete recordings/templates or "
                "revoke keys. Other detection and audio permissions are unchanged. "
                "Sound classification requires the same camera's audio permission: remove "
                "its saved sound permission first. No other role is changed automatically, "
                "including AI Key speech-to-text.</p>")
        for profile, store in self.aiports.items():
            instance_title = "AI Port" if profile == "aiport" else "AI Port " + profile.partition(":")[2]
            for feature, title in (("face", "Face recognition"), ("sound", "Sound classification"),
                                   ("speech", "Audio capture / Speech presence")):
                body += f"<section><h2>{_safe(instance_title)} — {title}</h2>"
                try:
                    revision, cameras = await asyncio.to_thread(store.recognition_permissions, feature)
                except (AiPortConfigurationError, OSError):
                    body += "<p>Saved permissions are unavailable. Reload after checking this profile.</p></section>"
                    continue
                if cameras is None:
                    body += "<p>Not configured in saved settings. Running status is not checked.</p></section>"
                    continue
                tokens = self._port_permission_tokens(cookie, profile, feature, revision, cameras)
                csrf = self.security.csrf_token(cookie)
                if tokens is None or csrf is None:
                    raise web.HTTPSeeOther("/login")
                hidden = (f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                          f"<input type='hidden' name='profile' value='{_safe(profile)}'>"
                          f"<input type='hidden' name='feature' value='{feature}'>"
                          f"<input type='hidden' name='revision' value='{revision}'>")
                confirm = ("<label><input type='checkbox' name='confirm' required>"
                           "I understand that these are saved permissions only; I must restart "
                           "this AI Port to change its running processing.</label>")
                body += f"<p>{len(cameras)} permitted cameras in saved settings.</p>"
                names = [labels.get(camera) for camera in cameras]
                if all(names) and len(set(names)) == len(names):
                    checks = "".join(
                        f"<label><input type='checkbox' name='keep_camera' value='{token}' checked>"
                        f"Keep permission: {_safe(labels[camera])}</label>" for token, camera in tokens.items())
                    body += ("<form method='post' action='/aiport-permissions'>" + hidden +
                             "<input type='hidden' name='mode' value='restrict'>"
                             "<p>Uncheck cameras to remove permission. Unchecking every camera "
                             "disables this saved role only.</p>" + checks + confirm +
                             "<button>Save reduced permissions</button></form>")
                else:
                    body += ("<p>Unambiguous camera names are unavailable. The whole saved role "
                             "can still be disabled below.</p>")
                body += ("<form method='post' action='/aiport-permissions'>" + hidden +
                         "<input type='hidden' name='mode' value='disable_all'>" + confirm +
                         f"<button>Disable all saved {feature} permissions</button></form></section>")
        if not self.aiports:
            body += "<p>No AI Port profiles are attached.</p>"
        if self._session(request) is None:
            raise web.HTTPSeeOther("/login")
        return _page("Saved AI Port permissions", body)

    async def restrict_aiport_permissions(self, request: web.Request) -> web.Response:
        fields = await request.post()
        cookie = self._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            self._audit("aiport_permissions", "forbidden")
            raise web.HTTPForbidden()
        apply_started = False
        profile = None
        try:
            scalar = {"csrf", "profile", "feature", "revision", "mode", "confirm"}
            if (set(fields) - scalar - {"keep_camera"}
                    or any(len(fields.getall(key)) != 1 for key in scalar)
                    or fields["confirm"] != "on" or fields["mode"] not in {"restrict", "disable_all"}):
                raise ValueError
            selected_profile, feature = fields["profile"], fields["feature"]
            if (not isinstance(selected_profile, str) or selected_profile not in self.aiports
                    or not isinstance(feature, str) or feature not in {"face", "sound", "speech"}):
                raise ValueError
            profile = selected_profile
            store = self.aiports[profile]
            revision, cameras = await asyncio.to_thread(store.recognition_permissions, feature)
            if (not isinstance(fields["revision"], str) or revision != fields["revision"]
                    or cameras is None):
                raise AiPortRevisionConflict("aiport_config_changed")
            tokens = self._port_permission_tokens(cookie, profile, feature, revision, cameras)
            if tokens is None:
                self._audit("aiport_permissions", "forbidden", profile)
                raise web.HTTPForbidden()
            selected = fields.getall("keep_camera", [])
            if (len(selected) > len(cameras) or any(not isinstance(t, str) or t not in tokens for t in selected)
                    or len(set(selected)) != len(selected) or fields["mode"] == "disable_all" and selected):
                raise ValueError
            retained = [tokens[token] for token in selected]
            preview = await asyncio.to_thread(store.preview_recognition_restriction, revision, feature, retained)
            if self._session(request, mutate=True, csrf=fields["csrf"]) is None:
                self._audit("aiport_permissions", "forbidden", profile)
                raise web.HTTPForbidden()
            apply_started = True
            result = await asyncio.to_thread(store.restrict_recognition, revision, feature, retained)
            if result.revision != preview.resulting_revision:
                raise AiPortConfigurationError("aiport_config_write_uncertain")
        except (KeyError, ValueError, TypeError, OSError, AiPortConfigurationError) as exc:
            certain = not apply_started or isinstance(exc, AiPortRevisionConflict)
            self._audit("aiport_permissions", "rejected" if certain else "uncertain", profile)
            title = "AI Port permissions not saved" if certain else "AI Port permission save outcome uncertain"
            hint = ("<p>Remove saved sound permissions for the affected cameras before removing "
                    "their audio permission. No other role was changed automatically.</p>"
                    if not apply_started and str(exc) == "aiport_audio_required_by_sound" else "")
            return _page(title, f"<h1>{title}</h1><p>Reload current settings before retrying. "
                         "Check the selection, confirmation and revision. The running processor "
                         "has not been stopped.</p>" + hint +
                         "<p><a href='/aiport-permissions'>Saved permissions</a></p>")
        self._audit("aiport_permissions", "saved_" + feature, profile)
        raise web.HTTPSeeOther("/aiport-permissions?saved=1")

    async def save_speech_provider(self, request: web.Request) -> web.Response:
        fields = await request.post()
        if self._session(request, mutate=True, csrf=fields.get("csrf")) is None:
            self._audit("speech_provider", "forbidden", "aikey")
            raise web.HTTPForbidden()
        created_key = None
        committed = False
        apply_started = False
        try:
            selection = {"provider": fields["provider"], "model": fields["model"].strip(),
                         "base_url": fields["base_url"].strip(),
                         "local_network": "local_network" in fields}
            if selection["provider"] == "openai" and "allow_cloud_audio" not in fields:
                raise ValueError
            language = fields.get("language", "").strip()
            if language:
                selection["language"] = language
            key = fields.get("api_key", "")
            if key:
                if not 8 <= len(key) <= 4096 or any(ch.isspace() for ch in key):
                    raise ValueError
                runtime_state = Path(load_config(self.aikey.path)["runtime"]["state_dir"])
                key_name = f"speech-key-{secrets.token_hex(8)}"
                created_key = self.aikey.path.parent / key_name
                selection["api_key_file"] = str(runtime_state / key_name)
            preview = await asyncio.to_thread(self.aikey.preview_speech, fields["revision"], selection)
            if created_key is not None:
                _private_directory(self.aikey.path.parent)
                atomic_private(created_key, key + "\n")
            apply_started = True
            result = await asyncio.to_thread(self.aikey.apply_speech, fields["revision"], selection)
            committed = True
            if result.revision != preview.resulting_revision:
                raise ValueError
        except (KeyError, ValueError, TypeError, OSError, ConfigurationStoreError) as exc:
            certain_rejection = self._failed_save_key(created_key, committed, apply_started, exc)
            title = "Speech settings not saved" if certain_rejection else "Speech save outcome uncertain"
            self._audit("speech_provider", "rejected" if certain_rejection else "uncertain", "aikey")
            return _page(title, f"<h1>{title}</h1>"
                         "<p class='error'>Check the speech provider, endpoint, key, audio "
                         "permission and current revision. Reload settings before retrying.</p>"
                         "<p><a href='/'>Back to settings</a></p>")
        self._audit("speech_provider", "saved", "aikey")
        raise web.HTTPSeeOther("/?saved=1")
    def _role_models_section(self, csrf: str) -> str:
        """Per-role models, one revision-checked form per editable role (#17)."""
        rows = []
        stores = [("aikey", "AI Key", self.aikey)] + [
            (profile, "this AI Port" if profile == "aiport" else "AI Port " + profile.partition(":")[2],
             store) for profile, store in self.aiports.items()]
        for profile, title, store in stores:
            try:
                revision = store.snapshot().revision
                roles = store.role_models()
            except (ConfigurationStoreError, AiPortConfigurationError):
                rows.append(f"<tr><td>{_safe(title)}</td><td colspan='3'>unavailable</td></tr>")
                continue
            for role, info in roles.items():
                label = self._ROLE_LABELS.get(role, role)
                if info["editable"]:
                    control = ("<form method='post' action='/role-model-preview'>"
                               f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                               f"<input type='hidden' name='profile' value='{_safe(profile)}'>"
                               f"<input type='hidden' name='role' value='{_safe(role)}'>"
                               f"<input type='hidden' name='revision' value='{_safe(revision)}'>"
                               f"<input name='model' required maxlength='128' "
                               f"value='{_safe(info['model'] or '')}' aria-label='{_safe(label)} model'>"
                               "<button>Preview model change</button></form>")
                    try:
                        saved, _, _ = store.catalog_configuration(revision, role)
                        destination = saved.get("base_url") or DEFAULT_BASE_URLS.get(saved.get("provider"))
                        control += ("<form method='post' action='/model-catalog'>"
                                    f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
                                    f"<input type='hidden' name='profile' value='{_safe(profile)}'>"
                                    f"<input type='hidden' name='role' value='{_safe(role)}'>"
                                    f"<input type='hidden' name='revision' value='{_safe(revision)}'>"
                                    f"<p>Catalog destination: {_safe(destination)}. Uses only this role's "
                                    "credential. Sends no camera media and runs no inference.</p>"
                                    "<button>List available model IDs</button></form>")
                        control += self._probe_form(csrf, profile, role, revision,
                                                    destination, saved.get("model"))
                    except (ConfigurationStoreError, AiPortConfigurationError):
                        pass
                else:
                    control = _safe(self._ROLE_REASONS.get(info["reason"], info["reason"]))
                rows.append(f"<tr><td>{_safe(title)}</td><td>{_safe(label)}</td>"
                            f"<td>{_safe(info['model'] or '—')}</td><td>{control}</td></tr>")
        return ("<section><h2 id='models-by-role'>Models by role</h2><p class='muted'>Changes only the model of one role; "
                "the provider, endpoint and key stay as they are. Search encoders are pinned by the "
                "index.</p><div class='scroll' role='region' aria-labelledby='models-by-role' tabindex='0'>"
                "<table><tr><th scope='col'>Profile</th><th scope='col'>Role</th>"
                "<th scope='col'>Model</th><th scope='col'>Change</th></tr>"
                + "".join(rows) + "</table></div></section>")

    def _probe_form(self, csrf, profile, role, revision, destination, model) -> str:
        if role not in {"caption", "detection", "speech", "ocr"}:
            return "<p>A synthetic test for this role is not available.</p>"
        if not isinstance(model, str) or not model.strip():
            return "<p>Save a model before running a synthetic test.</p>"
        grant = self._probe_grants.issue(profile, role, revision, csrf)
        if grant is None:
            return "<p>Synthetic test forms are temporarily full. Reload settings later.</p>"
        hidden = {"csrf": csrf, "profile": profile, "role": role,
                  "revision": revision, "grant": grant}
        medium = "one generated second of silent WAV audio" if role == "speech" else "one generated image"
        kind = "speech" if role == "speech" else "vision"
        return ("<form method='post' action='/provider-probe'>"
                + "".join(f"<input type='hidden' name='{name}' value='{_safe(value)}'>"
                          for name, value in hidden.items())
                + f"<p>Synthetic test destination: {_safe(destination)}; model: {_safe(model)}. "
                  f"Sends {medium}, not camera media. Tests one API path, not general "
                  "modality, model identity or recognition quality.</p>"
                  "<label><input type='checkbox' name='acknowledge_cost' value='yes' required> "
                  "Send this synthetic test. Provider charges may apply, separately from camera "
                  "caption/request caps.</label>"
                  f"<button>Run one synthetic {kind} test</button></form>")

    async def provider_probe(self, request: web.Request) -> web.Response:
        fields = await request.post()
        if self._session(request, mutate=True, csrf=fields.get("csrf")) is None:
            self._audit("provider_probe", "forbidden")
            raise web.HTTPForbidden()
        expected = {"csrf", "profile", "role", "revision", "grant", "acknowledge_cost"}
        if (set(fields) != expected or any(len(fields.getall(k)) != 1 for k in expected)
                or not all(isinstance(fields[k], str) for k in expected)
                or fields["acknowledge_cost"] != "yes" or fields["role"] not in {"caption", "detection", "speech", "ocr"}):
            self._audit("provider_probe", "invalid_request")
            raise web.HTTPBadRequest(text="A fresh synthetic-test form and cost acknowledgement are required")
        profile, role, revision = fields["profile"], fields["role"], fields["revision"]
        store = self.aikey if profile == "aikey" else self.aiports.get(profile)
        if store is None:
            raise web.HTTPBadRequest(text="Unknown profile")
        try:
            selection, runtime_state, lab = await asyncio.to_thread(
                store.catalog_configuration, revision, role)
        except (ConfigurationStoreError, AiPortConfigurationError):
            self._audit("provider_probe", "stale", profile)
            raise web.HTTPConflict(text="The configured role changed. Reload settings before testing.")
        if not isinstance(selection.get("model"), str) or not selection["model"].strip():
            raise web.HTTPBadRequest(text="Save a model before testing")
        try:
            self._probe_grants.begin(fields["grant"], profile, role, revision, fields["csrf"])
        except ProbeError as exc:
            if str(exc) == "probe_busy":
                self._audit("provider_probe", "busy", profile)
                raise web.HTTPTooManyRequests(headers={"Retry-After": "30"})
            self._audit("provider_probe", "grant_refused", profile)
            raise web.HTTPConflict(text="The synthetic-test form expired or was already used. Reload settings.")
        self._audit("provider_probe", "started", profile)
        try:
            provider = await asyncio.to_thread(catalog_provider, selection, speech=role == "speech",
                host_state=store.path.parent, runtime_state=runtime_state, lab=lab)
            report = await (probe_speech(provider) if role == "speech" else probe_vision(provider))
        except (CatalogError, OSError):
            self._audit("provider_probe", "preflight_unavailable", profile)
            return _page("Synthetic test unavailable", "<h1>Synthetic test unavailable</h1>"
                         "<p>The configured role or credential could not be verified. No provider "
                         "request was started and nothing changed. Reload settings for a new test.</p>"
                         "<p><a href='/'>Settings</a></p>")
        except ProbeError as exc:
            if str(exc) == "probe_not_started":
                self._audit("provider_probe", "preflight_unavailable", profile)
                return _page("Synthetic test unavailable", "<h1>Synthetic test unavailable</h1>"
                             "<p>The test was refused before HTTP. No provider request was sent and "
                             "nothing changed. Reload settings for a new explicit test.</p>"
                             "<p><a href='/'>Settings</a></p>")
            self._audit("provider_probe", "unavailable", profile)
            return _page("Synthetic test unavailable", "<h1>Synthetic test unavailable</h1>"
                         "<p>The bounded test could not be verified. A provider request may have been "
                         "charged. Nothing was activated or changed; no automatic retry occurred. "
                         "Reload settings for a new explicit test.</p><p><a href='/'>Settings</a></p>")
        finally:
            self._probe_grants.finish()
        if self._session(request) is None:
            raise web.HTTPForbidden()
        try:
            current_revision = (await asyncio.to_thread(store.snapshot)).revision
        except (ConfigurationStoreError, AiPortConfigurationError, OSError):
            current_revision = None
        if current_revision != revision:
            self._audit("provider_probe", "stale", profile)
            return _page("Synthetic result is stale", "<h1>Synthetic result is stale</h1>"
                         "<p>The settings changed during the test. Its result is not current. "
                         "A request may have been charged; no settings were activated or changed.</p>"
                         "<p><a href='/'>Settings</a></p>")
        matched = report["synthetic_silence_matched" if role == "speech" else "synthetic_image_matched"]
        self._audit("provider_probe", "matched" if matched else "mismatch", profile)
        if role == "speech":
            return _page("Synthetic speech test", "<h1>Synthetic speech test</h1>"
                         + f"<p>Profile: {_safe(profile)}; role: speech; "
                           f"model: {_safe(selection['model'])}; destination: {_safe(provider.base_url)}.</p>"
                         + ("<p>The API returned no parsed speech for generated silence.</p>" if matched else
                            "<p>The API returned parsed speech for generated silence; no transcript is shown.</p>")
                         + f"<p>Elapsed: {report['elapsed_ms']} ms. This tests one synthetic silence API path, "
                           "not voice recognition, general modality, internal model identity or quality. "
                           "No model was selected, no audio permission or configuration changed, "
                           "and no camera audio was sent. Provider charges may apply.</p>"
                           "<p><a href='/'>Settings</a></p>")
        return _page("Synthetic vision test", "<h1>Synthetic vision test</h1>"
                     + f"<p>Profile: {_safe(profile)}; role: {_safe(role)}; "
                       f"model: {_safe(selection['model'])}; destination: {_safe(provider.base_url)}.</p>"
                     + ("<p>The generated image answer matched.</p>" if matched else
                        "<p>The API replied, but the generated image answer did not match.</p>")
                     + f"<p>Elapsed: {report['elapsed_ms']} ms. This tests one synthetic API path, "
                       "not general modality, internal model identity or recognition quality. "
                       "No model was selected and no configuration changed. No camera media was sent. "
                       "Provider charges may apply.</p><p><a href='/'>Settings</a></p>")

    async def model_catalog(self, request: web.Request) -> web.Response:
        fields = await request.post()
        if self._session(request, mutate=True, csrf=fields.get("csrf")) is None:
            self._audit("model_catalog", "forbidden")
            raise web.HTTPForbidden()
        expected_fields = {"csrf", "profile", "role", "revision"}
        if set(fields) != expected_fields or any(len(fields.getall(k)) != 1 for k in expected_fields):
            self._audit("model_catalog", "invalid_request")
            raise web.HTTPBadRequest(text="Unexpected catalog fields")
        profile, role = fields.get("profile"), fields.get("role")
        if not isinstance(profile, str) or not isinstance(role, str):
            self._audit("model_catalog", "unknown_profile")
            raise web.HTTPBadRequest(text="Unknown profile or role")
        store = self.aikey if profile == "aikey" else self.aiports.get(profile)
        if store is None:
            self._audit("model_catalog", "unknown_profile")
            raise web.HTTPBadRequest(text="Unknown profile")
        try:
            selection, runtime_state, lab = await asyncio.to_thread(
                store.catalog_configuration, fields.get("revision"), role)
            if self._catalog_lock.locked() or time.monotonic() < self._catalog_next_at:
                self._audit("model_catalog", "busy", profile)
                raise web.HTTPTooManyRequests(headers={"Retry-After": "5"})
            async with self._catalog_lock:
                self._catalog_next_at = time.monotonic() + 5
                provider = await asyncio.to_thread(
                    catalog_provider, selection, speech=role == "speech", host_state=store.path.parent,
                    runtime_state=runtime_state, lab=lab)
                report = await fetch_catalog(provider)
            # A catalog bound to an older configuration is not offered as current.
            current = await asyncio.to_thread(store.snapshot)
            if current.revision != fields.get("revision"):
                raise CatalogError("catalog_configuration_changed")
        except (CatalogError, OSError, ConfigurationStoreError, AiPortConfigurationError):
            self._audit("model_catalog", "unavailable", profile)
            return _page("Model catalog unavailable", "<h1>Model catalog unavailable</h1>"
                         "<p>The configured role, revision, credential or catalog could not be "
                         "verified. This request changed no settings. Reload before retrying; manual "
                         "model IDs remain available.</p><p><a href='/'>Back to settings</a></p>")
        rows = "".join("<tr><td>" + _safe(model["id"]) + "</td><td>"
                       + ("unknown" if model["image_input_advertised"] is None else
                          "yes" if model["image_input_advertised"] else "no")
                       + "</td><td>" + ("yes" if model["provider_reported_remote"] is True else
                                       "no marker" if model["provider_reported_remote"] is False else "unknown")
                       + "</td></tr>" for model in report["models"])
        self._audit("model_catalog", "listed", profile)
        return _page("Available model IDs", "<h1>Available model IDs</h1>"
                     f"<p>Profile: {_safe(profile)}; role: {_safe(role)}. "
                     f"Catalog destination: {_safe(provider.base_url)}.</p>"
                     "<p>This is a complete bounded API catalog, not a capability or quality test. "
                     "Advertised capabilities and remote markers are provider claims. No inference "
                     "ran and no model was selected. Copy an ID into Models by role if appropriate; "
                     "a successful listing does not establish image/audio support or zero cost.</p>"
                     "<div class='table-scroll'><table><tr><th>Model ID</th>"
                     "<th>Provider advertises image input</th><th>Provider marks remote</th></tr>"
                     + rows + "</table></div>"
                     + ("<p>The API returned no models.</p>" if not rows else "")
                     + "<p><a href='/'>Back to settings</a></p>")

    async def preview_role_model(self, request: web.Request) -> web.Response:
        fields = await request.post()
        cookie = self._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            self._audit("role_model", "forbidden")
            raise web.HTTPForbidden()
        profile = fields.get("profile", "")
        store = self.aikey if profile == "aikey" else self.aiports.get(profile)
        if store is None:
            self._audit("role_model", "unknown_profile")
            raise web.HTTPBadRequest(text="Unknown profile")
        try:
            p = await asyncio.to_thread(store.preview_role_model, fields.get("revision"),
                                        fields.get("role"), fields.get("model"))
        except (ConfigurationStoreError, AiPortConfigurationError, OSError):
            self._audit("role_model", "preview_refused", profile)
            return _page("Model preview unavailable", "<h1>Model preview unavailable</h1>"
                         "<p>The role, model, revision or settings could not be validated. "
                         "Nothing has been saved or tested. Reload settings before trying again.</p>"
                         "<p><a href='/'>Back to settings</a></p>")
        # No save has happened; an expired/revoked session gets no confirmation form.
        if self._session(request) is None:
            self._audit("role_model", "forbidden", profile)
            raise web.HTTPForbidden()
        self._audit("role_model", "previewed", profile)
        changes = ("<p>Current jobs keep their current runtime and model. A controlled processor "
                   "restart is required to use the saved model for new jobs. This page does not "
                   "drain, cancel or restart a processor.</p>" if p.restart_required else
                   "<p>This model is already selected in saved settings. This request adds no "
                   "settings change or restart requirement. It does not check whether prior "
                   "changes still need activation.</p>")
        hidden = {"csrf": fields["csrf"], "profile": profile, "role": p.role,
                  "revision": p.current_revision, "model": p.proposed_model}
        form = "".join(f"<input type='hidden' name='{name}' value='{_safe(value)}'>"
                       for name, value in hidden.items())
        return _page("Preview model change", "<h1>Preview model change</h1>"
                     "<p>Nothing has been saved. No model request was sent.</p>"
                     f"<p>Role: {_safe(p.role)}. Saved model: {_safe(p.previous_model or '—')} "
                     f"→ {_safe(p.proposed_model)}.</p>" + changes +
                     "<p>Search index unchanged. Provider, destination, credentials and camera "
                     "permissions are unchanged. Changing an encoder requires a separate index "
                     "migration. No past events are reprocessed.</p><p>Model capability has not been tested. A valid model ID "
                     "or catalog entry is not proof of modality, quality or versioned weights.</p>"
                     "<p>Confirmation rechecks the same configuration revision; an intervening "
                     "save requires a fresh preview.</p><form method='post' action='/role-model'>"
                     + form + "<button>Confirm model change</button></form>"
                     "<p><a href='/'>Cancel and return to settings</a></p>")

    async def save_role_model(self, request: web.Request) -> web.Response:
        fields = await request.post()
        if self._session(request, mutate=True, csrf=fields.get("csrf")) is None:
            self._audit("role_model", "forbidden")
            raise web.HTTPForbidden()
        profile = fields.get("profile", "")
        store = self.aikey if profile == "aikey" else self.aiports.get(profile)
        if store is None:
            self._audit("role_model", "unknown_profile")
            raise web.HTTPBadRequest(text="Unknown profile")
        try:
            await asyncio.to_thread(store.apply_role_model, fields.get("revision"),
                                    fields.get("role"), fields.get("model"))
        except (ConfigurationStoreError, RevisionConflict, AiPortConfigurationError,
                AiPortRevisionConflict, OSError) as exc:
            certain = isinstance(exc, (RevisionConflict, AiPortRevisionConflict,
                                       UnsafeConfigurationChange))
            self._audit("role_model", "rejected" if certain else "uncertain", profile)
            title = "Model not saved" if certain else "Model save outcome uncertain"
            detail = ("The role, model name or revision was not accepted. Nothing was changed."
                      if certain else "The model change may already be saved. Reload settings "
                      "and check the current model before taking another action. Do not "
                      "automatically retry or undo this request.")
            return _page(title, f"<h1>{title}</h1><p class='error'>{detail}</p>"
                         "<p><a href='/'>Back to settings</a></p>")
        self._audit("role_model", "saved", profile)
        raise web.HTTPSeeOther("/?saved=1")

    async def rollback_provider(self, request: web.Request) -> web.Response:
        """Restore one profile's local settings from before its last provider save (#17)."""
        fields = await request.post()
        if self._session(request, mutate=True, csrf=fields.get("csrf")) is None:
            self._audit("provider_rollback", "forbidden")
            raise web.HTTPForbidden()
        profile = fields.get("profile", "aikey")
        store = self.aikey if profile == "aikey" else self.aiports.get(profile)
        if store is None:
            self._audit("provider_rollback", "unknown_profile")
            raise web.HTTPBadRequest(text="Unknown profile")
        try:
            await asyncio.to_thread(store.rollback_provider, fields.get("revision"))
        except (ConfigurationStoreError, RevisionConflict, AiPortConfigurationError,
                AiPortRevisionConflict, OSError) as exc:
            certain = isinstance(exc, (RevisionConflict, AiPortRevisionConflict,
                                       UnsafeConfigurationChange))
            self._audit("provider_rollback", "refused" if certain else "uncertain", profile)
            title = "Rollback not applied" if certain else "Rollback outcome uncertain"
            detail = ("The settings changed or this rollback was not accepted. Nothing was changed."
                      if certain else "The previous provider settings may already be restored. "
                      "Reload settings and check the current provider before taking another "
                      "action. Do not automatically retry or undo this request.")
            return _page(title, f"<h1>{title}</h1><p class='error'>{detail}</p>"
                         "<p><a href='/'>Back to settings</a></p>")
        self._audit("provider_rollback", "rolled_back", profile)
        raise web.HTTPSeeOther("/?rolledback=1")

    async def logout(self, request: web.Request) -> web.Response:
        fields = await request.post()
        cookie = self._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            self._audit("logout", "forbidden")
            raise web.HTTPForbidden()
        self.security.revoke(cookie)
        self._audit("logout", "signed_out")
        response = web.HTTPSeeOther("/login")
        response.del_cookie(self.cookie_name, path="/", httponly=True,
                            secure=self.origin.startswith("https://"), samesite="Strict")
        raise response


def _cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authenticated AI processor control site")
    commands = parser.add_subparsers(dest="command", required=True)
    initial = commands.add_parser("init", help="Set an administrator password locally")
    initial.add_argument("--state-dir", required=True, type=Path)
    initial.add_argument("--generate", action="store_true",
                         help="Generate a private one-time bootstrap password file")
    setup = commands.add_parser("setup", help="Authenticated fresh-directory AI Key preparation; never start/adopt")
    setup.add_argument("--state-dir", required=True, type=Path, help="Existing private administrator state")
    setup.add_argument("--directory", required=True, type=Path, help="New AI Key directory; existing paths refused")
    _listener_arguments(setup)
    run = commands.add_parser("run", help="Serve settings on loopback or explicit direct HTTPS")
    run.add_argument("--state-dir", required=True, type=Path)
    run.add_argument("--aikey-config", required=True, type=Path)
    run.add_argument("--aiport-config", type=Path,
                     help="Optional existing default AI Port configuration; omit for AI Key only")
    run.add_argument("--aiport-runtime-state-dir", type=Path,
                     help="AI Port state path inside its processor container")
    run.add_argument("--aiport-instance", action="append", default=[], metavar="NAME=CONFIG",
                     help="Add another AI Port instance with a private host config path")
    run.add_argument("--runtime-health", action="append", default=[], metavar="PROFILE=HTTPS_URL",
                     help="Explicit own-runtime private HTTPS healthz target; pinned to that profile certificate")
    run.add_argument("--maintenance-bridge", type=Path,
                     help="Host-maintained IPC root: catalog/receipts read-only, requests writable; no host plans")
    _listener_arguments(run)
    run.add_argument("--aiport-rollout", type=Path,
                     help="Private AI Port rollout slot file (needs the inventory settings)")
    run.add_argument("--search-state-dir", type=Path,
                     help="AI Key state directory holding search-profile.json (read-only)")
    run.add_argument("--search-backups", type=Path, help="search_backup output directory (read-only)")
    run.add_argument("--aikey-state-dir", type=Path,
                     help="AI Key state directory, for read-only retention counts")
    run.add_argument("--search-container",
                     help="PostgreSQL container for a read-only live row count")
    run.add_argument("--inventory-controller")
    run.add_argument("--inventory-api-key-file", type=Path)
    run.add_argument("--inventory-web-trust-file", type=Path)
    run.add_argument("--inventory-web-cert-file", type=Path)
    return parser


def _listener_arguments(command):
    command.add_argument("--port", type=int, default=8765)
    command.add_argument("--bind", default="127.0.0.1",
                         help="One literal private/loopback address; non-default requires HTTPS")
    command.add_argument("--origin", help="Exact HTTPS browser origin, including the listener port")
    command.add_argument("--tls-certificate", type=Path, help="Private owned PEM certificate/chain file")
    command.add_argument("--tls-private-key", type=Path, help="Private owned unencrypted PEM key file")


def _live_rows(container: str) -> Callable[[], Awaitable[int]]:
    import subprocess
    from .search_backup import counts

    async def read() -> int:
        value = await asyncio.to_thread(counts, subprocess.run, container)
        return int(value["tables"]["ramDetections"])
    return read


TLS_RELOAD_PROTOCOL = 1


def _reload_admin_tls(site) -> bool:
    """CLI-owned local operation; no HTTP route and no caller-selected paths."""
    try:
        loaded = site.listener.reload_tls()
    except (OSError, ValueError):
        loaded = False
    result = "ready" if loaded else "refused"
    audit = getattr(site, "audit", None)
    try:
        recorded = audit.record("tls_reload", result) if audit is not None else False
    except Exception:
        recorded = False
    print(json.dumps({"admin_tls_reload": result, "generation": site.listener.tls_generation,
                      "audit_recorded": recorded}), flush=True)
    return loaded


async def _run(site: ControlSite, port: int) -> None:
    runner = web.AppRunner(site.app(), access_log=None)
    await runner.setup()
    hup_installed = False
    previous_hup = None
    loop = None
    try:
        listener = web.TCPSite(runner, site.listener.host, port,
                               ssl_context=site.listener.ssl_context)
        await listener.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        if site.listener.ssl_context is not None and hasattr(signal, "SIGHUP"):
            previous_hup = signal.getsignal(signal.SIGHUP)

            def reload_if_running():
                if not stop.is_set():
                    _reload_admin_tls(site)

            loop.add_signal_handler(signal.SIGHUP, reload_if_running)
            hup_installed = True
            print(json.dumps({"admin_tls_reload_protocol": TLS_RELOAD_PROTOCOL}), flush=True)
        await stop.wait()
    finally:
        try:
            await runner.cleanup()
        finally:
            if hup_installed:
                loop.remove_signal_handler(signal.SIGHUP)
                signal.signal(signal.SIGHUP, previous_hup)


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
        if args.command == "setup":
            from .first_run_site import FirstRunSite
            site = FirstRunSite(args.directory,
                signing_key=signing_key, password_record=record, port=args.port,
                bind=args.bind, origin=args.origin, tls_certificate=args.tls_certificate,
                tls_private_key=args.tls_private_key, audit_log=state / "admin-audit.jsonl")
            print(json.dumps({"first_run_site": site.origin, "loopback_only": site.listener.loopback_only,
                              "https": site.listener.ssl_context is not None, "device_started": False}))
            asyncio.run(_run(site, args.port))
            return 0
        inventory_fields = (args.inventory_controller, args.inventory_api_key_file,
                            args.inventory_web_trust_file, args.inventory_web_cert_file)
        if any(value is not None for value in inventory_fields) and not all(
                value is not None for value in inventory_fields):
            raise ValueError("Complete pinned inventory settings are required")
        inventory_loader = (lambda: fetch_inventory(
            args.inventory_controller, api_key_file=args.inventory_api_key_file,
            trust_file=args.inventory_web_trust_file,
            cert_file=args.inventory_web_cert_file)) if all(
                value is not None for value in inventory_fields) else None
        instances = {}
        for assignment in args.aiport_instance:
            name, separator, config_path = assignment.partition("=")
            if not separator or not config_path or name in instances:
                raise ValueError("Invalid AI Port instance assignment")
            instances[name] = Path(config_path)
        runtime_health = {}
        for assignment in args.runtime_health:
            profile, separator, url = assignment.partition("=")
            if not separator or not profile or not url or profile in runtime_health:
                raise ValueError("Invalid runtime health target assignment")
            runtime_health[profile] = url
        site = ControlSite(args.aikey_config, args.aiport_config,
                           signing_key=signing_key, password_record=record, port=args.port,
                           aiport_runtime_state_dir=args.aiport_runtime_state_dir,
                           aiport_instances=instances,
                           runtime_health_urls=runtime_health,
                           maintenance_bridge=args.maintenance_bridge,
                           inventory_loader=inventory_loader,
                           rollout_path=args.aiport_rollout,
                           search_state_dir=args.search_state_dir,
                           search_backups_dir=args.search_backups,
                           aikey_state_dir=args.aikey_state_dir,
                           bind=args.bind, origin=args.origin,
                           tls_certificate=args.tls_certificate, tls_private_key=args.tls_private_key,
                           audit_log=state / "admin-audit.jsonl",
                           search_live_rows=_live_rows(args.search_container)
                           if args.search_container else None)
        site.aikey.snapshot()
        for port_store in site.aiports.values():
            port_store.snapshot()
        print(json.dumps({"control_site": site.origin,
                          "loopback_only": site.listener.loopback_only,
                          "https": site.listener.ssl_context is not None}))
        asyncio.run(_run(site, args.port))
        return 0
    except (OSError, ValueError, ConfigurationStoreError, AiPortConfigurationError) as exc:
        print(f"Control site unavailable: {type(exc).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
