"""Authenticated fresh Port files from one server-selected plan; never start/adopt."""

from __future__ import annotations

import asyncio
from functools import partial
import hashlib
import hmac
import json
import os
from pathlib import Path
import re

from aiohttp import web

from .aiport_candidate import parse_config
from .aiport_instance_state import _slot_address, provision_slot
from .control_site import _page, _private_bytes, _private_directory, _safe
from .first_run_site import FirstRunSite


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate plan field")
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError("Nonfinite plan value")


class FirstRunPortSite(FirstRunSite):
    """Reuse the existing listener/session/CSRF and retained-operation lifecycle."""

    def __init__(
        self, directory: Path, *, plan: Path, slot: int, controller_cert_file: Path, **kwargs
    ):
        requested = Path(directory).expanduser().absolute()
        parent = requested.parent.resolve(strict=True)
        _private_directory(parent)
        self.target_path = parent / requested.name
        if os.path.lexists(self.target_path):
            raise ValueError(
                "First-run directory already exists; fresh Port preparation refuses reuse"
            )
        self.plan_path = Path(plan).expanduser().absolute()
        self.slot = slot
        self.controller_cert_file = Path(controller_cert_file).expanduser().absolute()
        self._prepared_snapshot = None
        # Refuse unsupported/unaddressed selections before opening a listener.
        self._inputs()
        super().__init__(directory, **kwargs)

    def _audit(self, action, result, profile=None):
        # The inherited form authenticator labels Key preparation by default.
        # This separate mode has no Key operations or Key configuration routes.
        super()._audit(action, result, "aiport" if profile == "aikey" else profile)

    def app(self):
        app = web.Application(client_max_size=16384)
        app.router.add_get("/", self.index)
        app.router.add_get("/login", self.login_page)
        app.router.add_post("/login", self.login)
        app.router.add_post("/logout", self.logout)
        app.router.add_post("/create", self.create)
        app.router.add_get("/healthz", self.health)
        app.on_cleanup.append(self._drain_operations)
        return app

    def _inputs(self):
        raw = _private_bytes(self.plan_path, 2 * 1024 * 1024)
        plan = json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)
        address = _slot_address(plan, self.slot)
        certificate = _private_bytes(self.controller_cert_file, 16384)
        digest = hashlib.sha256(b"fresh-port-preparation/1")
        for value in (str(self.target_path).encode(), str(self.slot).encode(), raw, certificate):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        return digest.hexdigest(), plan, address

    def _snapshot(self):
        limits = {
            "config.json": 2 * 1024 * 1024,
            "identity.json": 4096,
            "device.crt": 16384,
            "device.key": 16384,
            "controller-ca.pem": 16384,
        }
        return {
            name: hashlib.sha256(_private_bytes(self.directory / name, limit)).digest()
            for name, limit in limits.items()
        }

    def _prepared(self):
        raw = _private_bytes(self.directory / "config.json", 2 * 1024 * 1024)
        current = self._snapshot()
        if self._prepared_snapshot is None or any(
            not hmac.compare_digest(value, self._prepared_snapshot[name])
            for name, value in current.items()
        ):
            raise ValueError("Prepared files changed")
        if not hmac.compare_digest(
            hashlib.sha256(raw).digest(), self._prepared_snapshot["config.json"]
        ):
            raise ValueError("Prepared configuration changed")
        return parse_config(raw, state_dir=self.directory, check_decoder_executable=False)

    async def health(self, _request):
        return web.json_response({"service": "first-run-aiport", "device_started_by_wizard": False})

    async def index(self, request):
        cookie = self._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        csrf = self.security.csrf_token(cookie)
        signout = (
            "<form method='post' action='/logout'>"
            f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
            "<button style='min-height:44px'>Sign out</button></form>"
        )
        if self._operations:
            return self._error(
                "Preparation in progress",
                "A private-file operation is still running. No new preparation is admitted; shutdown waits for it.",
                409,
            )
        if self.prepared:
            try:
                config = await self._file_action(self._prepared)
            except (ValueError, OSError, RuntimeError):
                return self._error(
                    "Prepared state changed or unavailable",
                    "Inspect the retained private state. This wizard never repairs, reinitializes or restarts it.",
                    409,
                )
            return _page(
                "AI Port files prepared",
                "<h1>Fresh AI Port files prepared</h1><p>This wizard did not start or adopt a device, "
                "pair a camera, enable a provider or contact a controller. Current runtime/adoption "
                "status was not queried.</p>"
                f"<dl><dt>Planned device address</dt><dd>{_safe(config['device_ip'])}</dd>"
                f"<dt>Controller address</dt><dd>{_safe(config['controller_ip'])}</dd>"
                f"<dt>New local identity MAC</dt><dd>{_safe(config['mac'])}</dd></dl>"
                "<p>Private identity, pinned public controller certificate and passive base configuration "
                "passed local file checks. No camera allowlist, model, provider or audio policy was created. "
                "The compatibility-version label is not installed vendor firmware or native acceptance.</p>"
                "<p>Keep these paths in the eventual runtime namespace. Deployment, image selection, "
                "adoption and camera pairing require their separate reviewed workflow. Do not initialize "
                "another identity for an existing adopted device.</p>" + signout,
            )
        if os.path.lexists(self.directory):
            return self._error(
                "Retained setup state",
                "The directory exists. Inspect the retained private state; this wizard never verifies/reuses or reinitializes an existing identity.",
                409,
            )
        try:
            revision, plan, address = await self._file_action(self._inputs)
        except (ValueError, OSError, RuntimeError):
            return self._error(
                "Selected inputs unavailable",
                "The server-selected plan or certificate is unavailable, changed or unsupported. No profile was created.",
                409,
            )
        count = len(plan["instances"][self.slot - 1]["camera_ids"])
        return _page(
            "First-run AI Port preparation",
            "<h1>Prepare one fresh AI Port</h1><p>Local files only. No device or container is started, "
            "no adoption/pairing/inference occurs and no controller/model request is sent.</p>"
            f"<p>Server-selected Protect plan: slot {_safe(self.slot)}, device address {_safe(address)}, "
            f"{count} planned camera entries. The plan is not a camera-permission grant; those cameras "
            "are not enrolled or copied into the passive configuration. ONVIF slots are unsupported.</p>"
            f"<p>New local directory: {_safe(self.directory)}.</p>"
            "<p>The server operator selected a new directory, private plan and public controller-certificate "
            "file. Existing or partial directories are never reused. Independent file edits invalidate "
            "this form. Absolute paths are not translated for a container.</p>"
            "<form method='post' action='/create'>"
            f"<input type='hidden' name='csrf' value='{_safe(csrf)}'>"
            f"<input type='hidden' name='revision' value='{_safe(revision)}'>"
            "<label>Controller private IPv4<input name='controller' required maxlength='15' style='min-height:44px'></label>"
            "<label>Independently verified controller SHA-256 fingerprint"
            "<input name='controller_pin' required minlength='64' maxlength='64' pattern='[0-9a-fA-F]{64}' style='min-height:44px'></label>"
            "<label style='min-height:44px;box-sizing:border-box;padding:8px 0'><input type='checkbox' name='acknowledge_trust' value='yes' required> "
            "I independently verified this public certificate fingerprint for that controller. "
            "The wizard will not contact it or establish native compatibility.</label>"
            "<label style='min-height:44px;box-sizing:border-box;padding:8px 0'><input type='checkbox' name='acknowledge_preparation' value='yes' required> "
            "This is a fresh identity, not a replacement for an adopted Port. Prepare private local "
            "files only; do not start, adopt, pair cameras or enable processing.</label>"
            "<button style='min-height:44px'>Prepare one new private AI Port profile</button></form>"
            + signout,
        )

    async def create(self, request):
        required = {
            "csrf",
            "revision",
            "controller",
            "controller_pin",
            "acknowledge_trust",
            "acknowledge_preparation",
        }
        fields, _ = await self._fields(request, required, action="first_run_create")
        if self._setup_lock.locked() or self._operations:
            raise web.HTTPConflict(text="A preparation operation is already running")
        async with self._setup_lock:
            if self.prepared or os.path.lexists(self.directory):
                self._audit("first_run_create", "refused", "aiport")
                raise web.HTTPConflict(text="Existing or partial setup is never reused")
            try:
                if (
                    fields["acknowledge_trust"] != "yes"
                    or fields["acknowledge_preparation"] != "yes"
                    or not re.fullmatch(r"[0-9a-f]{64}", fields["revision"])
                ):
                    raise ValueError("Invalid form")
                revision, plan, _ = await self._file_action(self._inputs)
                if not hmac.compare_digest(revision, fields["revision"]):
                    self._audit("first_run_create", "preflight_refused", "aiport")
                    return self._error(
                        "Selected inputs changed",
                        "Read the current form and review the server-selected plan/certificate again. No identity was created.",
                        409,
                    )
                operation = partial(
                    provision_slot,
                    plan,
                    self.slot,
                    self.directory,
                    controller_ip=fields["controller"],
                    controller_pin=fields["controller_pin"],
                    controller_cert_file=self.controller_cert_file,
                    create_only=True,
                )
                result = await self._file_action(operation)
                if result["state"] != "created":
                    raise ValueError("Fresh-only preparation required")
                self._prepared_snapshot = await self._file_action(self._snapshot)
                self.prepared = True
            except asyncio.CancelledError:
                self._audit("first_run_create", "interrupted_partial", "aiport")
                raise
            except (ValueError, OSError, RuntimeError):
                self._audit("first_run_create", "refused_or_partial", "aiport")
                return self._error(
                    "Preparation not verified",
                    "Check the fields, independently verified fingerprint and private server inputs. Any created state is retained. No automatic retry, deletion, reuse or repair occurs.",
                    400,
                )
            self._audit("first_run_create", "prepared", "aiport")
            raise web.HTTPSeeOther("/")
