"""Isolated real Chrome with fictional HTTPS processors; no production access."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import ssl
import sys
import tempfile
import time
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestServer  # noqa: E402
from aikey.admin_runtime import RuntimeObserver  # noqa: E402
from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.config import initialize  # noqa: E402
from aikey.control_site import ControlSite  # noqa: E402
from aikey.tls import ensure_identity_certificate, server_context  # noqa: E402


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect
    with tempfile.TemporaryDirectory(prefix="owned-processing-browser-", dir=output) as temporary:
        root = Path(temporary)
        key = root / "key" / "config.json"
        initialize(key, key.parent, controller_host="127.0.0.1")
        port = root / "port" / "config.json"
        port.parent.mkdir(mode=0o700)
        certificate, _ = ensure_identity_certificate(port.parent, "02000000F0B2")
        (port.parent / "controller-ca.pem").write_bytes(certificate.read_bytes())
        (port.parent / "controller-ca.pem").chmod(0o600)
        port.write_text(json.dumps({"controller_ip": "192.168.10.1", "device_ip": "192.168.10.20",
            "mac": "02000000F0B2", "firmware_version": "5.1.12",
            "controller_pin": hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.read_text())).hexdigest(),
            "paired_streams": [{"camera_mac": "02000000F0C3", "source_ip": "192.168.10.1",
                                "ffmpeg_path": sys.executable}],
            "live_pool_detector": {"checkpoint_path": str(port.parent / "fictional-model.pth"),
                "checkpoint_sha256": "a" * 64, "threshold": .8, "smart_types": ["person"]}}) + "\n")
        port.chmod(0o600)
        password = "fictional processing browser passphrase"
        site = ControlSite(key, port, signing_key=b"p" * 32,
            password_record=AdminSecurity.create_password_record(password), port=8765)
        site.aiport.snapshot()
        values = {
            "aikey": {"service": "local-aikey", "device": {"adopted": True, "connected": True},
                "worker": {"queued": 0, "active": 1, "pending": 1, "capacity": 20, "ledger": 32,
                    "captions": {"admitted": 5, "exhausted": 2, "deferred_fair_share": 1},
                    "failure_notes": {"scope": "current_process_publication_counts_not_current_job_outcomes",
                        "capacity": 1024, "current_process": {"written": 3, "already_recorded": 0,
                            "busy": 0, "capacity": 0, "unavailable": 1}}}},
            "aiport": {"service": "aiport-candidate", "adopted": True, "control_connected": True,
                "active_streams": 1, "pool_inference": {"attempts": 12, "successes": 10,
                    "api_request_cap": None, "api_consecutive_provider_failures": 0,
                    "pending_cameras": 0},
                "pool_cameras": [{"stream_active": True, "policy_enabled": True,
                    "attempts": 12, "successes": 10, "api_failures": 2,
                    "api_requests_remaining": 0, "api_request_budget_healthy": False}]}}
        calls, posts, errors, unowned = [], [], [], []
        peers, targets = [], {}
        for profile, directory in [("aikey", key.parent), ("aiport", port.parent)]:
            async def health(request, profile=profile):
                calls.append(profile)
                return web.json_response({**values[profile], "secret": "CANARY-PRIVATE-MEDIA"})
            app = web.Application()
            app.router.add_get("/healthz", health)
            peer = TestServer(app, host="127.0.0.1")
            peers.append(peer)
            await peer.start_server(ssl=server_context(directory))
            targets[profile] = str(peer.make_url("/healthz"))
        site.runtime_observer = RuntimeObserver(site._backup_profiles(), targets)
        config_bytes = {p: p.read_bytes() for p in (key, port)}
        admin = TestServer(site.app(), host="127.0.0.1")
        try:
            await admin.start_server()
            site.origin = str(admin.make_url("/")).rstrip("/")
            site.security = AdminSecurity(b"p" * 32, site.origin, allow_loopback_http=True)
            async with async_playwright() as driver:
                context = await driver.chromium.launch_persistent_context(
                    user_data_dir=str(root / "browser-profile"), executable_path=executable,
                    headless=True, chromium_sandbox=True, viewport=viewport,
                    is_mobile=name == "mobile", device_scale_factor=1,
                    args=["--disable-background-networking", "--disable-component-update",
                          "--disable-sync", "--no-first-run"])
                try:
                    async def route(route):
                        parsed = urlsplit(route.request.url)
                        if f"{parsed.scheme}://{parsed.netloc}" != site.origin:
                            unowned.append(route.request.url)
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route("**/*", route)
                    page = await context.new_page()
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.on("request", lambda r: posts.append(urlsplit(r.url).path)
                            if r.method == "POST" else None)
                    await page.goto(site.origin + "/processing")
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    await page.get_by_label("Password", exact=True).fill(password)
                    async with page.expect_navigation():
                        await page.get_by_role("button", name="Sign in", exact=True).click()
                    await page.get_by_role("link", name="Processing and budgets", exact=True).click()
                    await expect(page.get_by_role("heading", name="Processing and budgets")).to_be_visible()
                    await expect(page.get_by_text("0 queued jobs; 1 active jobs", exact=True)).to_be_visible()
                    await expect(page.get_by_text("1 active streams; 0 pending cameras", exact=True)).to_be_visible()
                    region = page.get_by_role("region", name="Processing overview", exact=True)
                    await region.focus()
                    assert await region.evaluate("el => el === document.activeElement")
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await page.screenshot(path=str(output / (name + "-processing-overview.png")), full_page=True)
                    summary = page.locator("summary", has_text="Processing details for AI Port")
                    await summary.focus()
                    await page.keyboard.press("Enter")
                    await expect(page.get_by_text("Runtime camera 1", exact=True)).to_be_visible()
                    await expect(page.get_by_text("Not configured", exact=True)).to_be_visible()
                    assert await summary.evaluate("el => el.parentElement.open")
                    assert await page.locator("form,button").count() == 0
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await page.screenshot(path=str(output / (name + "-processing-details.png")), full_page=True)
                    # A malformed peer must become unknown, not zero or the preceding good data.
                    values["aiport"]["pool_inference"]["attempts"] = "CANARY-PRIVATE-ERROR"
                    site.runtime_observer.cache.clear()
                    await page.get_by_role("link", name="Refresh", exact=True).click()
                    await expect(page.get_by_text("Runtime observation unavailable. Status unknown", exact=False)).to_be_visible()
                    assert "CANARY" not in await page.content()
                    assert password not in await page.content() and str(root) not in await page.content()
                    assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
                    site.security._clock = lambda: time.time() + 86400
                    await page.reload()
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    assert posts == ["/login"] and not errors and not unowned
                    assert all(p.read_bytes() == b for p, b in config_bytes.items())
                    result = {"viewport": viewport, "browser_version": context.browser.version,
                        "zero_unknown_and_uncapped_distinct": True, "keyboard_region_and_details": True,
                        "expired_session_refused": True, "settings_posts": 0, "health_reads": len(calls),
                        "real_controller_model_or_media_calls": 0, "unowned_requests": 0,
                        "fixture_config_bytes_unchanged": True, "body_horizontal_overflow": False,
                        "visual_review": "not_performed_by_runner"}
                finally:
                    await context.close()
        finally:
            await admin.close()
            for peer in peers:
                await peer.close()
    assert not root.exists()
    return {**result, "owned_profiles_and_servers_collected": True}


async def main(args):
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((REPO / "src/aikey").glob("*.py"))}
    results = {}
    for name, viewport in [("desktop", {"width": 1200, "height": 900}),
                           ("mobile", {"width": 390, "height": 844})]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((REPO / "src/aikey").glob("*.py"))}
    assert before == after
    (args.output / "result.json").write_text(json.dumps(
        {"scenarios": results, "source_modules": before}, indent=2) + "\n")
    print(json.dumps({"desktop_mobile": "passed", "owned_resources_collected": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable")
    asyncio.run(main(parser.parse_args()))
