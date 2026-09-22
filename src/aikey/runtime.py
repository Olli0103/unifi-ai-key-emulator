"""Assemble the device, job worker and optional search responder."""

import asyncio
from pathlib import Path

from aiohttp import web

from .device import DeviceService
from .discovery import DiscoveryService
from .search import SearchService
from .tls import client_context, server_context
from .worker import JobProcessor


class Application:
    def __init__(self, config: dict):
        self.config = config
        self.state_dir = Path(config["runtime"]["state_dir"])
        outbound = client_context(config)
        self.worker = JobProcessor(config, self.state_dir, ssl_context=outbound)
        self.credential_handler = None
        if config.get("database", {}).get("enabled") is True:
            from .database import PgCredentialRotator
            self.credential_handler = PgCredentialRotator(config, self.state_dir)
        self.device = DeviceService(config, self.state_dir, self.worker.submit,
                                    tls_context=outbound, queue_status=self.worker.status,
                                    credential_handler=self.credential_handler)
        self.search = SearchService(config, self.state_dir, ssl_context=outbound)
        self.discovery = DiscoveryService(config, info_provider=self.device.get_info,
                                           adopted_provider=lambda: self.device.status["adopted"])
        self.runner = None
        self.https_port = None

    async def start(self):
        # Validate the shared embedding identity before any listener or controller connection.
        self.search.validate_configuration()
        try:
            # Worker startup validates document-only profiles and creates idle clients.
            # It does not fetch media or contact the inference service until a job arrives.
            await self.worker.start()
            app = self.device.create_app()
            app.router.add_get("/healthz", self._health)
            self.runner = web.AppRunner(app, access_log=None)
            await self.runner.setup()
            options = self.config["runtime"]
            site = web.TCPSite(self.runner, options["bind"], options["https_port"],
                               ssl_context=server_context(self.state_dir))
            await site.start()
            self.https_port = site._server.sockets[0].getsockname()[1]
            if options.get("enable_http", False):
                if options["mode"] != "lab":
                    raise ValueError("Plain HTTP management is restricted to the local lab")
                await web.TCPSite(self.runner, options["bind"], options["http_port"]).start()
            await self.discovery.start()
            await self.device.start()
            await self.search.start()
        except BaseException:
            await self.stop()
            raise

    async def _health(self, request):
        return web.json_response({"service": "local-aikey", "version": "0.1.0",
            "device": self.device.status, "worker": self.worker.status(),
            "search": self.search.status, "discovery": self.discovery.status,
            "native_compatibility": "needs_evidence"})

    async def stop(self):
        outcomes = await asyncio.gather(self.device.stop(), self.search.stop(), self.discovery.stop(),
                                         self.worker.stop(), return_exceptions=True)
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
        errors = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
        if errors:
            raise RuntimeError("A service failed to close cleanly") from errors[0]
