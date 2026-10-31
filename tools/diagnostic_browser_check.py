"""Fictional redacted diagnostic download; no processor/provider/controller starts."""

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


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect
    with tempfile.TemporaryDirectory(prefix="owned-diagnostics-", dir=output) as temporary:
        root = Path(temporary)
        path = root / "key/config.json"
        initialize(path, path.parent, controller_host="127.0.0.1")
        original = path.read_bytes()
        password = "fictional redacted diagnostics browser passphrase"
        site = ControlSite(path, None, signing_key=b"c" * 32,
            password_record=AdminSecurity.create_password_record(password), port=8765)
        site.aikey.snapshot()
        calls = []
        async def read(profile):
            calls.append(profile)
            return {"observation": "runtime_health", "tls_ca_and_leaf_pin_verified": True,
                "runtime_configuration_revision": "CANARY-private-revision",
                "observed_at": "CANARY-private-observation", "private": "CANARY-cookie",
                "runtime": {"adopted": True, "connected": False, "search_connected": True,
                    "queued": 0, "active": 1, "pending": 1, "ledger": 10,
                    "caption_policy": None, "private": "CANARY-native-id-host-secret-transcript"}}
        from types import SimpleNamespace
        site.runtime_observer = SimpleNamespace(read=read)
        server = TestServer(site.app(), host="127.0.0.1")
        await server.start_server()
        site.origin = str(server.make_url("/")).rstrip("/")
        site.security = AdminSecurity(b"c" * 32, site.origin, allow_loopback_http=True)
        posts, errors, unowned = [], [], []
        try:
            async with async_playwright() as driver:
                context = await driver.chromium.launch_persistent_context(
                    user_data_dir=str(root / "browser"), executable_path=executable,
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
                    await page.goto(site.origin + "/diagnostics")
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    await page.get_by_label("Password", exact=True).fill(password)
                    async with page.expect_navigation():
                        await page.get_by_role("button", name="Sign in", exact=True).click()
                    await page.get_by_role("link", name="Redacted diagnostics", exact=True).click()
                    await expect(page.get_by_role("heading", name="Redacted diagnostics", exact=True)).to_be_visible()
                    assert not calls
                    assert "CANARY" not in await page.content() and password not in await page.content()
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await page.screenshot(path=str(output / (name + "-diagnostics.png")), full_page=True)
                    link = page.get_by_role("link", name="Download redacted JSON", exact=True)
                    await link.focus()
                    async with page.expect_download() as download_info:
                        await page.keyboard.press("Enter")
                    download = await download_info.value
                    assert download.suggested_filename == "aikey-redacted-diagnostics.json"
                    target = output / (name + "-diagnostics.json")
                    await download.save_as(str(target))
                    body = target.read_bytes()
                    value = json.loads(body)
                    assert value["schema"] == "aikey-redacted-diagnostics/1" and value["read_only"] is True
                    assert value["profile_counts"] == {"aikey":1,"aiport":0}
                    assert value["profiles"][0]["runtime"]["queued"] == 0
                    assert value["profiles"][0]["runtime"]["caption_policy_mode"] is None
                    assert "CANARY" not in body.decode() and password not in body.decode()
                    assert len(body) <= 65536 and calls == ["aikey"]
                    assert path.read_bytes() == original
                    assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
                    assert not site.admin_api.diagnostic_export.collecting
                    assert (await context.request.get(site.origin + "/api/admin/v1/diagnostics?url=PRIVATE")).status == 400
                    assert calls == ["aikey"]
                    site.security._clock = lambda: time.time() + 86400
                    assert (await context.request.get(site.origin + "/api/admin/v1/diagnostics")).status == 401
                    assert calls == ["aikey"]
                    await page.goto(site.origin + "/diagnostics")
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    assert posts == ["/login"] and not errors and not unowned
                    result = {"viewport": viewport, "browser_version": context.browser.version,
                        "disclosure_before_any_collection": True, "keyboard_downloaded_actual_json": True,
                        "suggested_filename_fixed": True, "unknown_not_zero": True,
                        "download_bytes": len(body), "download_sha256": hashlib.sha256(body).hexdigest(),
                        "source_configuration_bytes_unchanged": True, "query_and_expiry_refused": True,
                        "browser_storage_or_private_echo": False, "foreign_requests": 0,
                        "actual_processor_model_controller_or_media_operations": 0,
                        "body_horizontal_overflow": False, "visual_review": "not_performed_by_runner"}
                finally:
                    await context.close()
        finally:
            await server.close()
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
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted((REPO / "src/aikey").glob("*.py"))}
    (args.output / "result.json").write_text(json.dumps(
        {"scenarios": results, "source_modules": before}, indent=2) + "\n")
    print(json.dumps({"desktop_mobile_keyboard": "passed", "owned_resources_collected": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable")
    asyncio.run(main(parser.parse_args()))
