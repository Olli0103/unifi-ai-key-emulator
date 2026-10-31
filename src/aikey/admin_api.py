"""Versioned private admin JSON adapter; reuse the authenticated listener/stores.

This is not a public diagnostics/export endpoint. No new listener, credential,
runtime activation, process execution or arbitrary file path is accepted.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import time

from aiohttp import ClientError, web

from .aiport_config_store import (AiPortConfigurationError, AiPortRevisionConflict,
                                  AiPortUnsafePolicyChange)
from .config_store import (ConfigurationStoreError, RevisionConflict,
                           UnsafeConfigurationChange, _public)
from .model_roles import key_roles, port_roles
from .camera_inventory import InventoryError
from .protocol import ContractError, _json_object
from .admin_maintenance import MaintenanceError


SCHEMA = "aikey-admin/1"
PREFIX = "/api/admin/v1"
_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"}


def reply(value, *, status=200):
    return web.json_response({"schema": SCHEMA, **value}, status=status, headers=_HEADERS)


def error(code, status):
    result = {"error": code}
    if code == "outcome_uncertain":
        result.update(automatic_retry=False, next_action="reload_and_reconcile_saved_revision")
    return reply(result, status=status)


class AdminAPI:
    def __init__(self, site):
        self.site = site
        self.operations = set()
        self.inventory_lock = asyncio.Lock()
        self.inventory_cache = None

    def register(self, app):
        app.router.add_get(PREFIX + "/session", self.session)
        app.router.add_get(PREFIX + "/profiles", self.profiles)
        app.router.add_get(PREFIX + "/cameras", self.cameras)
        app.router.add_get(PREFIX + "/cameras/native-status", self.native_camera_status)
        app.router.add_get(PREFIX + "/maintenance", self.maintenance)
        app.router.add_get(PREFIX + "/maintenance/requests", self.maintenance_history)
        app.router.add_post(PREFIX + "/maintenance", self.maintenance_submit)
        app.router.add_get(PREFIX + "/maintenance/requests/{request_id}", self.maintenance_receipt)
        app.router.add_get(PREFIX + "/profiles/{profile}/configuration", self.configuration)
        app.router.add_get(PREFIX + "/profiles/{profile}/runtime", self.runtime)
        app.router.add_get(PREFIX + "/profiles/{profile}/identity", self.identity)
        for operation in ("preview", "apply", "rollback"):
            app.router.add_post(PREFIX + "/profiles/{profile}/" + operation, self.change)
        app.on_cleanup.append(self.drain)
        from .maintenance_console import MaintenanceConsole
        self.maintenance_console = MaintenanceConsole(self)
        self.maintenance_console.register(app)
        from .ocr_provider_control import OcrProviderControl
        self.ocr_provider_control = OcrProviderControl(self.site)
        self.ocr_provider_control.register(app)
        from .camera_threshold_control import CameraThresholdControl
        self.camera_threshold_control = CameraThresholdControl(self.site)
        self.camera_threshold_control.register(app)
        from .plate_camera_control import PlateCameraControl
        self.plate_camera_control = PlateCameraControl(self.site)
        self.plate_camera_control.register(app)
        from .processing_status import ProcessingStatus
        self.processing_status = ProcessingStatus(self)
        self.processing_status.register(app)
        from .speech_enrollment import SpeechEnrollment
        self.speech_enrollment = SpeechEnrollment(self.site)
        self.speech_enrollment.register(app)
        from .caption_policy_control import CaptionPolicyControl
        self.caption_policy_control = CaptionPolicyControl(self.site)
        self.caption_policy_control.register(app)
        from .caption_scope_control import CaptionScopeControl
        self.caption_scope_control = CaptionScopeControl(self.site)
        self.caption_scope_control.register(app)
        from .caption_camera_enrollment import CaptionCameraEnrollment
        self.caption_camera_enrollment = CaptionCameraEnrollment(self.site)
        self.caption_camera_enrollment.register(app)
        from .caption_setup_control import CaptionSetupControl
        self.caption_setup_control = CaptionSetupControl(self.site)
        self.caption_setup_control.register(app)
        from .worker_limits_control import WorkerLimitsControl
        self.worker_limits_control = WorkerLimitsControl(self.site)
        self.worker_limits_control.register(app)
        from .diagnostic_export import DiagnosticExport
        self.diagnostic_export = DiagnosticExport(self)
        self.diagnostic_export.register(app)

    async def maintenance_history(self, request):
        if not self.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        if self.site.maintenance is None:
            return reply({"configured": False, "requests": [], "worker_liveness": "not_asserted"})
        try:
            rows = await asyncio.to_thread(self.site.maintenance.history)
        except (MaintenanceError, OSError, ValueError):
            return error("maintenance_unavailable", 503)
        if not self.authenticated(request):
            return error("authentication_required", 401)
        return reply({"configured": True, "requests": rows, "worker_liveness": "not_asserted"})

    async def maintenance(self, request):
        if not self.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        if self.site.maintenance is None:
            return reply({"configured": False, "operations": [], "worker_liveness": "not_asserted"})
        try:
            rows = await asyncio.to_thread(self.site.maintenance.catalog)
            rows = [row for row in rows if self.store(row["profile"]) is not None]
        except (MaintenanceError, OSError, ValueError):
            return error("maintenance_unavailable", 503)
        if not self.authenticated(request):
            return error("authentication_required", 401)
        return reply({"configured": True, "operations": rows, "worker_liveness": "not_asserted"})

    async def maintenance_receipt(self, request):
        if not self.authenticated(request):
            return error("authentication_required", 401)
        if self.site.maintenance is None:
            return error("maintenance_unavailable", 503)
        try:
            result = await asyncio.to_thread(self.site.maintenance.receipt, request.match_info["request_id"])
        except (MaintenanceError, OSError, ValueError):
            return error("maintenance_unavailable", 503)
        if not self.authenticated(request):
            return error("authentication_required", 401)
        return reply(result)

    async def maintenance_submit(self, request):
        if not self.authenticated(request, mutate=True):
            return error("forbidden", 403)
        if self.site.maintenance is None or self.site.audit is None:
            return error("maintenance_unavailable", 503)
        try:
            if request.query or request.content_type != "application/json":
                raise ValueError
            payload = _json_object(await request.read())
            if set(payload) != {"operation", "revision", "request"}:
                raise ValueError
            from .admin_maintenance import _identifier, _REQUEST
            _identifier(payload["operation"])
            _identifier(payload["revision"])
            _identifier(payload["request"], _REQUEST)
            rows = await asyncio.to_thread(self.site.maintenance.catalog)
            matching = next((row for row in rows if row["operation"] == payload["operation"]), None)
            if matching is None or self.store(matching["profile"]) is None:
                return error("maintenance_plan_conflict", 409)
        except web.HTTPRequestEntityTooLarge:
            return error("request_too_large", 413)
        except (OSError, ValueError, TypeError):
            return error("invalid_request", 400)
        if not self.authenticated(request):
            return error("forbidden", 403)
        return await self.file_operation(self.queue_maintenance, payload, matching["profile"])

    async def queue_maintenance(self, payload, profile):
        # Admission must have durable audit authority before any queue write.
        if not self.site.audit.record("maintenance_submit", "admitted", profile=profile,
                operation_id=payload["operation"], request_id=payload["request"]):
            return error("audit_unavailable", 503)
        try:
            result = await asyncio.to_thread(self.site.maintenance.submit,
                payload["operation"], payload["revision"], payload["request"])
        except MaintenanceError:
            self.site._audit("maintenance_submit", "refused", profile=profile)
            return error("maintenance_plan_conflict", 409)
        except (OSError, ValueError):
            self.site._audit("maintenance_submit", "uncertain", profile=profile)
            return reply({"error": "outcome_uncertain", "automatic_retry": False,
                          "next_action": "read_same_maintenance_request_receipt"}, status=503)
        self.site._audit("maintenance_submit", "queued", profile=profile)
        return reply(result, status=202)

    def store(self, profile):
        return self.site.aikey if profile == "aikey" else self.site.aiports.get(profile)

    def authenticated(self, request, *, mutate=False):
        return self.site._session(request, mutate=mutate,
            csrf=request.headers.get("X-CSRF-Token") if mutate else None)

    async def file_operation(self, operation, *args):
        # A disconnected HTTP waiter cannot cancel a thread already committing.
        # Keep its result observed and drain admitted writes on service cleanup.
        task = asyncio.create_task(operation(*args))
        self.operations.add(task)
        def settled(done):
            self.operations.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(settled)
        return await asyncio.shield(task)

    async def drain(self, _):
        while self.operations:
            await asyncio.gather(*tuple(self.operations), return_exceptions=True)

    async def profiles(self, request):
        if not self.authenticated(request):
            return error("authentication_required", 401)
        rows = [{"id": "aikey", "kind": "aikey", "configuration_edit": True}]
        rows.extend({"id": name, "kind": "aiport", "configuration_edit": True}
                    for name in self.site.aiports)
        return reply({"profiles": rows, "activation": "saved_not_active_runtime"})

    async def session(self, request):
        cookie = self.authenticated(request)
        if not cookie:
            return error("authentication_required", 401)
        token = self.site.security.csrf_token(cookie)
        if token is None:
            return error("authentication_required", 401)
        return reply({"csrf_token": token, "mutation_limit": "shared_with_browser_forms",
                      "authorization": "current_runtime_session"})

    async def cameras(self, request):
        if not self.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        if self.site.inventory_loader is None:
            return error("inventory_unavailable", 503)
        try:
            async with asyncio.timeout(12):
                async with self.inventory_lock:
                    if self.inventory_cache is not None and self.inventory_cache[0] > time.monotonic():
                        result = self.inventory_cache[1]
                    else:
                        report = await self.site.inventory_snapshot()
                        def read():
                            from .admin_inventory import inventory_view
                            bindings = {}
                            for profile, store in self.site.aiports.items():
                                for mac in store.configured_camera_macs():
                                    bindings.setdefault(mac, []).append(profile)
                            return inventory_view(report, bindings)
                        result = await asyncio.to_thread(read)
                        self.inventory_cache = (time.monotonic() + 2, result)
        except (OSError, ValueError, ClientError, InventoryError,
                AiPortConfigurationError, asyncio.TimeoutError):
            return error("inventory_unavailable", 503)
        if not self.authenticated(request):
            return error("authentication_required", 401)
        return reply(result)

    async def native_status_view(self):
        from .native_camera_status import public_snapshot
        if self.site.native_status_loader is None:
            return {"configured": False, "observation": "not_configured", "cameras": None}
        async with asyncio.timeout(20):
            value = await self.site.native_status_loader()
        return {"configured": True, "observation": "native_camera_status",
                "snapshot": public_snapshot(value)}

    async def native_camera_status(self, request):
        if not self.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        from .native_camera_status import NativeStatusError
        try:
            result = await self.native_status_view()
        except NativeStatusError as exc:
            if not self.authenticated(request):
                return error("authentication_required", 401)
            return reply({"error": "native_status_unavailable", "reason": exc.code}, status=503)
        except (OSError, ValueError, ClientError, TimeoutError):
            if not self.authenticated(request):
                return error("authentication_required", 401)
            return error("native_status_unavailable", 503)
        if not self.authenticated(request):
            return error("authentication_required", 401)
        return reply(result)

    def view(self, profile, store):
        # Bind configuration and registry to one captured validated revision.
        content, config = (store._read(store.path) if profile == "aikey" else store._read())
        revision = hashlib.sha256(content).hexdigest()
        registry = key_roles(config, revision) if profile == "aikey" else port_roles(config, revision)
        return {"profile": profile, "revision": revision, "configuration": _public(config),
                "roles": registry, "activation": "saved_not_active_runtime",
                "configuration_edit": True}

    async def configuration(self, request):
        if not self.authenticated(request):
            return error("authentication_required", 401)
        profile = request.match_info["profile"]
        store = self.store(profile)
        if store is None:
            return error("unknown_profile", 404)
        try:
            result = await asyncio.to_thread(self.view, profile, store)
        except (ConfigurationStoreError, AiPortConfigurationError, OSError, ValueError):
            return error("configuration_unavailable", 503)
        if not self.authenticated(request):
            return error("authentication_required", 401)
        return reply(result)

    async def runtime(self, request):
        if not self.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        profile = request.match_info["profile"]
        if self.store(profile) is None:
            return error("unknown_profile", 404)
        result = await self.runtime_view(profile)
        if not self.authenticated(request):
            return error("authentication_required", 401)
        return reply({"profile": profile, **result,
            "native_operations": {"ptz": "not_supported_by_this_api",
                                  "recording": "controller_owned_not_measured"}})

    async def runtime_view(self, profile):
        """Compare a fresh saved read with a bounded, pinned startup observation."""
        result = await self.site.runtime_observer.read(profile)
        result.update(saved_revision=None, saved_revision_matches_runtime_startup=None)
        try:
            store = self.store(profile)
            content, config = await asyncio.to_thread(
                store._read, *([store.path] if profile == "aikey" else []))
            saved = hashlib.sha256(content).hexdigest()
            loaded = result.get("runtime_configuration_revision")
            result.update(saved_revision=saved,
                          saved_revision_matches_runtime_startup=(
                              hmac.compare_digest(saved, loaded) if loaded is not None else None))
            if profile != "aikey" and result.get("runtime") is not None:
                from .camera_bindings import binding_revision
                cameras = [entry["camera_mac"] for entry in config.get("paired_streams", [])]
                reported = result["runtime"].get("pool_binding_revision")
                matches = (hmac.compare_digest(reported, binding_revision(config["mac"], cameras))
                           if reported is not None and cameras else None)
                result.update(saved_revision=hashlib.sha256(content).hexdigest(),
                              saved_order_matches_reported_runtime_order=matches)
                observed = result["runtime"].get("cameras")
                if matches and observed is not None:
                    if len(observed) != len(cameras):
                        raise ValueError
                    for camera, mac in zip(observed, cameras, strict=True):
                        camera["saved_camera_mac"] = mac
        except (ConfigurationStoreError, AiPortConfigurationError, OSError, ValueError):
            result.update(saved_revision=None, saved_revision_matches_runtime_startup=None)
            if profile != "aikey":
                result["saved_order_matches_reported_runtime_order"] = None
                result["saved_binding_observation"] = "unavailable"
        return result

    async def identity(self, request):
        if not self.authenticated(request):
            return error("authentication_required", 401)
        profile = request.match_info["profile"]
        store = self.store(profile)
        if store is None:
            return error("unknown_profile", 404)
        def read():
            from .admin_identity import identity_status
            content, config = store._read(store.path) if profile == "aikey" else store._read()
            kind, directory = self.site._backup_profiles()[profile]
            return {"profile": profile, "saved_revision": hashlib.sha256(content).hexdigest(),
                    "identity": identity_status(kind, directory, config)}
        try:
            result = await asyncio.to_thread(read)
        except (ConfigurationStoreError, AiPortConfigurationError, OSError, ValueError):
            return error("identity_unavailable", 503)
        if not self.authenticated(request):
            return error("authentication_required", 401)
        return reply(result)

    async def change(self, request):
        profile = request.match_info["profile"]
        operation = request.path.rsplit("/", 1)[-1]
        action = "config_" + operation
        if not self.authenticated(request, mutate=True):
            self.site._audit(action, "forbidden", profile=profile)
            return error("forbidden", 403)
        store = self.store(profile)
        if store is None:
            self.site._audit(action, "unknown_profile", profile=profile)
            return error("unknown_profile", 404)
        expected = {"revision", "target_revision"} if operation == "rollback" else {"revision", "patch"}
        if operation == "apply":
            expected.add("preview_revision")
        try:
            if request.content_type != "application/json":
                raise ValueError
            payload = _json_object(await request.read())
            if set(payload) != expected or (operation != "rollback" and not isinstance(payload["patch"], dict)):
                raise ValueError
            if operation == "rollback" and (not isinstance(payload["target_revision"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", payload["target_revision"])):
                raise ValueError
        except web.HTTPRequestEntityTooLarge:
            self.site._audit(action, "rejected", profile=profile)
            return error("request_too_large", 413)
        except (ContractError, ValueError, TypeError):
            self.site._audit(action, "rejected", profile=profile)
            return error("invalid_request", 400)
        if not self.authenticated(request):
            self.site._audit(action, "forbidden", profile=profile)
            return error("forbidden", 403)
        if operation != "rollback":
            try:
                prepare = store.preview if profile == "aikey" else store.preview_patch
                preview = await asyncio.to_thread(prepare, payload["revision"], payload["patch"])
            except (RevisionConflict, AiPortRevisionConflict):
                self.site._audit(action, "conflict", profile=profile)
                return error("revision_conflict", 409)
            except (UnsafeConfigurationChange, AiPortUnsafePolicyChange):
                self.site._audit(action, "rejected", profile=profile)
                return error("unsafe_change", 400)
            except (ConfigurationStoreError, AiPortConfigurationError, OSError, ValueError):
                self.site._audit(action, "unavailable", profile=profile)
                return error("configuration_unavailable", 503)
            if not self.authenticated(request):
                self.site._audit(action, "forbidden", profile=profile)
                return error("forbidden", 403)
            if operation == "preview":
                self.site._audit(action, "previewed", profile=profile)
                return reply({"profile": profile, "current_revision": preview.current_revision,
                    "resulting_revision": preview.resulting_revision,
                    "configuration": preview.configuration, "changed_fields": list(preview.changed_fields),
                    "restart_required": preview.restart_required,
                    "current_jobs": "unchanged_until_explicit_processor_restart",
                    "activation": "saved_not_active_runtime"})
            proposed = payload["preview_revision"]
            if (not isinstance(proposed, str) or not re.fullmatch(r"[0-9a-f]{64}", proposed)
                    or not hmac.compare_digest(proposed, preview.resulting_revision)):
                self.site._audit(action, "conflict", profile=profile)
                return error("preview_conflict", 409)
        apply = store.apply if profile == "aikey" else store.apply_patch
        method, args = ((store.rollback, (payload["revision"], payload["target_revision"]))
                        if operation == "rollback" else (apply, (payload["revision"], payload["patch"])))
        return await self.file_operation(self.commit, profile, action, method, args)

    async def commit(self, profile, action, method, args):
        # Both the commit result and final audit outlive an HTTP disconnect.
        try:
            result = await asyncio.to_thread(method, *args)
        except (RevisionConflict, AiPortRevisionConflict):
            self.site._audit(action, "conflict", profile=profile)
            return error("revision_conflict", 409)
        except (UnsafeConfigurationChange, AiPortUnsafePolicyChange):
            self.site._audit(action, "rejected", profile=profile)
            return error("unsafe_change", 400)
        except (ConfigurationStoreError, AiPortConfigurationError, OSError, ValueError):
            # An IO/store failure after admission is not proof that it rolled back.
            self.site._audit(action, "uncertain", profile=profile)
            return error("outcome_uncertain", 503)
        self.site._audit(action, "saved", profile=profile)
        return reply({"profile": profile, "revision": result.revision,
                      "configuration": result.configuration,
                      "activation": "saved_not_active_runtime", "automatic_retry": False})
