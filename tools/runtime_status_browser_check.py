"""Real isolated Chrome: read-only startup comparison against owned TLS fixtures."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
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
from aikey.startup_configuration import observation  # noqa: E402
from aikey.tls import server_context  # noqa: E402


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect
    with tempfile.TemporaryDirectory(prefix="owned-runtime-browser-", dir=output) as temporary:
        root = Path(temporary)
        config = root / "config.json"
        initialize(config, root, controller_host="127.0.0.1")
        before = config.read_bytes()
        revision = hashlib.sha256(before).hexdigest()
        password = "fictional runtime browser passphrase"
        site = ControlSite(config, signing_key=b"r" * 32,
                           password_record=AdminSecurity.create_password_record(password), port=8765)
        value = {"service": "local-aikey", "device": {"adopted": True, "connected": True},
                 "startup_configuration": observation(revision)}
        requests, unowned, errors, posts = [], [], [], []
        async def health(request):
            requests.append(request.path)
            return web.json_response(value)
        peer_app = web.Application()
        peer_app.router.add_get("/healthz", health)
        peer = TestServer(peer_app, host="127.0.0.1")
        admin = TestServer(site.app(), host="127.0.0.1")
        try:
            await peer.start_server(ssl=server_context(root))
            site.runtime_observer = RuntimeObserver(site._backup_profiles(),
                {"aikey": str(peer.make_url("/healthz"))})
            await admin.start_server()
            site.origin = str(admin.make_url("/")).rstrip("/")
            site.security = AdminSecurity(b"r" * 32, site.origin, allow_loopback_http=True)
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
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: posts.append(urlsplit(request.url).path)
                            if request.method == "POST" else None)
                    await page.goto(site.origin + "/runtime-status")
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    await page.locator("input[name='password']").fill(password)
                    await page.get_by_role("button", name="Sign in", exact=True).click()
                    await page.get_by_role("link", name="Runtime configuration", exact=True).click()
                    await expect(page.get_by_role("heading", name="Runtime configuration")).to_be_visible()
                    await expect(page.get_by_text("Matches startup snapshot", exact=True)).to_be_visible()
                    await page.get_by_role("region", name="Configuration comparisons").focus()
                    assert await page.get_by_role("region", name="Configuration comparisons").evaluate(
                        "el => el === document.activeElement")
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await page.screenshot(path=str(output / (name + "-runtime-match.png")), full_page=True)
                    config.write_bytes(before + b"\n")  # Owned fixture: formatting-only new saved revision.
                    await page.get_by_role("link", name="Refresh", exact=True).click()
                    await expect(page.get_by_text("Different from startup snapshot", exact=False)).to_be_visible()
                    await expect(page.get_by_text("Matches startup snapshot", exact=True)).to_have_count(0)
                    assert await page.locator("form,button").count() == 0
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await page.screenshot(path=str(output / (name + "-runtime-different.png")), full_page=True)
                    config.write_bytes(before)
                    value.pop("startup_configuration")
                    site.runtime_observer.cache.clear()  # Explicit owned fixture switch, not a product retry.
                    await page.get_by_role("link", name="Refresh", exact=True).click()
                    await expect(page.get_by_text("Unknown — no comparable startup observation", exact=True)).to_be_visible()
                    assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
                    assert str(root) not in await page.content() and password not in await page.content()
                    await page.screenshot(path=str(output / (name + "-runtime-unknown.png")), full_page=True)
                    site.security._clock = lambda: time.time() + 86400
                    await page.reload()
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    assert posts == ["/login"] and not unowned and not errors
                    assert config.read_bytes() == before and 1 <= len(requests) <= 3
                    result = {"viewport": viewport, "browser_version": context.browser.version,
                        "match_mismatch_unknown_visible": True, "keyboard_region_focus": True,
                        "expired_session_refused": True, "settings_posts": 0, "health_reads": len(requests),
                        "real_controller_model_or_media_calls": 0, "unowned_requests": 0,
                        "fixture_bytes_restored": True, "body_horizontal_overflow": False,
                        "local_session_storage_entries": 0, "visual_review": "not_performed_by_runner"}
                finally:
                    await context.close()
        finally:
            await admin.close()
            await peer.close()
    assert not root.exists()
    return {**result, "owned_profiles_and_servers_collected": True}


async def main(args):
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    modules = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((REPO / "src/aikey").glob("*.py"))}
    results = {}
    for name, viewport in [("desktop", {"width": 1200, "height": 900}),
                           ("mobile", {"width": 390, "height": 844})]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    (args.output / "result.json").write_text(json.dumps(
        {"scenarios": results, "source_modules": modules}, indent=2) + "\n")
    print(json.dumps({"desktop_mobile": "passed", "owned_resources_collected": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable")
    asyncio.run(main(parser.parse_args()))
