"""Isolated Chrome on fictional native metadata, never a real controller/account."""

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

from aiohttp.test_utils import TestServer  # noqa: E402
from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.config import initialize  # noqa: E402
from aikey.control_site import ControlSite  # noqa: E402
from aikey.native_camera_status import parse_bootstrap  # noqa: E402


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect

    with tempfile.TemporaryDirectory(prefix="owned-native-status-", dir=output) as temporary:
        root = Path(temporary)
        config = root / "config.json"
        initialize(config, root, controller_host="127.0.0.1")
        before = config.read_bytes()
        password = "fictional native status browser password"
        site = ControlSite(config, signing_key=b"s" * 32,
            password_record=AdminSecurity.create_password_record(password), port=8765)
        report = parse_bootstrap({"nvr": {"version": "7.3.74"}, "cameras": [
            {"id": "1" * 24, "mac": "02000000F001", "modelKey": "camera",
             "name": "Fictional hall <not markup>", "state": "CONNECTED", "isRecording": True,
             "featureFlags": {"isPtz": False},
             "channels": [{"id": 0, "width": 1920, "height": 1080, "fps": 20}]},
            {"id": "2" * 24, "mac": "02000000F002", "modelKey": "camera",
             "name": "Fictional offline", "state": "DISCONNECTED", "isRecording": False,
             "featureFlags": {"isPtz": True}},
            {"id": "3" * 24, "mac": "02000000F003", "modelKey": "camera",
             "name": "Fictional unknown", "state": "CONNECTED"}]}, observed_at=int(time.time()))
        report["session_logout_acknowledged"] = True
        reads, posts, errors, unowned = [], [], [], []

        async def read():
            reads.append(True)
            return report

        admin = TestServer(site.app(), host="127.0.0.1")
        try:
            await admin.start_server()
            site.origin = str(admin.make_url("/")).rstrip("/")
            site.security = AdminSecurity(b"s" * 32, site.origin, allow_loopback_http=True)
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
                    page.on("request", lambda r: posts.append(urlsplit(r.url).path)
                            if r.method == "POST" else None)
                    await page.goto(site.origin + "/camera-status")
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    await page.get_by_label("Password", exact=True).fill(password)
                    await page.get_by_role("button", name="Sign in", exact=True).click()
                    await page.get_by_role("link", name="Native camera status", exact=True).click()
                    await expect(page.get_by_text("No native login was attempted", exact=False)).to_be_visible()
                    assert not reads
                    site.native_status_loader = read
                    await page.get_by_role("link", name="Refresh", exact=True).click()
                    for label in ["Reported active", "Reported inactive", "Reported supported",
                                  "Reported unsupported", "Fictional hall <not markup>"]:
                        await expect(page.get_by_text(label, exact=True)).to_be_visible()
                    assert await page.locator("not, img, script").count() == 0
                    await page.get_by_role("region", name="Native camera status", exact=True).focus()
                    assert await page.get_by_role("region", name="Native camera status", exact=True).evaluate(
                        "el => el === document.activeElement")
                    assert await page.locator("form,button").count() == 0
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await page.screenshot(path=str(output / (name + "-native-status.png")), full_page=True)
                    report["fetched_at"] -= 60
                    await page.get_by_role("link", name="Refresh", exact=True).click()
                    await expect(page.get_by_text("No stale observation is substituted", exact=False)).to_be_visible()
                    await expect(page.get_by_text("Reported active", exact=True)).to_have_count(0)
                    await page.screenshot(path=str(output / (name + "-native-unavailable.png")), full_page=True)
                    assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
                    assert password not in await page.content() and str(root) not in await page.content()
                    site.security._clock = lambda: time.time() + 86400
                    await page.reload()
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    assert posts == ["/login"] and len(reads) == 2 and not errors and not unowned
                    assert config.read_bytes() == before
                    result = {"viewport": viewport, "browser_version": context.browser.version,
                        "unconfigured_true_false_unknown_and_stale_refusal_visible": True,
                        "keyboard_region_focus": True, "no_camera_control_forms": True,
                        "expired_session_refused": True, "unowned_requests": 0,
                        "real_native_account_controller_media_or_model_requests": 0,
                        "fixture_profile_bytes_preserved": True, "body_horizontal_overflow": False,
                        "visual_review": "not_performed_by_runner"}
                finally:
                    await context.close()
        finally:
            await admin.close()
    assert not root.exists()
    return {**result, "owned_profile_and_server_collected": True}


async def main(args):
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    modules = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((REPO / "src/aikey").glob("*.py"))}
    results = {}
    for name, viewport in [("desktop", {"width": 1200, "height": 900}),
                           ("mobile", {"width": 390, "height": 844})]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    (args.output / "result.json").write_text(json.dumps({"scenarios": results,
                                                       "source_modules": modules}, indent=2) + "\n")
    print(json.dumps({"desktop_mobile": "passed", "owned_resources_collected": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable")
    asyncio.run(main(parser.parse_args()))
