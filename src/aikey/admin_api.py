"""Versioned private admin JSON adapter; reuse the authenticated listener/stores.

This is not a public diagnostics/export endpoint. No new listener, credential,
runtime activation, process execution or arbitrary file path is accepted.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re

from aiohttp import web

from .aiport_config_store import AiPortConfigurationError
from .config_store import (ConfigurationStoreError, RevisionConflict,
                           UnsafeConfigurationChange, _public)
from .model_roles import key_roles, port_roles
from .protocol import ContractError, _json_object


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

    def register(self, app):
        app.router.add_get(PREFIX + "/session", self.session)
        app.router.add_get(PREFIX + "/profiles", self.profiles)
        app.router.add_get(PREFIX + "/profiles/{profile}/configuration", self.configuration)
        for operation in ("preview", "apply", "rollback"):
            app.router.add_post(PREFIX + "/profiles/{profile}/" + operation, self.change)
        app.on_cleanup.append(self.drain)

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
        rows.extend({"id": name, "kind": "aiport", "configuration_edit": False}
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

    def view(self, profile, store):
        # Bind configuration and registry to one captured validated revision.
        content, config = (store._read(store.path) if profile == "aikey" else store._read())
        revision = hashlib.sha256(content).hexdigest()
        registry = key_roles(config, revision) if profile == "aikey" else port_roles(config, revision)
        return {"profile": profile, "revision": revision, "configuration": _public(config),
                "roles": registry, "activation": "saved_not_active_runtime",
                "configuration_edit": profile == "aikey"}

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
        # Port-specific provider/scope actions remain on their existing typed
        # handlers until the Port full-patch contract is implemented.
        if profile != "aikey":
            self.site._audit(action, "unsupported", profile=profile)
            return error("operation_not_supported", 409)
        expected = {"revision", "target_revision"} if operation == "rollback" else {"revision", "patch"}
        if operation == "apply":
            expected.add("preview_revision")
        try:
            if request.content_type != "application/json":
                raise ValueError
            payload = _json_object(await request.read())
            if set(payload) != expected or (operation != "rollback" and not isinstance(payload["patch"], dict)):
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
                preview = await asyncio.to_thread(store.preview, payload["revision"], payload["patch"])
            except RevisionConflict:
                self.site._audit(action, "conflict", profile=profile)
                return error("revision_conflict", 409)
            except UnsafeConfigurationChange:
                self.site._audit(action, "rejected", profile=profile)
                return error("unsafe_change", 400)
            except (ConfigurationStoreError, OSError, ValueError):
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
        method, args = ((store.rollback, (payload["revision"], payload["target_revision"]))
                        if operation == "rollback" else (store.apply, (payload["revision"], payload["patch"])))
        return await self.file_operation(self.commit, profile, action, method, args)

    async def commit(self, profile, action, method, args):
        # Both the commit result and final audit outlive an HTTP disconnect.
        try:
            result = await asyncio.to_thread(method, *args)
        except RevisionConflict:
            self.site._audit(action, "conflict", profile=profile)
            return error("revision_conflict", 409)
        except UnsafeConfigurationChange:
            self.site._audit(action, "rejected", profile=profile)
            return error("unsafe_change", 400)
        except (ConfigurationStoreError, OSError, ValueError):
            # An IO/store failure after admission is not proof that it rolled back.
            self.site._audit(action, "uncertain", profile=profile)
            return error("outcome_uncertain", 503)
        self.site._audit(action, "saved", profile=profile)
        return reply({"profile": profile, "revision": result.revision,
                      "configuration": result.configuration,
                      "activation": "saved_not_active_runtime", "automatic_retry": False})
